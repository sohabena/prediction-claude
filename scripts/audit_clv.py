"""
Closing Line Value (CLV) auditor (S33). Read-only.

For recent settled virtual bets it reports:

- ``clv``: stored closing CLV from ``virtual_bets.clv`` (``shared.metrics.clv_prob``,
  probability points, positive = beat the closing price). The only stored CLV.
- ``clv_60s``: the same formula against the same-side price 60 s after placement
  (first valid tick at or after ``placed_at + 60 s``), read from ``odds_ticks``.
  It matches the manual-betting latency. Not stored anywhere.
- ``clv_odds_pct``: the odds-ratio form (BACK ``odds/closing - 1``, LAY
  ``closing/odds - 1``) in percent. Shown here only, never stored.

Breakdowns are by ``agent_version`` and by the closing-price source
(``match_results.source``). Simulated matches are excluded in Python with
``scraper.match_filter.is_simulated_name``. Holdout evaluations
(``agent_version LIKE 'holdout_eval:%'``) and ticks not from ``lotusbook`` or
``betfair`` are excluded in SQL. The session runs in a READ ONLY transaction.

Usage: ``python -m scripts.audit_clv --limit 100 [--source betfair|lotusbook|all]``. The default is
``CLV_REFERENCE_SOURCE`` (S45: ``lotusbook``, since Betfair is blocked); ``all`` is the S33 behaviour.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import defaultdict
from datetime import timedelta
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text

from scraper.match_filter import is_simulated_name
from shared.constants import CLV_REFERENCE_SOURCE
from shared.logging import setup_logging
from shared.metrics import bet_clv, clv_prob, portfolio_clv, selection_of, side_of

logger = setup_logging("audit_clv")

POST_PLACEMENT_SECONDS = 60
# In-play bets: the price near match end restates the result, so the primary in-play measure is
# CLV at a fixed horizon after placement (also the S42 trust metric for in-play bets).
HORIZON_SECONDS = 600

BETS_SQL = text("""
    SELECT CAST(vb.bet_id AS TEXT) AS bet_id, vb.match_id, vb.placed_at, vb.action,
           vb.team, vb.odds, vb.stake, vb.closing_odds, vb.clv, vb.agent_version,
           mr.source AS closing_source, mr.team_home, mr.team_away
    FROM virtual_bets vb
    LEFT JOIN match_results mr ON mr.match_id = vb.match_id
    WHERE vb.settled_at IS NOT NULL
      AND (vb.agent_version IS NULL OR vb.agent_version NOT LIKE 'holdout_eval:%')
    ORDER BY vb.placed_at DESC
    LIMIT :limit
""")

_TICKS_SELECT = """
    SELECT match_id, time, competition, team_home, team_away,
           back_home, lay_home, back_away, lay_away, is_live, source
    FROM odds_ticks
    WHERE match_id = ANY(:match_ids)
      AND {source_filter}
    ORDER BY match_id, time
