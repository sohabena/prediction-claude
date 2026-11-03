"""Canonical betting metrics: CLV, Sharpe, drawdown, profit factor.

Pure functions, no I/O. Every consumer (settlement, graduation, backtester,
reports) must use these so that a number means one thing everywhere.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional

import numpy as np


def side_of(action: Any) -> str:
    """Return ``"BACK"`` or ``"LAY"`` for an action name / enum / side string."""
    name = getattr(action, "name", None) or str(action)
    return "LAY" if "LAY" in name.upper() else "BACK"


def selection_of(action: Any) -> str:
    """Return ``"HOME"`` or ``"AWAY"`` for an action name / enum; ``""`` when neither."""
    name = (getattr(action, "name", None) or str(action)).upper()
    if "HOME" in name:
        return "HOME"
    if "AWAY" in name:
        return "AWAY"
    return ""


def clv_prob(side: Any, entry_odds: float, closing_odds: float) -> float:
    """Closing line value in probability points (positive = favourable).

    BACK: ``1/close - 1/entry``; LAY: ``1/entry - 1/close``.
    Invalid odds (<= 1.0) give 0.0.
    """
    if entry_odds is None or closing_odds is None:
        return 0.0
    if entry_odds <= 1.0 or closing_odds <= 1.0:
        return 0.0
    diff = 1.0 / closing_odds - 1.0 / entry_odds
    return -diff if side_of(side) == "LAY" else diff


def sharpe(per_period_pnl: Iterable[float], periods_per_year: Optional[float] = None) -> float:
    """Mean / sample-std of per-period P&L. Not annualised unless asked.

    Fewer than 2 periods or zero variance returns 0.0. Use :func:`sharpe_stats`
    when the sample size n must be reported alongside.
    """
    arr = np.asarray(list(per_period_pnl), dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 0.0:
        return 0.0
    value = float(np.mean(arr)) / std
    if periods_per_year:
        value *= math.sqrt(periods_per_year)
    return value


def sharpe_stats(per_period_pnl: Iterable[float], periods_per_year: Optional[float] = None) -> dict[str, float]:
    """Sharpe plus the sample size it was computed from."""
    vals = list(per_period_pnl)
    return {"sharpe": sharpe(vals, periods_per_year), "n": len(vals)}


def _as_date(d: Any) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.fromisoformat(str(d)).date()


def daily_series(bets: Iterable[Any], start: Any, end: Any) -> list[float]:
    """Per-calendar-day P&L from ``start`` to ``end`` inclusive, zero days filled.

    ``bets`` are ``(day_or_datetime, pnl)`` tuples or dicts with ``day`` (or
    ``date``/``placed_at``) and ``pnl`` (or ``profit_loss``).
    """
    totals: dict[date, float] = defaultdict(float)
    for b in bets:
        if isinstance(b, dict):
            d = b.get("day", b.get("date", b.get("placed_at")))
            p = b.get("pnl", b.get("profit_loss", 0.0))
        else:
            d, p = b
        if d is None or p is None:
            continue
        totals[_as_date(d)] += float(p)
    s, e = _as_date(start), _as_date(end)
    out: list[float] = []
    cur = s
    while cur <= e:
        out.append(totals.get(cur, 0.0))
        cur += timedelta(days=1)
    return out


def max_drawdown(equity_curve: Iterable[float]) -> tuple[float, float]:
    """Return ``(absolute, fraction_of_peak)`` max drawdown of an equity curve."""
    arr = np.asarray(list(equity_curve), dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    peaks = np.maximum.accumulate(arr)
    dd = peaks - arr
    abs_dd = float(np.max(dd))
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(peaks > 0, dd / peaks, 0.0)
    return abs_dd, float(np.max(frac))


def profit_factor(pnls: Iterable[float]) -> float:
    """Gross profit / gross loss. ``inf`` if profitable with no losses, else 0.0 if empty."""
    arr = np.asarray(list(pnls), dtype=float)
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    if losses == 0.0:
        return math.inf if gains > 0 else 0.0
    return gains / losses


def per_match_pnl(bets: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Sum P&L per ``match_id`` (accepts ``pnl`` or ``profit_loss``)."""
    out: dict[str, float] = defaultdict(float)
    for b in bets:
        mid = b.get("match_id")
        if mid is None:
            continue
        out[str(mid)] += float(b.get("pnl", b.get("profit_loss", 0.0)) or 0.0)
    return dict(out)


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def bet_clv(bet: Any, field: str = "clv") -> Optional[float]:
    """Per-bet CLV for ``field`` (a VirtualBet, a mapping, or a row).

    ``field="clv"`` returns the stored value. When it is missing but
    ``closing_odds`` is present, it is recomputed with :func:`clv_prob`
    (the same definition, never a second one). Any other field is read as-is.
    Returns ``None`` when no valid value exists.
    """
    value = _field(bet, field)
    if value is not None:
        return float(value)
    if field != "clv":
        return None
    closing = _field(bet, "closing_odds")
    entry = _field(bet, "odds")
    action = _field(bet, "action")
    if closing is None or entry is None or action is None:
        return None
    if float(closing) <= 1.0 or float(entry) <= 1.0:
        return None
    return clv_prob(side_of(action), float(entry), float(closing))


