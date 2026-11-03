"""S73 improvement loop: score each paper variant from its own bets, rank them, suspend a variant that breaks the
daily loss rule, and write the state the trader reads (``models/paper/primary.json``).

Everything here is pure except ``load_rows`` (one read-only SELECT on ``paper_bets``) and ``write_state``. Nothing
places a bet. The review never changes a bet row.

Definitions (per variant, over every paper bet recorded so far, because R3 counts settled bets over the whole history)
- Fill rate: filled / (filled + unfilled), over decided bets (pending bets are not decided yet).
- CLV: mean of the per-bet ``clv`` over filled bets that have one (probability points, shared.metrics.clv_prob), and
  the stake-weighted mean. Headline CIs are match-clustered (shared.metrics.cluster_bootstrap, S84): bets on one match
  move together, so the match is the resampling unit. Each figure carries its match and bet counts; a CI from fewer
  than ``MIN_CLUSTERS_FOR_CI`` matches is labelled "too few matches".
- P&L: total of ``pnl`` over settled and void bets, with a match-clustered 95% CI on that total.
- Segments: CLV and P&L are also given for pre-match and in-play bets (``paper_bets.in_play``) separately.
- Per-bet intervals, which ignore the match correlation, appear only in the ``detail`` block, never in a headline.
- Calibration: Brier score of ``p_used`` (probability of the bet's selection) against whether that selection won, over
  settled bets only (voids have no win or loss).
- Exposure: per match, the worst home/away outcome P&L over the variant's fills (the same convention as
  ``strategy.model_edge.Book``), and the maximum over matches. The cap is 100,000 per match.
- R3 progress: settled bets (status ``settled``) and distinct settled matches, against 300 and 30, and whether the CLV
  CI lower bound is above zero.

Placebo controls (S85)
- ``placebo_random`` and ``placebo_favourite`` trade at the decision times of the reference variant (the primary, else
  w05_e03), with its stake. They are never ranked, never primary and never signals. Each strategy variant's CLV is also
  given as an excess over ``placebo_random``: per match, mean CLV(variant) minus mean CLV(placebo_random) on that match,
  then a match-clustered bootstrap over those matches. ``placebo`` in the state says whether no variant's excess CI lies
  above zero, in one plain sentence.

Ranking
- Each variant with at least ``MIN_BETS_FOR_RANK`` CLV bets has a normal approximation of its CLV posterior, mean and
  se = sd / sqrt(n). A seeded Thompson draw over the eligible, non-suspended, non-placebo variants gives each its P(best).
- ``primary`` is the non-suspended eligible variant with the highest CLV CI lower bound (mean breaks ties). It is a
  label only: every variant keeps trading so the evidence keeps growing.

Suspension (the S42 kill-switch daily loss rule, mirrored)
- A variant is suspended when its settled P&L on the last full UTC day is at or below ``-KILL_DAILY_LOSS`` times its
  own bankroll. The rule is evaluated only at a review, and the suspension lasts until the next review. A suspended
  variant opens no new risk (the trader gives it ``max_trades_per_match = 0``); reduce and green-up still run.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from shared.constants import KILL_DAILY_LOSS
from shared.metrics import MIN_CLUSTERS_FOR_CI, cluster_bootstrap
from strategy.base import bet_gross_pnl

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "models" / "paper" / "primary.json"

REVIEW_AT = time(0, 5)  # UTC; the daily review runs from this time, for the last full UTC day
PLACEBO_RANDOM = "placebo_random"  # S85: a random side and selection at the reference's decision times and stakes
PLACEBO_FAVOURITE = "placebo_favourite"  # S85: backs the market favourite at the reference's decision times
PLACEBO_VARIANTS = (PLACEBO_RANDOM, PLACEBO_FAVOURITE)
DEFAULT_REFERENCE = "w05_e03"  # S85: the placebos' reference when there is no primary yet
MIN_BETS_FOR_RANK = 30  # CLV bets a variant needs before it can be ranked
R3_SETTLED_BETS = 300  # plan: >= 300 settled paper bets ...
R3_MATCHES = 30  # ... over >= 30 matches, with the CLV CI lower bound above zero
N_RESAMPLES = 2_000  # bootstrap resamples per statistic
THOMPSON_DRAWS = 10_000
SEED = 20261103  # fixed, so a review of the same ledger always gives the same numbers
CI_MIN_N = 5  # fewer values than this: no interval (NaN), as in shared.metrics.portfolio_clv

FILLED = ("filled", "settled", "void")  # bets that were matched at some point
SETTLED = ("settled", "void")  # bets with a final P&L
_RESAMPLE_CHUNK = 500


def _utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def review_day_for(now: datetime) -> date:
    """The last full UTC day: the day whose P&L a review at ``now`` judges."""
    return _utc(now).date() - timedelta(days=1)


def review_due(now: datetime, last_reviewed: Optional[date]) -> Optional[date]:
    """The UTC day a review must cover now, or None. Due from 00:05 UTC, once per day, and it catches up after a
    restart (the day after ``last_reviewed`` is due as soon as the clock is past 00:05)."""
    local = _utc(now)
    if local.time() < REVIEW_AT:
        return None
    day = review_day_for(local)
    if last_reviewed is not None and last_reviewed >= day:
        return None
    return day


def _day_of(value: Any) -> Optional[date]:
    dt = _utc(value)
    return dt.date() if dt is not None else None


# ----------------------------------------------------------------------------- statistics
def mean_ci(values: Sequence[float], *, n_resamples: int = N_RESAMPLES, seed: int = SEED) -> tuple[float, float, float]:
    """(mean, lower, upper) with a percentile bootstrap 95% CI. Fewer than CI_MIN_N values gives NaN bounds."""
    if not values:
        return math.nan, math.nan, math.nan
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    n = arr.size
    if n < CI_MIN_N:
        return mean, math.nan, math.nan
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    for start in range(0, n_resamples, _RESAMPLE_CHUNK):
        k = min(_RESAMPLE_CHUNK, n_resamples - start)
        idx = rng.integers(0, n, size=(k, n))
        means[start:start + k] = arr[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return mean, float(lo), float(hi)


def worst_exposure(fills: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Per match, the worst home/away outcome loss (>= 0) over the given fills."""
    pnl: dict[str, dict[str, float]] = defaultdict(lambda: {"home": 0.0, "away": 0.0})
    for row in fills:
        for outcome in ("home", "away"):
            pnl[str(row["match_id"])][outcome] += bet_gross_pnl(
                row["side"], row["selection"], float(row["price_filled"]), float(row["stake"]), outcome
            )
    return {mid: max(0.0, -min(v.values())) for mid, v in pnl.items()}