"""

# S44: ``--source`` picks the price feed. Betfair is the binding CLV definition; "all" is the S33 behaviour.
SOURCES = ("betfair", "lotusbook", "all")
_SOURCE_FILTERS = {
    "betfair": "source = 'betfair'",
    "lotusbook": "source = 'lotusbook'",
    "all": "source IN ('lotusbook', 'betfair')",
}
TICKS_SQL_BY_SOURCE = {name: text(_TICKS_SELECT.format(source_filter=f)) for name, f in _SOURCE_FILTERS.items()}
TICKS_SQL = TICKS_SQL_BY_SOURCE["all"]


def ticks_sql(source: str) -> Any:
    """Tick query for one price source. Raises ValueError for an unknown source."""
    if source not in TICKS_SQL_BY_SOURCE:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    return TICKS_SQL_BY_SOURCE[source]


def _price_field(side: str, selection: str) -> str:
    return f"{'lay' if side == 'LAY' else 'back'}_{selection.lower()}"


def _valid_action(action: Any) -> bool:
    name = str(action or "").upper()
    return ("BACK" in name or "LAY" in name) and selection_of(name) != ""


def price_after(
    ticks: Sequence[Mapping[str, Any]],
    placed_at: Any,
    side: str,
    selection: str,
    seconds: float = POST_PLACEMENT_SECONDS,
) -> Optional[float]:
    """Same-side price of the first valid tick at or after ``placed_at + seconds``.

    ``ticks`` must be sorted by ``time``. Returns ``None`` when no such tick exists.
    """
    cutoff = placed_at + timedelta(seconds=seconds)
    field = _price_field(side, selection)
    for tick in ticks:
        if tick["time"] < cutoff:
            continue
        price = tick.get(field)
        if price is not None and float(price) > 1.0:
            return float(price)
    return None


def odds_ratio_pct(side: str, entry: float, closing: Optional[float]) -> Optional[float]:
    """Odds-ratio CLV in percent (report only): BACK ``entry/closing - 1``, LAY ``closing/entry - 1``."""
    if closing is None or entry is None or entry <= 1.0 or closing <= 1.0:
        return None
    ratio = entry / closing if side == "BACK" else closing / entry
    return (ratio - 1.0) * 100.0


def build_audit_rows(
    bets: Sequence[Mapping[str, Any]],
    ticks_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Turn SQL bet rows and their match ticks into per-bet audit rows.

    Returns ``(rows, counts)``. Simulated matches and bets whose action is not a
    BACK/LAY on HOME/AWAY are skipped and counted.
    """
    counts = {"bets_read": len(bets), "excluded_simulated": 0, "excluded_unknown_action": 0}
    rows: list[dict[str, Any]] = []
    for bet in bets:
        match_id = str(bet["match_id"])
        ticks = sorted(ticks_by_match.get(match_id, ()), key=lambda t: t["time"])
        competition = next((t.get("competition") for t in ticks if t.get("competition")), "")
        tick_teams = {t.get("team_home") for t in ticks} | {t.get("team_away") for t in ticks}
        if is_simulated_name(competition, bet.get("team_home"), bet.get("team_away"), *tick_teams):
            counts["excluded_simulated"] += 1
            continue
        if not _valid_action(bet.get("action")):
            counts["excluded_unknown_action"] += 1
            continue

        action = bet["action"]
        side = side_of(action)
        selection = selection_of(action)
        entry = float(bet["odds"])
        closing = bet.get("closing_odds")
        closing_f = float(closing) if closing is not None else None

        price_60 = price_after(ticks, bet["placed_at"], side, selection)
        price_10m = price_after(ticks, bet["placed_at"], side, selection, seconds=HORIZON_SECONDS)
        rows.append({
            "bet_id": bet.get("bet_id"),
            "match_id": match_id,
            "side": side,
            "selection": selection,
            "agent_version": bet.get("agent_version") or "(none)",
            "closing_source": bet.get("closing_source") or "(unknown)",
            "odds": entry,
            "closing_odds": closing_f,
            "clv": bet_clv(bet, "clv"),
            "price_60s": price_60,
            "clv_60s": clv_prob(side, entry, price_60) if price_60 is not None else None,
            "price_10m": price_10m,
            "clv_10m": clv_prob(side, entry, price_10m) if price_10m is not None else None,
            "clv_odds_pct": odds_ratio_pct(side, entry, closing_f),
        })
    return rows, counts


