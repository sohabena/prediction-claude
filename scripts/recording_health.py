"""Read-only recording health report for the LotusBook recorder (S37).

Per UTC day it reports matches seen vs recorded, ticks per match, the median and p95 gap between
ticks, the share of recorded matches with match context and with a result. It also counts
synthetic rows (``source != 'lotusbook'``) and simulated rows (SRL / Virtual / E-Cricket names)
in ``odds_ticks``, and reads the recorder heartbeat written by the scraper and the watchdog.

From S50 on, SRL matches are recorded on purpose and tagged, so simulated rows are reported as a
count only. Only synthetic rows are contamination.

Exit codes:
    0  clean and the recorder is fresh
    1  synthetic rows exist in ``odds_ticks``
    2  the recorder heartbeat is stale or missing (no contamination found)

Usage::

    python scripts/recording_health.py [--days 7] [--max-feed-age-min 10] [--stall-minutes 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.match_filter import is_simulated_name  # noqa: E402
from shared.logging import setup_logging  # noqa: E402

logger = setup_logging("recording_health")

RECORDED_SOURCE = "lotusbook"
LOG_DIR = ROOT / "logs"
FEED_PATH = LOG_DIR / "scraper_feed.json"  # written by scraper.manager.write_feed_state
HEARTBEAT_PATH = LOG_DIR / "scraper_heartbeat.json"  # written by scripts/run_scraper_forever.py

EXIT_OK = 0
EXIT_CONTAMINATED = 1
EXIT_STALE = 2


# --------------------------------------------------------------------------- helpers
def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(row)


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _day(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).date().isoformat()


def within_match_gaps(times: Iterable[datetime]) -> list[float]:
    """Seconds between consecutive timestamps of ONE match (never across matches)."""
    ordered = sorted(times)
    return [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:])]


def gap_stats(gaps: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Median and p95 of a list of gaps in seconds."""
    if not gaps:
        return None, None
    ordered = sorted(gaps)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return statistics.median(ordered), p95


