"""Canonical betting metrics: CLV, Sharpe, drawdown, profit factor.

Pure functions, no I/O. Every consumer (settlement, graduation, backtester,
reports) must use these so that a number means one thing everywhere.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

import numpy as np


def side_of(action: Any) -> str:
    """Return ``"BACK"`` or ``"LAY"`` for an action name / enum / side string."""
    name = getattr(action, "name", None) or str(action)
    return "LAY" if "LAY" in name.upper() else "BACK"


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
