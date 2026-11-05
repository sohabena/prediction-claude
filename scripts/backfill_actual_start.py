"""S86 backfill: ``actual_start_time`` and ``actual_start_source`` for recorded matches, from the recorded rows.

For every registry row whose ``actual_start_time`` is NULL (real and SRL alike), the candidate is, by precedence:

1. ``listing_backfill``: the first ``odds_ticks`` row of the match with ``is_live = true`` (LotusBook source);
2. ``score_backfill``: the first ``match_context`` row of the match in the first innings with ``overs > 0``
   (a ball bowled), when no listing tick exists.

A match with neither has no candidate and is left alone. Times are the recorded row times, so the backfill never
invents a start earlier than the data shows.

Usage::

    python scripts/backfill_actual_start.py            # dry run (default): prints the table and the summary
    python scripts/backfill_actual_start.py --apply    # writes only actual_start_time and actual_start_source,
                                                       # and only where actual_start_time is NULL

The dry run reads in a READ ONLY transaction. ``--apply`` is the operator's step; nothing runs it automatically.
Exit codes: 0 done (or dry run), 1 the S86 columns are missing (run the migration first) or the database failed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.actual_start import (  # noqa: E402
    BACKFILL_PRECEDENCE,
    SOURCE_LISTING_BACKFILL,
    SOURCE_SCORE_BACKFILL,
    pick_actual_start,
)
from scraper.match_filter import is_simulated_name  # noqa: E402

DIFFERS_MIN = 15  # a backfilled start further than this from match_start_time is counted in the summary

COLUMN_SQL = (
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
    "WHERE table_name = 'match_training_status' AND column_name = 'actual_start_time') AS present"
)
REGISTRY_SQL = """
    SELECT s.match_id, s.team_home, s.team_away, s.competition, s.is_simulated, s.match_start_time,
           s.actual_start_time, s.actual_start_source
    FROM match_training_status s
    WHERE s.actual_start_time IS NULL
      AND s.match_id NOT LIKE 'cric\_%'  -- synthetic Cricsheet matches are not recorded LotusBook matches
      AND (s.match_id IN (SELECT match_id FROM odds_ticks)
           OR s.match_id IN (SELECT match_id FROM match_context))
"""
LISTING_SQL = """
    SELECT match_id, MIN(time) AS first_live_tick
    FROM odds_ticks
    WHERE is_live AND source = 'lotusbook'
    GROUP BY match_id
"""
SCORE_SQL = """
    SELECT match_id, MIN(time) AS first_ball
    FROM match_context
    WHERE innings = 1 AND overs > 0
    GROUP BY match_id
"""
UPDATE_SQL = """
    UPDATE match_training_status
       SET actual_start_time = :when, actual_start_source = :source
     WHERE match_id = :mid AND actual_start_time IS NULL
"""


def _is_real(row: Mapping[str, Any]) -> bool:
    return not (bool(row.get("is_simulated"))
                or is_simulated_name(row.get("competition"), row.get("team_home"), row.get("team_away")))


def plan_backfill(
    registry: Sequence[Mapping[str, Any]],
    first_live: Mapping[str, datetime],
    first_ball: Mapping[str, datetime],
) -> list[dict[str, Any]]:
    """One plan row per registry row with a NULL actual start. Pure: no database access.

    ``when`` and ``source`` are None when no recorded signal exists for the match. ``delta_min`` is the candidate
    minus ``match_start_time`` in minutes (None when either is missing).
    """
    plan: list[dict[str, Any]] = []
    for row in registry:
        mid = str(row["match_id"])
        chosen = pick_actual_start(
            [
                (SOURCE_LISTING_BACKFILL, first_live.get(mid)),
                (SOURCE_SCORE_BACKFILL, first_ball.get(mid)),
            ],
            BACKFILL_PRECEDENCE,
        )
        when, source = (chosen[1], chosen[0]) if chosen else (None, None)
        start = row.get("match_start_time")
        delta = (when - start).total_seconds() / 60.0 if (when is not None and start is not None) else None
        plan.append({
            "match_id": mid,
            "team_home": row.get("team_home"),
            "team_away": row.get("team_away"),
            "competition": row.get("competition"),
            "real": _is_real(row),
            "match_start_time": start,
            "when": when,
            "source": source,
            "delta_min": delta,
        })
    return plan


def summarise_plan(plan: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for the dry run: candidates by source, no-signal matches, and matches more than 15 min off the start."""
    by_source: dict[str, int] = {}
    for item in plan:
        if item["source"]:
            by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    with_signal = [item for item in plan if item["when"] is not None]
    off = [item for item in with_signal if item["delta_min"] is not None and abs(item["delta_min"]) > DIFFERS_MIN]
    return {
        "matches_without_actual_start": len(plan),
        "real": sum(1 for item in plan if item["real"]),
        "simulated": sum(1 for item in plan if not item["real"]),
        "with_signal": len(with_signal),
        "by_source": by_source,
        "no_signal": len(plan) - len(with_signal),
        "no_match_start_time": sum(1 for item in with_signal if item["match_start_time"] is None),
        f"differs_over_{DIFFERS_MIN}_min": len(off),
        f"differs_over_{DIFFERS_MIN}_min_real": sum(1 for item in off if item["real"]),
        f"differs_over_{DIFFERS_MIN}_min_simulated": sum(1 for item in off if not item["real"]),
    }