def classify_groups(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Count synthetic and simulated rows from ``GROUP BY source, names`` aggregates."""
    synthetic = 0
    simulated = 0
    examples: list[str] = []
    for g in groups:
        g = _row_dict(g)
        n = int(g.get("n") or 0)
        if n <= 0:
            continue
        if g.get("source") != RECORDED_SOURCE:
            synthetic += n
            examples.append(f"source={g.get('source')!r} match={g.get('team_home')} v {g.get('team_away')}")
        if is_simulated_name(g.get("competition"), g.get("team_home"), g.get("team_away")):
            simulated += n
            examples.append(f"simulated {g.get('team_home')} v {g.get('team_away')} ({g.get('competition')})")
    return {"synthetic_rows": synthetic, "simulated_rows": simulated, "examples": examples[:10]}


def summarise_days(
    tick_rows: Iterable[Any],
    seen_rows: Iterable[Any],
    context_ids: set[str],
    result_ids: set[str],
) -> list[dict[str, Any]]:
    """Per-day coverage from recorded (lotusbook) ticks and discovery rows."""
    seen_by_day: dict[str, set[str]] = defaultdict(set)
    for row in seen_rows:
        d = _row_dict(row)
        ts = _parse_ts(d.get("created_at"))
        if ts is not None and d.get("match_id"):
            seen_by_day[_day(ts)].add(str(d["match_id"]))

    times_by_day_match: dict[str, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    for row in tick_rows:
        d = _row_dict(row)
        if d.get("source") != RECORDED_SOURCE:
            continue
        ts = _parse_ts(d.get("time"))
        if ts is None or not d.get("match_id"):
            continue
        times_by_day_match[_day(ts)][str(d["match_id"])].append(ts)

    days = sorted(set(seen_by_day) | set(times_by_day_match))
    summary: list[dict[str, Any]] = []
    for day in days:
        per_match = times_by_day_match.get(day, {})
        recorded = set(per_match)
        seen = seen_by_day.get(day, set())
        counts = sorted(len(ts_list) for ts_list in per_match.values())
        # Gaps are pooled per match, then summarised; the worst match's p95 is shown too.
        match_gaps = {mid: within_match_gaps(ts_list) for mid, ts_list in per_match.items()}
        pooled = [g for gs in match_gaps.values() for g in gs]
        median_gap, p95_gap = gap_stats(pooled)
        worst_match_p95 = max(
            (p for p in (gap_stats(gs)[1] for gs in match_gaps.values()) if p is not None),
            default=None,
        )
        summary.append({
            "day": day,
            "seen": len(seen),
            "recorded": len(recorded),
            "missed": len(seen - recorded),
            "ticks": sum(counts),
            "ticks_per_match_median": statistics.median(counts) if counts else 0,
            "gap_median_s": median_gap,
            "gap_p95_s": p95_gap,
            "gap_p95_worst_match_s": worst_match_p95,
            "with_context": (len(recorded & context_ids) / len(recorded)) if recorded else 0.0,
            "with_result": (len(recorded & result_ids) / len(recorded)) if recorded else 0.0,
        })
    return summary


def feed_status(
    feed: Optional[dict[str, Any]],
    watchdog: Optional[dict[str, Any]],
    now: datetime,
    *,
    max_feed_age_min: float,
    stall_minutes: float,
) -> dict[str, Any]:
    """Decide whether the recorder is stale from the scraper feed and the watchdog heartbeat."""
    reasons: list[str] = []
    if feed is None:
        reasons.append(f"no scraper feed file at {FEED_PATH} (recorder never ran?)")
    else:
        updated = _parse_ts(feed.get("updated_at"))
        if updated is None or (now - updated) > timedelta(minutes=max_feed_age_min):
            reasons.append(f"scraper feed not updated for over {max_feed_age_min:g} min")
        live = int(feed.get("live_matches") or 0)
        last_live = _parse_ts(feed.get("last_live_event_at"))
        if live > 0 and (last_live is None or (now - last_live) > timedelta(minutes=stall_minutes)):
            reasons.append(f"recorder stalled: {live} live match(es), no live event for over {stall_minutes:g} min")
    if watchdog is None:
        reasons.append(f"no watchdog heartbeat at {HEARTBEAT_PATH} (scripts/run_scraper_forever.py not running)")
    else:
        beat = _parse_ts(watchdog.get("updated_at"))
        if beat is None or (now - beat) > timedelta(minutes=max_feed_age_min):
            reasons.append(f"watchdog heartbeat older than {max_feed_age_min:g} min")
    return {"stale": bool(reasons), "reasons": reasons}


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- DB layer
async def collect(session: Any, *, since: datetime) -> dict[str, Any]:
    """Run the read-only queries and return the report data (no writes)."""
    from sqlalchemy import text

    seen = (await session.execute(
        text("SELECT match_id, created_at FROM match_training_status WHERE created_at >= :since"),
        {"since": since},
    )).fetchall()
    ticks = (await session.execute(
        text(
            "SELECT time, match_id, source, team_home, team_away, competition FROM odds_ticks "
            "WHERE time >= :since AND source = :src ORDER BY match_id, time"
        ),
        {"since": since, "src": RECORDED_SOURCE},
    )).fetchall()
    groups = (await session.execute(
        text(
            "SELECT source, competition, team_home, team_away, COUNT(*) AS n FROM odds_ticks "
            "GROUP BY source, competition, team_home, team_away"
        )
    )).fetchall()
    context = (await session.execute(
        text("SELECT DISTINCT match_id FROM match_context WHERE time >= :since"), {"since": since}
    )).fetchall()
    results = (await session.execute(text("SELECT match_id FROM match_results"))).fetchall()

    context_ids = {str(_row_dict(r)["match_id"]) for r in context}
    result_ids = {str(_row_dict(r)["match_id"]) for r in results}
    contamination = classify_groups(groups)
    return {
        "days": summarise_days(ticks, seen, context_ids, result_ids),
        "contamination": contamination,
    }


def exit_code(report: dict[str, Any], feed: dict[str, Any]) -> int:
    # Simulated (SRL) rows are recorded on purpose since S50: counted and shown, never a failure.
    if report["contamination"]["synthetic_rows"]:
        return EXIT_CONTAMINATED
    if feed["stale"]:
        return EXIT_STALE
    return EXIT_OK


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}{unit}"
    return f"{value}{unit}"


def render(report: dict[str, Any], feed: dict[str, Any]) -> str:
    lines = [
        f"{'day':<11}{'seen':>6}{'rec':>6}{'missed':>8}{'ticks':>8}{'tick/m':>8}"
        f"{'gap med':>10}{'gap p95':>10}{'ctx%':>7}{'res%':>7}",
    ]
    for d in report["days"]:
        lines.append(
            f"{d['day']:<11}{d['seen']:>6}{d['recorded']:>6}{d['missed']:>8}{d['ticks']:>8}"
            f"{_fmt(d['ticks_per_match_median']):>8}{_fmt(d['gap_median_s'], 's'):>10}"
            f"{_fmt(d['gap_p95_s'], 's'):>10}{d['with_context'] * 100:>6.0f}%{d['with_result'] * 100:>6.0f}%"
        )
    if not report["days"]:
        lines.append("no recorded matches in the window")
    c = report["contamination"]
    lines.append(f"synthetic rows in odds_ticks: {c['synthetic_rows']}")
    lines.append(f"simulated rows in odds_ticks (SRL, recorded on purpose, not a failure): {c['simulated_rows']}")
    for ex in c["examples"]:
        lines.append(f"  - {ex}")
    lines.append("recorder: " + ("STALE" if feed["stale"] else "fresh"))
    for reason in feed["reasons"]:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    from shared.db import get_session

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    async with get_session() as session:
        report = await collect(session, since=since)
    feed = feed_status(
        _read_json(FEED_PATH), _read_json(HEARTBEAT_PATH), now,
        max_feed_age_min=args.max_feed_age_min, stall_minutes=args.stall_minutes,
    )
    print(render(report, feed))
    code = exit_code(report, feed)
    logger.info("recording_health", exit_code=code, days=len(report["days"]))
    return code


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=7, help="look-back window in days (default 7)")
    p.add_argument("--max-feed-age-min", type=float, default=10.0)
    p.add_argument("--stall-minutes", type=float, default=10.0)
    return asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