def brier(rows: Iterable[Mapping[str, Any]]) -> tuple[float, int]:
    """Brier score of ``p_used`` against the selection winning, over settled bets with a home or away result."""
    errors = []
    for row in rows:
        if row.get("status") != "settled" or row.get("result") not in ("home", "away"):
            continue
        if row.get("p_used") is None:
            continue
        won = 1.0 if row["result"] == row["selection"] else 0.0
        errors.append((float(row["p_used"]) - won) ** 2)
    if not errors:
        return math.nan, 0
    return float(np.mean(errors)), len(errors)


def _asked(row: Mapping[str, Any]) -> float:
    """The stake asked at decision (S87); rows written before S87 have no ``stake_asked``, so their stake is it."""
    asked = row.get("stake_asked")
    return float(asked) if asked is not None else float(row["stake"])


def fill_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sizing figures of one variant's bets (S87). ``rows`` are that variant's paper bets.

    Decided bets are the filled (and settled or void) and the unfilled ones. Fill rate on stake is the filled stake over
    the stake asked on the decided bets. A fill is size-capped when its stake is below the stake asked (the shown size
    cut it, or the 10,000 cap of an unknown size did). Slippage is the adverse move from the decision price to the
    fill price, in decimal odds (positive = worse for the bet): BACK is decision - fill, LAY is fill - decision.
    """
    fills = [r for r in rows if r.get("status") in FILLED]
    unfilled = [r for r in rows if r.get("status") == "unfilled"]
    decided = fills + unfilled
    asked_total = sum(_asked(r) for r in decided)
    filled_stake = sum(float(r["stake"]) for r in fills)
    capped = [r for r in fills if _asked(r) - float(r["stake"]) > 1e-9]
    unknown = [r for r in fills if r.get("fill_note") == "size_unknown"]
    adverse: list[float] = []
    adverse_pct: list[float] = []
    for r in fills:
        if r.get("price_filled") is None:
            continue
        decision = (r.get("book") or {}).get(f"{'back' if r['side'] == 'BACK' else 'lay'}_{r['selection']}")
        if decision is None or float(decision) <= 1.0:
            continue
        move = float(decision) - float(r["price_filled"]) if r["side"] == "BACK" else float(r["price_filled"]) - float(decision)
        adverse.append(move)
        adverse_pct.append(move / float(decision))
    return {
        "decided_bets": len(decided),
        "asked_stake": asked_total,
        "filled_stake": filled_stake,
        "stake_fill_rate": (filled_stake / asked_total) if asked_total > 0 else math.nan,
        "size_capped_fills": len(capped),
        "size_capped_share": (len(capped) / len(fills)) if fills else math.nan,
        "size_capped_stake_lapsed": sum(_asked(r) - float(r["stake"]) for r in capped),
        "size_unknown_fills": len(unknown),
        "size_unknown_share": (len(unknown) / len(fills)) if fills else math.nan,
        "size_zero_unfilled": sum(1 for r in unfilled if r.get("fill_note") == "size_zero"),
        "slippage_n": len(adverse),
        "mean_adverse_slippage": (sum(adverse) / len(adverse)) if adverse else math.nan,
        "mean_adverse_slippage_pct": (sum(adverse_pct) / len(adverse_pct)) if adverse_pct else math.nan,
    }


# ----------------------------------------------------------------------------- per variant
def _finite(value: Any) -> bool:
    return value is not None and math.isfinite(float(value))


def is_placebo(name: str) -> bool:
    """True for the S85 placebo controls: never ranked, never primary, never shown as signals."""
    return name in PLACEBO_VARIANTS


def reference_name(state: Mapping[str, Any]) -> str:
    """The variant the placebos copy: the primary in the state file when there is one (and it is not a placebo), else
    ``DEFAULT_REFERENCE``."""
    primary = state.get("primary") if isinstance(state, Mapping) else None
    return str(primary) if primary and not is_placebo(str(primary)) else DEFAULT_REFERENCE


def _clv_means_by_match(rows: Sequence[Mapping[str, Any]], name: str) -> dict[str, tuple[float, int]]:
    """Per match: (mean CLV, number of CLV bets) of one variant's filled bets with a finite CLV."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("variant") != name or row.get("status") not in FILLED or not _finite(row.get("clv")):
            continue
        grouped[str(row["match_id"])].append(float(row["clv"]))
    return {mid: (sum(vals) / len(vals), len(vals)) for mid, vals in grouped.items()}