def _stamp(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def format_table(plan: Sequence[Mapping[str, Any]]) -> str:
    """Fixed-width table of the plan, one line per match. Times are UTC, minute resolution."""
    header = (f"{'match_id':<16} {'kind':<5} {'match':<44} {'match_start_time':<17} "
              f"{'candidate':<17} {'source':<17} {'delta':>7}")
    lines = [header, "-" * len(header)]
    for item in plan:
        match = f"{item['team_home']} v {item['team_away']}"[:44]
        delta = f"{item['delta_min']:+.0f}m" if item["delta_min"] is not None else "-"
        lines.append(
            f"{item['match_id']:<16} {'real' if item['real'] else 'srl':<5} {match:<44} "
            f"{_stamp(item['match_start_time']):<17} {_stamp(item['when']):<17} "
            f"{(item['source'] or '-'):<17} {delta:>7}"
        )
    return "\n".join(lines)


async def read_plan(session: Any) -> list[dict[str, Any]]:
    """The plan from the database. SELECTs only."""
    from sqlalchemy import text

    registry = [dict(r._mapping) for r in (await session.execute(text(REGISTRY_SQL))).fetchall()]
    first_live = {str(r._mapping["match_id"]): r._mapping["first_live_tick"]
                  for r in (await session.execute(text(LISTING_SQL))).fetchall()}
    first_ball = {str(r._mapping["match_id"]): r._mapping["first_ball"]
                  for r in (await session.execute(text(SCORE_SQL))).fetchall()}
    return plan_backfill(registry, first_live, first_ball)


async def apply_plan(session: Any, plan: Sequence[Mapping[str, Any]]) -> int:
    """Write the candidates. Only the two S86 columns, and only where actual_start_time is still NULL. Returns rows."""
    from sqlalchemy import text

    written = 0
    for item in plan:
        if item["when"] is None:
            continue
        result = await session.execute(
            text(UPDATE_SQL), {"when": item["when"], "source": item["source"], "mid": item["match_id"]}
        )
        written += (getattr(result, "rowcount", 0) or 0)
    return written


async def run(apply: bool, session_factory: Any = None) -> int:
    """Dry run by default. ``session_factory`` (a callable returning an async context manager) is for tests."""
    from sqlalchemy import text

    close_database = None
    if session_factory is None:
        from shared.db import close_database as _close, get_session

        session_factory, close_database = get_session, _close
    try:
        async with session_factory() as session:
            if not apply:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            if not bool((await session.execute(text(COLUMN_SQL))).scalar()):
                print("actual_start_time column not present: apply the S86 migration (backend/db_init.py) first.")
                return 1
            plan = await read_plan(session)
            print(format_table(plan) if plan else "No recorded match without an actual start.")
            print()
            print("summary:")
            for key, value in summarise_plan(plan).items():
                print(f"  {key}: {value}")
            if not apply:
                print("\ndry run: nothing written. Re-run with --apply to write actual_start_time where it is NULL.")
                return 0
            written = await apply_plan(session, plan)
            print(f"\napplied: {written} row(s) written (actual_start_time, actual_start_source).")
            return 0
    except Exception as exc:
        print(f"backfill failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    finally:
        if close_database is not None:
            await close_database()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S86: backfill actual_start_time from recorded rows (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="write the candidates (default: dry run)")
    args = parser.parse_args(argv)
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
