"""S73 paper trading report: the daily review and the markdown report for the paper variants. S88: the report leads with
the value-first scorecard (Tier A), then the diagnostics (Tier B), then the supporting detail.

Usage::

    python scripts/paper_report.py              # review now, write reports/paper/<YYYYMMDD>.md and models/paper/primary.json
    python scripts/paper_report.py --dry-run    # print the report, write nothing

The trader loop (scripts/run_paper_trader.py) calls ``run_due_review`` every cycle; it does the same work once a day
from 00:05 UTC. The only writes are the report file and ``models/paper/primary.json``. The database is read with
``SET TRANSACTION READ ONLY``.

Headline order (S88): (1) settled real matches with a tradable book, (2) per variant the match-clustered CLV excess over
placebo_random, pre-match and in-play apart, (3) result coverage (the S76 missing list), (4) fill realism (S87), (5) R1,
R2 and R3 progress in matches, (6) the in-play model gate per format (S89). Tier-C measures (row counts, Elo
comparisons, per-bet intervals) are not in the report; the monitor keeps its own checks.
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
from virtual_trading import scorecard as sc  # noqa: E402

REPORT_DIR = ROOT / "reports" / "paper"
TOP_TRADES = 5
RECENT_MATCHES = 20
MAX_LISTED = 20  # rows listed in a table of missing results or live matches
Section = dict[str, Any]


def _num(value: Any, digits: int = 4, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    spec = f"{'+' if signed else ''}.{digits}f"
    return format(float(value), spec)


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _headline(block: Mapping[str, Any], point: Any, lo: Any, hi: Any, kind: str) -> str:
    """A match-clustered headline. ``kind`` is 'clv', 'clv_w' (stake-weighted CLV) or 'pnl' (total P&L); the block's
    own match and bet counts for that statistic are shown."""
    if kind == "pnl":
        return review.headline(point, lo, hi, matches=block["pnl_matches"], bets=block["pnl_bets"], digits=0)
    return review.headline(point, lo, hi, matches=block["clv_matches"], bets=block["clv_bets"])


def _name(variant: Mapping[str, Any]) -> str:
    """A variant's name, marked as a placebo control when it is one (S85: never a signal, never primary)."""
    return f"{variant['variant']} (placebo)" if variant.get("placebo") else str(variant["variant"])


def _strategy_names(state: Mapping[str, Any]) -> list[str]:
    return [str(v["variant"]) for v in state["variants"] if not v.get("placebo")]


