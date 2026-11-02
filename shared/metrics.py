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