def excess_over_placebo(
    rows: Sequence[Mapping[str, Any]],
    names: Iterable[str],
    *,
    control: str = PLACEBO_RANDOM,
    n_resamples: int = N_RESAMPLES,
    seed: int = SEED,
) -> dict[str, dict[str, Any]]:
    """Each variant's CLV as an excess over the control (S85), match-clustered.

    Per match both sides have CLV: the item is mean CLV(variant) minus mean CLV(control) on that match (each side's mean
    is unweighted over its bets). The bootstrap resamples those matches (shared.metrics.cluster_bootstrap). A match
    where the control has no CLV bet is left out, since there is no difference to take. ``matches`` and ``bets`` count
    the matches used and the variant's CLV bets on them. ``above_zero`` is True when the CI lower bound is above zero.
    """
    control_means = _clv_means_by_match(rows, control)
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        if name == control:
            continue
        mine = _clv_means_by_match(rows, name)
        matches = sorted(set(mine) & set(control_means))
        diffs = [mine[m][0] - control_means[m][0] for m in matches]
        boot = cluster_bootstrap(diffs, matches, n_boot=n_resamples, seed=seed)
        lo, hi = boot["ci_lower"], boot["ci_upper"]
        out[name] = {
            "matches": len(matches),
            "bets": sum(mine[m][1] for m in matches),
            "control_bets": sum(control_means[m][1] for m in matches),
            "estimate": boot["estimate"],
            "ci_lo": lo,
            "ci_hi": hi,
            "too_few_matches": len(matches) < MIN_CLUSTERS_FOR_CI,
            "above_zero": bool(not math.isnan(lo) and lo > 0.0),
        }
    return out