def portfolio_clv(
    bets: Iterable[Any], field: str = "clv", n_bootstrap: int = 10_000
) -> dict[str, Any]:
    """Summary of CLV across bets: mean, median, positive rate, n and a bootstrap 95% CI.

    ``positive_clv_rate`` is the share of bets with CLV strictly above zero.
    The CI is the percentile bootstrap of the mean from
    ``backtesting.report.bootstrap_ci``. Empty input, or fewer than 5 bets
    (where that helper cannot estimate an interval), gives NaN for the
    statistics rather than a fabricated 0.0. ``clv_sharpe`` is deliberately absent.
    """
    values = [v for v in (bet_clv(b, field) for b in bets) if v is not None and math.isfinite(v)]
    n = len(values)
    nan = math.nan
    if n == 0:
        return {
            "n": 0, "mean_clv": nan, "median_clv": nan, "positive_clv_rate": nan,
            "ci95_lower": nan, "ci95_upper": nan, "ci95_excludes_zero": False,
        }
    arr = np.asarray(values, dtype=float)
    out: dict[str, Any] = {
        "n": n,
        "mean_clv": float(np.mean(arr)),
        "median_clv": float(np.median(arr)),
        "positive_clv_rate": float(np.mean(arr > 0.0)),
    }
    if n < 5:
        out.update({"ci95_lower": nan, "ci95_upper": nan, "ci95_excludes_zero": False})
        return out
    # Lazy import: backtesting.report imports this module.
    from backtesting.report import bootstrap_ci

    ci = bootstrap_ci(values, n_bootstrap=n_bootstrap, ci=0.95, statistic="mean")
    out.update({
        "ci95_lower": ci["ci_lower"],
        "ci95_upper": ci["ci_upper"],
        "ci95_excludes_zero": bool(ci["significant"]),
    })
    return out


# ----------------------------------------------------------------------------- match-clustered bootstrap (S84)
MIN_CLUSTERS_FOR_CI = 10  # a CI from fewer clusters (matches) is reported, but labelled "too few matches"
CLUSTER_SEED = 20261103  # the same seed as the rest of the evidence code, so one ledger gives one set of numbers
CLUSTER_STATISTICS = ("mean", "sum_per_cluster")
_CLUSTER_CHUNK_CELLS = 2_000_000  # bootstrap draws are generated in blocks of about this many indices


def _ordered_labels(labels: Iterable[Any]) -> list[Any]:
    """Cluster labels in their natural order (integers numerically, strings alphabetically), else by type and repr."""
    items = list(labels)
    try:
        return sorted(items)
    except TypeError:
        return sorted(items, key=lambda x: (type(x).__name__, repr(x)))


