"""Conservative execution model for the real-price backtester (S39).

Every fill is no better than a real manual bet would get:
- the decision at time T sees only ticks with ``time <= T - latency``;
- the order reaches the market at ``T + bet_delay`` and fills at the first tick at or after it;
- a BACK crosses the spread at the back price (``back_<sel>``), a LAY at the lay price
  (``lay_<sel>``). Without a ladder the fill is ``ltp`` moved ``spread_ticks_basic`` ticks
  against us;
- no fill when the fill price is through the order's limit;
- stake is capped at ``max_volume_frac`` of the selection's traded volume in the window;
- commission is charged on positive net market winnings at settlement (see ``commission_on``).

Ladder orientation: the spec's "opposite best price" is read as crossing the spread, which
is the less favourable side of the quote for the taker. If the real ladder orientation
differs, change ``fill_price`` only.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from rl.environment import shift_price
from shared.constants import MAX_BET_LIABILITY_PCT
from strategy.base import OrderIntent, bet_liability

PRESETS: dict[str, dict[str, float]] = {
    "manual": {"latency_s": 60.0},      # a person reads the signal and places the bet by hand
    "automated": {"latency_s": 8.0},    # comparison only; no automated betting is planned
}


def valid_price(value: Any) -> Optional[float]:
    """Float odds if ``value`` is a usable price (> 1.0), else None."""
    if value is None:
        return None
    price = float(value)
    return price if price > 1.0 else None


def quoted_price(tick: dict[str, Any], side: str, selection: str) -> Optional[float]:
    """Ladder price a bet on ``side``/``selection`` crosses at, or None when the tick has none."""
    return valid_price(tick.get(f"{'back' if side == 'BACK' else 'lay'}_{selection}"))


def limit_ok(price: float, side: str, limit: Optional[float]) -> bool:
    """True when ``price`` is within the order's limit (BACK: not below; LAY: not above)."""
    if limit is None:
        return True
    return price >= limit if side == "BACK" else price <= limit


@dataclass(frozen=True)
class ExecutionModel:
    """Latency and cost assumptions. ``commission`` is a fraction of positive net winnings."""

    latency_s: float = 8.0
    bet_delay_s: float = 5.0
    commission: float = 0.05
    spread_ticks_basic: int = 1
    max_volume_frac: float = 0.05
    name: str = "custom"

    @classmethod
    def preset(cls, name: str) -> "ExecutionModel":
        if name not in PRESETS:
            raise ValueError(f"unknown execution preset {name!r}; expected one of {sorted(PRESETS)}")
        return cls(name=name, **PRESETS[name])

    def commission_on(self, market_gross_pnl: float) -> float:
        """Commission on the net market winnings of one match outcome."""
        return self.commission * max(0.0, market_gross_pnl)

    def fill_price(self, tick: dict[str, Any], side: str, selection: str) -> Optional[float]:
        """Price a bet fills at on ``tick``, or None when the tick has no usable price."""
        quoted = quoted_price(tick, side, selection)
        if quoted is not None:
            return quoted
        ltp = valid_price(tick.get(f"ltp_{selection}"))
        if ltp is None:
            return None
        against = -self.spread_ticks_basic if side == "BACK" else self.spread_ticks_basic
        return shift_price(ltp, against)


@dataclass(frozen=True)
class Fill:
    """A matched order: the tick it matched on, its price, and the stake that matched."""

    index: int
    time: float
    price: float
    stake: float


def _window_volume(ticks: Sequence[dict[str, Any]], decision_index: int, fill_index: int, selection: str) -> Optional[float]:
    """Volume traded between the decision and the fill. ``traded_volume_*`` is cumulative."""
    key = f"traded_volume_{selection}"
    before = ticks[decision_index].get(key)
    after = ticks[fill_index].get(key)
    if before is None or after is None:
        return None
    return max(0.0, float(after) - float(before))


def plan_fill(
    model: ExecutionModel,
    ticks: Sequence[dict[str, Any]],
    times: Sequence[float],
    decision_index: int,
    decision_time: float,
    intent: OrderIntent,
    *,
    bankroll: float,
) -> tuple[Optional[int], Optional[Fill]]:
    """Decide when and at what price an order fills.

    Returns ``(check_index, fill)``. ``check_index`` is the first tick at or after
    ``decision_time + bet_delay`` (None when the market has no such tick). ``fill`` is None
    when the order lapses. The per-bet liability cap (``MAX_BET_LIABILITY_PCT``) is applied
    here as well.
    """
    check = bisect_left(times, decision_time + model.bet_delay_s)
    if check >= len(times):
        return None, None
    price = model.fill_price(ticks[check], intent.side, intent.selection)
    if price is None or not limit_ok(price, intent.side, intent.price_limit):
        return check, None

    stake = intent.stake
    unit_liability = bet_liability(intent.side, price, 1.0)
    if bankroll > 0 and unit_liability > 0:
        stake = min(stake, MAX_BET_LIABILITY_PCT * bankroll / unit_liability)
    window = _window_volume(ticks, decision_index, check, intent.selection)
    if window is not None:
        stake = min(stake, model.max_volume_frac * window)
    if stake <= 0:
        return check, None
    return check, Fill(index=check, time=times[check], price=price, stake=stake)