def excess_sentence(excess: Mapping[str, Mapping[str, Any]]) -> str:
    """One plain sentence for the daily review: which strategy variants beat the placebo, or that none do."""
    strategies = [name for name in sorted(excess) if not is_placebo(name)]
    # an interval from fewer than MIN_CLUSTERS_FOR_CI matches is not evidence, so it does not count as above zero
    above = [name for name in strategies if excess[name]["above_zero"] and not excess[name]["too_few_matches"]]
    if above:
        return (
            f"Excess CLV over {PLACEBO_RANDOM} is above zero (95% lower bound, at least {MIN_CLUSTERS_FOR_CI} "
            f"matches) for {', '.join(above)}."
        )
    return (
        f"No variant's CLV beats {PLACEBO_RANDOM}: no excess-CLV confidence interval lies above zero on at least "
        f"{MIN_CLUSTERS_FOR_CI} matches."
    )


def clustered_block(fills: Sequence[Mapping[str, Any]], settled: Sequence[Mapping[str, Any]], *,
                    n_resamples: int = N_RESAMPLES) -> dict[str, Any]:
    """Match-clustered CLV and P&L for one set of bets (all bets, or one segment).

    ``fills`` are the filled bets (CLV is taken over those with a finite ``clv``); ``settled`` are the settled and void
    bets (P&L is the total of their ``pnl``). Each statistic resamples matches. The ``*_matches`` and ``*_bets`` counts
    are the matches and bets behind that statistic. ``clv_w_*`` is the stake-weighted mean CLV.
    """
    clv_rows = [r for r in fills if _finite(r.get("clv"))]
    clv_ids = [str(r["match_id"]) for r in clv_rows]
    clv_vals = [float(r["clv"]) for r in clv_rows]
    clv = cluster_bootstrap(clv_vals, clv_ids, n_boot=n_resamples, seed=SEED)
    clv_w = cluster_bootstrap(clv_vals, clv_ids, weights=[float(r["stake"]) for r in clv_rows],
                              n_boot=n_resamples, seed=SEED)
    pnl_rows = [r for r in settled if _finite(r.get("pnl"))]
    pnl = cluster_bootstrap([float(r["pnl"]) for r in pnl_rows], [str(r["match_id"]) for r in pnl_rows],
                            statistic="sum_per_cluster", n_boot=n_resamples, seed=SEED)
    return {
        "clv_matches": clv["n_clusters"],
        "clv_bets": clv["n_items"],
        "clv_mean": clv["estimate"],
        "clv_ci_lo": clv["ci_lower"],
        "clv_ci_hi": clv["ci_upper"],
        "clv_se": clv["se"],
        "clv_too_few_matches": clv["too_few_clusters"],
        "clv_w_mean": clv_w["estimate"],
        "clv_w_ci_lo": clv_w["ci_lower"],
        "clv_w_ci_hi": clv_w["ci_upper"],
        "clv_w_too_few_matches": clv_w["too_few_clusters"],
        "pnl": pnl["estimate"] if pnl["n_items"] else 0.0,
        "pnl_matches": pnl["n_clusters"],
        "pnl_bets": pnl["n_items"],
        "pnl_ci_lo": pnl["ci_lower"],
        "pnl_ci_hi": pnl["ci_upper"],
        "pnl_too_few_matches": pnl["too_few_clusters"],
    }