def _block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    odds_pct = [r["clv_odds_pct"] for r in rows if r.get("clv_odds_pct") is not None]
    return {
        "n_bets": len(rows),
        "closing": portfolio_clv(rows, field="clv"),
        "post_60s": portfolio_clv(rows, field="clv_60s"),
        "post_10m": portfolio_clv(rows, field="clv_10m"),
        "clv_odds_pct_mean": float(sum(odds_pct) / len(odds_pct)) if odds_pct else math.nan,
    }


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Overall block plus breakdowns by ``agent_version`` and closing-price source."""
    by_version: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_version[row["agent_version"]].append(row)
        by_source[row["closing_source"]].append(row)
    return {
        "overall": _block(rows),
        "by_agent_version": {k: _block(v) for k, v in sorted(by_version.items())},
        "by_closing_source": {k: _block(v) for k, v in sorted(by_source.items())},
    }


async def run_audit(session: Any, limit: int, source: str = "all") -> dict[str, Any]:
    """Run the read-only audit over the ``limit`` most recent settled bets, priced from ``source`` ticks."""
    tick_query = ticks_sql(source)  # validate before touching the session
    await session.execute(text("SET TRANSACTION READ ONLY"))
    bet_result = await session.execute(BETS_SQL, {"limit": limit})
    bets = [dict(r) for r in bet_result.mappings().all()]

    ticks_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    match_ids = sorted({str(b["match_id"]) for b in bets})
    if match_ids:
        tick_result = await session.execute(tick_query, {"match_ids": match_ids})
        for r in tick_result.mappings().all():
            ticks_by_match[str(r["match_id"])].append(dict(r))

    rows, counts = build_audit_rows(bets, ticks_by_match)
    logger.info("clv_audit_built", **counts, rows=len(rows), source=source)
    return {"counts": counts, "source": source, **summarise(rows)}


def _fmt(value: Any, signed: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:+.4f}" if signed else f"{value:.4f}"


def _line(label: str, block: Mapping[str, Any]) -> str:
    closing = block["closing"]
    post = block["post_60s"]
    ci = f"[{_fmt(closing['ci95_lower'])}, {_fmt(closing['ci95_upper'])}]"
    ci60 = f"[{_fmt(post['ci95_lower'])}, {_fmt(post['ci95_upper'])}]"
    return (
        f"{label:<22} n={closing['n']:<5} clv={_fmt(closing['mean_clv'])} "
        f"med={_fmt(closing['median_clv'])} pos={_fmt(closing['positive_clv_rate'], False)} "
        f"ci95={ci}\n"
        f"{'':<22} n60={post['n']:<4} clv60s={_fmt(post['mean_clv'])} "
        f"med={_fmt(post['median_clv'])} pos={_fmt(post['positive_clv_rate'], False)} "
        f"ci95={ci60} odds_pct={_fmt(block['clv_odds_pct_mean'])}\n"
        f"{'':<22} n10m={block['post_10m']['n']:<3} clv10m={_fmt(block['post_10m']['mean_clv'])} "
        f"ci95=[{_fmt(block['post_10m']['ci95_lower'])}, {_fmt(block['post_10m']['ci95_upper'])}]"
    )


def format_report(report: Mapping[str, Any]) -> str:
    """Plain-text report. Missing statistics print as n/a, never as 0."""
    counts = report["counts"]
    lines = [
        "CLV audit (read-only). clv = clv_prob at match close (probability points);",
        "clv60s = clv_prob at the same-side price 60 s after placement; odds_pct = odds-ratio (report only).",
        f"bets read={counts['bets_read']} excluded_simulated={counts['excluded_simulated']} "
        f"excluded_unknown_action={counts['excluded_unknown_action']}",
        "",
        _line("ALL", report["overall"]),
    ]
    for title, key in (("agent_version", "by_agent_version"), ("closing_source", "by_closing_source")):
        lines.append("")
        lines.append(f"-- by {title} --")
        for name, block in report[key].items():
            lines.append(_line(name[:22], block))
    return "\n".join(lines)


async def _run_cli(limit: int, source: str) -> str:
    from shared.db import close_database, get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            report = await run_audit(session, limit, source=source)
            await session.rollback()
    finally:
        await close_database()
    return format_report(report)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only CLV audit of settled virtual bets.")
    parser.add_argument("--limit", type=int, default=100,
                        help="most recent settled bets to audit (default 100)")
    parser.add_argument("--source", choices=SOURCES, default=CLV_REFERENCE_SOURCE,
                        help="price feed for the 60 s and 10 m CLV: betfair or lotusbook (default: CLV_REFERENCE_SOURCE), or all")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    print(asyncio.run(_run_cli(args.limit, args.source)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
