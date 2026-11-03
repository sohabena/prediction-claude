"""S72 paper trader: place virtual bets on live and upcoming real matches (no real money, no site access).

Usage::

    python scripts/run_paper_trader.py --once          # one cycle, then exit (manual checks)
    python scripts/run_paper_trader.py --loop          # a cycle every 15 s, until stopped (the Docker service)
    python scripts/run_paper_trader.py --once --include-simulated   # also SRL matches (off by default)

Each cycle writes one JSON line to logs/paper_trader.log. Writes go only to the paper_bets table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import setup_logging  # noqa: E402
from virtual_trading import paper_review  # noqa: E402
from virtual_trading import paper_trader as pt  # noqa: E402

logger = setup_logging("paper_trader")

CYCLE_SECONDS = 15


def _error_record(exc: BaseException) -> dict:
    return {
        "cycle_at": datetime.now(timezone.utc).isoformat(),
        "error": type(exc).__name__,
        "message": str(exc)[:500],
    }


async def _loop(probability, variants, include_simulated: bool, log_path: Path) -> None:
    from scripts import paper_report  # S73: the daily review and report

    last_reviewed: Optional[date] = paper_review.last_review_day(paper_review.load_state())
    while True:
        started = asyncio.get_running_loop().time()
        try:
            report = await pt.run_once(probability, variants, include_simulated=include_simulated, log_path=log_path)
            print(json.dumps(report, default=str), flush=True)
        except Exception as exc:  # keep the service alive; the cycle's writes were rolled back
            record = _error_record(exc)
            pt.append_log(record, log_path)
            logger.error("paper_cycle_failed", error=type(exc).__name__, message=str(exc)[:200])
        try:
            last_reviewed = await paper_report.run_due_review(datetime.now(timezone.utc), last_reviewed)
        except Exception as exc:  # a failed review is retried next cycle; trading continues
            logger.error("paper_review_failed", error=type(exc).__name__, message=str(exc)[:200])
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, CYCLE_SECONDS - elapsed))


async def _once(probability, variants, include_simulated: bool, log_path: Path) -> int:
    report = await pt.run_once(probability, variants, include_simulated=include_simulated, log_path=log_path)
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


async def _run(args, probability, variants) -> int:
    from shared.db import close_database

    try:
        if args.once:
            return await _once(probability, variants, args.include_simulated, args.log)
        await _loop(probability, variants, args.include_simulated, args.log)
        return 0
    except Exception as exc:
        if args.once:
            print(json.dumps(_error_record(exc)), flush=True)
            return 1
        raise
    finally:
        await close_database()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S72 paper trader (virtual bets only).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--loop", action="store_true", help="run a cycle every 15 s")
    parser.add_argument("--include-simulated", action="store_true", help="also trade simulated (SRL) matches")
    parser.add_argument("--log", type=Path, default=pt.LOG_PATH, help="JSON-lines log file")
    args = parser.parse_args(argv)

    probability = pt.load_probability_service(pt.ROOT)
    variants = pt.build_variants()
    logger.info("paper_trader_start", mode="once" if args.once else "loop", variants=len(variants))
    return asyncio.run(_run(args, probability, variants))


if __name__ == "__main__":
    raise SystemExit(main())
