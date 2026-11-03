"""S74 replay: run the exact paper-trading cycle (``paper_trader.run_cycle``) over every recorded match, as if live.

Usage::

    python scripts/replay_paper.py --from 2026-11-01                       # every recorded match, all variants
    python scripts/replay_paper.py --from 2026-11-01 --variants w05_e03,market_only --out reports/replay/quick
    python scripts/replay_paper.py --from 2026-11-01 --include-srl         # also simulated (SRL) matches

Usage (S87)::

    python scripts/replay_paper.py --from 2026-11-01 --to 2026-11-03T10:56:38Z --out reports/replay/s87 --compare-size-cap
    python scripts/replay_paper.py --from 2026-11-01 --fill-delay 30     # fill delay 30, 60 (default) or 120 s
    python scripts/replay_paper.py --from 2026-11-01 --size-cap          # fills cut to the size shown (unit unverified)

Sizes (S87): a fill is cut to the size the match page showed for that side and price at the fill time (as of the fill
time, never after it). No size known: at most 10,000, marked ``size_unknown``. ``--compare-size-cap`` runs the replay
twice, capped (reported) and uncapped, and writes the uncapped summary to ``<out>/uncapped``. The summary then carries
capped minus uncapped P&L per variant, with a match-clustered 95% CI.

Clock: decisions every 15 s of recorded time from ``--from`` to ``--to`` (default: the last recorded tick). When no
real match has a fresh price and no bet is open, the clock jumps to the next recorded tick on the 15 s grid. Nothing
can happen in between, so the jump gives the same decisions as stepping (a parity-tested shortcut).

Reads: the recorded rows are loaded once, read-only (``virtual_trading.replay_feed.ReplayFeed``), with the session set
to READ ONLY. Writes: the in-memory ledger (``virtual_trading.memory_session.MemorySession``) and the files under
``--out`` (summary.json, summary.md, bets.csv). ``paper_bets`` is never touched.

Output per variant: intents, fills, settled bets, CLV mean and P&L total with seeded 95% CIs that resample matches (S84),
each for all bets and split pre-match / in-play, the worst case per match, churn (trades per match and the share of
trades under 2,000 stake), and a per-match table. Per-bet intervals appear only in a detail table. S75 guard
refusals are counted by reason across the run. The
summary files do not contain wall-clock times, so the same inputs give the same files. Runtime is printed only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.models.paper_bets import PaperBet  # noqa: E402
from virtual_trading import paper_ledger as ledger  # noqa: E402
from virtual_trading import paper_review as review  # noqa: E402
from virtual_trading import paper_trader as pt  # noqa: E402
from virtual_trading.memory_session import MemorySession  # noqa: E402
from virtual_trading.replay_feed import ReplayFeed  # noqa: E402
from shared.metrics import cluster_bootstrap  # noqa: E402

STEP = timedelta(seconds=15)  # the live cycle's period
SMALL_TRADE = 2_000.0  # plan: churn is measured as the share of trades under this stake
REPLAY_DIR = ROOT / "reports" / "replay"
BET_COLUMNS = [c.name for c in PaperBet.__table__.columns]
TRADE_STATUSES = review.FILLED  # filled, settled or void: the bets that were matched


# ----------------------------------------------------------------------------- the clock
@dataclass
class ReplayResult:
    session: MemorySession
    start: datetime
    end: datetime
    cycles: int = 0  # cycles that ran (decisions taken at each)
    skipped_steps: int = 0  # grid steps jumped over (no fresh price, no open bet)
    intents: list[dict[str, Any]] = field(default_factory=list)  # every intent, with decided_at
    skipped_registry: dict[str, int] = field(default_factory=dict)
    skipped_matches: dict[str, int] = field(default_factory=dict)
    guard_refusals: dict[str, int] = field(default_factory=dict)  # S75: every refusal, by reason, summed over cycles
    matches_considered: set[str] = field(default_factory=set)


def _grid_ceil(t: datetime, start: datetime) -> datetime:
    """The first 15 s grid point (from ``start``) at or after ``t``."""
    steps = math.ceil((t - start) / STEP)
    return start + STEP * steps


def _add_counts(into: dict, counts: Mapping[str, int]) -> None:
    for key, value in counts.items():
        into[key] = into.get(key, 0) + int(value)


async def replay_window(
    feed: Any,
    probability: Any,
    variants: Sequence[pt.Variant],
    *,
    start: datetime,
    end: datetime,
    include_simulated: bool = False,
    guards: Optional[pt.GuardConfig] = None,
    fill_delay: timedelta = ledger.FILL_DELAY,
    size_cap: bool = False,  # same default as run_cycle (S87 cap off until the size unit is verified)
    reference: str = review.DEFAULT_REFERENCE,  # S85: the variant the placebo controls copy
) -> ReplayResult:
    """Step the clock from ``start`` to ``end`` (inclusive) and run ``run_cycle`` at each step on the in-memory ledger.
    ``guards`` defaults to the S75 settings (``GuardConfig()``)."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    result = ReplayResult(session=MemorySession(), start=start, end=end)
    session = result.session
    now = start
    while now <= end:
        feed.seek(now)
        open_bets = any(r.status in ("pending", "filled") for r in session.rows)
        fresh = await feed.last_prices(now)
        if not fresh and not open_bets:
            nxt = feed.next_price_time(now)
            if nxt is None or nxt > end:
                result.skipped_steps += max(0, math.floor((end - now) / STEP))
                break
            target = _grid_ceil(nxt, start)
            if target > end:
                break
            result.skipped_steps += int((target - now) / STEP) - 1
            now = target
            continue

        report = await pt.run_cycle(
            session, feed, probability, variants, now=now, include_simulated=include_simulated,
            guards=guards or pt.GuardConfig(), fill_delay=fill_delay, size_cap=size_cap, reference=reference,
        )
        result.cycles += 1
        _add_counts(result.skipped_registry, report["skipped_registry"])
        _add_counts(result.skipped_matches, report["skipped_matches"])
        _add_counts(result.guard_refusals, report["guard_refusals"])
        result.matches_considered.update(m["match_id"] for m in report["matches"])
        for intent in report["intent_detail"]:
            result.intents.append({**intent, "decided_at": now.isoformat()})
        now = now + STEP
    return result


