"""Build the S58 training dataset from LotusBook recordings.

Reads the database read-only (``SET TRANSACTION READ ONLY``; SELECT statements only), links each recorded match to
its Cricsheet match, and writes::

    data/training/<YYYYMMDD-HHMM>/matches.jsonl
    data/training/<YYYYMMDD-HHMM>/snapshots.jsonl
    data/training/<YYYYMMDD-HHMM>/manifest.json

Usage::

    python scripts/build_training_dataset.py --dry-run     # counts only, writes nothing
    python scripts/build_training_dataset.py               # writes the dataset

Logic lives in ``training/dataset.py``; this script only reads, links and writes.
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
from training.dataset import (  # noqa: E402
    DEFAULT_SNAPSHOT_OFFSETS,
    INPLAY_EVERY_S,
    STALE_LIMIT_S,
    CricsheetIndex,
    build_dataset,
    group_by_match,
    summarise,
)

logger = setup_logging("build_training_dataset")

RAW_DIR = ROOT / "data" / "raw"
TRAINING_DIR = ROOT / "data" / "training"
CRICSHEET_DIRS = ("ipl", "odis", "t20s", "tests")

REGISTRY_SQL = (
    "SELECT match_id, team_home, team_away, competition, training_status, match_start_time, is_simulated "
    "FROM match_training_status"
)
# S86: the registry adds actual_start_time when the column exists (migration applied); otherwise the old query runs
# and every match falls back to match_start_time.
REGISTRY_ACTUAL_START_SQL = (
    "SELECT match_id, team_home, team_away, competition, training_status, match_start_time, is_simulated, "
    "actual_start_time FROM match_training_status"
)
ACTUAL_START_COLUMN_SQL = (
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
    "WHERE table_name = 'match_training_status' AND column_name = 'actual_start_time')"
)
PRICES_SQL = (
    "SELECT match_id, time, back_home, lay_home, back_away, lay_away, source "
    "FROM v_match_odds ORDER BY match_id, time"
)
RESULTS_SQL = (
    "SELECT match_id, winner, loser, result_type, source, completed_at, team_home, team_away FROM match_results"
)


def _team_alias() -> dict[str, str]:
    """Franchise and national-team aliases shared with the Cricsheet harmonizer."""
    from scripts.cricsheet_harmonizer import CANONICAL_TEAMS

    return dict(CANONICAL_TEAMS)


def _load_cricsheet_entries() -> list[dict[str, Any]]:
    """Read the ``info`` block (teams, dates) of every Cricsheet JSON file."""
    entries: list[dict[str, Any]] = []
    for folder in CRICSHEET_DIRS:
        for path in sorted((RAW_DIR / folder).glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    info = json.load(handle).get("info", {})
            except (OSError, ValueError):
                logger.warning("cricsheet_file_unreadable", path=str(path))
                continue
            entries.append(
                {
                    "match_id": f"{folder}/{path.stem}",
                    "teams": info.get("teams"),
                    "dates": info.get("dates") or [],
                }
            )
    return entries


async def _read_database() -> tuple[list[dict], list[dict], list[dict], Optional[str]]:
    """Read registry, recorded prices and results. Read-only transaction; SELECTs only."""
    from sqlalchemy import text

    from shared.config import get_settings
    from shared.db import close_database, get_session

    settings = get_settings()
    db_label = f"{settings.database.host}:{settings.database.port}/{settings.database.db}"
    try:
        async with get_session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            has_actual = bool((await session.execute(text(ACTUAL_START_COLUMN_SQL))).scalar())
            registry_sql = REGISTRY_ACTUAL_START_SQL if has_actual else REGISTRY_SQL
            registry = [dict(r._mapping) for r in (await session.execute(text(registry_sql))).fetchall()]
            prices = [dict(r._mapping) for r in (await session.execute(text(PRICES_SQL))).fetchall()]
            results = [dict(r._mapping) for r in (await session.execute(text(RESULTS_SQL))).fetchall()]
    finally:
        await close_database()
    return registry, prices, results, db_label


def _git_sha() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = completed.stdout.strip()
    return sha or None


def _jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")


async def _run(args: argparse.Namespace) -> int:
    registry, prices, results, db_label = await _read_database()
    logger.info("database_read", registry=len(registry), price_rows=len(prices), results=len(results))

    entries = _load_cricsheet_entries()
    index = CricsheetIndex(entries, team_alias=_team_alias())

    dataset = build_dataset(
        registry,
        group_by_match(prices),
        {str(r["match_id"]): r for r in results},
        index,
        snapshot_offsets=DEFAULT_SNAPSHOT_OFFSETS,
        inplay_every_s=INPLAY_EVERY_S,
    )
    summary = summarise(dataset, price_rows_total=len(prices))
    summary["cricsheet_files_read"] = len(entries)

    if args.dry_run:
        print(json.dumps(summary, indent=2, default=str))
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out_dir = Path(args.out_dir) if args.out_dir else TRAINING_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=False)
    _jsonl(out_dir / "matches.jsonl", dataset["matches"])
    _jsonl(out_dir / "snapshots.jsonl", dataset["snapshots"])
    manifest = {
        "dataset": "s58_training_dataset",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "database": db_label,
        "source_views": ["match_training_status", "v_match_odds", "match_results"],
        "cricsheet_dirs": list(CRICSHEET_DIRS),
        "snapshot_offsets": [[label, seconds] for label, seconds in DEFAULT_SNAPSHOT_OFFSETS],
        "inplay_every_s": INPLAY_EVERY_S,
        "stale_limit_s": STALE_LIMIT_S,
        "counts": summary,
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, default=str)
    logger.info("dataset_written", path=str(out_dir), matches=summary["matches"], snapshots=summary["snapshots"])
    print(json.dumps({"written_to": str(out_dir), **summary}, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the S58 training dataset (read-only DB).")
    parser.add_argument("--dry-run", action="store_true", help="print counts only; write nothing")
    parser.add_argument("--out-dir", default=None, help="output directory (default data/training/<stamp>)")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
