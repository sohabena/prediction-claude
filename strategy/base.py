"""Strategy interface contract (S39), implemented by S40.

The real-price backtester and the live manual-bet runner call strategies only through
this module. A strategy sees one observed tick at a time, its own position, and a
``MarketFeed`` that can only reach ticks that were visible at the decision (no look-ahead).
Match results never reach a strategy: ``reset`` receives metadata without the result and
every tick handed out has its ``_meta`` removed.

Exchange conventions (per selection, ``home`` or ``away``):
- BACK at odds o, stake S: win pays S(o-1), lose costs S. Liability = S.
- LAY at odds o, stake S: win (selection loses) pays S, lose costs S(o-1). Liability = S(o-1).
- A draw settles both selections as losers.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

SIDES = ("BACK", "LAY")
SELECTIONS = ("home", "away")
OUTCOMES = ("home", "away", "draw")


class LookAheadError(RuntimeError):
    """A strategy asked for market data that was not visible at its decision."""


@dataclass(frozen=True)
class OrderIntent:
    """An order a strategy wants placed at the current decision.

    ``price_limit`` is the worst acceptable odds: the lowest for BACK, the highest for LAY.
    ``None`` accepts whatever price the fill sees. ``stake`` is S in the maths above.
    """

    side: str
    selection: str
    price_limit: Optional[float]
    stake: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if self.selection not in SELECTIONS:
            raise ValueError(f"selection must be one of {SELECTIONS}, got {self.selection!r}")
        if not self.stake > 0:
            raise ValueError(f"stake must be positive, got {self.stake!r}")
        if self.price_limit is not None and not self.price_limit > 1.0:
            raise ValueError(f"price_limit must be > 1.0 or None, got {self.price_limit!r}")


@dataclass(frozen=True)
class MatchedBet:
    """A bet that has been matched by the execution model."""

    side: str
    selection: str
    price: float
    stake: float
    placed_at: float
    matched_at: float
    fill_index: int
    reason: str = ""


def bet_liability(side: str, price: float, stake: float) -> float:
    """Worst-case loss of one bet."""
    return stake if side == "BACK" else stake * (price - 1.0)


def bet_gross_pnl(side: str, selection: str, price: float, stake: float, outcome: str) -> float:
    """Gross P&L of one matched bet before commission, for a settled ``outcome``."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    selection_won = outcome == selection  # a draw is never a selection win
    if side == "BACK":
        return stake * (price - 1.0) if selection_won else -stake
    if side == "LAY":
        return -stake * (price - 1.0) if selection_won else stake
    raise ValueError(f"side must be one of {SIDES}, got {side!r}")


class MarketFeed:
    """Read-only view of the ticks visible at a decision.

    ``last_index`` is the index of the most recent visible tick. Positive offsets raise
    ``LookAheadError``. Ticks carry no ``_meta``.
    """

    def __init__(self, ticks: Sequence[dict[str, Any]], times: Sequence[float], last_index: int) -> None:
        self._ticks = ticks
        self._times = times
        self._last = last_index

    @property
    def now(self) -> float:
        """Epoch seconds of the current (most recent visible) tick."""
        return self._times[self._last]

    def __len__(self) -> int:
        return self._last + 1

    def history(self) -> Sequence[dict[str, Any]]:
        """Visible ticks in time order; the last element is the current tick. Do not mutate."""
        return self._ticks[: self._last + 1]

    def peek(self, offset: int = 0) -> dict[str, Any]:
        """Tick at ``offset`` from the current one (0 or negative). Future offsets raise."""
        if offset > 0:
            raise LookAheadError(f"future tick +{offset} requested at visible index {self._last}")
        idx = self._last + offset
        if idx < 0:
            raise IndexError("no visible tick at that offset")
        return self._ticks[idx]

    def lagged(self, seconds: float) -> Optional[dict[str, Any]]:
        """Latest visible tick at or before ``now - seconds``, or None."""
        cutoff = self.now - seconds
        idx = bisect_right(self._times, cutoff, 0, self._last + 1) - 1
        return self._ticks[idx] if idx >= 0 else None


@dataclass
class Position:
    """A strategy's book for one match: matched bets, orders still open, and the feed."""

    match_id: str
    bankroll: float
    matched: list[MatchedBet] = field(default_factory=list)
    open_orders: list[OrderIntent] = field(default_factory=list)
    feed: Optional[MarketFeed] = None

    def pnl(self, outcome: str) -> float:
        """Gross P&L of matched bets if ``outcome`` occurs (before commission)."""
        return sum(bet_gross_pnl(b.side, b.selection, b.price, b.stake, outcome) for b in self.matched)

    @property
    def pnl_if_home(self) -> float:
        return self.pnl("home")

    @property
    def pnl_if_away(self) -> float:
        return self.pnl("away")

    @property
    def pnl_if_draw(self) -> float:
        return self.pnl("draw")

    @property
    def liability(self) -> float:
        """Worst-case loss over the three outcomes (0 when the book cannot lose)."""
        return max(0.0, -min(self.pnl_if_home, self.pnl_if_away, self.pnl_if_draw))

    def is_flat(self) -> bool:
        return not self.matched and not self.open_orders


@runtime_checkable
class Strategy(Protocol):
    """Contract for backtested and live strategies (S39 contract, implemented by S40)."""

    name: str

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        """Start a new match. ``match_meta_without_result`` never contains result fields."""

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        """React to the current observed tick and return orders to place (may be empty)."""
