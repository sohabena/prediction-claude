"""S97 dataset: market-dynamics snapshots from the LotusBook recordings, for training and evaluation.

Reads the database read-only (``SET TRANSACTION READ ONLY``; SELECT statements only) and writes::

    data/market_dynamics/dataset.parquet   (or dataset.csv when pyarrow is not installed)
    data/market_dynamics/manifest.json

One row per 30 s snapshot, in play, of every real canonical match with recorded prices (SRL and the second event id
of a fixture are skipped). Logic lives in ``training/market_dynamics.py``; this script only reads, builds and writes.

Usage::

    python scripts/build_market_dynamics_dataset.py --dry-run     # counts only, writes nothing
    python scripts/build_market_dynamics_dataset.py               # writes the dataset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import setup_logging  # noqa: E402
from training.market_dynamics import FEATURES, HORIZON_S, SNAPSHOT_EVERY_S, VERSION, build_dataset  # noqa: E402

logger = setup_logging("build_market_dynamics_dataset")

OUT_DIR = ROOT / "data" / "market_dynamics"
CONTEXT_SQL = (
    "SELECT match_id, time, is_live, score, wickets, overs, innings, balls_remaining, batting_team, bowling_team, "
    "run_rate, req_run_rate, match_format, status FROM match_context ORDER BY match_id, time"
)


async def _read_database() -> tuple[list[dict], list[dict], list[dict], str]:
    """Registry, recorded prices and score rows. Read-only transaction; SELECTs only."""
    from sqlalchemy import text

    from scripts.build_training_dataset import (  # the registry and price queries of the S58 builder
        ACTUAL_START_COLUMN_SQL,
        CANONICAL_COLUMN_SQL,
        PRICES_SQL,
        _registry_sql,
    )
    from shared.config import get_settings
    from shared.db import close_database, get_session

    settings = get_settings()
    db_label = f"{settings.database.host}:{settings.database.port}/{settings.database.db}"
    try:
        async with get_session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            has_actual = bool((await session.execute(text(ACTUAL_START_COLUMN_SQL))).scalar())
            has_canonical = bool((await session.execute(text(CANONICAL_COLUMN_SQL))).scalar())
            registry = [dict(r._mapping) for r in (await session.execute(text(_registry_sql(has_actual, has_canonical)))).fetchall()]
            prices = [dict(r._mapping) for r in (await session.execute(text(PRICES_SQL))).fetchall()]
            scores = [dict(r._mapping) for r in (await session.execute(text(CONTEXT_SQL))).fetchall()]
    finally:
        await close_database()
    return registry, prices, scores, db_label


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["match_id"]), []).append(row)
    return grouped


def _git_sha() -> Optional[str]:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True,
                                   timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _write(rows: list[dict], path: Path) -> Path:
    """Parquet when pyarrow is installed (the trainer image does not pin it, so CSV is the fallback)."""
    import pandas as pd

    frame = pd.DataFrame(rows)
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        path = path.with_suffix(".csv")
        frame.to_csv(path, index=False)
        return path
    frame.to_parquet(path, index=False)
    return path


async def _run(args: argparse.Namespace) -> int:
    registry, prices, scores, db_label = await _read_database()
    logger.info("database_read", registry=len(registry), price_rows=len(prices), score_rows=len(scores))
    rows, stats = build_dataset(registry, _group(prices), _group(scores))
    summary = {
        "rows": len(rows),
        "matches": len({r["match_id"] for r in rows}),
        "rows_with_scoreboard": sum(1 for r in rows if float(r["no_scoreboard"]) < 0.5),
        "rows_without_scoreboard": sum(1 for r in rows if float(r["no_scoreboard"]) >= 0.5),
        "stats": stats,
        "features": list(FEATURES),
        "snapshot_every_s": SNAPSHOT_EVERY_S,
        "horizon_s": HORIZON_S,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, default=str))
        return 0
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = _write(rows, out_dir / "dataset.parquet")
    manifest = {
        "dataset": "s97_market_dynamics",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "database": db_label,
        "data_file": data_path.name,
        "source_views": ["match_training_status", "v_match_odds", "match_context"],
        "counts": summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    logger.info("dataset_written", path=str(data_path), rows=len(rows), matches=summary["matches"])
    print(json.dumps({"written_to": str(data_path), **summary}, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the S97 market-dynamics dataset (read-only DB).")
    parser.add_argument("--dry-run", action="store_true", help="print counts only; write nothing")
    parser.add_argument("--out-dir", default=None, help="output directory (default data/market_dynamics)")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