def _segment_rows(variant: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """All bets, then pre-match and in-play, for one variant's summary."""
    segments = variant.get("segments") or {}
    return [
        ("all", variant),
        ("pre-match", segments.get("prematch", {})),
        ("in-play", segments.get("inplay", {})),
    ]


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
        "| match | teams | in play | fills | stake filled | worst-case exposure (largest variant) | settled |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for mid, group in ordered:
        fills = [r for r in group if r["status"] in review.FILLED]
        home = _team(group, "home", "home team (name not recorded)")
        away = _team(group, "away", "away team (name not recorded)")
        # the worst case is per variant (each variant has its own cap); the column shows the largest of them
        per_variant = {}
        for r in fills:
            per_variant.setdefault(str(r["variant"]), []).append(r)
        worst = max((max(review.worst_exposure(vf).values(), default=0.0) for vf in per_variant.values()), default=0.0)
        settled = all(r["status"] in review.SETTLED for r in fills) and bool(fills)
        lines.append(
            f"| {mid} | {home} v {away} | {'yes' if any(r['in_play'] for r in group) else 'no'} | "
            f"{len(fills)} | {sum(float(r['stake']) for r in fills):,.0f} | "
            f"{worst:,.0f} | {'yes' if settled else 'no'} |"
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


def _section(title: str, lines: Sequence[str] = (), header: Optional[Sequence[str]] = None,
             rows: Sequence[Sequence[str]] = ()) -> Section:
    return {"title": title, "lines": list(lines), "header": list(header) if header else None,
            "rows": [list(r) for r in rows]}


# ----------------------------------------------------------------------------- Tier A: the headline
def _excess_for(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> dict[str, dict[str, Any]]:
    return review.excess_over_placebo(rows, names, control=review.PLACEBO_RANDOM)


def _excess_section(state: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> Section:
    """(b) each strategy variant's CLV as an excess over placebo_random, per match, match-clustered, pre-match and
    in-play apart. A segment is labelled 'too few matches' below the minimum number of matches."""
    names = _strategy_names(state)
    pre = _excess_for([r for r in rows if not bool(r.get("in_play"))], names)
    live = _excess_for([r for r in rows if bool(r.get("in_play"))], names)
    table = []
    for name in names:
        table.append([
            name,
            review.headline(pre[name]["estimate"], pre[name]["ci_lo"], pre[name]["ci_hi"],
                            matches=pre[name]["matches"], bets=pre[name]["bets"]) if name in pre else "n/a",
            review.headline(live[name]["estimate"], live[name]["ci_lo"], live[name]["ci_hi"],
                            matches=live[name]["matches"], bets=live[name]["bets"]) if name in live else "n/a",
        ])
    return _section(
        "CLV excess over placebo_random, match-clustered",
        [
            f"Each figure is the variant's CLV minus placebo_random's on each match, resampled over matches, in "
            f"probability points, with the matches and CLV bets behind it. Placebos trade the reference variant's "
            f"times and stakes with a random side (never a signal).",
            f"Pre-match: {review.excess_sentence(pre)}",
            f"In-play: {review.excess_sentence(live)}",
        ],
        ["variant", "pre-match excess CLV [95% CI] (matches / bets)", "in-play excess CLV [95% CI] (matches / bets)"],
        table,
    )


def _tradable_section(counts: Optional[Mapping[str, Any]]) -> Section:
    """(a) settled real matches with a tradable book, total and the last 7 days."""
    title = "Settled real matches with a tradable book"
    if counts is None:
        return _section(title, ["Not computed in this render."])
    return _section(title, [
        f"**{counts['tradable_settled']}** settled real matches have a tradable book (both sides back and lay on the "
        f"match odds before the off or in play). **{counts['tradable_settled_recent']}** of them settled in the last "
        f"{counts['recent_days']} days.",
        f"Settled real matches in all: {counts['settled_real']}, of which "
        f"{counts['settled_real'] - counts['tradable_settled']} have no tradable book. Settled means a win with a "
        f"winner, a tie, no result or an abandonment (the same rule as the result coverage below).",
    ])


def _missing_section(missing: Optional[Sequence[Mapping[str, Any]]]) -> Section:
    """(c) result coverage: real matches with no decided result (S76). The title keeps the S76 wording."""
    if missing is None:
        return _section("Result coverage (not checked in this render)")
    if not missing:
        return _section("Results missing", [
            "None: every real match with a tick in the last 7 days has a decided result or is under 3 h old.",
        ])
    header = ["match", "teams", "competition", "start", "last tick", "result"]
    rows = []
    for item in missing[:MAX_LISTED]:
        start = item["match_start_time"]
        start_text = f"{start:%Y-%m-%d %H:%M} UTC" if start else "n/a"
        rows.append([
            str(item["match_id"]), f"{item['team_home']} v {item['team_away']}", item["competition"] or "n/a",
            start_text, f"{item['last_tick']:%Y-%m-%d %H:%M} UTC", item["result_type"] or "no row",
        ])
    more = f" Showing the {MAX_LISTED} oldest of {len(missing)}." if len(missing) > MAX_LISTED else ""
    return _section("Results missing", [
        f"{len(missing)} real match(es) with last tick over 3 h old and no win, tie, no_result or abandoned result. "
        'Settle each with `python scripts/set_result.py --match <id> --winner "<team>"` (or `--tie` / `--no-result`).' + more,
    ], header, rows)


def _missing_block(missing: Optional[Sequence[Mapping[str, Any]]]) -> list[str]:
    """The S76 results-missing block as markdown (``## Results missing``), for tests and for the dashboard."""
    return _section_md(_missing_section(missing), "##")


def _fills_section(state: Mapping[str, Any]) -> Section:
    """(d) fill realism: stake fill rate and the size-capped share, with the state of the live size cap."""
    if sc.live_size_cap():
        cap_text = ("Live size cap: ON. A fill is cut to the size the match page showed at the fill time; a fill cut "
                    "below the stake asked is size-capped.")
    else:
        cap_text = ("Live size cap: OFF until the size unit is verified (decision 2026-11-03). Live fills are not cut to "
                    "the size the match page showed, so the figures below describe the fills as made and are not yet a "
                    "test of fill realism.")
    rows = []
    for v in state["variants"]:
        if v.get("placebo"):
            continue
        z = v.get("sizing") or {}
        rows.append([
            _name(v), _pct(v["fill_rate"]), _pct(z.get("stake_fill_rate")), _pct(z.get("size_capped_share")),
            _pct(z.get("size_unknown_share")),
            f"{_pct(z.get('mean_adverse_slippage_pct'))}",
        ])
    return _section(
        "Fill realism",
        [cap_text,
         "Fill rate is the fills over the decided bets; stake fill rate is the filled stake over the stake asked; "
         "adverse slippage is the move from the decision price to the fill price, as a share of the decision odds."],
        ["variant", "fill rate (count)", "stake fill rate", "size-capped share", "size-unknown share",
         "mean adverse slippage"],
        rows,
    )


def _progress_section(state: Mapping[str, Any], counts: Optional[Mapping[str, Any]]) -> Section:
    """(e) R1, R2 and R3 progress, counted in matches. R3 per strategy variant (settled matches and settled bets)."""
    title = "Progress to R3, R1 and R2 (matches, not bets)"
    if counts is None:
        return _section(title, ["Not computed in this render."])
    if counts["r2_matches"] is None:
        r2 = (f"R2 (strategy on real prices): held-out count not available (the production model cut-off could not be "
              f"read); target {counts['r2_target']} held-out matches.")
    else:
        r2 = (f"R2 (strategy on real prices): {counts['r2_matches']} of {counts['r2_target']} held-out matches "
              f"(settled, tradable, started after {counts['r2_cutoff']}, the newest production model's training cut-off).")
    lines = [
        f"R1 (model against the market, log loss): {counts['r1_matches']} of {counts['r1_target']} matches with a "
        f"pre-match price and a decided win. A tie, no result or abandonment has no home or away outcome to score.",
        r2,
        f"R3 for a variant: at least {sc.R3_SETTLED_BETS} settled paper bets over at least {sc.R3_MATCHES} matches, "
        f"with the CLV confidence interval's lower bound above zero.",
    ]
    rows = []
    for v in state["variants"]:
        if v.get("placebo"):
            continue
        rows.append([
            _name(v),
            f"{v['settled_matches']} of {sc.R3_MATCHES}",
            f"{v['settled_bets']} of {sc.R3_SETTLED_BETS}",
            "yes" if (v.get("clv_ci_lo") is not None and v["clv_ci_lo"] > 0) else "no",
            "yes" if v["r3_met"] else "no",
        ])
    return _section(title, lines,
                    ["variant", "settled matches", "settled bets", "CLV lower bound above zero", "R3 met"], rows)


def _gate_section(gate: Optional[Mapping[str, Any]]) -> Section:
    """(f) the in-play model gate per format (S89), from models/registry/inplay_gate.json."""
    title = "In-play model gate (S89)"
    if gate is None:
        return _section(title, ["Not loaded in this render."])
    rows = []
    for g in sc.gate_rows(gate):
        diff = "n/a" if g["logloss_diff"] is None else f"{float(g['logloss_diff']):+.4f}"
        rows.append([g["format"], "open" if g["open"] else "closed", str(g["matches"]), diff])
    return _section(
        title,
        ["The in-play model may open new risk for a format only when its gate is open: at least 15 decided matches, "
         "a log loss no worse than the market's, and a CI lower bound below zero. The gate does not restrict paper "
         "trading (decision 2026-11-03); it governs in-play signals to the operator."],
        ["format", "gate", "decided matches", "log loss, model minus market"],
        rows,
    )


def headline_sections(
    state: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    counts: Optional[Mapping[str, Any]] = None,
    missing: Optional[Sequence[Mapping[str, Any]]] = None,
    gate: Optional[Mapping[str, Any]] = None,
) -> list[Section]:
    """The Tier-A sections in the S88 order. Shared by the report and the dashboard."""
    return [
        _tradable_section(counts),
        _excess_section(state, rows),
        _missing_section(missing),
        _fills_section(state),
        _progress_section(state, counts),
        _gate_section(gate),
    ]


# ----------------------------------------------------------------------------- Tier B: diagnostics
def tier_sections(tier: Optional[Mapping[str, Any]]) -> list[Section]:
    """Worst case per match against the cap, trades per match against the ticket budget, guard refusals, rated share."""
    if tier is None:
        return [_section("Diagnostics", ["Not computed in this render."])]
    cap = float(tier["cap"])
    budget = int(tier["budget"])
    worst_rows = [
        [v["variant"], f"{v['worst_max']:,.0f}", _pct(v["worst_share"]), str(v["matches_over_cap"]), str(v["matches"])]
        for v in tier["variants"]
    ]
    trade_rows = [
        [v["variant"], str(v["trades_max"]), str(v["matches_over_budget"]), str(v["matches"])]
        for v in tier["variants"]
    ]
    refusals = tier.get("refusals")
    if refusals is None:
        guard_lines = ["No trader log yet, so no guard refusals to count."]
    else:
        cycles, counts = refusals
        listed = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"
        guard_lines = [f"{cycles} cycles in the last {tier['guard_window_hours']:g} h. Refusals by reason: {listed}."]
    rated = tier["rated_share"]
    return [
        _section(f"Worst case per match against the {cap:,.0f} cap",
                 ["The worst home or away outcome of a variant's fills on one match, against the cap."],
                 ["variant", "max worst case", "share of cap", "matches over cap", "matches"], worst_rows),
        _section(f"Trades per match against the {budget}-ticket budget",
                 ["Every ticket counts, whatever its reason (S90). A match over the budget is a churn signal."],
                 ["variant", "max trades on one match", f"matches over {budget}", "matches"], trade_rows),
        _section("Guard refusals (last 24 h)", guard_lines),
        _section("Rated-match share", [
            f"{tier['rated_matches']} of {tier['ledger_matches']} matches in the paper ledger had a model view "
            f"(a model probability on at least one decision): "
            + ("n/a." if math.isnan(rated) else f"{rated * 100:.0f}%.")
        ]),
    ]


# ----------------------------------------------------------------------------- markdown
def _section_md(section: Section, level: str) -> list[str]:
    out = [f"{level} {section['title']}", ""]
    for line in section["lines"]:
        out.append(line)
        out.append("")
    if section["header"]:
        out.append("| " + " | ".join(section["header"]) + " |")
        out.append("|" + "|".join("---" for _ in section["header"]) + "|")
        for row in section["rows"]:
            out.append("| " + " | ".join(str(c) for c in row) + " |")
        out.append("")
    return out


def _primary_lines(state: Mapping[str, Any]) -> list[str]:
    variants = state["variants"]
    ranking = state["ranking"]
    out = []
    primary = state.get("primary")
    if primary:
        row = next(v for v in variants if v["variant"] == primary)
        out.append(
            f"- Primary: **{primary}**, CLV {_headline(row, row['clv_mean'], row['clv_ci_lo'], row['clv_ci_hi'], 'clv')} "
            f"({state['primary_reason']})."
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
    return out


def render(
    state: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    now: datetime,
    missing: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    counts: Optional[Mapping[str, Any]] = None,
    gate: Optional[Mapping[str, Any]] = None,
    tier: Optional[Mapping[str, Any]] = None,
) -> str:
    """The markdown report. ``state`` is ``review.review(...)``'s result for the same ``rows``. ``missing`` is the S76
    reconciliation list (its section says 'not checked' when it is None). ``counts``, ``gate`` and ``tier`` are the S88
    scorecard pieces (``scorecard.match_counts``, the gate file, ``scorecard.tier_b``); each says 'not computed' if absent."""
    variants = state["variants"]
    out: list[str] = []
    out.append(f"# PHOENIX paper trading report, {now.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC")
    out.append("")
    out.append(
        f"Evidence: paper bets decided from {state.get('first_decided_at') or 'n/a'} to "
        f"{state.get('last_decided_at') or 'n/a'}. Virtual only: no real money, no site access. The daily loss rule "
        f"judges the last full UTC day, {state['review_day']}."
    )
    out.append("")
    out.append("## Headline (Tier A: the measures that decide money)")
    out.append("")
    out.append("Every interval resamples matches, because a match's bets move together. Each figure shows its matches "
               "and bets. A count below the rung's target is progress, not a result.")
    out.append("")
    for section in headline_sections(state, rows, counts=counts, missing=missing, gate=gate):
        out.extend(_section_md(section, "###"))
    out.append("## Tier B: diagnostics (shown, never a headline)")
    out.append("")
    for section in tier_sections(tier):
        out.extend(_section_md(section, "###"))
    out.append("## Supporting detail")
    out.append("")
    out.append("### Primary and suspensions")
    out.append("")
    out.extend(_primary_lines(state))
    out.append("")
    out.append("### Detail: CLV and P&L by segment, match-clustered")
    out.append("")
    out.append("CLV is in probability points; P&L is the total, gross of commission. Each figure shows its matches "
               "and bets.")
    out.append("")
    out.append("| variant | segment | CLV mean [95% CI] | CLV stake-weighted [95% CI] | P&L total [95% CI] |")
    out.append("|---|---|---|---|---|")
    for v in variants:
        for label, block in _segment_rows(v):
            out.append(
                f"| {_name(v)} | {label} | {_headline(block, block['clv_mean'], block['clv_ci_lo'], block['clv_ci_hi'], 'clv')} | "
                f"{_headline(block, block['clv_w_mean'], block['clv_w_ci_lo'], block['clv_w_ci_hi'], 'clv_w')} | "
                f"{_headline(block, block['pnl'], block['pnl_ci_lo'], block['pnl_ci_hi'], 'pnl')} |"
            )
    out.append("")
    out.append("### Variants")
    out.append("")
    out.append(
        "| variant | bets | filled | fill rate | matches | settled | void | Brier (n) | max worst-case | day P&L | "
        "R3 bets left | R3 matches left | R3 met |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|")
    for v in variants:
        out.append(
            f"| {_name(v)} | {v['bets']} | {v['filled']} | {_pct(v['fill_rate'])} | {v['matches']} | "
            f"{v['settled_bets']} | {v['void_bets']} | "
            f"{_num(v['brier'])} ({v['brier_n']}) | "
            f"{v['max_worst_exposure']:,.0f} | {v['day_pnl']:,.0f} | {v['r3_bets_left']} | "
            f"{v['r3_matches_left']} | {'yes' if v['r3_met'] else 'no'} |"
        )
    out.append("")
    out.append("### Fills and sizes (S87)")
    out.append("")
    out.append(
        "A fill is size-capped when the size the match page showed cut it below the stake asked. Size-unknown fills had "
        "no size at the fill time and were capped at 10,000. Adverse slippage is the move from the decision price to "
        "the fill price, in decimal odds (positive is worse for the bet)."
    )
    out.append("")
    out.append("| variant | fill rate (count) | stake fill rate | size-capped fills | size-capped share | "
               "stake lapsed | size-unknown share | size-zero unfilled | mean adverse slippage (odds / %) |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for v in variants:
        z = v.get("sizing") or {}
        out.append(
            f"| {_name(v)} | {_pct(v['fill_rate'])} | {_pct(z.get('stake_fill_rate'))} | "
            f"{z.get('size_capped_fills', 0)} | {_pct(z.get('size_capped_share'))} | "
            f"{z.get('size_capped_stake_lapsed', 0) or 0:,.0f} | {_pct(z.get('size_unknown_share'))} | "
            f"{z.get('size_zero_unfilled', 0)} | "
            f"{_num(z.get('mean_adverse_slippage'), 4, True)} / {_pct(z.get('mean_adverse_slippage_pct'))} |"
        )
    out.append("")
    out.append("### Matches")
    out.append("")
    out.extend(_match_block(rows) if rows else ["No paper bets yet."])
    out.append("")
    out.append("### Best and worst trades (settled)")
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


def load_gate() -> dict[str, dict[str, Any]]:
    """The in-play gate file (S89): every format closed when it is missing or unreadable."""
    from training.inplay_eval import load_inplay_gate

    return load_inplay_gate()


async def gather(session: Any, now: datetime, *, state_path: Path = review.STATE_PATH,
                 log_path: Optional[Path] = None) -> dict[str, Any]:
    """Every input of the report and the dashboard, read from ``session`` (the caller sets READ ONLY). Also runs the
    review, so the result is the same whichever of the two asks."""
    from scripts.results_reconciliation import missing_results

    rows = await review.load_rows(session)
    missing = await missing_results(session, now)
    facts = await sc.load_match_facts(session)
    counts = sc.match_counts(facts, now, sc.held_out_cutoff())
    # S85: the placebos copy the primary in the state file (read before this review overwrites it)
    reference = review.reference_name(review.load_state(state_path))
    state = review.review(rows, bankrolls(), now=now, reference=reference)
    refusals = sc.read_refusals(now, log_path) if log_path is not None else sc.read_refusals(now)
    return {
        "rows": rows,
        "state": state,
        "missing": missing,
        "counts": counts,
        "gate": load_gate(),
        "tier": sc.tier_b(rows, state, now=now, refusals=refusals),
    }


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

    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        data = await gather(session, now, state_path=state_path)
    state = data["state"]
    text_report = render(state, data["rows"], now, data["missing"], counts=data["counts"], gate=data["gate"],
                         tier=data["tier"])
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