def summarise_variant(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    bankroll: float,
    review_day: date,
    n_resamples: int = N_RESAMPLES,
) -> dict[str, Any]:
    """Every number the report and the ranking use for one variant. ``rows`` are that variant's paper bets.

    Headline CLV and P&L fields are match-clustered. ``segments`` repeats them for pre-match and in-play bets.
    ``detail`` holds the per-bet intervals, for reference only.
    """
    fills = [r for r in rows if r.get("status") in FILLED]
    settled = [r for r in fills if r.get("status") in SETTLED]
    settled_only = [r for r in fills if r.get("status") == "settled"]
    unfilled = sum(1 for r in rows if r.get("status") == "unfilled")
    decided = len(fills) + unfilled

    whole = clustered_block(fills, settled, n_resamples=n_resamples)
    clv_values = [float(r["clv"]) for r in fills if _finite(r.get("clv"))]
    _, clv_bet_lo, clv_bet_hi = mean_ci(clv_values, n_resamples=n_resamples)
    clv_sd = float(np.std(clv_values, ddof=1)) if len(clv_values) > 1 else math.nan
    pnl_values = [float(r["pnl"]) for r in settled if _finite(r.get("pnl"))]
    _, pnl_bet_lo, pnl_bet_hi = mean_ci(pnl_values, n_resamples=n_resamples)

    segments: dict[str, Any] = {}
    for label, in_play in (("prematch", False), ("inplay", True)):
        seg_fills = [r for r in fills if bool(r.get("in_play")) is in_play]
        seg_settled = [r for r in settled if bool(r.get("in_play")) is in_play]
        segments[label] = {
            "matches": len({str(r["match_id"]) for r in seg_fills}),
            "bets": len(seg_fills),
            **clustered_block(seg_fills, seg_settled, n_resamples=n_resamples),
        }

    exposure = worst_exposure(fills)
    settled_matches = {str(r["match_id"]) for r in settled_only}
    day_pnl = sum(
        float(r["pnl"]) for r in settled
        if r.get("pnl") is not None and _day_of(r.get("settled_at")) == review_day
    )
    brier_score, brier_n = brier(rows)
    clv_ci_ok = bool(not math.isnan(whole["clv_ci_lo"]) and whole["clv_ci_lo"] > 0.0)
    return {
        "variant": name,
        "bankroll": float(bankroll),
        "bets": len(rows),
        "filled": len(fills),
        "unfilled": unfilled,
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "fill_rate": (len(fills) / decided) if decided else math.nan,
        "matches": len({str(r["match_id"]) for r in fills}),
        "settled_bets": len(settled_only),
        "void_bets": sum(1 for r in settled if r.get("status") == "void"),
        "settled_matches": len(settled_matches),
        "clv_n": len(clv_values),
        **whole,
        "segments": segments,
        "detail": {
            "clv_bet_sd": clv_sd,
            "clv_bet_ci_lo": clv_bet_lo,
            "clv_bet_ci_hi": clv_bet_hi,
            "pnl_per_bet_ci_lo": pnl_bet_lo,
            "pnl_per_bet_ci_hi": pnl_bet_hi,
            "note": "per-bet intervals treat bets as independent, so they are narrower than the match-clustered ones",
        },
        "brier": brier_score,
        "brier_n": brier_n,
        "sizing": fill_metrics(rows),
        "max_worst_exposure": max(exposure.values(), default=0.0),
        "day_pnl": float(day_pnl),
        "r3_bets_left": max(0, R3_SETTLED_BETS - len(settled_only)),
        "r3_matches_left": max(0, R3_MATCHES - len(settled_matches)),
        "r3_met": bool(len(settled_only) >= R3_SETTLED_BETS and len(settled_matches) >= R3_MATCHES and clv_ci_ok),
        "placebo": is_placebo(name),  # S85: a control, never ranked or shown as a signal
    }


def headline(point: Any, lo: Any, hi: Any, *, matches: int, bets: int, digits: int = 4, signed: bool = True) -> str:
    """One headline figure: the point, its 95% CI, then 'n matches / n bets'. Under MIN_CLUSTERS_FOR_CI matches the
    CI is labelled 'too few matches'."""
    def f(x: Any) -> str:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "n/a"
        return format(float(x), f"{'+' if signed else ''}.{digits}f")

    label = f"{matches} {'match' if matches == 1 else 'matches'} / {bets} {'bet' if bets == 1 else 'bets'}"
    if matches < MIN_CLUSTERS_FOR_CI:
        label += "; too few matches"
    return f"{f(point)} [{f(lo)}, {f(hi)}] ({label})"


def suspensions(summaries: Iterable[Mapping[str, Any]], *, review_day: date) -> dict[str, str]:
    """Variants whose settled P&L on ``review_day`` breaks the daily loss rule. Maps name to the reason."""
    out: dict[str, str] = {}
    for s in summaries:
        limit = KILL_DAILY_LOSS * float(s["bankroll"])
        if s["bankroll"] > 0 and s["day_pnl"] <= -limit:
            out[s["variant"]] = (
                f"daily paper loss {s['day_pnl']:.2f} on {review_day.isoformat()} <= "
                f"-{KILL_DAILY_LOSS:.0%} of bankroll {s['bankroll']:.0f}"
            )
    return out


