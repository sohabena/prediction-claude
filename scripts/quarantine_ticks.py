"""Quarantine odds_ticks rows that do not belong to the match they are stored under (S57).

Two rules, both pure functions over tick dicts so the scraper guard, the data quality monitor and this CLI
share one definition:

* ``post_settlement``: per match, the first *decisive* tick is the settlement. Every later tick that comes after
  a gap of at least 5 minutes and is not decisive is flagged, and so is every tick after it. (LotusBook reset to a
  fresh market under the same id after a finished match.)
* ``cross_match_copy``: two different match ids with the same ``time`` and identical non-null
  ``(back_home, lay_home, back_away, lay_away)``. Both rows are flagged; which side is the copy cannot be told.

Default is a dry run (SELECT only, prints counts per rule and per match). ``--apply`` moves the flagged rows into
``odds_ticks_quarantine`` (one INSERT ... SELECT and one DELETE per rule, all in one transaction). Rows are kept,
never deleted outright.

Usage::

    python scripts/quarantine_ticks.py            # dry run (default)
    python scripts/quarantine_ticks.py --apply    # move flagged rows (operator only)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RULE_POST_SETTLEMENT = "post_settlement"
RULE_CROSS_MATCH = "cross_match_copy"
RULE_PLACEHOLDER = "placeholder_book"
RULE_AFTER_RESULT = "after_result"
RULES = (RULE_POST_SETTLEMENT, RULE_CROSS_MATCH, RULE_PLACEHOLDER, RULE_AFTER_RESULT)

# LotusBook shows a default book (seen: 1.92/1.99/2.02/2.10) when a market opens or reopens with no real price. The
# same full four-price book on this many different matches is a placeholder, not a price of any of them.
PLACEHOLDER_MIN_MATCHES = 3
# Results whose completion time comes from an outside authority; ticks after it belong to no live match.
TRUSTED_RESULT_SOURCES = ("public_scorecard", "lotusbook_scoreboard", "lotusbook_settled")

DECISIVE_BACK = 1.05  # one side back at or below this ...
DECISIVE_LAY = 1.06  # ... or lay at or below this ...
DECISIVE_OPPONENT_BACK = 15.0  # ... while the other side is backed at 15 or more, or not quoted
GAP_S = 300.0  # a gap of at least this many seconds between ticks of one match

Key = tuple[str, datetime, int]  # (match_id, time, tick_seq): the primary key of odds_ticks

BOOK_FIELDS = ("back_home", "lay_home", "back_away", "lay_away")

# Columns of odds_ticks copied to odds_ticks_quarantine, in order, before ``reason``.
COPY_COLUMNS = (
    "time", "match_id", "tick_seq", "team_home", "team_away", "competition",
    "back_home", "lay_home", "back_draw", "lay_draw", "back_away", "lay_away",
    "volume_back_home", "volume_lay_home", "volume_back_away", "volume_lay_away",
    "implied_prob_home", "implied_prob_away", "overround",
    "score", "wickets", "overs", "innings", "is_live", "source", "scrape_latency_ms",
)


# --------------------------------------------------------------------------- pure rules
def is_decisive(tick: dict[str, Any]) -> bool:
    """True when the match has settled: one side is priced at or below 1.05 back / 1.06 lay while the other
    side is backed at 15 or more, or is not quoted."""
    for side, other in (("home", "away"), ("away", "home")):
        opponent = tick.get(f"back_{other}")
        if opponent is not None and opponent < DECISIVE_OPPONENT_BACK:
            continue
        back, lay = tick.get(f"back_{side}"), tick.get(f"lay_{side}")
        if (back is not None and back <= DECISIVE_BACK) or (lay is not None and lay <= DECISIVE_LAY):
            return True
    return False


def book_key(tick: dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    """The four-price book, or None when any price is missing (such a book is never a copy)."""
    prices = tuple(tick.get(f) for f in BOOK_FIELDS)
    if any(p is None for p in prices):
        return None
    return prices  # type: ignore[return-value]


def _key(tick: dict[str, Any]) -> Key:
    return (tick["match_id"], tick["time"], tick["tick_seq"])


def post_settlement_flags(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flagged ticks of ONE match (any order in, time order out)."""
    ordered = sorted(ticks, key=lambda t: (t["time"], t.get("tick_seq") or 0))
    flagged: list[dict[str, Any]] = []
    decided = False
    quarantining = False
    previous_time: Optional[datetime] = None
    for tick in ordered:
        if quarantining:
            flagged.append(tick)
            continue
        dec = is_decisive(tick)
        if decided and not dec and previous_time is not None:
            if (tick["time"] - previous_time).total_seconds() >= GAP_S:
                quarantining = True
                flagged.append(tick)
        if dec:
            decided = True
        previous_time = tick["time"]
    return flagged