# ----------------------------------------------------------------------------- metrics
def _bet_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [{name: getattr(row, name) for name in BET_COLUMNS} for row in rows]


def _churn(fills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matches = {str(r["match_id"]) for r in fills}
    tiny = sum(1 for r in fills if float(r["stake"]) < SMALL_TRADE)
    return {
        "trades": len(fills),
        "trade_matches": len(matches),
        "trades_per_match": (len(fills) / len(matches)) if matches else math.nan,
        "tiny_trades": tiny,
        "tiny_share": (tiny / len(fills)) if fills else math.nan,
    }


def _pnl(rows: Sequence[Mapping[str, Any]]) -> float:
    return sum(float(r["pnl"]) for r in rows if r["status"] in review.SETTLED and r.get("pnl") is not None)


def _size_comparison(
    capped: Sequence[Mapping[str, Any]], uncapped: Sequence[Mapping[str, Any]], variants: Sequence[pt.Variant]
) -> dict[str, Any]:
    """Capped minus uncapped, per variant (S87). The interval resamples matches (the per-match difference is the item)."""
    out: dict[str, Any] = {}
    for variant in variants:
        c = [r for r in capped if r["variant"] == variant.name]
        u = [r for r in uncapped if r["variant"] == variant.name]
        matches = sorted({str(r["match_id"]) for r in c + u})
        per_match: list[float] = []
        for mid in matches:
            pc = _pnl([r for r in c if str(r["match_id"]) == mid])
            pu = _pnl([r for r in u if str(r["match_id"]) == mid])
            per_match.append(pc - pu)
        boot = cluster_bootstrap(per_match, matches, statistic="sum_per_cluster", n_boot=review.N_RESAMPLES,
                                 seed=review.SEED) if matches else {}
        fills_c = [r for r in c if r["status"] in review.FILLED]
        fills_u = [r for r in u if r["status"] in review.FILLED]
        out[variant.name] = {
            "pnl_capped": _pnl(c),
            "pnl_uncapped": _pnl(u),
            "pnl_difference": _pnl(c) - _pnl(u),
            "pnl_difference_ci_lo": boot.get("ci_lower"),
            "pnl_difference_ci_hi": boot.get("ci_upper"),
            "pnl_difference_matches": boot.get("n_clusters", 0),
            "fills_capped": len(fills_c),
            "fills_uncapped": len(fills_u),
            "filled_stake_capped": sum(float(r["stake"]) for r in fills_c),
            "filled_stake_uncapped": sum(float(r["stake"]) for r in fills_u),
            "max_worst_case_capped": max(review.worst_exposure(fills_c).values(), default=0.0),
            "max_worst_case_uncapped": max(review.worst_exposure(fills_u).values(), default=0.0),
        }
    return out


def build_summary(
    result: ReplayResult,
    variants: Sequence[pt.Variant],
    feed: Any,
    *,
    settings: Mapping[str, Any],
    uncapped: Optional[ReplayResult] = None,
    reference: str = review.DEFAULT_REFERENCE,
) -> dict[str, Any]:
    """Every number of the report, as plain data. Deterministic: no wall-clock times. ``uncapped`` (S87) is the same
    replay with no size cap, for the capped-versus-uncapped comparison. ``reference`` (S85) is the variant the placebo
    controls copied; the excess CLV of each variant over ``placebo_random`` is in ``placebo``."""
    rows = _bet_dicts(result.session.rows)
    day = review.review_day_for(result.end)
    intents_by_variant: dict[str, list[dict[str, Any]]] = {}
    for intent in result.intents:
        intents_by_variant.setdefault(intent["variant"], []).append(intent)

    variant_out: dict[str, Any] = {}
    for variant in variants:
        mine = [r for r in rows if r["variant"] == variant.name]
        s = review.summarise_variant(variant.name, mine, bankroll=variant.bankroll, review_day=day)
        fills = [r for r in mine if r["status"] in TRADE_STATUSES]
        s["intents"] = len(intents_by_variant.get(variant.name, []))
        s["intents_by_side"] = _count(intents_by_variant.get(variant.name, []), lambda i: f"{i['side']}_{i['selection']}")
        s["churn"] = _churn(fills)
        s["max_worst_case_per_match"] = s["max_worst_exposure"]
        variant_out[variant.name] = s

    per_match: list[dict[str, Any]] = []
    match_ids = sorted({i["match_id"] for i in result.intents} | {str(r["match_id"]) for r in rows})
    for mid in match_ids:
        info = feed.match_info(mid)
        cells: dict[str, Any] = {}
        for variant in variants:
            mine = [r for r in rows if r["variant"] == variant.name and str(r["match_id"]) == mid]
            fills = [r for r in mine if r["status"] in TRADE_STATUSES]
            if not mine:
                continue
            cells[variant.name] = {
                "intents": sum(1 for i in intents_by_variant.get(variant.name, []) if i["match_id"] == mid),
                "fills": len(fills),
                "settled": sum(1 for r in fills if r["status"] == "settled"),
                "stake": round(sum(float(r["stake"]) for r in fills), 2),
                "pnl": round(sum(float(r["pnl"]) for r in fills if r.get("pnl") is not None), 2),
                "worst_case": round(review.worst_exposure(fills).get(mid, 0.0), 2),
            }
        per_match.append({
            "match_id": mid,
            "team_home": info.get("team_home"),
            "team_away": info.get("team_away"),
            "competition": info.get("competition"),
            "is_simulated": bool(info.get("is_simulated")),
            "variants": cells,
        })

    comparison = None
    if uncapped is not None:
        comparison = _size_comparison(rows, _bet_dicts(uncapped.session.rows), variants)

    excess = review.excess_over_placebo(rows, [v.name for v in variants])
    placebo = {
        "control": review.PLACEBO_RANDOM,
        "reference": reference,
        "excess": excess,
        "sentence": review.excess_sentence(excess),
    }

    return {
        "window": {
            "from": result.start.isoformat(),
            "to": result.end.isoformat(),
            "step_seconds": int(STEP.total_seconds()),
            "cycles_run": result.cycles,
            "grid_steps_skipped": result.skipped_steps,
            "matches_considered": len(result.matches_considered),
        },
        "settings": dict(settings),
        "skipped_registry": dict(sorted(result.skipped_registry.items())),
        "skipped_matches": dict(sorted(result.skipped_matches.items())),
        "guard_refusals": dict(sorted(result.guard_refusals.items())),
        "ledger_rows": len(rows),
        "variants": variant_out,
        "size_cap_comparison": comparison,
        "placebo": placebo,
        "per_match": per_match,
    }


def _count(items: Sequence[Mapping[str, Any]], key: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        k = key(item)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


# ----------------------------------------------------------------------------- outputs
def _n(value: Any, digits: int = 4, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return format(float(value), f"{'+' if signed else ''}.{digits}f")


def _ci(mean: Any, lo: Any, hi: Any, digits: int = 4) -> str:
    return f"{_n(mean, digits, True)} [{_n(lo, digits, True)}, {_n(hi, digits, True)}]"


def _headline(point: Any, lo: Any, hi: Any, matches: int, bets: int, digits: int = 4) -> str:
    """Match-clustered figure with its counts; labelled 'too few matches' under MIN_CLUSTERS_FOR_CI matches."""
    return review.headline(point, lo, hi, matches=matches, bets=bets, digits=digits)


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _label(name: str) -> str:
    """A variant's name in the tables; the placebo controls are marked (S85: never a signal, never primary)."""
    return f"{name} (placebo)" if review.is_placebo(name) else name


def _placebo_lines(placebo: Mapping[str, Any]) -> list[str]:
    """S85: each strategy variant's CLV as an excess over placebo_random (match-clustered), and the plain sentence."""
    out = ["## Placebo controls (S85): excess CLV over placebo_random, match-clustered", ""]
    out.append(f"The placebos copy the decision times and stakes of the reference variant ({placebo['reference']}). They "
               "are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus "
               "placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches "
               "is labelled 'too few matches'.")
    out.append("")
    out.append(str(placebo["sentence"]))
    out.append("")
    out.append("| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on "
               "those matches | above zero on 10+ matches |")
    out.append("|---|---|---:|---|")
    for name, e in placebo["excess"].items():
        if review.is_placebo(name):
            continue
        out.append(
            f"| {name} | {_headline(e['estimate'], e['ci_lo'], e['ci_hi'], e['matches'], e['bets'])} | "
            f"{e['control_bets']} | {'yes' if e['above_zero'] and not e['too_few_matches'] else 'no'} |"
        )
    out.append("")
    return out


def render_markdown(summary: Mapping[str, Any]) -> str:
    w = summary["window"]
    out = [
        "# PHOENIX paper replay",
        "",
        f"Window {w['from']} to {w['to']}; {w['matches_considered']} matches considered; {w['cycles_run']} cycles run, "
        f"{w['grid_steps_skipped']} grid steps skipped (no fresh price, no open bet). Settings: {summary['settings']}.",
        "",
        "Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the "
        "reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample "
        "matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than "
        "10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.",
        "",
        "## Per variant (match-clustered)",
        "",
        "| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] "
        "(matches / bets) | max worst case / match | trades per match | tiny trades |",
        "|---|---:|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for name, v in summary["variants"].items():
        churn = v["churn"]
        out.append(
            f"| {_label(name)} | {v['intents']} | {v['filled']} | {v['settled_bets']} | {v['void_bets']} | "
            f"{_headline(v['clv_mean'], v['clv_ci_lo'], v['clv_ci_hi'], v['clv_matches'], v['clv_bets'])} | "
            f"{_headline(v['pnl'], v['pnl_ci_lo'], v['pnl_ci_hi'], v['pnl_matches'], v['pnl_bets'], digits=0)} | "
            f"{v['max_worst_case_per_match']:,.0f} | "
            f"{_n(churn['trades_per_match'], 2)} | {churn['tiny_trades']} ({_pct(churn['tiny_share'])}) |"
        )
    if summary.get("placebo"):
        out += [""] + _placebo_lines(summary["placebo"])
    out += ["", "## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price", ""]
    out.append("| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | "
               "stake lapsed | size-unknown share | mean adverse slippage (odds / %) |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for name, v in summary["variants"].items():
        z = v["sizing"]
        out.append(
            f"| {_label(name)} | {z['decided_bets']} | {v['filled']} | {v['unfilled']} | {_pct(v['fill_rate'])} | "
            f"{_pct(z['stake_fill_rate'])} | {_pct(z['size_capped_share'])} ({z['size_capped_fills']}) | "
            f"{z['size_capped_stake_lapsed']:,.0f} | {_pct(z['size_unknown_share'])} ({z['size_unknown_fills']}) | "
            f"{_n(z['mean_adverse_slippage'], 4, True)} / {_pct(z['mean_adverse_slippage_pct'])} |"
        )
    if summary.get("size_cap_comparison"):
        out += ["", "## Size cap: capped (reported) vs uncapped, P&L in currency (S87)", ""]
        out.append("The difference is capped minus uncapped. Its 95% CI resamples matches. Uncapped is the same replay "
                   "with no size: the pre-S87 fill, in which a fill takes the whole asked stake.")
        out.append("")
        out.append("| variant | P&L capped | P&L uncapped | difference [95% CI] (matches) | filled stake capped | "
                   "filled stake uncapped | fills capped / uncapped | max worst case capped / uncapped |")
        out.append("|---|---:|---:|---|---:|---:|---:|---:|")
        for name, c in summary["size_cap_comparison"].items():
            diff_ci = _ci(c["pnl_difference"], c["pnl_difference_ci_lo"], c["pnl_difference_ci_hi"], 0)
            out.append(
                f"| {name} | {c['pnl_capped']:,.0f} | {c['pnl_uncapped']:,.0f} | {diff_ci} "
                f"({c['pnl_difference_matches']}) | {c['filled_stake_capped']:,.0f} | "
                f"{c['filled_stake_uncapped']:,.0f} | {c['fills_capped']} / {c['fills_uncapped']} | "
                f"{c['max_worst_case_capped']:,.0f} / {c['max_worst_case_uncapped']:,.0f} |"
            )
    out += ["", "## Pre-match and in-play, separately (match-clustered)", ""]
    out.append("| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | "
               "P&L total [95% CI] (matches / bets) |")
    out.append("|---|---|---:|---|---|---|")
    for name, v in summary["variants"].items():
        for label, block in (("pre-match", v["segments"]["prematch"]), ("in-play", v["segments"]["inplay"])):
            out.append(
                f"| {_label(name)} | {label} | {block['bets']} | "
                f"{_headline(block['clv_mean'], block['clv_ci_lo'], block['clv_ci_hi'], block['clv_matches'], block['clv_bets'])} | "
                f"{_headline(block['clv_w_mean'], block['clv_w_ci_lo'], block['clv_w_ci_hi'], block['clv_matches'], block['clv_bets'])} | "
                f"{_headline(block['pnl'], block['pnl_ci_lo'], block['pnl_ci_hi'], block['pnl_matches'], block['pnl_bets'], digits=0)} |"
            )
    out += ["", "## Detail: per-bet intervals (reference only; they ignore the match correlation)", ""]
    out.append("| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |")
    out.append("|---|---|---|")
    for name, v in summary["variants"].items():
        detail = v["detail"]
        out.append(
            f"| {_label(name)} | {_ci(v['clv_mean'], detail['clv_bet_ci_lo'], detail['clv_bet_ci_hi'])} | "
            f"{_ci(None, detail['pnl_per_bet_ci_lo'], detail['pnl_per_bet_ci_hi'], 2)} |"
        )
    out += ["", "## Intents by side", ""]
    for name, v in summary["variants"].items():
        out.append(f"- {_label(name)}: {', '.join(f'{k} {c}' for k, c in v['intents_by_side'].items()) or 'none'}")
    out += ["", "## Skips (counted per cycle, summed over the run)", ""]
    out.append(f"- Registry: {summary['skipped_registry'] or 'none'}")
    out.append(f"- Matches: {summary['skipped_matches'] or 'none'}")
    out += ["", "## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)", ""]
    out.append(f"- {summary.get('guard_refusals') or 'none'}")
    out += ["", "## Per match", ""]
    names = list(summary["variants"])
    out.append("| match | teams | competition | " + " | ".join(f"{_label(n)} fills / worst" for n in names) + " |")
    out.append("|---|---|---|" + "|".join("---:" for _ in names) + "|")
    for m in summary["per_match"]:
        cells = []
        for n in names:
            c = m["variants"].get(n)
            cells.append("-" if c is None else f"{c['fills']} / {c['worst_case']:,.0f}")
        teams = f"{m['team_home']} v {m['team_away']}" + (" (SRL)" if m["is_simulated"] else "")
        out.append(f"| {m['match_id']} | {teams} | {m['competition'] or ''} | " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out) + "\n"


def write_outputs(summary: Mapping[str, Any], session: MemorySession, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(review._clean(dict(summary)), indent=2, sort_keys=True), encoding="utf-8")
    paths.append(json_path)
    md_path = out_dir / "summary.md"
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    paths.append(md_path)
    csv_path = out_dir / "bets.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BET_COLUMNS)
        writer.writeheader()
        for row in _bet_dicts(session.rows):
            writer.writerow({
                k: (json.dumps(review._clean(v), sort_keys=True, default=str) if isinstance(v, (dict, list)) else
                    ("" if v is None else v.isoformat() if isinstance(v, datetime) else v))
                for k, v in row.items()
            })
    paths.append(csv_path)
    return paths


# ----------------------------------------------------------------------------- driver
async def _load(start: Optional[datetime], end: Optional[datetime]) -> tuple[ReplayFeed, datetime, datetime]:
    from shared.db import close_database, get_session

    try:
        async with get_session() as session:
            await session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
            await session.execute(text("SET TRANSACTION READ ONLY"))
            last = (await session.execute(text("SELECT max(time) FROM v_match_odds"))).scalar()
            first = (await session.execute(text("SELECT min(time) FROM v_match_odds"))).scalar()
            if last is None:
                raise SystemExit("no recorded prices in v_match_odds")
            stop = end or last
            feed = await ReplayFeed.load(session, end=stop)
    finally:
        await close_database()
    begin = start or first
    return feed, begin, stop


def _parse_day(text_value: str) -> datetime:
    dt = datetime.fromisoformat(text_value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _select_variants(names: Optional[str]) -> tuple[pt.Variant, ...]:
    variants = pt.build_variants()
    if not names:
        return variants
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    known = {v.name for v in variants}
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise SystemExit(f"unknown variant(s) {unknown}; known: {sorted(known)}")
    return tuple(v for v in variants if v.name in wanted)


async def _run(args: argparse.Namespace, probability: Any, variants: Sequence[pt.Variant]) -> int:
    started = time.perf_counter()
    start_arg = _parse_day(args.from_) if args.from_ else None
    end_arg = _parse_day(args.to) if args.to else None
    if args.compare_size_cap and args.size_cap:
        raise SystemExit("--compare-size-cap runs both the capped and the uncapped replay; drop --size-cap")
    feed, start, end = await _load(start_arg, end_arg)
    loaded = time.perf_counter()
    if end < start:
        raise SystemExit("--to is before --from")
    guards_cfg = pt.GuardConfig()
    fill_delay = timedelta(seconds=args.fill_delay)
    # Off by default, like the live trader: the unit of the shown size is not verified (2026-11-03)
    size_cap = (bool(args.size_cap) or bool(args.compare_size_cap)) and not args.no_size_cap
    # S85: the placebos copy the primary in models/paper/primary.json when there is one, else w05_e03
    reference = args.reference or review.reference_name(review.load_state())
    result = await replay_window(
        feed, probability, variants, start=start, end=end, include_simulated=args.include_srl, guards=guards_cfg,
        fill_delay=fill_delay, size_cap=size_cap, reference=reference,
    )
    uncapped = None
    if args.compare_size_cap:
        uncapped = await replay_window(
            feed, probability, variants, start=start, end=end, include_simulated=args.include_srl, guards=guards_cfg,
            fill_delay=fill_delay, size_cap=False, reference=reference,
        )
    replayed = time.perf_counter()
    settings = {
        "include_srl": bool(args.include_srl),
        "variants": [v.name for v in variants],
        "reference": reference,
        "cycle_seconds": int(STEP.total_seconds()),
        "fill_delay_seconds": int(fill_delay.total_seconds()),
        "size_cap": size_cap,
        "unknown_size_cap": ledger.UNKNOWN_SIZE_CAP,
        "lay_size_unit": ledger.LAY_SIZE_UNIT,
        "max_match_loss": pt.MAX_MATCH_LOSS,
        "guards": asdict(guards_cfg),
        "suspensions": "none (every variant trades; the live S73 state is not applied)",
    }
    summary = build_summary(result, variants, feed, settings=settings, uncapped=uncapped, reference=reference)
    out_dir = Path(args.out) if args.out else REPLAY_DIR / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    write_outputs(summary, result.session, out_dir)
    if uncapped is not None:
        uncapped_summary = build_summary(
            uncapped, variants, feed, settings={**settings, "size_cap": False}, reference=reference
        )
        write_outputs(uncapped_summary, uncapped.session, out_dir / "uncapped")
    print(f"replay {start.isoformat()} .. {end.isoformat()}: {result.cycles} cycles, "
          f"{result.skipped_steps} grid steps skipped, {len(result.intents)} intents, "
          f"{len(result.session.rows)} ledger rows")
    print(f"load {loaded - started:.1f}s, replay {replayed - loaded:.1f}s, total {time.perf_counter() - started:.1f}s")
    print(f"wrote {out_dir}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S74 replay of the paper trader over recorded history (read-only).")
    parser.add_argument("--from", dest="from_", help="first decision time, e.g. 2026-11-01 (UTC); default: first tick")
    parser.add_argument("--to", help="last decision time (UTC); default: last recorded tick")
    parser.add_argument("--variants", help="comma-separated variant names; default: all")
    parser.add_argument("--reference", help="variant the placebo controls copy (S85); default: the primary in "
                                            "models/paper/primary.json, else w05_e03")
    parser.add_argument("--out", help="output directory; default: reports/replay/<timestamp>")
    parser.add_argument("--include-srl", action="store_true", help="also replay simulated (SRL) matches")
    parser.add_argument("--fill-delay", type=int, choices=(30, 60, 120), default=60,
                        help="seconds from decision to fill (S87); default 60, the live delay")
    parser.add_argument("--size-cap", action="store_true",
                        help="cut fills to the size shown on the match page (off by default; size unit unverified)")
    parser.add_argument("--no-size-cap", action="store_true",
                        help="do not cut fills to the size shown (the pre-S87 fill)")
    parser.add_argument("--compare-size-cap", action="store_true",
                        help="run the capped and the uncapped replay; the uncapped summary goes to <out>/uncapped")
    args = parser.parse_args(argv)
    variants = _select_variants(args.variants)
    probability = pt.load_probability_service(pt.ROOT)  # blocking; before the event loop starts
    return asyncio.run(_run(args, probability, variants))


if __name__ == "__main__":
    raise SystemExit(main())
