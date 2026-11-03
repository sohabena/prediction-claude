"""S76 results reconciliation: real matches whose last tick is more than 3 h old with no decided result.

A decided result is ``win`` (with a winner), ``tie``, ``no_result`` or ``abandoned``. Anything else (no row,
``unresolved``, a ``win`` with an empty winner) is missing. Simulated (SRL, Virtual, E-Cricket) matches are never
listed: they are recorded on purpose and have no result that anyone checks.

Usage::

    python scripts/results_reconciliation.py                  # list missing results (last 7 days)
    python scripts/results_reconciliation.py --lookback-days 3

Read-only: the SQL is a SELECT run in a READ ONLY transaction. Exit code 0 when nothing is missing, 2 otherwise.
The data quality monitor (``results_missing``) and the paper report use the same SQL and selection rule.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.match_filter import is_simulated_name  # noqa: E402

STALE_AFTER = timedelta(hours=3)  # S76: a real match silent for longer than this with no result is missing
LOOKBACK = timedelta(days=7)  # the report's window; the monitor uses its own window (24 h)
SETTLED_RESULT_TYPES = frozenset({"win", "tie", "no_result", "abandoned"})

# One row per match with ticks in the window: last tick, names, registry flags and the stored result (if any).
# Parameters: :since (window start), :now (reference time). Read-only.
RESULTS_SQL = """
    WITH ticks AS (
        SELECT match_id, time FROM odds_ticks WHERE time >= :since
        UNION ALL
        SELECT match_id, time FROM market_ticks WHERE time >= :since
    ),
    last_tick AS (
        SELECT match_id, MAX(time) AS last_time FROM ticks GROUP BY match_id
    ),
    names AS (
        SELECT match_id, MAX(team_home) AS team_home, MAX(team_away) AS team_away,
               MAX(competition) AS competition
        FROM odds_ticks WHERE time >= :since GROUP BY match_id
    )
    SELECT l.match_id, n.team_home, n.team_away,
           COALESCE(NULLIF(n.competition, ''), s.competition) AS competition,
           s.is_simulated, s.match_start_time,
           l.last_time,
           EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - l.last_time)) AS age_s,
           r.match_id IS NOT NULL AS has_result,
           r.winner,
           r.result_type
    FROM last_tick l
    LEFT JOIN names n ON n.match_id = l.match_id
    LEFT JOIN match_results r ON r.match_id = l.match_id
    LEFT JOIN match_training_status s ON s.match_id = l.match_id
"""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_real(row: Mapping[str, Any]) -> bool:
    """Not simulated, by the registry flag or by the scraper's name rule (the same rule the monitor uses)."""
    if row.get("is_simulated"):
        return False
    return not is_simulated_name(row.get("competition"), row.get("team_home"), row.get("team_away"))


def has_decided_result(row: Mapping[str, Any]) -> bool:
    """True for a stored win (with a winner), tie, no_result or abandoned result."""
    result_type = str(row.get("result_type") or "").strip().lower()
    if result_type not in SETTLED_RESULT_TYPES:
        return False
    if result_type == "win":
        return bool(str(row.get("winner") or "").strip())
    return True


def select_missing(
    rows: Sequence[Mapping[str, Any]], now: datetime, stale_after: timedelta = STALE_AFTER,
) -> list[dict[str, Any]]:
    """Real matches whose last tick is more than ``stale_after`` before ``now`` and that have no decided result.

    Returns one dict per match, oldest last tick first: match_id, team_home, team_away, competition,
    match_start_time, last_tick, age_hours, result_type (None when no row) and has_result.
    """
    missing: list[dict[str, Any]] = []
    for row in rows:
        last = row.get("last_time")
        if last is None:
            continue
        last = _aware(last)
        if now - last <= stale_after:
            continue
        if not is_real(row) or has_decided_result(row):
            continue
        start = row.get("match_start_time")
        missing.append({
            "match_id": str(row["match_id"]),
            "team_home": row.get("team_home") or "",
            "team_away": row.get("team_away") or "",
            "competition": row.get("competition") or "",
            "match_start_time": _aware(start) if isinstance(start, datetime) else None,
            "last_tick": last,
            "age_hours": (now - last).total_seconds() / 3600.0,
            "result_type": row.get("result_type"),
            "has_result": bool(row.get("has_result")),
        })
    missing.sort(key=lambda item: item["last_tick"])
    return missing


def format_line(item: Mapping[str, Any]) -> str:
    start = item["match_start_time"]
    start_text = f"{start:%Y-%m-%d %H:%M} UTC" if start else "start not recorded"
    result = item["result_type"] or "no result row"
    return (
        f"{item['team_home']} v {item['team_away']} ({item['match_id']}) | {item['competition'] or 'competition not recorded'} "
        f"| start {start_text} | last tick {item['last_tick']:%Y-%m-%d %H:%M} UTC "
        f"({item['age_hours']:.1f} h ago) | result: {result}"
    )


async def missing_results(session: Any, now: datetime, lookback: timedelta = LOOKBACK) -> list[dict[str, Any]]:
    """Run RESULTS_SQL on ``session`` (the caller sets the read-only transaction) and select the missing matches."""
    from sqlalchemy import text

    result = await session.execute(text(RESULTS_SQL), {"since": now - lookback, "now": now})
    rows = [dict(r) for r in result.mappings().all()]
    return select_missing(rows, now)


async def _main(lookback_days: float) -> int:
    from sqlalchemy import text

    from shared.db import close_database, get_session

    now = datetime.now(timezone.utc)
    try:
        async with get_session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            missing = await missing_results(session, now, timedelta(days=lookback_days))
    finally:
        await close_database()
    print(f"{len(missing)} real match(es) with last tick over {STALE_AFTER.total_seconds() / 3600:g} h old and no "
          f"win/tie/no_result (last {lookback_days:g} days)")
    for item in missing:
        print("  " + format_line(item))
    return 2 if missing else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S76 list real matches missing a result (read-only).")
    parser.add_argument("--lookback-days", type=float, default=LOOKBACK.days,
                        help="look-back window in days for tick activity (default 7)")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.lookback_days))


if __name__ == "__main__":
    raise SystemExit(main())