def cross_match_flags(ticks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ticks (any matches) that share a time and a four-price book with a tick of another match id."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for tick in ticks:
        book = book_key(tick)
        if book is not None:
            groups[(tick["time"], book)].append(tick)
    flagged: list[dict[str, Any]] = []
    for members in groups.values():
        if len({m["match_id"] for m in members}) >= 2:
            flagged.extend(members)
    return flagged


def placeholder_flags(ticks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ticks whose full four-price book appears on PLACEHOLDER_MIN_MATCHES or more different matches."""
    ticks = list(ticks)
    owners: dict[tuple, set[str]] = defaultdict(set)
    for tick in ticks:
        book = book_key(tick)
        if book is not None:
            owners[book].add(tick["match_id"])
    placeholders = {b for b, ids in owners.items() if len(ids) >= PLACEHOLDER_MIN_MATCHES}
    return [t for t in ticks if book_key(t) in placeholders]


def after_result_flags(ticks: Iterable[dict[str, Any]], completed_at: dict[str, datetime]) -> list[dict[str, Any]]:
    """Ticks recorded after the match's trusted result time."""
    return [t for t in ticks if t["match_id"] in completed_at and t["time"] > completed_at[t["match_id"]]]


def classify(ticks: list[dict[str, Any]], completed_at: Optional[dict[str, datetime]] = None) -> dict[Key, str]:
    """Map every flagged row's key to the rule that flags it. A row flagged by several rules keeps the first one."""
    by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tick in ticks:
        by_match[tick["match_id"]].append(tick)
    reasons: dict[Key, str] = {}
    for tick in (t for mt in by_match.values() for t in post_settlement_flags(mt)):
        reasons[_key(tick)] = RULE_POST_SETTLEMENT
    for tick in cross_match_flags(ticks):
        reasons.setdefault(_key(tick), RULE_CROSS_MATCH)
    for tick in placeholder_flags(ticks):
        reasons.setdefault(_key(tick), RULE_PLACEHOLDER)
    for tick in after_result_flags(ticks, completed_at or {}):
        reasons.setdefault(_key(tick), RULE_AFTER_RESULT)
    return reasons


RESULTS_SQL = """
    SELECT match_id, completed_at FROM match_results
    WHERE source IN ('public_scorecard', 'lotusbook_scoreboard', 'lotusbook_settled')
"""


def batch_copy_match_ids(events: Iterable[Any]) -> set[str]:
    """Match ids in one scrape batch whose four-price book equals another match's book in that batch.

    ``events`` are objects with ``match_id`` and the four price attributes (OddsEvent). Used by the recorder guard.
    """
    owners: dict[tuple, set[str]] = defaultdict(set)
    for ev in events:
        book = book_key({f: getattr(ev, f, None) for f in BOOK_FIELDS})
        if book is not None:
            owners[book].add(ev.match_id)
    return {mid for ids in owners.values() if len(ids) >= 2 for mid in ids}


# --------------------------------------------------------------------------- database (CLI only)
LOAD_SQL = """
    SELECT time, match_id, tick_seq, team_home, team_away, competition,
           back_home, lay_home, back_away, lay_away
    FROM odds_ticks
    WHERE source = 'lotusbook'  -- recorded prices only; synthetic/imported rows follow other rules
    ORDER BY match_id, time, tick_seq
"""


def _move_sql(verb: str) -> str:
    """INSERT ... SELECT (copy) or DELETE (remove) of rows named by parallel key arrays."""
    keys = """
        JOIN unnest(CAST(:times AS timestamptz[]), CAST(:match_ids AS text[]), CAST(:seqs AS bigint[]))
             AS k(time, match_id, tick_seq)
          ON o.time = k.time AND o.match_id = k.match_id AND o.tick_seq = k.tick_seq
    """
    cols = ", ".join(f"o.{c}" for c in COPY_COLUMNS)
    if verb == "copy":
        target = ", ".join(COPY_COLUMNS) + ", reason"
        return (f"INSERT INTO odds_ticks_quarantine ({target}) "
                f"SELECT {cols}, :reason FROM odds_ticks o {keys}")
    return f"DELETE FROM odds_ticks o USING unnest(CAST(:times AS timestamptz[]), CAST(:match_ids AS text[]), " \
           f"CAST(:seqs AS bigint[])) AS k(time, match_id, tick_seq) " \
           f"WHERE o.time = k.time AND o.match_id = k.match_id AND o.tick_seq = k.tick_seq"


def _arrays(keys: list[Key]) -> dict[str, list[Any]]:
    return {
        "times": [k[1] for k in keys],
        "match_ids": [k[0] for k in keys],
        "seqs": [k[2] for k in keys],
    }


async def _load(session: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    result = await session.execute(text(LOAD_SQL))
    return [dict(r._mapping) for r in result.fetchall()]


async def _load_results(session: Any) -> dict[str, datetime]:
    from sqlalchemy import text

    result = await session.execute(text(RESULTS_SQL))
    return {r._mapping["match_id"]: r._mapping["completed_at"] for r in result.fetchall()}


def _summarise(ticks: list[dict[str, Any]], reasons: dict[Key, str]) -> dict[str, Any]:
    names: dict[str, tuple[str, str]] = {}
    per_rule: dict[str, int] = defaultdict(int)
    per_match: dict[str, dict[str, Any]] = {}
    total_rows: dict[str, int] = defaultdict(int)
    for t in ticks:
        total_rows[t["match_id"]] += 1
        names.setdefault(t["match_id"], (t["team_home"], t["team_away"]))
    for t in ticks:
        reason = reasons.get(_key(t))
        if reason is None:
            continue
        per_rule[reason] += 1
        row = per_match.setdefault(t["match_id"], {
            "match_id": t["match_id"],
            "teams": f"{names[t['match_id']][0]} v {names[t['match_id']][1]}",
            "rows": total_rows[t["match_id"]],
            "flagged": defaultdict(int),
            "first": t["time"],
            "last": t["time"],
        })
        row["flagged"][reason] += 1
        row["first"] = min(row["first"], t["time"])
        row["last"] = max(row["last"], t["time"])
    return {"per_rule": dict(per_rule), "per_match": list(per_match.values()), "total": len(reasons)}


def _print_summary(summary: dict[str, Any], applied: bool) -> None:
    mode = "APPLIED" if applied else "DRY RUN (no rows moved)"
    print(f"quarantine_ticks: {mode}")
    for rule in RULES:
        print(f"  {rule:<18} {summary['per_rule'].get(rule, 0):>6} row(s)")
    print(f"  {'total':<18} {summary['total']:>6} row(s)")
    for m in sorted(summary["per_match"], key=lambda r: r["match_id"]):
        flagged = ", ".join(f"{rule}={m['flagged'][rule]}" for rule in RULES if m["flagged"].get(rule))
        print(f"  {m['match_id']}  {m['teams']}  rows={m['rows']}  {flagged}  "
              f"{m['first'].isoformat()} .. {m['last'].isoformat()}")


async def quarantine_now() -> dict[str, Any]:
    """Move every flagged row to odds_ticks_quarantine (copy, then delete) in one transaction. Leaves the engine open,
    so the hourly monitor can call it before its checks."""
    from sqlalchemy import text

    from shared.db import get_session

    async with get_session() as session:
        ticks = await _load(session)
        completed = await _load_results(session)
        reasons = classify(ticks, completed)
        summary = _summarise(ticks, reasons)
        for rule in RULES:
            keys = [k for k, r in reasons.items() if r == rule]
            if not keys:
                continue
            arrays = _arrays(keys)
            await session.execute(text(_move_sql("copy")), {**arrays, "reason": rule})
            await session.execute(text(_move_sql("delete")), arrays)
    return summary


async def run(apply: bool) -> dict[str, Any]:
    from sqlalchemy import text

    from shared.db import get_engine, get_session

    try:
        if not apply:
            async with get_session() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                ticks = await _load(session)
                completed = await _load_results(session)
            reasons = classify(ticks, completed)
            summary = _summarise(ticks, reasons)
            _print_summary(summary, applied=False)
            return summary

        summary = await quarantine_now()
        _print_summary(summary, applied=True)
        return summary
    finally:
        await get_engine().dispose()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the counts only (default)")
    mode.add_argument("--apply", action="store_true", help="move flagged rows to odds_ticks_quarantine")
    args = p.parse_args(argv)
    asyncio.run(run(apply=args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
