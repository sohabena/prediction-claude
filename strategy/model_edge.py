"""S70 model-edge strategy: target and hedge rules for one match (pure, no I/O).

``decide`` turns a blended probability and a takeable book into at most one order for one match. It reads no
database, feed or clock: ``now`` and ``last_trade_at`` are passed in. Stakes are in the same units as the bankroll.

Probability
    ``p = w * p_model + (1 - w) * p_market``. The ``market_only`` control uses ``p = p_market``. Without ``p_model``
    (and not ``market_only``) no new risk is taken: only ``reduce`` and ``green_up`` trades, using ``p_market``.

Book
    ``Book`` holds the net P&L if home wins and if away wins, starting at (0, 0). ``balance = home - away``. The
    favoured outcome is home when the balance is positive and away when it is negative. A draw or a void settles
    at zero, as in cricket, so only the two outcomes are tracked. Each trade changes the expected P&L
    ``E = p * home + (1 - p) * away`` by ``stake * edge(side, p_selection, price)``.

Rules (evaluated in this order, at most one order per call)
1. Reduce / flip. Let ``f`` be the favoured outcome. If backing ``f`` at its back price has
   ``edge <= -hedge_edge``, LAY ``f`` at its lay price, sized by fractional Kelly. The trade is labelled ``reduce``
   if the balance stays on its side, ``flip`` if it crosses zero.
2. Green-up. If ``-hedge_edge < edge(back f) < hedge_edge`` and the locked profit ``min(home, away)`` is positive,
   trade to equalise the outcome P&Ls. The candidates are the two trades whose balance effect opposes the balance
   (LAY f, or BACK the other selection). The one that leaves the higher common P&L is taken, with the exact stake.
3. Enter / add. The best candidate with ``edge >= min_edge`` is sized by fractional Kelly on the bankroll. It is
   labelled ``enter`` when the book is flat, ``add`` when it moves the balance in the favoured direction, and
   ``reduce`` or ``flip`` when it moves against it.

Sizing and cap
    Kelly stakes are rounded down by ``strategy.value_policy.round_stake``. Every trade is then shrunk to the largest
    stake that keeps the worst outcome at or above ``-max_match_loss``. A LAY trade is sized at its limit price (the
    highest fill it may take, S75): its liability grows with the fill price. That shrunk stake, and the green-up stake,
    are exact and not rounded. A trade below ``SIGNAL_MIN_STAKE`` is dropped.

Gates
    ``enter``, ``add`` and ``flip`` are blocked by the cooldown (``cooldown_s`` since ``last_trade_at``), by
    ``max_trades_per_match``, and by a missing ``p_model``. ``reduce`` and ``green_up`` are never blocked by these.

Price
    Every intent's ``price_limit`` is the worst price at which half the minimum edge remains (never better than the
    decision price; hedges keep the decision price). The fill model
    rejects the order if the price has moved against it by the fill time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Optional

from shared.constants import SIGNAL_MIN_STAKE
from strategy import staking
from strategy.base import MatchedBet, OrderIntent, bet_gross_pnl

BACK = staking.BACK
LAY = staking.LAY
HOME = "home"
AWAY = "away"
REASONS = ("enter", "add", "reduce", "flip", "green_up")
NEW_RISK_REASONS = frozenset({"enter", "add", "flip"})
_EPS = 1e-9
# Takeable-price keys, as in strategy.staking.BOOK_KEYS: (selection, side, key), in evaluation order.
_KEYS = (
    (HOME, BACK, "back_home"),
    (HOME, LAY, "lay_home"),
    (AWAY, BACK, "back_away"),
    (AWAY, LAY, "lay_away"),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Variant:
    """One strategy setting. ``market_only`` is the control: it uses the de-vigged market probability alone."""

    name: str
    w: float
    min_edge: float
    kelly_fraction: float = 0.25
    max_match_loss: float = 100_000.0
    max_trade_risk: float = 25_000.0  # risk added by one enter/add/flip trade, so the budget lasts the whole match
    min_trade_stake: float = 1_000.0  # smaller trades are noise a person would not place by hand
    bankroll: float = 5_000_000.0
    hedge_edge: float = 0.01
    max_trades_per_match: int = 40
    cooldown_s: float = 120.0
    market_only: bool = False

    def __post_init__(self) -> None:
        _require(0.0 <= self.w <= 1.0, f"w must be in [0, 1], got {self.w!r}")
        _require(math.isfinite(self.min_edge) and self.min_edge >= 0.0, f"min_edge must be >= 0, got {self.min_edge!r}")
        _require(0.0 < self.kelly_fraction <= 1.0, f"kelly_fraction must be in (0, 1], got {self.kelly_fraction!r}")
        _require(self.max_match_loss > 0.0, f"max_match_loss must be > 0, got {self.max_match_loss!r}")
        _require(self.max_trade_risk > 0.0, f"max_trade_risk must be > 0, got {self.max_trade_risk!r}")
        _require(self.bankroll > 0.0, f"bankroll must be > 0, got {self.bankroll!r}")
        _require(self.hedge_edge >= 0.0, f"hedge_edge must be >= 0, got {self.hedge_edge!r}")
        _require(self.max_trades_per_match >= 0, f"max_trades_per_match must be >= 0, got {self.max_trades_per_match!r}")
        _require(self.cooldown_s >= 0.0, f"cooldown_s must be >= 0, got {self.cooldown_s!r}")


@dataclass
class Book:
    """Net P&L of one match's matched bets if home wins and if away wins, plus the trade count."""

    home_pnl: float = 0.0
    away_pnl: float = 0.0
    trades: int = 0

    def apply(self, bet: MatchedBet) -> None:
        """Add one matched bet (gross of commission) to the book."""
        self.home_pnl += bet_gross_pnl(bet.side, bet.selection, bet.price, bet.stake, HOME)
        self.away_pnl += bet_gross_pnl(bet.side, bet.selection, bet.price, bet.stake, AWAY)
        self.trades += 1

    @property
    def balance(self) -> float:
        """home_pnl - away_pnl: positive when the book favours home."""
        return self.home_pnl - self.away_pnl

    @property
    def locked(self) -> float:
        """Smallest outcome P&L: the profit locked whatever happens."""
        return min(self.home_pnl, self.away_pnl)

    def worst(self) -> float:
        """Worst-case loss (>= 0), the same convention as virtual_trading.portfolio.worst_loss."""
        return max(0.0, -self.locked)


