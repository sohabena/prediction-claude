"""S89: test the in-play model against the LotusBook in-play price on our recordings, and write the in-play gate.

Reads the database read-only (``SET TRANSACTION READ ONLY``; SELECT statements only) and writes::

    reports/inplay_eval/<YYYYMMDD>/summary.md
    reports/inplay_eval/<YYYYMMDD>/summary.json
    models/registry/inplay_gate.json          (the flag the trader reads; skipped with --no-gate)

Usage::

    python scripts/evaluate_inplay.py                  # today's date, writes the report and the gate
    python scripts/evaluate_inplay.py --no-gate        # report only

Population, scoring, clustering and the gate rule are documented in ``training/inplay_eval.py``. The in-play model and
ratings are loaded by ``virtual_trading.paper_trader.load_probability_service``, the same loader the trader uses.
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

from training import inplay_eval as ie  # noqa: E402

REPORT_ROOT = ROOT / "reports" / "inplay_eval"
REGISTRY_SQL = (
    "SELECT match_id, team_home, team_away, competition, match_start_time, is_simulated, score_pipeline, "
    "toss_winner, toss_decision FROM match_training_status WHERE score_pipeline = :pipeline"
)
RESULTS_SQL = (
    "SELECT match_id, winner, loser, result_type, source, completed_at, team_home, team_away "
    "FROM match_results WHERE match_id = ANY(:ids)"
)
CONTEXT_SQL = (
    "SELECT match_id, time, is_live, score, wickets, overs, innings, balls_remaining, batting_team, bowling_team, "
    "run_rate, req_run_rate, match_format, status FROM match_context WHERE match_id = ANY(:ids) ORDER BY match_id, time"
)
PRICES_SQL = (
    "SELECT match_id, time, back_home, lay_home, back_away, lay_away, source FROM v_match_odds "
    "WHERE match_id = ANY(:ids) AND time >= :since AND time <= :until ORDER BY match_id, time"
)


async def _read_database() -> dict[str, Any]:
    """Registry, results, states and prices for decided real livescore-v2 matches. One read-only transaction."""
    from sqlalchemy import text

    from shared.config import get_settings
    from shared.db import close_database, get_session

    settings = get_settings()
    db_label = f"{settings.database.host}:{settings.database.port}/{settings.database.db}"
    try:
        async with get_session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            registry = [dict(r) for r in (await session.execute(
                text(REGISTRY_SQL), {"pipeline": ie.PIPELINE})).mappings().all()]
            real_ids = sorted({str(r["match_id"]) for r in registry if not r["is_simulated"]})
            results = [dict(r) for r in (await session.execute(
                text(RESULTS_SQL), {"ids": real_ids})).mappings().all()] if real_ids else []
            decided_ids = sorted({str(r["match_id"]) for r in results
                                  if str(r.get("result_type") or "").lower() == "win"})
            contexts: list[dict[str, Any]] = []
            prices: list[dict[str, Any]] = []
            if decided_ids:
                contexts = [dict(r) for r in (await session.execute(
                    text(CONTEXT_SQL), {"ids": decided_ids})).mappings().all()]
                times = [r["time"] for r in contexts if r.get("time") is not None]
                if times:
                    since, until = min(times) - ie.STALE_PAD, max(times) + ie.SOFT_HORIZON + ie.STALE_PAD
                    prices = [dict(r) for r in (await session.execute(
                        text(PRICES_SQL), {"ids": decided_ids, "since": since, "until": until})).mappings().all()]
    finally:
        await close_database()
    return {"registry": registry, "results": results, "contexts": contexts, "prices": prices, "db": db_label}


def _git_sha() -> Optional[str]:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True,
                                   timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _load_service() -> Any:
    # Lazy: importing the trader pulls in its ledger modules, which only this command needs.
    from virtual_trading.paper_trader import load_probability_service

    return load_probability_service(ROOT)


def run(stamp: str, *, out_root: Path, gate_path: Path, write_gate: bool, n_boot: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    data = asyncio.run(_read_database())
    service = _load_service()
    evaluation = ie.evaluate_matches(service, data["registry"], data["results"], data["contexts"], data["prices"])
    summary = ie.summarise(evaluation["records"], n_boot=n_boot, evaluated_at=now.isoformat())

    meta = {
        "counts": {**evaluation["counts"], "states_evaluated": len(evaluation["records"])},
        "skips": evaluation["skips"],
    }
    out_dir = Path(out_root) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "s89_inplay_eval",
        "generated_at_utc": now.isoformat(),
        "git_sha": _git_sha(),
        "database": data["db"],
        "pipeline": ie.PIPELINE,
        "soft_horizon_s": int(ie.SOFT_HORIZON.total_seconds()),
        "devig": "band",
        "counts": meta["counts"],
        "skips": meta["skips"],
        "rule": ie.RULE_TEXT,
        **summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(ie.json_safe(payload), indent=2, default=str, allow_nan=False),
                                          encoding="utf-8")
    (out_dir / "summary.md").write_text(ie.render_markdown(summary, meta), encoding="utf-8")
    if write_gate:
        ie.write_gate(summary["gate"], gate_path)
    return {"out_dir": str(out_dir), "gate": summary["gate"], "counts": meta["counts"], "skips": meta["skips"],
            "formats": summary["formats"], "gate_written": str(gate_path) if write_gate else None}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S89 in-play model vs market test and gate (read-only DB).")
    parser.add_argument("--date", default=None, help="report folder, YYYYMMDD (default: today, UTC)")
    parser.add_argument("--out-root", type=Path, default=REPORT_ROOT)
    parser.add_argument("--gate-path", type=Path, default=ie.INPLAY_GATE_PATH)
    parser.add_argument("--no-gate", action="store_true", help="write the report only; leave the gate file alone")
    parser.add_argument("--n-boot", type=int, default=ie.N_BOOT)
    args = parser.parse_args(argv)
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    result = run(stamp, out_root=args.out_root, gate_path=args.gate_path, write_gate=not args.no_gate,
                 n_boot=args.n_boot)
    print(json.dumps(ie.json_safe(result), indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
