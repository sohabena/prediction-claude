"""Read-only data quality monitor for the LotusBook recording run (S55).

Runs twelve checks over the last N hours (default 24) of ``odds_ticks``, ``market_ticks``,
``match_context``, ``match_results`` and ``match_training_status``. It prints a table, appends one
JSON line to ``logs/data_quality.jsonl`` and exits:

    0  every check is ok
    1  at least one check failed (or the database could not be read)
    2  warnings only

SQL is ``SELECT`` only, runs in a READ ONLY transaction and takes parameters, never built values.
Simulated (SRL) matches are recorded on purpose: they are counted and reported, never a failure.

Usage::

    python scripts/data_quality_monitor.py [--window-hours 24]          # run once
    python scripts/data_quality_monitor.py --interval 3600             # repeat hourly (container)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.match_filter import is_simulated_name  # noqa: E402
from scripts.quarantine_ticks import RULES, classify  # noqa: E402
from scripts.link_twins import (  # noqa: E402
    CANONICAL_COLUMN_SQL, TWIN_ROWS_SQL, MigrationMissing, build_plan, link_now, unlinked_links,
)
from scripts.results_reconciliation import RESULTS_SQL, select_missing  # noqa: E402
from shared.logging import setup_logging  # noqa: E402
from training.registry import DEFAULT_ROOT, DRIFT_MIN_SETTLED, Registry, drift_assessment, read_jsonl  # noqa: E402

logger = setup_logging("data_quality_monitor")

LOG_PATH = ROOT / "logs" / "data_quality.jsonl"

OK, WARN, FAIL = "ok", "warn", "fail"
EXIT_BY_STATUS = {OK: 0, FAIL: 1, WARN: 2}

# Thresholds (seconds unless stated)
LIVE_FRESH_FAIL_S = 15 * 60  # newest row older than this while a match is live: fail
RESULT_STALE_S = 6 * 3600  # last tick older than this with no result row: fail
GAP_WARN_S = 6 * 60  # gap inside one live match: warn (the live heartbeat stores at least every 5 min, plus scrape slack)
RESULT_PROVISIONAL_S = 45 * 60  # matches seen within this long may still be in play (scraper UNRESOLVED_ABSENCE_SECONDS)
# Live listing ticks are re-stored every 5 min when unchanged only since this deploy; before it, a gap cannot tell a
# quiet market from a recorder outage, so such gaps are reported apart and not scored.
LIVE_HEARTBEAT_SINCE = datetime(2026, 11, 3, 4, 5, tzinfo=timezone.utc)
TIE_SECONDS = 10  # clock slack between a listing tick and the match-page rows before it
MIN_ODDS = 1.01  # valid prices lie in [MIN_ODDS, MAX_ODDS]; 1.01 is the minimum quoted price
MAX_ODDS = 1000.0
COVERAGE_WARN = 0.90  # share of live matches with match-page rows
DUP_FAIL = 0.01  # share of identical consecutive market rows
LAY_WARN = 0.01  # share of live listing ticks with null lays and no match-page match-odds coverage
LAY_FAIL = 0.10
PIPELINE_V2 = "livescore-v2"  # S68: score feed pipeline that carries the live score (see parsers/lotusbook_score.py)
PIPELINE_LEGACY = "legacy"  # S68: score feed pipeline with no score in its body
DRIFT_WINDOW_DAYS = 7  # S65: settled matches used for the champion's calibration and drift check
PAPER_LOG_PATH = ROOT / "logs" / "paper_trader.log"  # S73: one JSON line per paper trader cycle
PAPER_FRESH_FAIL_S = 5 * 60  # trader log older than this while matches are live: fail
PAPER_SETTLE_FAIL_S = 12 * 3600  # filled bets still open this long after their result: fail
# Per match, per variant: mirrors HARD_MATCH_LOSS in virtual_trading/paper_trader.py (the hard limit on the worst outcome)
# Operator, 2026-11-03: the 100,000 budget has 20% tolerance; a worst case beyond 120,000 (the trader's HARD_MATCH_LOSS)
# is a failure.
PAPER_MATCH_LOSS = 120_000.0
PAPER_SANITY_CONTROL = "market_only"  # S75: the control variant, which should place (almost) nothing
PAPER_SANITY_CONTROL_MAX = 2  # trades by the control in 24 h
# A filled bet whose probability differs from the price's implied probability by more than this is implausible. A
# probability gap, not the edge: at long odds a few points of probability is a large edge (2026-11-03: 0.085 vs
# 0.043 at 23.0 is an edge of 0.95, a real model-market disagreement, not a pricing bug).
PAPER_SANITY_PROB_GAP_MAX = 0.20
# Operator, 2026-11-04: the 30-ticket budget limits new risk (enter, add, flip); hedges (reduce, green-up) may go past
# it. More new-risk bets than the budget on one variant-match is a failure; a busy match with hedges is a review.
PAPER_SANITY_NEW_RISK_MAX = 30  # new-risk matched bets by one variant on one match in 24 h (the ticket budget)
PAPER_SANITY_MATCH_TRADES_REVIEW = 45  # all matched bets by one variant on one match, hedges included: review
# Bets decided before the S90 strategy went live (2026-11-03 12:36 UTC, written into the two SQL queries) are not
# counted: they were placed by code that S75 and S90 replaced (S90 limits a variant to 10 tickets per match).
# S77: pre-match match pages. Matches that started on or after PREMATCH_PAGES_SINCE are scored. The operator sets
# this to the time the S77 scraper was deployed (placeholder below) before the first run that should count.
PREMATCH_PAGES_SINCE = datetime(2026, 11, 3, 11, 1, tzinfo=timezone.utc)  # S77 scraper deploy
PREMATCH_PAGE_WARN = 0.90  # share of scored real matches with a match-page row before their start
START_GAP_WARN_S = 15 * 60  # S86: listing and JSON starts further apart than this disagree
ACTUAL_LATE_WARN_S = 60 * 60  # S86: actual start later than match_start_time by more than this is reported
# Lead before the start, in minutes: same variable and default as the scraper (PREMATCH_PAGE_LEAD_MIN, 75).
PREMATCH_PAGE_LEAD_MIN = float(os.environ.get("PREMATCH_PAGE_LEAD_MIN") or 75)

Fetch = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]

# --------------------------------------------------------------------------- SQL (SELECT only)
SQL: dict[str, str] = {
    "freshness": """
        SELECT
          EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - (SELECT MAX(time) FROM odds_ticks WHERE time >= :since))) AS odds_age_s,
          EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - (SELECT MAX(time) FROM market_ticks WHERE time >= :since))) AS market_age_s,
          (SELECT COUNT(*) FROM (
              SELECT DISTINCT ON (match_id) match_id, is_live
              FROM odds_ticks WHERE time >= :since
              ORDER BY match_id, time DESC, tick_seq DESC
          ) latest WHERE latest.is_live) AS live_now
    """,
    "odds_prices": """
        SELECT
          (SELECT COUNT(*) FROM odds_ticks t
             CROSS JOIN LATERAL (VALUES (t.back_home), (t.lay_home), (t.back_draw),
                                        (t.lay_draw), (t.back_away), (t.lay_away)) AS p(v)
           WHERE t.time >= :since AND p.v = 0) AS zero_prices,
          (SELECT COUNT(*) FROM odds_ticks t
             CROSS JOIN LATERAL (VALUES (t.back_home), (t.lay_home), (t.back_draw),
                                        (t.lay_draw), (t.back_away), (t.lay_away)) AS p(v)
           WHERE t.time >= :since AND p.v <> 0 AND (p.v < :min_odds OR p.v > :max_odds)) AS out_of_range,
          (SELECT COUNT(*) FROM odds_ticks t
           WHERE t.time >= :since AND (t.back_home > t.lay_home
                                       OR t.back_draw > t.lay_draw
                                       OR t.back_away > t.lay_away)) AS crossed,
          (SELECT COUNT(*) FROM odds_ticks t WHERE t.time >= :since) AS rows
    """,
    "market_prices": """
        SELECT
          (SELECT COUNT(*) FROM market_ticks t
             CROSS JOIN LATERAL (VALUES (t.back), (t.lay)) AS p(v)
           WHERE t.time >= :since AND p.v = 0) AS zero_prices,
          -- The 1.01 floor is the exchange's (match odds). Bookmaker-style markets quote finer steps near the end of
          -- a match (2026-11-03: "Bookmaker 0 Commission" at 1.0075), so for them only a price above 1.0 is required.
          (SELECT COUNT(*) FROM market_ticks t
             CROSS JOIN LATERAL (VALUES (t.back), (t.lay)) AS p(v)
           WHERE t.time >= :since AND p.v <> 0
             AND (CASE WHEN t.market_type = 'match_odds' THEN p.v < :min_odds ELSE p.v <= 1.0 END
                  OR p.v > :max_odds)) AS out_of_range,
          (SELECT COUNT(*) FROM market_ticks t
           WHERE t.time >= :since AND t.market_type IN ('match_odds', 'bookmaker')
             AND t.back > t.lay) AS crossed,
          (SELECT COUNT(*) FROM market_ticks t WHERE t.time >= :since) AS rows
    """,
    "duplicates": """
        WITH ordered AS (
            SELECT time, market_type, back, lay, status, line_no, rate_no, line_yes, rate_yes,
                   back_size, lay_size, runner_status,
                   LAG(back_size) OVER w AS p_back_size,
                   LAG(lay_size) OVER w AS p_lay_size,
                   LAG(runner_status) OVER w AS p_runner_status,
                   LAG(time) OVER w AS p_time,
                   LAG(back) OVER w AS p_back,
                   LAG(lay) OVER w AS p_lay,
                   LAG(status) OVER w AS p_status,
                   LAG(line_no) OVER w AS p_line_no,
                   LAG(rate_no) OVER w AS p_rate_no,
                   LAG(line_yes) OVER w AS p_line_yes,
                   LAG(rate_yes) OVER w AS p_rate_yes
            FROM market_ticks
            WHERE time >= :since
            WINDOW w AS (PARTITION BY match_id, market_type, market_name, selection ORDER BY time)
        )
        SELECT COUNT(*) AS rows,
               COUNT(*) FILTER (WHERE p_time IS NOT NULL
                   AND back IS NOT DISTINCT FROM p_back
                   AND lay IS NOT DISTINCT FROM p_lay
                   AND status IS NOT DISTINCT FROM p_status
                   AND line_no IS NOT DISTINCT FROM p_line_no
                   AND rate_no IS NOT DISTINCT FROM p_rate_no
                   AND line_yes IS NOT DISTINCT FROM p_line_yes
                   AND rate_yes IS NOT DISTINCT FROM p_rate_yes
                   AND back_size IS NOT DISTINCT FROM p_back_size
                   AND lay_size IS NOT DISTINCT FROM p_lay_size
                   AND runner_status IS NOT DISTINCT FROM p_runner_status
                   -- an unchanged match-odds row 5 min after the last one is the match-page heartbeat, not a duplicate
                   AND NOT (market_type = 'match_odds' AND time - p_time >= INTERVAL '290 seconds')) AS duplicates
        FROM ordered
    """,
    "live_matches": """
        WITH live AS (
            SELECT match_id,
                   MAX(team_home) AS team_home,
                   MAX(team_away) AS team_away,
                   MAX(competition) AS competition,
                   COUNT(*) AS odds_rows,
                   MAX(time) AS last_live
            FROM odds_ticks
            WHERE time >= :since AND is_live
            GROUP BY match_id
        )
        SELECT l.match_id, l.team_home, l.team_away, l.competition, l.odds_rows,
               (l.last_live < COALESCE((SELECT MIN(time) FROM market_ticks), now())) AS before_match_pages,
               EXISTS (SELECT 1 FROM market_ticks m
                       WHERE m.match_id = l.match_id AND m.time >= :since) AS has_market,
               EXISTS (SELECT 1 FROM match_context c
                       WHERE c.match_id = l.match_id AND c.time >= :since) AS has_context
        FROM live l
    """,
    "prematch_pages": """
        WITH started AS (
            -- Scored on matches that actually started (S86 actual_start_time), at their real off: a scheduled start
            -- can be stale (2026-11-03: New Zealand v India, still at a pre-timezone-fix 20:30, had not started).
            SELECT s.match_id, s.team_home, s.team_away, s.competition, s.actual_start_time AS match_start_time,
                   (SELECT MIN(o.time) FROM odds_ticks o
                     WHERE o.match_id = s.match_id
                       AND o.time BETWEEN s.actual_start_time - INTERVAL '2 days' AND s.actual_start_time) AS first_listed
            FROM match_training_status s
            WHERE s.actual_start_time IS NOT NULL
              AND s.actual_start_time >= CAST(:prematch_since AS timestamptz)
              AND s.actual_start_time >= CAST(:since AS timestamptz)
              AND s.actual_start_time <= CAST(:now AS timestamptz)
        )
        SELECT st.match_id, st.team_home, st.team_away, st.competition, st.match_start_time,
               (st.first_listed IS NOT NULL
                AND st.first_listed <= st.match_start_time
                    - CAST(:prematch_lead_s AS double precision) * INTERVAL '1 second') AS listed_in_time,
               EXISTS (SELECT 1 FROM market_ticks m
                       WHERE m.match_id = st.match_id
                         AND m.time < st.match_start_time
                         AND m.time >= st.match_start_time - INTERVAL '2 days') AS has_prematch_page
        FROM started st
    """,
    "score_pipeline_column": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'match_training_status' AND column_name = 'score_pipeline'
        ) AS present
    """,
    "score_pipelines": """
        SELECT s.match_id, s.score_pipeline
        FROM match_training_status s
        WHERE s.score_pipeline IS NOT NULL
          AND s.match_id IN (SELECT match_id FROM odds_ticks WHERE time >= :since)
    """,
    "lay_completeness": """
        WITH pages AS (SELECT MIN(time) AS start FROM market_ticks),
        t AS (
            SELECT o.time,
                   (o.lay_home IS NULL AND o.lay_draw IS NULL AND o.lay_away IS NULL
                    AND (o.back_home IS NOT NULL OR o.back_draw IS NOT NULL OR o.back_away IS NOT NULL)) AS no_lay,
                   EXISTS (
                       SELECT 1 FROM market_ticks m
                       WHERE m.match_id = o.match_id
                         AND m.market_type = 'match_odds'
                         AND m.time <= o.time + make_interval(secs => CAST(:tie_seconds AS double precision))
                         AND m.time >= o.time - INTERVAL '30 minutes'
                   ) AS covered
            FROM odds_ticks o
            WHERE o.time >= :since AND o.is_live AND o.source = 'lotusbook'
        )
        SELECT COUNT(*) AS live_ticks,
               COUNT(*) FILTER (WHERE no_lay) AS null_lay_ticks,
               COUNT(*) FILTER (WHERE no_lay AND covered) AS recoverable,
               COUNT(*) FILTER (WHERE no_lay AND NOT covered
                                  AND (pages.start IS NULL OR t.time < pages.start)) AS before_match_pages
        FROM t CROSS JOIN pages
    """,
    "gaps": """
        WITH span AS (
            SELECT match_id, MIN(time) AS t0, MAX(time) AS t1
            FROM odds_ticks
            WHERE time >= :since AND is_live
            GROUP BY match_id
        ),
        ticks AS (
            SELECT o.match_id, o.time, FALSE AS halted
            FROM odds_ticks o JOIN span s ON s.match_id = o.match_id
            WHERE o.time >= :since AND o.time BETWEEN s.t0 AND s.t1
            UNION ALL
            -- A match-odds row showing the market closed or suspended: the site offered no price after it, so a gap
            -- that follows is a stoppage (rain, bad light, a market reset), not lost recording (2026-11-03, lb_76227860).
            SELECT m.match_id, m.time,
                   (m.market_type = 'match_odds' AND lower(m.status) IN ('closed', 'suspended')) AS halted
            FROM market_ticks m JOIN span s ON s.match_id = m.match_id
            WHERE m.time >= :since AND m.time BETWEEN s.t0 AND s.t1
        ),
        per_time AS (
            SELECT match_id, time, bool_or(halted) AS halted FROM ticks GROUP BY match_id, time
        ),
        gaps AS (
            SELECT match_id, time,
                   EXTRACT(EPOCH FROM time - LAG(time) OVER (PARTITION BY match_id ORDER BY time)) AS gap_s,
                   COALESCE(LAG(halted) OVER (PARTITION BY match_id ORDER BY time), FALSE) AS after_halt
            FROM per_time
        )
        SELECT match_id,
               MAX(gap_s) FILTER (WHERE time >= :heartbeat_since AND NOT after_halt) AS max_gap_s,
               MAX(gap_s) FILTER (WHERE time < :heartbeat_since AND NOT after_halt) AS max_gap_before_heartbeat_s,
               MAX(gap_s) FILTER (WHERE after_halt) AS max_halted_gap_s
        FROM gaps
        GROUP BY match_id
    """,
    "registry_coverage": """
        SELECT
          (SELECT COUNT(*) FROM (
              SELECT match_id FROM odds_ticks WHERE time >= :since
              UNION
              SELECT match_id FROM market_ticks WHERE time >= :since
          ) seen
           WHERE NOT EXISTS (SELECT 1 FROM match_training_status s WHERE s.match_id = seen.match_id)
          ) AS missing_registry,
          (SELECT COUNT(*) FROM match_training_status s
           WHERE (s.competition IS NULL OR s.competition = '')
             AND s.match_id IN (
                 SELECT match_id FROM odds_ticks WHERE time >= :since
                 UNION
                 SELECT match_id FROM market_ticks WHERE time >= :since
             )
          ) AS empty_registry_competition
    """,
    "odds_totals": """
        SELECT COUNT(*) AS rows,
               COUNT(*) FILTER (WHERE competition IS NULL OR competition = '') AS empty_competition
        FROM odds_ticks
        WHERE time >= :since
    """,
    "match_names": """
        SELECT match_id,
               MAX(team_home) AS team_home,
               MAX(team_away) AS team_away,
               MAX(competition) AS competition,
               COUNT(*) AS odds_rows,
               COUNT(DISTINCT team_home || ' v ' || team_away) AS name_variants
        FROM odds_ticks
        WHERE time >= :since
        GROUP BY match_id
    """,
    "is_simulated_column": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'match_training_status' AND column_name = 'is_simulated'
        ) AS present
    """,
    "is_simulated_rows": """
        SELECT COUNT(*) AS n
        FROM match_training_status m
        WHERE m.is_simulated IS TRUE
          AND EXISTS (SELECT 1 FROM odds_ticks o WHERE o.match_id = m.match_id AND o.time >= :since)
    """,
    "misattribution_ticks": """
        SELECT o.time, o.match_id, o.tick_seq, o.team_home, o.team_away,
               o.back_home, o.lay_home, o.back_away, o.lay_away
        FROM odds_ticks o
        WHERE o.source = 'lotusbook'
          AND o.match_id IN (SELECT DISTINCT match_id FROM odds_ticks WHERE time >= :since)
        ORDER BY o.match_id, o.time, o.tick_seq
    """,
    "settled_recent": """
        SELECT r.match_id, r.winner, r.team_home, r.team_away
        FROM match_results r
        WHERE r.completed_at >= CAST(:now AS timestamptz) - INTERVAL '7 days'
          AND r.winner IS NOT NULL AND r.winner <> ''
    """,
    "paper_open_fills": """
        SELECT variant, match_id, side, selection, price_filled, stake
        FROM paper_bets
        WHERE status = 'filled' AND price_filled IS NOT NULL
    """,
    "paper_stale_settlement": """
        SELECT p.variant, p.match_id, COUNT(*) AS bets, MIN(m.completed_at) AS result_at
        FROM paper_bets p
        JOIN match_results m ON m.match_id = p.match_id
        WHERE p.status = 'filled'
          AND m.result_type IN ('win', 'tie', 'no_result', 'no result', 'abandoned', 'abandonment')
          AND m.completed_at <= CAST(:now AS timestamptz) - INTERVAL '12 hours'
        GROUP BY p.variant, p.match_id
    """,
    "paper_sanity_trades": """
        SELECT variant, match_id, COUNT(*) AS orders,
               COUNT(*) FILTER (WHERE status IN ('filled', 'settled', 'void')) AS trades,
               COUNT(*) FILTER (WHERE status IN ('filled', 'settled', 'void')
                                AND reason IN ('enter', 'add', 'flip')) AS new_risk
        FROM paper_bets
        WHERE decided_at >= GREATEST(CAST(:now AS timestamptz) - INTERVAL '24 hours', TIMESTAMPTZ '2026-11-03 12:36:00+00')
        GROUP BY variant, match_id
    """,
    "paper_sanity_edges": """
        SELECT variant, match_id, side, selection, edge, price_filled, p_used
        FROM paper_bets
        WHERE status IN ('filled', 'settled', 'void')
          AND price_filled IS NOT NULL
          AND decided_at >= GREATEST(CAST(:now AS timestamptz) - INTERVAL '24 hours', TIMESTAMPTZ '2026-11-03 12:36:00+00')
    """,
    "actual_start_columns": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'match_training_status' AND column_name = 'actual_start_time'
        ) AS present
    """,
    "start_times": """
        SELECT s.match_id, s.team_home, s.team_away, s.competition, s.match_start_time,
               s.actual_start_time, s.actual_start_source,
               (SELECT MIN(o.time) FROM odds_ticks o
                 WHERE o.match_id = s.match_id AND o.is_live AND o.source = 'lotusbook') AS first_live_tick
        FROM match_training_status s
        WHERE s.match_id IN (SELECT match_id FROM odds_ticks WHERE time >= :since)
    """,
    "results": RESULTS_SQL,  # S76: shared with scripts/results_reconciliation.py
    "canonical_column": CANONICAL_COLUMN_SQL,  # S96: presence of match_training_status.canonical_match_id
    "twin_rows": TWIN_ROWS_SQL,  # S96: lb_ registry rows with match-page counts and results (scripts/link_twins.py)
    # S105: a bookmaker ("_BR") match-odds market stored under the plain match_odds name on a match that also has the
    # exchange market. A row whose relabel would collide with an existing key is counted apart (colliding).
    "bookmaker_leak": """
        WITH leaks AS (
            SELECT m.match_id,
                   EXISTS (
                       SELECT 1 FROM market_ticks x
                       WHERE x.time = m.time AND x.match_id = m.match_id AND x.market_type = m.market_type
                         AND x.market_name = 'match_odds#' || m.market_id AND x.selection = m.selection
                   ) AS colliding
            FROM market_ticks m
            WHERE m.time >= :since
              AND m.market_type = 'match_odds' AND m.market_name = 'match_odds'
              AND RIGHT(m.market_id, 3) = '_BR'
              AND EXISTS (
                  SELECT 1 FROM market_ticks e
                  WHERE e.match_id = m.match_id AND e.market_type = 'match_odds' AND e.time >= :since
                    AND e.market_id IS NOT NULL AND RIGHT(e.market_id, 3) <> '_BR'
              )
        )
        SELECT l.match_id,
               MAX(s.team_home) AS team_home,
               MAX(s.team_away) AS team_away,
               MAX(s.competition) AS competition,
               COUNT(*) FILTER (WHERE NOT l.colliding) AS leaked,
               COUNT(*) FILTER (WHERE l.colliding) AS colliding
        FROM leaks l
        LEFT JOIN match_training_status s ON s.match_id = l.match_id
        GROUP BY l.match_id
    """,
}

_PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


async def _db_fetch(session: Any, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one named read-only query; only the parameters the SQL uses are bound."""
    from sqlalchemy import text

    sql = SQL[name]
    wanted = set(_PARAM_RE.findall(sql))
    result = await session.execute(text(sql), {k: v for k, v in params.items() if k in wanted})
    return [dict(row._mapping) for row in result.fetchall()]


# --------------------------------------------------------------------------- helpers
def _check(name: str, status: str, value: Any, detail: str) -> dict[str, Any]:
    return {"name": name, "status": status, "value": value, "detail": detail}


def _num(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _int(value: Any) -> int:
    return int(value or 0)


def _split_live(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split matches into (real, simulated) using the same name rule as the scraper."""
    real, sim = [], []
    for r in rows:
        bucket = sim if is_simulated_name(r.get("competition"), r.get("team_home"), r.get("team_away")) else real
        bucket.append(r)
    return real, sim


def _match_label(r: dict[str, Any]) -> str:
    return f"{r.get('team_home')} v {r.get('team_away')} ({r.get('match_id')})"


def _memo(fetch: Fetch) -> Fetch:
    """Each query runs once per report, even when several checks read it."""
    cache: dict[str, list[dict[str, Any]]] = {}

    async def cached(name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if name not in cache:
            cache[name] = await fetch(name, params)
        return cache[name]

    return cached


# --------------------------------------------------------------------------- checks
async def check_freshness(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    r = (await fetch("freshness", p))[0]
    odds_age, market_age = _num(r["odds_age_s"]), _num(r["market_age_s"])
    live = _int(r["live_now"])
    ages = [a for a in (odds_age, market_age) if a is not None]
    newest = min(ages) if ages else None
    detail = (
        f"newest odds_ticks {_fmt_age(odds_age)} ago, newest market_ticks {_fmt_age(market_age)} ago; "
        f"{live} match(es) live now"
    )
    if live > 0 and (newest is None or newest > LIVE_FRESH_FAIL_S):
        return _check("freshness", FAIL, newest, detail + f"; fail above {LIVE_FRESH_FAIL_S // 60} min while live")
    return _check("freshness", OK, newest, detail)


def _last_cycle_at(path: Path) -> Optional[datetime]:
    """``cycle_at`` of the last JSON line in the paper trader log (reads only the file's tail), or None."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        if not lines:
            return None
        cycle_at = json.loads(lines[-1].decode("utf-8")).get("cycle_at")
        value = datetime.fromisoformat(str(cycle_at))
    except (OSError, ValueError, AttributeError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "no rows"
    return f"{seconds:.0f}s"


async def check_price_sanity(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    o = (await fetch("odds_prices", p))[0]
    m = (await fetch("market_prices", p))[0]
    zero = _int(o["zero_prices"]) + _int(m["zero_prices"])
    crossed = _int(o["crossed"]) + _int(m["crossed"])
    out = _int(o["out_of_range"]) + _int(m["out_of_range"])
    rows = _int(o["rows"]) + _int(m["rows"])
    detail = (
        f"{rows} rows; zero prices stored {zero}; crossed books {crossed}; "
        f"outside [{MIN_ODDS}, {MAX_ODDS:g}] {out}"
    )
    status = FAIL if (zero or crossed) else WARN if out else OK
    return _check("price_sanity", status, zero + crossed + out, detail)


async def check_duplicates(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    r = (await fetch("duplicates", p))[0]
    rows, dup = _int(r["rows"]), _int(r["duplicates"])
    share = dup / rows if rows else 0.0
    detail = f"{dup} of {rows} market_ticks rows identical to the previous row of the same market"
    status = FAIL if share > DUP_FAIL else WARN if dup else OK
    return _check("duplicates", status, share, detail)


async def check_match_page_coverage(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """Every live match, SRL included, should have a match page. Matches whose live play ended before match-page
    recording began (2026-11-03) cannot have one; they are reported apart and not scored."""
    rows = await fetch("live_matches", p)
    _, sim = _split_live(rows)
    before = [r for r in rows if r.get("before_match_pages")]
    scored = [r for r in rows if not r.get("before_match_pages")]
    if not scored:
        return _check("match_page_coverage", OK, None,
                      f"no live matches since match-page recording began ({len(before)} earlier)")
    with_market = [r for r in scored if r["has_market"]]
    share = len(with_market) / len(scored)
    missing = [_match_label(r) for r in scored if not r["has_market"]][:5]
    detail = f"{len(with_market)} of {len(scored)} live matches have match-page rows ({len(sim)} of all are SRL)"
    if missing:
        detail += "; no match page: " + ", ".join(missing)
    if before:
        detail += f"; {len(before)} ended before match-page recording began (not scored)"
    status = WARN if share < COVERAGE_WARN else OK
    return _check("match_page_coverage", status, share, detail)


async def check_prematch_pages(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S77: real matches that started in the window (and were listed early enough for a pre-match page to open)
    should have match-page rows before their start. Matches listed later than the lead are not scored. SRL has no
    pre-match listing and is left out."""
    rows = await fetch("prematch_pages", p)
    real = [r for r in rows if not is_simulated_name(r.get("competition"), r.get("team_home"), r.get("team_away"))]
    scored = [r for r in real if r.get("listed_in_time")]
    if not scored:
        return _check("prematch_pages", OK, None,
                      f"no real match started since {PREMATCH_PAGES_SINCE:%Y-%m-%d %H:%M} UTC was listed in time "
                      "for a pre-match page")
    with_page = [r for r in scored if r["has_prematch_page"]]
    share = len(with_page) / len(scored)
    missing = [_match_label(r) for r in scored if not r["has_prematch_page"]][:5]
    detail = (f"{len(with_page)} of {len(scored)} real matches started in the window have pre-match match-page rows "
              f"(lead {PREMATCH_PAGE_LEAD_MIN:g} min)")
    late = len(real) - len(scored)
    if late:
        detail += f"; {late} listed too late for a pre-match page (not scored)"
    if missing:
        detail += "; no pre-match page: " + ", ".join(missing)
    status = WARN if share < PREMATCH_PAGE_WARN else OK
    return _check("prematch_pages", status, share, detail)


async def check_lay_completeness(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """Listing ticks often carry back prices only. That is fine when the match page was being recorded (its
    match_odds rows are change-only and hold the lays, or show none were offered); it is a gap when it was not.
    Ticks recorded before match-page recording existed (2026-11-03) cannot be covered; they are reported apart."""
    r = (await fetch("lay_completeness", p))[0]
    live, null_lay, covered = _int(r["live_ticks"]), _int(r["null_lay_ticks"]), _int(r["recoverable"])
    before = _int(r.get("before_match_pages", 0))
    uncovered = null_lay - covered - before
    scored = live - before
    share = uncovered / scored if scored else 0.0
    detail = (
        f"{null_lay} of {live} live listing ticks have no lay; {covered} covered by match-page match odds "
        f"(last 30 min), {uncovered} not covered"
    )
    if before:
        detail += f"; {before} recorded before match-page recording began (cannot be covered, not scored)"
    status = FAIL if share > LAY_FAIL else WARN if share > LAY_WARN else OK
    return _check("lay_completeness", status, share, detail)


async def check_score_coverage(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S68: only matches whose match page is served by the livescore-v2 score feed are scored. Each of them with
    match-page rows must have match_context rows in the window; one without fails. Legacy matches (no score in
    their feed) and matches whose pipeline is not recorded yet are reported apart and never fail.

    The pipeline comes from match_training_status.score_pipeline. Until that column exists (migration 007 not
    applied), no match has a pipeline and nothing is scored.
    """
    real, _ = _split_live(await fetch("live_matches", p))
    with_market = [r for r in real if r["has_market"]]
    column = bool((await fetch("score_pipeline_column", p))[0]["present"])
    pipelines = (
        {str(r["match_id"]): r["score_pipeline"] for r in await fetch("score_pipelines", p)} if column else {}
    )
    v2 = [r for r in with_market if pipelines.get(str(r["match_id"])) == PIPELINE_V2]
    legacy = [r for r in with_market if pipelines.get(str(r["match_id"])) == PIPELINE_LEGACY]
    unread = len(with_market) - len(v2) - len(legacy)
    apart = f"{len(legacy)} on the legacy pipeline (no score in its feed, reported apart)"
    if unread:
        apart += f"; {unread} with no pipeline recorded yet"
    if not column:
        apart += "; score_pipeline column not present (migration 007 not applied)"
    if not v2:
        return _check("score_coverage", OK, None, f"no live livescore-v2 match with match-page rows; {apart}")
    no_context = [r for r in v2 if not r["has_context"]]
    share = len(no_context) / len(v2)
    detail = f"{len(no_context)} of {len(v2)} livescore-v2 matches with match-page rows have no score state; {apart}"
    if no_context:
        detail += "; e.g. " + ", ".join(_match_label(r) for r in no_context[:3])
    return _check("score_coverage", FAIL if no_context else OK, share, detail)


async def check_registry(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    cov = (await fetch("registry_coverage", p))[0]
    totals = (await fetch("odds_totals", p))[0]
    names = await fetch("match_names", p)
    sim_col = bool((await fetch("is_simulated_column", p))[0]["present"])
    missing = _int(cov["missing_registry"])
    empty_reg = _int(cov["empty_registry_competition"])
    empty_odds = _int(totals["empty_competition"])
    variants = [r for r in names if _int(r["name_variants"]) > 1]
    if sim_col:
        sim_rows = str(_int((await fetch("is_simulated_rows", p))[0]["n"]))
    else:
        sim_rows = "column not present"
    detail = (
        f"{missing} recorded match id(s) without a match_training_status row; "
        f"empty competition on {empty_odds} of {_int(totals['rows'])} odds rows and {empty_reg} registry rows; "
        f"{len(variants)} match id(s) with team-name variants; simulated matches in registry: {sim_rows}"
    )
    status = WARN if variants else OK
    if missing or empty_odds or empty_reg:
        status = FAIL
    return _check("registry", status, missing, detail)


async def check_results(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    # SRL included: its results are recorded too (S50). S96: a non-canonical twin is left out; its canonical match
    # carries the result.
    real = [r for r in await fetch("results", p) if r.get("canonical_match_id") is None]
    stale = [r for r in real if _num(r["age_s"]) is not None
             and _num(r["age_s"]) > RESULT_STALE_S and not r["has_result"] and r.get("went_live") is not False]
    # An 'unresolved' row for a match that is still being recorded is provisional (it left the listing for a while,
    # e.g. an innings break); it only counts once the match has been quiet for RESULT_PROVISIONAL_S.
    # A tie, no result or abandonment has no winner by definition (S76: entered from public scorecards). A simulated
    # (SRL) match has no public scorecard, so an unresolved one is final: reported, not failed.
    from scripts.results_reconciliation import is_real as _is_real_match
    srl_unresolved = [r for r in real if r["has_result"] and r.get("result_type") == "unresolved"
                      and not _is_real_match(r)]
    empty = [r for r in real if r["has_result"] and not (r["winner"] or "").strip() and _is_real_match(r)
             and r.get("result_type") not in ("tie", "no_result", "draw", "abandoned")
             and not (r.get("result_type") == "unresolved" and _num(r["age_s"]) is not None
                      and _num(r["age_s"]) < RESULT_PROVISIONAL_S)]
    detail = (
        f"{len(stale)} match(es) with last tick over {RESULT_STALE_S // 3600} h old and no result; "
        f"{len(empty)} result(s) with empty winner"
    )
    if stale:
        detail += "; e.g. " + ", ".join(_match_label(r) for r in stale[:3])
    if empty:
        detail += "; empty winner: " + ", ".join(_match_label(r) for r in empty[:3])
    if srl_unresolved:
        detail += f"; {len(srl_unresolved)} SRL match(es) unresolved from prices (final: no public scorecard)"
    status = FAIL if (stale or empty) else OK
    return _check("results", status, len(stale) + len(empty), detail)


async def check_results_missing(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S76: real matches (SRL excluded) whose last tick is over 3 h old with no win, tie, no_result or abandoned
    result. A warning: the operator settles them with scripts/set_result.py. Same rule as the reconciliation report;
    the window is this monitor's (24 h), so an older silent match is not seen here."""
    missing = select_missing(await fetch("results", p), p["now"])
    detail = f"{len(missing)} real match(es) with last tick over 3 h old and no decided result"
    if missing:
        detail += "; e.g. " + ", ".join(_match_label(r) for r in missing[:3])
    return _check("results_missing", WARN if missing else OK, len(missing), detail)


async def check_model_drift(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S65: calibration of the champion blend on the last 7 days of settled matches it predicted, and PSI of its
    p_market and p_model against the baseline stored in its manifest. Warn-only below DRIFT_MIN_SETTLED settled
    matches; no champion means nothing to check."""
    registry = Registry(Path(p.get("registry_root") or DEFAULT_ROOT))
    version = registry.champion_version("blend")
    if version is None:
        return _check("model_drift", OK, None, "no champion blend promoted; nothing to check")
    manifest = registry.manifest("blend", version)
    predictions = read_jsonl(registry.version_dir("blend", version) / "predictions.jsonl")
    if not predictions:
        return _check("model_drift", WARN, None, f"champion blend {version} has no predictions artefact")
    by_match: dict[str, dict[str, Any]] = {}
    for pred in predictions:
        by_match.setdefault(str(pred["match_id"]), pred)
    rows: list[dict[str, Any]] = []
    unmatched = 0
    for settled in await fetch("settled_recent", p):
        pred = by_match.get(str(settled["match_id"]))
        if pred is None:
            continue
        winner = (settled.get("winner") or "").strip().lower()
        if winner and winner == (settled.get("team_home") or "").strip().lower():
            home_won = 1
        elif winner and winner == (settled.get("team_away") or "").strip().lower():
            home_won = 0
        else:
            unmatched += 1
            continue
        rows.append({"p_home": pred["p_home"], "p_market": pred.get("p_market"),
                     "p_model": pred.get("p_model"), "home_won": home_won})
    report = drift_assessment(manifest.get("baseline", {}), rows)
    level = report["level"]
    enforced = report["n"] >= DRIFT_MIN_SETTLED
    if not enforced and level == "fail":
        level = "warn"  # warn-only until there are enough settled matches to trust the statistics
    status = {"ok": OK, "warn": WARN, "fail": FAIL}[level]
    psi_txt = ", ".join(
        f"PSI {k} {v['psi']:.3f} (n {v['n']})" if v else f"PSI {k} n/a"
        for k, v in report["psi"].items()
    )
    detail = (
        f"champion blend {version}: {report['n']} settled match(es) in the last {DRIFT_WINDOW_DAYS} days "
        f"(enforced from {DRIFT_MIN_SETTLED}); "
    )
    if report["n"]:
        detail += (f"mean p_home {report['mean_p_home']:.3f} vs observed {report['observed_rate']:.3f}; "
                   f"{psi_txt}")
    if unmatched:
        detail += f"; {unmatched} result(s) with unmatched team names"
    value = report["calibration_gap"]
    return _check("model_drift", status, value, detail)


async def check_gaps(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """Longest gap between rows inside each live match, SRL included. Only gaps that end after the live heartbeat
    existed are scored; earlier ones are reported apart."""
    rows = await fetch("live_matches", p)
    names = {r["match_id"]: r for r in rows}
    gaps = [r for r in await fetch("gaps", p) if r["match_id"] in names]
    before = [r for r in gaps if r.get("max_gap_before_heartbeat_s") is not None
              and float(r["max_gap_before_heartbeat_s"]) > GAP_WARN_S]
    scored = [r for r in gaps if r.get("max_gap_s") is not None]
    if not scored:
        detail = "no live match with more than one row since the live heartbeat"
        if before:
            detail += f"; {len(before)} match(es) had longer gaps before it (not scored)"
        return _check("gaps", OK, None, detail)
    worst = max(scored, key=lambda r: float(r["max_gap_s"]))
    worst_s = float(worst["max_gap_s"])
    long_gaps = [r for r in scored if float(r["max_gap_s"]) > GAP_WARN_S]
    detail = (
        f"longest gap inside a live match {worst_s:.0f}s ({_match_label(names[worst['match_id']])}); "
        f"{len(long_gaps)} live match(es) with a gap over {GAP_WARN_S // 60} min"
    )
    if before:
        detail += f"; {len(before)} match(es) had longer gaps before the live heartbeat (not scored)"
    halted = [r for r in gaps if r.get("max_halted_gap_s") is not None and float(r["max_halted_gap_s"]) > GAP_WARN_S]
    if halted:
        detail += f"; {len(halted)} match(es) with a longer gap while the market was closed or suspended (not scored)"
    return _check("gaps", WARN if long_gaps else OK, worst_s, detail)


async def check_srl(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    _, sim = _split_live(await fetch("match_names", p))
    rows = sum(_int(r["odds_rows"]) for r in sim)
    detail = f"{len(sim)} simulated (SRL) match(es) with {rows} odds_ticks rows; recorded on purpose, not a failure"
    return _check("srl", OK, len(sim), detail)


async def check_misattribution(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S57: rows in the window that the post-settlement and cross-match rules would quarantine. Any is a failure:
    the recorder should have refused them (scraper guard), so they mean the guard or the rules need attention."""
    ticks = await fetch("misattribution_ticks", p)
    reasons = classify(ticks)
    flagged = [t for t in ticks if t["time"] >= p["since"] and (t["match_id"], t["time"], t["tick_seq"]) in reasons]
    per_rule = {rule: sum(1 for t in flagged if reasons[(t["match_id"], t["time"], t["tick_seq"])] == rule)
                for rule in RULES}
    matches = sorted({(t["team_home"], t["team_away"], t["match_id"]) for t in flagged})
    detail = (
        f"{len(flagged)} row(s) in the window flagged: "
        + ", ".join(f"{rule} {per_rule[rule]}" for rule in RULES)
    )
    if matches:
        detail += "; matches: " + ", ".join(f"{h} v {a} ({mid})" for h, a, mid in matches[:3])
    return _check("misattribution", FAIL if flagged else OK, len(flagged), detail)


async def check_bookmaker_leak(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S105: rows of a bookmaker ("_BR") match-odds market stored under the plain match_odds name on a real match that
    also has the exchange market. The bookmaker's runners are always suspended, so v_match_odds would serve an
    unbettable book. Leaked rows fail. A row whose relabel would collide with an existing key is left as it is by the
    hourly harmonise step and only warns (the operator may remove the duplicate). Simulated (SRL) matches have no
    exchange market and are left out in any case."""
    real, _ = _split_live(await fetch("bookmaker_leak", p))
    leaked = sum(_int(r["leaked"]) for r in real)
    colliding = sum(_int(r["colliding"]) for r in real)
    detail = f"{leaked} bookmaker-market row(s) under the plain match_odds name on real matches with an exchange market"
    leaking = [r for r in real if _int(r["leaked"])]
    if leaking:
        detail += "; " + ", ".join(f"{_match_label(r)} {_int(r['leaked'])} row(s)" for r in leaking[:3])
    detail += f"; {colliding} row(s) left as they are because the relabel would collide with an existing row"
    colliders = [r for r in real if _int(r["colliding"])]
    if colliders:
        detail += "; collisions: " + ", ".join(f"{_match_label(r)} {_int(r['colliding'])}" for r in colliders[:3])
    status = FAIL if leaked else WARN if colliding else OK
    return _check("bookmaker_leak", status, leaked + colliding, detail)


async def check_paper_trading(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S73: the paper trader is running while matches are live, no variant's worst-case exposure on a match is beyond
    the 100,000 cap, and no filled paper bet is still open 12 h after its result. Any breach is a failure."""
    from strategy.base import bet_gross_pnl

    live = _int((await fetch("freshness", p))[0]["live_now"])
    problems: list[str] = []
    log_path = Path(p.get("paper_log") or PAPER_LOG_PATH)
    cycle_at = _last_cycle_at(log_path)
    age = None if cycle_at is None else (p["now"] - cycle_at).total_seconds()
    if age is None:
        trader = "no trader log"
    else:
        trader = f"last cycle {age:.0f}s ago"
    if live > 0 and (age is None or age > PAPER_FRESH_FAIL_S):
        problems.append(f"trader stale ({trader}) while {live} match(es) live")

    pnl: dict[tuple[str, str], dict[str, float]] = {}
    for row in await fetch("paper_open_fills", p):
        bucket = pnl.setdefault((row["variant"], str(row["match_id"])), {"home": 0.0, "away": 0.0})
        for outcome in ("home", "away"):
            bucket[outcome] += bet_gross_pnl(row["side"], row["selection"], float(row["price_filled"]),
                                             float(row["stake"]), outcome)
    worst_by_key = {key: max(0.0, -min(v.values())) for key, v in pnl.items()}
    over = sorted((key, w) for key, w in worst_by_key.items() if w > PAPER_MATCH_LOSS)
    if over:
        problems.append("worst-case beyond " + f"{PAPER_MATCH_LOSS:,.0f}" + ": " + ", ".join(
            f"{variant} {mid} ({w:,.0f})" for (variant, mid), w in over[:3]))

    stale = await fetch("paper_stale_settlement", p)
    stale_bets = sum(_int(r["bets"]) for r in stale)
    if stale_bets:
        problems.append(f"{stale_bets} filled paper bet(s) on {len(stale)} match(es) still open over "
                        f"{PAPER_SETTLE_FAIL_S // 3600} h after the result")

    max_worst = max(worst_by_key.values(), default=0.0)
    detail = (f"{trader}; {live} match(es) live; {len(worst_by_key)} variant-match position(s) open, "
              f"max worst-case {max_worst:,.0f} (cap {PAPER_MATCH_LOSS:,.0f})")
    if problems:
        return _check("paper_trading", FAIL, max_worst, detail + "; " + "; ".join(problems))
    return _check("paper_trading", OK, max_worst, detail)


def _prob_gap(row: dict[str, Any]) -> Optional[float]:
    """|p_used - 1/price|: how far the bet's probability is from the one its fill price implies."""
    p, price = _num(row.get("p_used")), _num(row.get("price_filled"))
    if p is None or price is None or price <= 1.0:
        return None
    return abs(p - 1.0 / price)


async def check_paper_sanity(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S75: a sanity check on the paper trader's decisions over the last 24 h. It fails when the market_only control
    placed more than 2 orders, or when one variant made more new-risk bets (enter, add, flip) on one match than the
    30-ticket budget. A variant-match with more than 45 matched bets (hedges included) and a filled bet with a
    probability gap over 20 points are warnings to review."""
    per_key = await fetch("paper_sanity_trades", p)
    # The control is judged on every order it placed (any order means it saw an edge); churn on matched bets only:
    # an order that did not fill within the 60 s delay is not a bet (2026-11-04: 54 orders, 33 bets on a 50-over match).
    control = sum(_int(r.get("orders", r["trades"])) for r in per_key if r["variant"] == PAPER_SANITY_CONTROL)
    busiest = max((_int(r["trades"]) for r in per_key), default=0)
    problems: list[str] = []
    if control > PAPER_SANITY_CONTROL_MAX:
        problems.append(f"{PAPER_SANITY_CONTROL} placed {control} trades in 24 h (max {PAPER_SANITY_CONTROL_MAX})")
    # Hedges may pass the budget (operator, 2026-11-04): only new risk beyond it is a failure. A row without the
    # new_risk count (older fakes) is judged on all its matched bets.
    risky = sorted(
        ((r["variant"], str(r["match_id"]), _int(r.get("new_risk", r["trades"]))) for r in per_key
         if _int(r.get("new_risk", r["trades"])) > PAPER_SANITY_NEW_RISK_MAX),
        key=lambda item: -item[2],
    )
    if risky:
        problems.append(f"{len(risky)} variant-match(es) over {PAPER_SANITY_NEW_RISK_MAX} new-risk bets: " + ", ".join(
            f"{variant} {mid} ({n})" for variant, mid, n in risky[:3]))
    busy = sorted(
        ((r["variant"], str(r["match_id"]), _int(r["trades"])) for r in per_key
         if _int(r["trades"]) > PAPER_SANITY_MATCH_TRADES_REVIEW),
        key=lambda item: -item[2],
    )
    busy_note = ""
    if busy:
        busy_note = (f"{len(busy)} variant-match(es) over {PAPER_SANITY_MATCH_TRADES_REVIEW} matched bets with hedges "
                     "(review): " + ", ".join(f"{variant} {mid} ({n})" for variant, mid, n in busy[:3]))
    edges = await fetch("paper_sanity_edges", p)
    high = [r for r in edges if (_prob_gap(r) or 0.0) > PAPER_SANITY_PROB_GAP_MAX]
    # A large probability gap is a warning to review, not a failure: late in a chase the in-play model and the price
    # can genuinely differ by 20 points (2026-11-03, West Indies A v England Lions: 0.90 v 0.69, and the model was
    # right). The control trading and churn stay failures.
    gap_note = ""
    if high:
        gap_note = (f"{len(high)} filled bet(s) with a probability gap over {PAPER_SANITY_PROB_GAP_MAX:.0%} (review): "
                    + ", ".join(f"{r['variant']} {r['match_id']} {r['side']} {r['selection']} "
                                f"(gap {_prob_gap(r):.3f})" for r in high[:3]))
    most_new = max((_int(r.get("new_risk", r["trades"])) for r in per_key), default=0)
    detail = (f"{control} {PAPER_SANITY_CONTROL} trade(s) in 24 h (max {PAPER_SANITY_CONTROL_MAX}); most new-risk "
              f"bets on a variant-match {most_new} (max {PAPER_SANITY_NEW_RISK_MAX}); busiest variant-match {busiest} "
              f"matched bet(s) with hedges (review over {PAPER_SANITY_MATCH_TRADES_REVIEW}); {len(edges)} matched "
              f"bet(s) checked for a probability gap over {PAPER_SANITY_PROB_GAP_MAX:.0%}")
    notes = [n for n in (busy_note, gap_note) if n]
    if problems:
        return _check("paper_sanity", FAIL, busiest, "; ".join([detail, *problems, *notes]))
    if notes:
        return _check("paper_sanity", WARN, busiest, "; ".join([detail, *notes]))
    return _check("paper_sanity", OK, busiest, detail)


async def check_start_time_consistency(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S86: real matches (SRL excluded) whose listing-derived start and JSON-derived start differ by more than 15 min,
    or whose actual_start_time is more than 60 min after match_start_time. A warning only.

    The listing start is not stored: match_start_time is overwritten by the market JSON's openDate once the JSON has
    arrived. The first live listing tick therefore stands in for the listing start, so a match whose listing goes live
    well after its scheduled time is reported too (that is the late start the check is for). Matches recorded before
    the S86 columns existed have no actual_start_time; they are counted, not scored.
    """
    if not bool((await fetch("actual_start_columns", p))[0]["present"]):
        return _check("start_time_consistency", OK, None,
                      "actual_start_time column not present (S86 migration not applied); not scored")
    real, _ = _split_live(await fetch("start_times", p))
    listing_gap: list[dict[str, Any]] = []
    late: list[dict[str, Any]] = []
    unset = 0
    for r in real:
        start, actual, first_live = r.get("match_start_time"), r.get("actual_start_time"), r.get("first_live_tick")
        # Once the actual off is recorded, a listing that went live late is explained (a delayed start, e.g. rain);
        # only an unexplained gap is a warning. A start more than an hour late is still reported below.
        if (actual is None and start is not None and first_live is not None
                and abs((first_live - start).total_seconds()) > START_GAP_WARN_S):
            listing_gap.append(r)
        if start is not None and actual is not None and (actual - start).total_seconds() > ACTUAL_LATE_WARN_S:
            late.append(r)
        if actual is None:
            unset += 1
    detail = (
        f"{len(listing_gap)} real match(es) with listing and JSON starts over {START_GAP_WARN_S // 60} min apart "
        f"(first live tick vs match_start_time); {len(late)} with actual_start_time over "
        f"{ACTUAL_LATE_WARN_S // 60} min after match_start_time; {unset} real match(es) with no actual_start_time yet"
    )
    if listing_gap:
        detail += "; listing gap: " + ", ".join(_match_label(r) for r in listing_gap[:3])
    if late:
        detail += "; late start: " + ", ".join(_match_label(r) for r in late[:3])
    value = len(listing_gap) + len(late)
    return _check("start_time_consistency", WARN if value else OK, value, detail)


async def check_duplicate_fixtures(fetch: Fetch, p: dict[str, Any]) -> dict[str, Any]:
    """S96: one real fixture listed under two lb_ event ids (same teams, competition and start). A twin pair that is
    not linked to its canonical match, or whose decided results conflict, is a warning. The hourly harmonise step
    (--quarantine) links the pairs and copies results, so a warning means that step has not run or has refused.
    Until the canonical_match_id column exists (S96 migration not applied) nothing is scored."""
    if not bool((await fetch("canonical_column", p))[0]["present"]):
        return _check("duplicate_fixtures", OK, None,
                      "canonical_match_id column not present (S96 migration not applied); not scored")
    rows = await fetch("twin_rows", p)
    plan = build_plan(rows)
    pending = unlinked_links(plan, rows)
    detail = (f"{len(plan.links)} twin pair(s) found; {len(pending)} not linked to their canonical match; "
              f"{len(plan.conflicts)} pair(s) with conflicting decided results")
    if pending:
        detail += "; e.g. " + ", ".join(f"{link.twin_id} -> {link.canonical_id}" for link in pending[:3])
    if plan.conflicts:
        detail += "; conflict: " + "; ".join(c.detail for c in plan.conflicts[:2])
    value = len(pending) + len(plan.conflicts)
    return _check("duplicate_fixtures", WARN if value else OK, value, detail)


CHECKS: list[Callable[[Fetch, dict[str, Any]], Awaitable[dict[str, Any]]]] = [
    check_freshness,
    check_price_sanity,
    check_duplicates,
    check_match_page_coverage,
    check_lay_completeness,
    check_score_coverage,
    check_registry,
    check_results,
    check_gaps,
    check_srl,
    check_misattribution,
    check_bookmaker_leak,  # S105, next to the other row-integrity check; the tail of CHECKS is unchanged
    check_model_drift,  # S65, appended so the existing check order is unchanged
    check_paper_trading,  # S73, appended last
    check_prematch_pages,  # S77, appended last
    check_results_missing,  # S76, appended last (its logic sits next to check_results)
    check_paper_sanity,  # S75, appended last
    check_start_time_consistency,  # S86, appended last
    check_duplicate_fixtures,  # S96, appended last
]


def overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {c["status"] for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK


async def run_checks(fetch: Fetch, *, now: datetime, window_hours: float,
                     registry_root: Optional[Path] = None) -> dict[str, Any]:
    """Run every check through ``fetch(name, params)``; returns the report record (no I/O apart from the registry
    artefacts read by the model_drift check)."""
    params = {
        "now": now,
        "since": now - timedelta(hours=window_hours),
        "min_odds": MIN_ODDS,
        "max_odds": MAX_ODDS,
        "tie_seconds": TIE_SECONDS,
        "heartbeat_since": LIVE_HEARTBEAT_SINCE,
        "prematch_since": PREMATCH_PAGES_SINCE,
        "prematch_lead_s": PREMATCH_PAGE_LEAD_MIN * 60,
        "registry_root": registry_root or DEFAULT_ROOT,
    }
    cached = _memo(fetch)
    checks = [await fn(cached, params) for fn in CHECKS]
    overall = overall_status(checks)
    return {
        "ts": now.isoformat(),
        "window_hours": window_hours,
        "overall": overall,
        "exit_code": EXIT_BY_STATUS[overall],
        "checks": checks,
    }


def render(record: dict[str, Any]) -> str:
    lines = [f"PHOENIX data quality, last {record['window_hours']:g} h, run at {record['ts']}"]
    lines.append(f"{'status':<7}{'check':<22}{'value':>10}  detail")
    for c in record["checks"]:
        value = "-" if c["value"] is None else (f"{c['value']:.3f}" if isinstance(c["value"], float) else str(c["value"]))
        lines.append(f"{c['status'].upper():<7}{c['name']:<22}{value:>10}  {c['detail']}")
    lines.append(f"overall: {record['overall'].upper()} (exit {record['exit_code']})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- run
async def run_once(window_hours: float) -> dict[str, Any]:
    from sqlalchemy import text

    from shared.db import get_session

    now = datetime.now(timezone.utc)
    try:
        async with get_session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))

            async def fetch(name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
                return await _db_fetch(session, name, params)

            record = await run_checks(fetch, now=now, window_hours=window_hours)
    except Exception as exc:  # the monitor must keep running and report the failure
        logger.error("data_quality_db_error", error=f"{type(exc).__name__}: {exc}")
        checks = [_check("database", FAIL, None, f"{type(exc).__name__}: {exc}"[:300])]
        record = {
            "ts": now.isoformat(),
            "window_hours": window_hours,
            "overall": FAIL,
            "exit_code": EXIT_BY_STATUS[FAIL],
            "checks": checks,
        }
    _append(record)
    print(render(record))
    logger.info("data_quality_run", overall=record["overall"], exit_code=record["exit_code"])
    return record


def _append(record: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        print(f"could not write {LOG_PATH}: {exc}", file=sys.stderr)


HARMONISE_SQL = """
    UPDATE odds_ticks o SET competition = m.competition
      FROM match_training_status m
     WHERE o.match_id = m.match_id AND o.time > now() - INTERVAL '2 days'
       AND (o.competition IS NULL OR o.competition = '') AND m.competition <> ''
"""


# S105: relabel a bookmaker leg stored under the exchange's name to its own name, so the plain match_odds name
# belongs to the exchange market alone. UPDATE only (never a delete). A row whose new key already exists is skipped:
# the duplicate stays for the operator, and the check reports it as a warning. Simulated matches are excluded, and so
# is a match with no exchange market (an SRL match keeps its plain name by design).
BOOKMAKER_LEAK_RELABEL_SQL = """
    UPDATE market_ticks m SET market_name = 'match_odds#' || m.market_id
     WHERE m.time > now() - INTERVAL '2 days'
       AND m.market_type = 'match_odds' AND m.market_name = 'match_odds'
       AND RIGHT(m.market_id, 3) = '_BR'
       AND EXISTS (
           SELECT 1 FROM market_ticks e
            WHERE e.match_id = m.match_id AND e.market_type = 'match_odds' AND e.time > now() - INTERVAL '2 days'
              AND e.market_id IS NOT NULL AND RIGHT(e.market_id, 3) <> '_BR')
       AND NOT EXISTS (
           SELECT 1 FROM match_training_status s WHERE s.match_id = m.match_id AND s.is_simulated)
       AND NOT EXISTS (
           SELECT 1 FROM market_ticks x
            WHERE x.time = m.time AND x.match_id = m.match_id AND x.market_type = m.market_type
              AND x.market_name = 'match_odds#' || m.market_id AND x.selection = m.selection)
"""


async def _relabel_bookmaker_leak() -> None:
    """S105: relabel leaked bookmaker rows, then log how many collisions were skipped (the check's colliding rows)."""
    from sqlalchemy import text

    from shared.db import get_session

    try:
        async with get_session() as session:
            result = await session.execute(text(BOOKMAKER_LEAK_RELABEL_SQL))
            left = await _db_fetch(session, "bookmaker_leak",
                                   {"since": datetime.now(timezone.utc) - timedelta(days=2)})
        skipped = sum(_int(r["colliding"]) for r in left)
        logger.info("bookmaker_leak_relabelled", rows=result.rowcount, skipped_collisions=skipped)
    except Exception as exc:
        logger.error("bookmaker_leak_relabel_failed", error=str(exc))


async def _harmonise() -> None:
    """Fill a competition missing on recent ticks (stored before the listing menu arrived) from the registry, relabel
    bookmaker legs stored under the exchange's name (S105), then link twin event ids and copy decided results between
    them (S96, the same functions as scripts/link_twins.py --apply)."""
    from sqlalchemy import text

    from shared.db import get_session

    try:
        async with get_session() as session:
            result = await session.execute(text(HARMONISE_SQL))
        logger.info("harmonise_applied", competitions_filled=result.rowcount)
    except Exception as exc:
        logger.error("harmonise_failed", error=str(exc))
    await _relabel_bookmaker_leak()
    await _link_twins()


async def _link_twins() -> None:
    """S96: link twins (canonical_match_id only) and copy decided results. Refused, and logged, until the migration."""
    from shared.db import get_session

    try:
        async with get_session() as session:
            summary = await link_now(session, apply=True)
        applied = summary["applied"] or {}
        logger.info("twins_linked", pairs=len(summary["plan"].links), **applied)
    except MigrationMissing as exc:
        logger.info("twins_link_skipped", reason=str(exc))
    except Exception as exc:
        logger.error("twins_link_failed", error=str(exc))


async def _quarantine() -> None:
    """S57: move rows that are not prices of their match to odds_ticks_quarantine before checking."""
    from scripts.quarantine_ticks import quarantine_now

    try:
        summary = await quarantine_now()
        logger.info("quarantine_applied", moved=summary["total"], per_rule=summary["per_rule"])
    except Exception as exc:  # the checks still run; misattribution will report what is left
        logger.error("quarantine_failed", error=str(exc))


async def _main(args: argparse.Namespace) -> int:
    from shared.db import get_engine

    try:
        if args.interval <= 0:
            if args.quarantine:
                await _quarantine()
                await _harmonise()
            record = await run_once(args.window_hours)
            return record["exit_code"]
        while True:
            if args.quarantine:
                await _quarantine()
                await _harmonise()
            await run_once(args.window_hours)
            await asyncio.sleep(args.interval)
    finally:
        await get_engine().dispose()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--window-hours", type=float, default=24.0, help="look-back window in hours (default 24)")
    p.add_argument("--interval", type=float, default=0.0,
                   help="seconds between runs; 0 (default) runs once and exits with the report's code")
    p.add_argument("--quarantine", action="store_true",
                   help="before each run: move misattributed odds_ticks rows to odds_ticks_quarantine (S57) and fill "
                        "missing competitions from the registry")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