@dataclass(frozen=True)
class _Cand:
    side: str
    selection: str
    price: float
    p: float    # win probability of the selection
    edge: float  # strategy.staking.edge at p and price
    vh: float   # change of home_pnl per unit of stake
    va: float   # change of away_pnl per unit of stake


def _prob(value: object, name: str) -> float:
    p = float(value)  # type: ignore[arg-type]
    _require(math.isfinite(p) and 0.0 <= p <= 1.0, f"{name} must be a probability in [0, 1], got {value!r}")
    return p


def _effect(side: str, selection: str, price: float) -> tuple[float, float]:
    """Change of (home_pnl, away_pnl) per unit of stake. BACK wins price-1 on its selection, LAY loses price-1."""
    if side == BACK:
        win = price - 1.0
        return (win, -1.0) if selection == HOME else (-1.0, win)
    loss = -(price - 1.0)
    return (loss, 1.0) if selection == HOME else (1.0, loss)


def _takeable(book_prices: Mapping[str, object]) -> dict[tuple[str, str], float]:
    prices: dict[tuple[str, str], float] = {}
    for selection, side, key in _KEYS:
        raw = book_prices.get(key)
        if raw is None:
            continue
        price = float(raw)  # type: ignore[arg-type]
        if math.isfinite(price) and price > 1.0:
            prices[(selection, side)] = price
    return prices