def cluster_bootstrap(
    values: Iterable[Any],
    clusters: Iterable[Any],
    *,
    weights: Optional[Iterable[Any]] = None,
    statistic: str = "mean",
    n_boot: int = 2000,
    seed: int = CLUSTER_SEED,
    population: Optional[Iterable[Any]] = None,
) -> dict[str, Any]:
    """Point estimate and seeded percentile 95% CI with the cluster (match) as the resampling unit.

    Bets on one match move together, so resampling bets understates the uncertainty. Here each draw picks
    ``K`` clusters with replacement and every item of a chosen cluster comes with it.

    - ``statistic="mean"``: the mean of the items, ``sum(w * v) / sum(w)`` over the drawn clusters. With no
      ``weights`` it is the unweighted mean per item; with ``weights`` (e.g. stake) it is the stake-weighted mean.
    - ``statistic="sum_per_cluster"``: the total of the values (e.g. P&L). ``weights`` is not allowed here.

    ``values`` and ``clusters`` are parallel sequences. Items whose value (or weight) is None or not finite are
    dropped. ``population`` lists clusters that count as units even when they have no kept item (for example a
    match with fills but no settled P&L); they take part in the resampling as zero contributions.

    Returns a dict: ``estimate``, ``ci_lower``, ``ci_upper``, ``se`` (bootstrap standard error), ``n_clusters``
    (units, including the population), ``n_items``, ``too_few_clusters`` (True below ``MIN_CLUSTERS_FOR_CI``),
    ``statistic``, ``weighted``, ``n_boot``, ``seed``. Estimates and bounds are NaN when they cannot be formed
    (no items, or fewer than two clusters for the interval). Deterministic for a given seed and input.
    """
    if statistic not in CLUSTER_STATISTICS:
        raise ValueError(f"statistic must be one of {CLUSTER_STATISTICS}, got {statistic!r}")
    vals = np.asarray(list(values), dtype=float)
    labels = list(clusters)
    if vals.shape[0] != len(labels):
        raise ValueError(f"values ({vals.shape[0]}) and clusters ({len(labels)}) differ in length")
    weighted = weights is not None
    if weighted and statistic != "mean":
        raise ValueError("weights apply to statistic='mean' only")
    w = None
    if weighted:
        w = np.asarray(list(weights), dtype=float)
        if w.shape != vals.shape:
            raise ValueError("weights must have one entry per value")
    keep = np.isfinite(vals)
    if w is not None:
        keep &= np.isfinite(w)
        if np.any(w[keep] < 0.0):
            raise ValueError("weights must be >= 0")
    kept_labels = [lab for lab, k in zip(labels, keep) if k]
    vals = vals[keep]
    w = w[keep] if w is not None else np.ones(vals.shape[0], dtype=float)

    # Units: every cluster seen in the items or the population, in their natural order, so the result does not
    # depend on the order the caller listed them.
    units: dict[Any, int] = {}
    for lab in _ordered_labels(set(kept_labels) | set(population or ())):
        units[lab] = len(units)
    k_units = len(units)
    n_items = int(vals.shape[0])
    nan = math.nan
    out: dict[str, Any] = {
        "estimate": nan, "ci_lower": nan, "ci_upper": nan, "se": nan,
        "n_clusters": k_units, "n_items": n_items, "too_few_clusters": k_units < MIN_CLUSTERS_FOR_CI,
        "statistic": statistic, "weighted": weighted, "n_boot": int(n_boot), "seed": int(seed),
    }
    if n_items == 0:
        return out

    index = np.array([units[lab] for lab in kept_labels], dtype=int)
    num = np.bincount(index, weights=w * vals if statistic == "mean" else vals, minlength=k_units)
    den = np.bincount(index, weights=w, minlength=k_units)
    if statistic == "sum_per_cluster":
        out["estimate"] = float(num.sum())
    else:
        total_den = float(den.sum())
        out["estimate"] = float(num.sum()) / total_den if total_den > 0.0 else nan
    if k_units < 2 or n_boot <= 0:
        return out

    rng = np.random.default_rng(seed)
    stats = np.empty(int(n_boot), dtype=float)
    block = max(1, _CLUSTER_CHUNK_CELLS // k_units)
    for start in range(0, int(n_boot), block):
        size = min(block, int(n_boot) - start)
        draws = rng.integers(0, k_units, size=(size, k_units))
        total = num[draws].sum(axis=1)
        if statistic == "mean":
            count = den[draws].sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                total = np.where(count > 0.0, total / np.where(count > 0.0, count, 1.0), nan)
        stats[start:start + size] = total
    finite = stats[np.isfinite(stats)]
    if finite.size >= 2:
        lo, hi = np.percentile(finite, [2.5, 97.5])
        out["ci_lower"], out["ci_upper"] = float(lo), float(hi)
        out["se"] = float(np.std(finite, ddof=1))
    return out