def rank(
    summaries: Sequence[Mapping[str, Any]],
    *,
    suspended: Iterable[str] = (),
    draws: int = THOMPSON_DRAWS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Thompson P(best) and the primary variant. See the module docstring for the rule."""
    blocked = set(suspended)
    eligible = [
        s for s in summaries
        if s["clv_n"] >= MIN_BETS_FOR_RANK and math.isfinite(s["clv_mean"]) and math.isfinite(s["clv_se"])
        and s["variant"] not in blocked and not is_placebo(s["variant"])  # S85: controls are never ranked
    ]
    p_best: dict[str, float] = {}
    if eligible:
        means = np.array([s["clv_mean"] for s in eligible], dtype=float)
        ses = np.array([s["clv_se"] for s in eligible], dtype=float)
        rng = np.random.default_rng(seed)
        samples = rng.normal(means, ses, size=(draws, len(eligible)))
        counts = np.bincount(samples.argmax(axis=1), minlength=len(eligible))
        p_best = {s["variant"]: float(c) / draws for s, c in zip(eligible, counts)}
    primary = None
    if eligible:
        best = max(eligible, key=lambda s: (s["clv_ci_lo"], s["clv_mean"]))
        primary = best["variant"]
    return {
        "min_bets": MIN_BETS_FOR_RANK,
        "draws": draws,
        "eligible": [s["variant"] for s in eligible],
        "p_best": p_best,
        "primary": primary,
    }


def review(
    rows: Sequence[Mapping[str, Any]],
    bankrolls: Mapping[str, float],
    *,
    now: datetime,
    n_resamples: int = N_RESAMPLES,
    draws: int = THOMPSON_DRAWS,
    reference: str = DEFAULT_REFERENCE,
) -> dict[str, Any]:
    """The full review state from every paper bet. ``bankrolls`` maps variant name to its bankroll, in variant order.
    ``reference`` names the variant the placebos copy (S85), for the report."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    day = review_day_for(now)
    by_variant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_variant[str(row["variant"])].append(row)
    summaries = [
        summarise_variant(name, by_variant.get(name, []), bankroll=bankroll, review_day=day, n_resamples=n_resamples)
        for name, bankroll in bankrolls.items()
    ]
    suspended = suspensions(summaries, review_day=day)
    ranking = rank(summaries, suspended=suspended, draws=draws)
    excess = excess_over_placebo(rows, list(bankrolls), n_resamples=n_resamples)
    placebo = {
        "control": PLACEBO_RANDOM,
        "reference": reference,
        "excess": excess,
        "sentence": excess_sentence(excess),
    }
    primary = ranking["primary"]
    if primary is not None:
        reason = (
            f"highest CLV 95% lower bound among {len(ranking['eligible'])} eligible, non-suspended variant(s)"
        )
    elif not ranking["eligible"]:
        reason = f"no variant has {MIN_BETS_FOR_RANK} CLV bets yet"
    else:
        reason = "every eligible variant is suspended"
    decided_at = [_utc(r["decided_at"]) for r in rows if r.get("decided_at") is not None]
    return _clean({
        "generated_at": _utc(now).isoformat(),
        "review_day": day.isoformat(),
        "primary": primary,
        "primary_reason": reason,
        "suspended": suspended,
        "ranking": ranking,
        "placebo": placebo,
        "variants": summaries,
        "bets_seen": len(rows),
        "first_decided_at": min(decided_at).isoformat() if decided_at else None,
        "last_decided_at": max(decided_at).isoformat() if decided_at else None,
    })


def _clean(value: Any) -> Any:
    """JSON-safe copy: NaN and infinities become None, datetimes become ISO strings, numpy scalars become Python."""
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ----------------------------------------------------------------------------- state file
def write_state(state: Mapping[str, Any], path: Path = STATE_PATH) -> Path:
    """Write the review state atomically (temp file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_clean(dict(state)), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    """The last review state, or {} when there is none or it cannot be read (then nothing is suspended)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def suspended_names(state: Mapping[str, Any]) -> frozenset[str]:
    return frozenset((state.get("suspended") or {}).keys())


def last_review_day(state: Mapping[str, Any]) -> Optional[date]:
    raw = state.get("review_day")
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


# ----------------------------------------------------------------------------- database (one read)
async def load_rows(session: Any) -> list[dict[str, Any]]:
    """Every paper bet as a plain dict. The only database read in this module (the caller sets READ ONLY)."""
    from sqlalchemy import select

    from backend.models.paper_bets import PaperBet

    result = (await session.execute(select(PaperBet).order_by(PaperBet.decided_at))).scalars().all()
    columns = [c.name for c in PaperBet.__table__.columns]
    return [{name: getattr(row, name) for name in columns} for row in result]