def _candidates(prices: dict[tuple[str, str], float], p: float) -> list[_Cand]:
    out: list[_Cand] = []
    for selection, side, _key in _KEYS:
        price = prices.get((selection, side))
        if price is None:
            continue
        p_sel = p if selection == HOME else 1.0 - p
        vh, va = _effect(side, selection, price)
        out.append(_Cand(side, selection, price, p_sel, staking.edge(side, p_sel, price), vh, va))
    return out


def _find(cands: list[_Cand], selection: str, side: str) -> Optional[_Cand]:
    return next((c for c in cands if c.selection == selection and c.side == side), None)


def blend_probability(variant: Variant, p_model: Optional[float], p_market: float) -> float:
    """``w * p_model + (1 - w) * p_market``; ``p_market`` for the control or when there is no ``p_model``."""
    return _blend(variant, p_model, p_market)[0]


def _blend(variant: Variant, p_model: Optional[float], p_market: float) -> tuple[float, bool]:
    """(probability, new risk allowed). New risk needs a model probability unless the variant is the control."""
    pm = _prob(p_market, "p_market")
    if variant.market_only:
        return pm, True
    if p_model is None:
        return pm, False
    return variant.w * _prob(p_model, "p_model") + (1.0 - variant.w) * pm, True


def _kelly_stake(variant: Variant, cand: _Cand) -> float:
    """Fractional Kelly on the bankroll, rounded down to a human stake (0 when there is no edge)."""
    if cand.edge <= 0.0:
        return 0.0
    # Local import: strategy.value_policy pulls in the fair-value model stack.
    from strategy.value_policy import round_stake

    kelly = staking.kelly_of_bankroll(cand.side, cand.p, cand.price)
    risk = variant.kelly_fraction * kelly * variant.bankroll  # money at risk: stake for BACK, liability for LAY
    raw = risk if cand.side == BACK else risk / (cand.price - 1.0)
    return round_stake(raw)


def _cap_room(home: float, away: float, vh: float, va: float, cap: float) -> float:
    """Largest stake for which both outcome P&Ls stay at or above ``-cap`` after the trade."""
    room = math.inf
    for current, effect in ((home, vh), (away, va)):
        if effect < 0.0:
            room = min(room, (current + cap) / -effect)
        elif current < -cap - _EPS:
            return 0.0
    return max(0.0, room)


def _label(balance: float, delta_balance: float) -> str:
    """enter when flat; add when the trade moves the balance in its own direction; otherwise reduce or flip."""
    if abs(balance) <= _EPS:
        return "enter"
    if balance * delta_balance > 0.0:
        return "add"
    new_balance = balance + delta_balance
    crossed = new_balance * balance < 0.0 and abs(new_balance) > _EPS
    return "flip" if crossed else "reduce"


def _reprice(cand: _Cand, price: float) -> _Cand:
    """The same candidate at another price: the outcome effects change, the probability does not."""
    vh, va = _effect(cand.side, cand.selection, price)
    return replace(cand, price=price, vh=vh, va=va)


def _build(
    variant: Variant,
    book: Book,
    cand: _Cand,
    stake: float,
    reason: Optional[str],
) -> Optional[OrderIntent]:
    """Cap-shrink ``stake`` for ``cand``, drop it below the minimum, and label it unless ``reason`` is given."""
    limit = _limit_price(variant, cand)
    if cand.side == LAY and limit > cand.price:
        # A LAY bet's liability grows with its fill price, and the fill may be as high as the limit. Size it at the
        # limit so the worst case holds whatever price fills. BACK exposure is the stake, whatever the price.
        cand = _reprice(cand, limit)
    room = _cap_room(book.home_pnl, book.away_pnl, cand.vh, cand.va, variant.max_match_loss)
    size = min(stake, room)
    if reason is None:
        reason = _label(book.balance, (cand.vh - cand.va) * size)
    if reason in ("enter", "add", "flip"):
        # Risk-adding trades are capped one at a time so the match budget can still be traded on both sides later
        risk_per_unit = 1.0 if cand.side == BACK else cand.price - 1.0
        size = min(size, variant.max_trade_risk / risk_per_unit)
    if not size >= max(SIGNAL_MIN_STAKE, variant.min_trade_stake):
        return None
    return OrderIntent(cand.side, cand.selection, limit, size, reason)


