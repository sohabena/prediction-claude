"""S73 paper trading report: the daily review and the markdown report for the paper variants.

Usage::

    python scripts/paper_report.py              # review now, write reports/paper/<YYYYMMDD>.md and models/paper/primary.json
    python scripts/paper_report.py --dry-run    # print the report, write nothing

The trader loop (scripts/run_paper_trader.py) calls ``run_due_review`` every cycle; it does the same work once a day
from 00:05 UTC. The only writes are the report file and ``models/paper/primary.json``. The database is read with
``SET TRANSACTION READ ONLY``.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from virtual_trading import paper_review as review  # noqa: E402

REPORT_DIR = ROOT / "reports" / "paper"
TOP_TRADES = 5
RECENT_MATCHES = 20


def _num(value: Any, digits: int = 4, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    spec = f"{'+' if signed else ''}.{digits}f"
    return format(float(value), spec)


def _ci(mean: Any, lo: Any, hi: Any, digits: int = 4) -> str:
    return f"{_num(mean, digits, True)} [{_num(lo, digits, True)}, {_num(hi, digits, True)}]"


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _team(rows: Sequence[Mapping[str, Any]], selection: str, fallback: str) -> str:
    for row in rows:
        if row["selection"] == selection and row.get("team"):
            return str(row["team"])
    return fallback


def _match_block(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Recent matches: teams, in-play flag, fills and stake, and the largest worst-case exposure over variants."""
    by_match: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_match.setdefault(str(row["match_id"]), []).append(row)
    ordered = sorted(
        by_match.items(),
        key=lambda kv: max(r["decided_at"] for r in kv[1]),
        reverse=True,
    )[:RECENT_MATCHES]
    lines = [
        "| match | teams | in play | fills | stake filled | worst-case exposure (max variant) | settled |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for mid, group in ordered:
        fills = [r for r in group if r["status"] in review.FILLED]
        home = _team(group, "home", "home team (name not recorded)")
        away = _team(group, "away", "away team (name not recorded)")
        exposure = review.worst_exposure(fills)
        settled = all(r["status"] in review.SETTLED for r in fills) and bool(fills)
        lines.append(
            f"| {mid} | {home} v {away} | {'yes' if any(r['in_play'] for r in group) else 'no'} | "
            f"{len(fills)} | {sum(float(r['stake']) for r in fills):,.0f} | "
            f"{max(exposure.values(), default=0.0):,.0f} | {'yes' if settled else 'no'} |"
        )
    if len(by_match) > RECENT_MATCHES:
        lines.append(f"\nShowing the {RECENT_MATCHES} most recent of {len(by_match)} matches.")
    return lines


def _trade_line(row: Mapping[str, Any]) -> str:
    price = _num(row.get("price_filled"), 2)
    return (
        f"| {row['variant']} | {row['match_id']} | {row['side']} {row['selection']} ({row.get('team') or ''}) | "
        f"{price} | {float(row['stake']):,.0f} | {_num(row.get('pnl'), 0, True)} | "
        f"{row.get('result') or 'n/a'} | {_num(row.get('clv'), 4, True)} |"
    )


def _missing_block(missing: Sequence[Mapping[str, Any]]) -> list[str]:
    """S76: real matches with no decided result (scripts/results_reconciliation.py)."""
    lines = ["## Results missing", ""]
    if not missing:
        lines.append("None: every real match with a tick in the last 7 days has a decided result or is under 3 h old.")
        return lines
    lines.append(
        f"{len(missing)} real match(es) with last tick over 3 h old and no win, tie, no_result or abandoned result. "
        'Settle each with `python scripts/set_result.py --match <id> --winner "<team>"` (or `--tie` / `--no-result`).'
    )
    lines.append("")
    lines.append("| match | teams | competition | start | last tick | result |")
    lines.append("|---|---|---|---|---|---|")
    for item in missing[:RECENT_MATCHES]:
        start = item["match_start_time"]
        start_text = f"{start:%Y-%m-%d %H:%M} UTC" if start else "n/a"
        lines.append(
            f"| {item['match_id']} | {item['team_home']} v {item['team_away']} | {item['competition'] or 'n/a'} | "
            f"{start_text} | {item['last_tick']:%Y-%m-%d %H:%M} UTC | {item['result_type'] or 'no row'} |"
        )
    if len(missing) > RECENT_MATCHES:
        lines.append(f"\nShowing the {RECENT_MATCHES} oldest of {len(missing)}.")
    return lines


def render(
    state: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    now: datetime,
    missing: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    """The markdown report. ``state`` is ``review.review(...)``'s result for the same ``rows``. ``missing`` is the S76
    reconciliation list; its section is written only when it is given."""
    variants = state["variants"]
    ranking = state["ranking"]
    out: list[str] = []
    out.append(f"# PHOENIX paper trading report, {now.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC")
    out.append("")
    out.append(
        f"Evidence: every paper bet recorded through this time ({state['bets_seen']} rows, first decided "
        f"{state.get('first_decided_at') or 'n/a'}, last {state.get('last_decided_at') or 'n/a'}). Virtual only: no "
        f"real money, no site access. The daily loss rule judges the last full UTC day, {state['review_day']}."
    )
    out.append("")
    out.append("## Primary and suspensions")
    out.append("")
    primary = state.get("primary")
    if primary:
        row = next(v for v in variants if v["variant"] == primary)
        out.append(
            f"- Primary: **{primary}**, CLV {_ci(row['clv_mean'], row['clv_ci_lo'], row['clv_ci_hi'])} "
            f"over {row['clv_n']} CLV bets ({state['primary_reason']})."
        )
    else:
        out.append(f"- Primary: none ({state['primary_reason']}).")
    suspended = state.get("suspended") or {}
    if suspended:
        for name, reason in sorted(suspended.items()):
            out.append(f"- Suspended: **{name}**, {reason}. No new risk until the next review.")
    else:
        out.append("- Suspended: none.")
    p_best = ranking.get("p_best") or {}
    if p_best:
        ranked = ", ".join(f"{k} {v:.1%}" for k, v in sorted(p_best.items(), key=lambda kv: -kv[1]))
        out.append(f"- Thompson P(best) on CLV ({ranking['draws']} draws): {ranked}.")
    out.append("")
    out.append("## Variants")
    out.append("")
    out.append(
        "| variant | bets | filled | fill rate | matches | settled | void | CLV mean [95% CI] | P&L [95% CI] | "
        "Brier (n) | max worst-case | day P&L | R3 bets left | R3 matches left | R3 met |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---:|---|")
    for v in variants:
        out.append(
            f"| {v['variant']} | {v['bets']} | {v['filled']} | {_pct(v['fill_rate'])} | {v['matches']} | "
            f"{v['settled_bets']} | {v['void_bets']} | {_ci(v['clv_mean'], v['clv_ci_lo'], v['clv_ci_hi'])} | "
            f"{v['pnl']:,.0f} total; per bet {_ci(v['pnl_mean'], v['pnl_ci_lo'], v['pnl_ci_hi'], 2)} | "
            f"{_num(v['brier'])} ({v['brier_n']}) | "
            f"{v['max_worst_exposure']:,.0f} | {v['day_pnl']:,.0f} | {v['r3_bets_left']} | "
            f"{v['r3_matches_left']} | {'yes' if v['r3_met'] else 'no'} |"
        )
    out.append("")
    out.append(
        "CLV is in probability points against the reference price (same side, shared.metrics.clv_prob). P&L is gross "
        "of commission (not modelled). Worst-case exposure is the worst home/away outcome over a match's fills, against "
        "the 100,000 cap. CIs are seeded percentile bootstraps."
    )
    out.append("")
    out.append("## Progress to R3")
    out.append("")
    out.append(
        f"R3 for a variant: at least {review.R3_SETTLED_BETS} settled paper bets over at least {review.R3_MATCHES} "
        "matches, with the CLV CI lower bound above zero."
    )
    out.append("")
    for v in variants:
        out.append(
            f"- {v['variant']}: {v['settled_bets']}/{review.R3_SETTLED_BETS} settled bets, "
            f"{v['settled_matches']}/{review.R3_MATCHES} matches, "
            f"{v['r3_bets_left']} settled bets until R3"
            + ("; R3 met" if v["r3_met"] else "")
        )
    out.append("")
    if missing is not None:
        out.extend(_missing_block(missing))
        out.append("")
    out.append("## Matches")
    out.append("")
    out.extend(_match_block(rows) if rows else ["No paper bets yet."])
    out.append("")
    out.append("## Best and worst trades (settled)")
    out.append("")
    settled = [r for r in rows if r["status"] in review.SETTLED and r.get("pnl") is not None]
    if settled:
        header = (
            "| variant | match | bet | price | stake | P&L | result | CLV |\n"
            "|---|---|---|---:|---:|---:|---|---:|"
        )
        ranked = sorted(settled, key=lambda r: float(r["pnl"]))
        out.append("Best:")
        out.append("")
        out.append(header)
        out.extend(_trade_line(r) for r in reversed(ranked[-TOP_TRADES:]))
        out.append("")
        out.append("Worst:")
        out.append("")
        out.append(header)
        out.extend(_trade_line(r) for r in ranked[:TOP_TRADES])
    else:
        out.append("No settled paper bets yet.")
    out.append("")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- run
def bankrolls() -> dict[str, float]:
    """Each variant's bankroll, from the one place the variants are built (the trader's grid)."""
    from virtual_trading.paper_trader import build_variants

    return {v.name: v.bankroll for v in build_variants()}


async def run_review(
    now: Optional[datetime] = None,
    *,
    state_path: Path = review.STATE_PATH,
    report_dir: Path = REPORT_DIR,
    write: bool = True,
) -> tuple[dict[str, Any], str, Optional[Path]]:
    """Read the ledger, run the review, and (when ``write``) write the state and the report. Returns (state, text,
    report path or None)."""
    from sqlalchemy import text

    from shared.db import get_session

    from scripts.results_reconciliation import missing_results

    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        rows = await review.load_rows(session)
        missing = await missing_results(session, now)
    state = review.review(rows, bankrolls(), now=now)
    text_report = render(state, rows, now, missing)
    if not write:
        return state, text_report, None
    review.write_state(state, state_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{now.astimezone(timezone.utc):%Y%m%d}.md"
    path.write_text(text_report, encoding="utf-8")
    return state, text_report, path


async def run_due_review(now: datetime, last_reviewed: Optional[date], **kwargs: Any) -> Optional[date]:
    """The trader loop's daily hook. Runs the review and report once per UTC day from 00:05, and returns the day it
    covered (or ``last_reviewed`` when nothing was due)."""
    day = review.review_due(now, last_reviewed)
    if day is None:
        return last_reviewed
    await run_review(now, **kwargs)
    return day


async def _main(args: argparse.Namespace) -> int:
    from shared.db import close_database

    try:
        state, text_report, path = await run_review(write=not args.dry_run)
    finally:
        await close_database()
    print(text_report)
    if path is not None:
        print(f"wrote {path}")
        print(f"wrote {review.STATE_PATH}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S73 paper trading report and review (virtual bets only).")
    parser.add_argument("--dry-run", action="store_true", help="print the report; write no file")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
