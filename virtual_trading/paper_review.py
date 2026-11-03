"""S73 improvement loop: score each paper variant from its own bets, rank them, suspend a variant that breaks the
daily loss rule, and write the state the trader reads (``models/paper/primary.json``).

Everything here is pure except ``load_rows`` (one read-only SELECT on ``paper_bets``) and ``write_state``. Nothing
places a bet. The review never changes a bet row.

Definitions (per variant, over every paper bet recorded so far, because R3 counts settled bets over the whole history)
- Fill rate: filled / (filled + unfilled), over decided bets (pending bets are not decided yet).
- CLV: mean of the per-bet ``clv`` over filled bets that have one (probability points, shared.metrics.clv_prob), with a
  seeded percentile bootstrap 95% CI.
- P&L: sum of ``pnl`` over settled and void bets, with a bootstrap 95% CI on the per-bet mean.
- Calibration: Brier score of ``p_used`` (probability of the bet's selection) against whether that selection won, over
  settled bets only (voids have no win or loss).
- Exposure: per match, the worst home/away outcome P&L over the variant's fills (the same convention as
  ``strategy.model_edge.Book``), and the maximum over matches. The cap is 100,000 per match.
- R3 progress: settled bets (status ``settled``) and distinct settled matches, against 300 and 30, and whether the CLV
  CI lower bound is above zero.

Ranking
- Each variant with at least ``MIN_BETS_FOR_RANK`` CLV bets has a normal approximation of its CLV posterior, mean and
  se = sd / sqrt(n). A seeded Thompson draw over the eligible, non-suspended variants gives each its P(best).
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
from strategy.base import bet_gross_pnl

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "models" / "paper" / "primary.json"

REVIEW_AT = time(0, 5)  # UTC; the daily review runs from this time, for the last full UTC day
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


# ----------------------------------------------------------------------------- per variant
def summarise_variant(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    bankroll: float,
    review_day: date,
    n_resamples: int = N_RESAMPLES,
) -> dict[str, Any]:
    """Every number the report and the ranking use for one variant. ``rows`` are that variant's paper bets."""
    fills = [r for r in rows if r.get("status") in FILLED]
    settled = [r for r in fills if r.get("status") in SETTLED]
    settled_only = [r for r in fills if r.get("status") == "settled"]
    unfilled = sum(1 for r in rows if r.get("status") == "unfilled")
    decided = len(fills) + unfilled

    clv_values = [float(r["clv"]) for r in fills if r.get("clv") is not None and math.isfinite(float(r["clv"]))]
    clv_mean, clv_lo, clv_hi = mean_ci(clv_values, n_resamples=n_resamples)
    clv_sd = float(np.std(clv_values, ddof=1)) if len(clv_values) > 1 else math.nan
    clv_se = clv_sd / math.sqrt(len(clv_values)) if len(clv_values) > 1 else math.nan

    pnl_values = [float(r["pnl"]) for r in settled if r.get("pnl") is not None]
    pnl_total = float(sum(pnl_values))
    pnl_mean, pnl_lo, pnl_hi = mean_ci(pnl_values, n_resamples=n_resamples)

    exposure = worst_exposure(fills)
    settled_matches = {str(r["match_id"]) for r in settled_only}
    day_pnl = sum(
        float(r["pnl"]) for r in settled
        if r.get("pnl") is not None and _day_of(r.get("settled_at")) == review_day
    )
    brier_score, brier_n = brier(rows)
    clv_ci_ok = bool(not math.isnan(clv_lo) and clv_lo > 0.0)
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
        "clv_mean": clv_mean,
        "clv_ci_lo": clv_lo,
        "clv_ci_hi": clv_hi,
        "clv_sd": clv_sd,
        "clv_se": clv_se,
        "pnl": pnl_total,
        "pnl_mean": pnl_mean,
        "pnl_ci_lo": pnl_lo,
        "pnl_ci_hi": pnl_hi,
        "brier": brier_score,
        "brier_n": brier_n,
        "max_worst_exposure": max(exposure.values(), default=0.0),
        "day_pnl": float(day_pnl),
        "r3_bets_left": max(0, R3_SETTLED_BETS - len(settled_only)),
        "r3_matches_left": max(0, R3_MATCHES - len(settled_matches)),
        "r3_met": bool(len(settled_only) >= R3_SETTLED_BETS and len(settled_matches) >= R3_MATCHES and clv_ci_ok),
    }


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
        and s["variant"] not in blocked
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
) -> dict[str, Any]:
    """The full review state from every paper bet. ``bankrolls`` maps variant name to its bankroll, in variant order."""
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