def _limit_price(variant: Variant, cand: _Cand) -> float:
    """Worst price a person placing the bet 60 s later should still take: where half the minimum edge remains,
    never better than the decision price. BACK edge p·o − 1 ≥ m gives o ≥ (1+m)/p; LAY edge 1 − p·o ≥ m gives
    o ≤ (1−m)/p. Hedges (edge near zero) keep the decision price."""
    m = variant.min_edge / 2.0
    if cand.p <= 0.0 or cand.edge < m:
        return cand.price
    if cand.side == BACK:
        return max(1.01, min(cand.price, (1.0 + m) / cand.p))
    return max(cand.price, (1.0 - m) / cand.p)


def _green_up(variant: Variant, book: Book, cands: list[_Cand]) -> list[OrderIntent]:
    """Equalise the outcome P&Ls with the candidate that leaves the highest common P&L."""
    balance = book.balance
    best: Optional[tuple[float, _Cand, float]] = None
    for cand in cands:
        effect = cand.vh - cand.va  # change of the balance per unit of stake
        if effect * balance >= 0.0:
            continue  # this trade would not reduce the balance
        stake = -balance / effect
        common = book.home_pnl + stake * cand.vh  # equals away_pnl + stake * cand.va
        if best is None or common > best[0]:
            best = (common, cand, stake)
    if best is None:
        return []
    intent = _build(variant, book, best[1], best[2], reason="green_up")
    return [intent] if intent is not None else []


def decide(
    variant: Variant,
    p_model: Optional[float],
    p_market: float,
    book_prices: Mapping[str, object],
    position: Book,
    *,
    now: float,
    last_trade_at: Optional[float],
) -> list[OrderIntent]:
    """At most one order for one match, from the rules in the module docstring. Pure: the inputs are not changed.

    ``book_prices`` uses the keys ``back_home``, ``lay_home``, ``back_away``, ``lay_away``; missing or invalid prices
    are skipped. ``position`` holds the matched bets only: do not call this while an earlier order is unfilled.
    """
    p, new_risk_ok = _blend(variant, p_model, p_market)
    cands = _candidates(_takeable(book_prices), p)
    book = position
    balance = book.balance
    cooling = last_trade_at is not None and now - last_trade_at < variant.cooldown_s
    gated = cooling or book.trades >= variant.max_trades_per_match

    def allowed(reason: str) -> bool:
        return reason not in NEW_RISK_REASONS or (new_risk_ok and not gated)

    if abs(balance) > _EPS:
        favoured = HOME if balance > 0.0 else AWAY
        hold = _find(cands, favoured, BACK)
        if hold is not None and hold.edge <= -variant.hedge_edge:
            # Reduce or flip: the favoured outcome is now overpriced, so trade the opposite side.
            opposite = _find(cands, favoured, LAY)
            if opposite is None:
                return []
            intent = _build(variant, book, opposite, _kelly_stake(variant, opposite), reason=None)
            return [intent] if intent is not None and allowed(intent.reason) else []
        if hold is not None and hold.edge < variant.hedge_edge and book.locked > _EPS:
            return _green_up(variant, book, cands)

    best: Optional[tuple[float, OrderIntent]] = None
    for cand in cands:
        if cand.edge <= 0.0 or cand.edge < variant.min_edge:
            continue
        intent = _build(variant, book, cand, _kelly_stake(variant, cand), reason=None)
        if intent is None or not allowed(intent.reason):
            continue
        if best is None or cand.edge > best[0]:
            best = (cand.edge, intent)
    return [best[1]] if best is not None else []
