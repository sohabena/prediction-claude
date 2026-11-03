"""S75 decision-time guards for the paper trader (pure: no I/O, no clock, no database).

``run_cycle`` asks two questions of every decision before it is recorded. A refusal carries one reason string, and
``run_cycle`` counts the refusals per cycle in its report (``guard_refusals``).

Book guards refuse every trade on a match, risk-reducing trades included, because the book itself cannot be trusted:
- ``no_band``: the two runners' prices allow no probability range (training.market band is None);
- ``band_contradiction``: the range both runners allow is empty;
- ``crossed_book``: a runner's back price is not below its lay price;
- ``p_market_outside_band``: the market probability does not lie inside the range both runners' prices allow.

New-risk guards refuse enter, add and flip only. reduce and green_up always pass them:
- ``stale_book``: a book field is at least ``in_play_max_age_s`` old (60 s in play) or ``prematch_max_age_s`` (15 min);
- ``spread_too_wide``: (lay - back) / back of the runner traded is above ``max_spread`` (15%);
- ``flip_cooldown``: a flip within ``flip_cooldown_s`` (5 min) of the last flip on the same match and variant;
- ``trade_rate``: ``max_trades_per_window`` (3) decisions of any kind on the same match and variant in the last
  ``trade_window_s`` (10 min).

The history for the flip and rate rules is the ledger's decisions (``decided_at``, ``reason``), passed in as
``(timestamp, reason)`` pairs, so a replay sees the same history as the live trader.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from strategy.model_edge import AWAY, HOME, NEW_RISK_REASONS
from training.market import band_bounds

BOOK_REFUSALS = ("no_band", "band_contradiction", "crossed_book", "p_market_outside_band")
NEW_RISK_REFUSALS = ("stale_book", "spread_too_wide", "flip_cooldown", "trade_rate")
REFUSAL_REASONS = BOOK_REFUSALS + NEW_RISK_REFUSALS
_TOL = 1e-12


@dataclass(frozen=True)
class GuardConfig:
    """Defaults from the S75 story. Every rule can be tuned here; the replay report states the values it used."""

    max_spread: float = 0.15  # (lay - back) / back above this: no new risk on that runner
    in_play_max_age_s: float = 60.0  # book fields must be younger than this for in-play decisions
    prematch_max_age_s: float = 900.0  # and than this before the start (15 min)
    max_trades_per_window: int = 3  # N decisions per match and variant ...
    trade_window_s: float = 600.0  # ... in a rolling window of this many seconds
    flip_cooldown_s: float = 300.0  # no flip within this long of the last flip

    def __post_init__(self) -> None:
        if not (math.isfinite(self.max_spread) and self.max_spread >= 0.0):
            raise ValueError(f"max_spread must be >= 0, got {self.max_spread!r}")
        for name in ("in_play_max_age_s", "prematch_max_age_s", "trade_window_s", "flip_cooldown_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0.0):
                raise ValueError(f"{name} must be >= 0, got {value!r}")
        if self.max_trades_per_window < 0:
            raise ValueError(f"max_trades_per_window must be >= 0, got {self.max_trades_per_window!r}")

    @property
    def history_seconds(self) -> float:
        """How far back the decision history must reach for the rate and flip rules."""
        return max(self.trade_window_s, self.flip_cooldown_s)


def _price(value: Any) -> Optional[float]:
    """A usable decimal price (finite and above 1), or None."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 1.0 else None


def spread(book: Mapping[str, Any], selection: str) -> Optional[float]:
    """(lay - back) / back for one runner, or None when either price is missing."""
    back, lay = _price(book.get(f"back_{selection}")), _price(book.get(f"lay_{selection}"))
    if back is None or lay is None:
        return None
    return (lay - back) / back


def book_refusal(book: Mapping[str, Any], p_market: Optional[float]) -> Optional[str]:
    """The book-level refusal for one match, or None. Applies to every trade, reduce and green_up included."""
    for selection in (HOME, AWAY):
        back, lay = _price(book.get(f"back_{selection}")), _price(book.get(f"lay_{selection}"))
        if back is not None and lay is not None and lay <= back:
            return "crossed_book"
    bounds = band_bounds(book)
    if bounds is None:
        return "no_band"
    low, high = bounds
    if low > high + _TOL:
        return "band_contradiction"
    if p_market is None or not (low - _TOL <= p_market <= high + _TOL):
        return "p_market_outside_band"
    return None


def intent_refusal(
    intent: Any,
    *,
    book: Mapping[str, Any],
    ages: Mapping[str, Optional[float]],
    in_play: bool,
    recent: Sequence[tuple[float, str]],
    now: float,
    cfg: GuardConfig,
) -> Optional[str]:
    """The new-risk refusal for one order intent, or None. ``ages`` maps a book field to its age in seconds.

    ``recent`` holds ``(decided_at timestamp, reason)`` for every earlier decision on this match and variant.
    Reduce and green_up intents are never refused here.
    """
    if intent.reason not in NEW_RISK_REASONS:
        return None
    max_age = cfg.in_play_max_age_s if in_play else cfg.prematch_max_age_s
    for field, age in ages.items():
        if _price(book.get(field)) is not None and age is not None and age >= max_age:
            return "stale_book"
    runner_spread = spread(book, intent.selection)
    if runner_spread is not None and runner_spread > cfg.max_spread:
        return "spread_too_wide"
    if intent.reason == "flip":
        flips = [ts for ts, reason in recent if reason == "flip" and ts <= now]
        if flips and now - max(flips) < cfg.flip_cooldown_s:
            return "flip_cooldown"
    in_window = sum(1 for ts, _reason in recent if now - cfg.trade_window_s < ts <= now)
    if in_window >= cfg.max_trades_per_window:
        return "trade_rate"
    return None


__all__ = [
    "BOOK_REFUSALS",
    "GuardConfig",
    "NEW_RISK_REFUSALS",
    "REFUSAL_REASONS",
    "book_refusal",
    "intent_refusal",
    "spread",
]
