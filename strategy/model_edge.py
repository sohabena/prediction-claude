"""S70 model-edge strategy, with the S90 target position and ticket budget (pure, no I/O).

``decide`` turns a blended probability and a takeable book into at most one order for one match. It reads no
database, feed or clock: ``now``, ``last_trade_at`` and ``in_play`` are passed in. Stakes are in the same units as the
bankroll.

Probability
    ``p = w * p_model + (1 - w) * p_market``. The ``market_only`` control uses ``p = p_market``. Without ``p_model``
    (and not ``market_only``) no new risk is taken: only ``reduce`` and ``green_up`` trades, using ``p_market``.

Book
    ``Book`` holds the net P&L if home wins and if away wins, starting at (0, 0). ``balance = home - away``. The
    favoured outcome is home when the balance is positive and away when it is negative. A draw or a void settles
    at zero, as in cricket, so only the two outcomes are tracked. A BACK home or LAY away ticket of stake ``s`` at
    price ``o`` moves the balance by ``+o * s``; a BACK away or LAY home ticket by ``-o * s``. ``trades`` counts every
    filled ticket of the match, whatever its reason (ledger_state counts the filled rows).

Target position (S90)
    The target balance is the fractional-Kelly position on the best candidate with ``edge >= min_edge``: its Kelly
    stake times its balance effect, at the decision price. Each decision trades the balance toward the target in ONE
    ticket, sized to close the whole gap, and only when the gap exceeds the no-trade band
    ``no_trade_share * max_trade_risk`` (default 40%). A position already at or beyond the target is held. A
    target on the other side of zero closes the position first and opens the new side in the same ticket.

Rules (evaluated in this order, at most one order per call)
1. Reduce / flip. Let ``f`` be the favoured outcome. If backing ``f`` at its back price has
   ``edge <= -hedge_edge``, the target is the Kelly position on LAY ``f`` and the ticket moves the balance toward it.
   The ticket is labelled ``reduce`` when the balance stays on its side, ``flip`` when it crosses zero.
2. Green-up. If ``-hedge_edge < edge(back f) < hedge_edge`` and the locked profit ``min(home, away)`` is positive
   and ``|balance| > band``, trade to equalise the outcome P&Ls. The candidates are the two trades whose balance effect
   opposes the balance (LAY f, or BACK the other selection). The one that leaves the higher common P&L is taken, with
   the exact stake.
3. Target. Otherwise the target is the Kelly position on the best candidate with ``edge >= min_edge``. The ticket
   moves the balance toward it: ``enter`` from flat, ``add`` when it extends the balance's side, ``reduce`` when it
   moves toward zero without crossing (a smaller target on the same side, or a target on the other side that is
   reached by closing only), ``flip`` when it crosses zero.

Ticket budget (S90)
    A match per variant has ``ticket_budget`` tickets (default 30: the operator wants 20 to 30 bets per match),
    counted from ``Book.trades`` and shared by every reason. When ``ticket_reserve`` (2) or fewer are left, only risk-reducing tickets (``reduce``, ``green_up``) are
    allowed. Reducing tickets are never blocked by the budget, so a cap-required reduce always goes through. A flip
    that the budget or the gates refuse is cut to its close-out part: a reduce to flat.

Pre-match share (S90)
    Before the off (``in_play=False``) the worst case of the match may not go below
    ``prematch_budget_share * max_match_loss`` (default 50,000). In play the cap is ``max_match_loss``.

Gates
    ``enter``, ``add`` and ``flip`` are blocked by the cooldown (``cooldown_s`` since ``last_trade_at``), by
    ``max_trades_per_match``, by the ticket budget, and by a missing ``p_model``. ``reduce`` and ``green_up`` are
    never blocked by these.

Sizing and cap
    Kelly stakes are rounded down by ``strategy.value_policy.round_stake``, and so are the gap-closing stakes. The gap
    is converted to a stake at the decision price, the price the target is set at. Enter and add tickets (and the
    new-risk part of a flip) are capped at ``max_trade_risk`` of risk per ticket (stake for BACK, liability for LAY),
    with a LAY's risk taken at its limit. Every trade is then shrunk to the largest stake that keeps the worst outcome
    at or above ``-cap``, where ``cap`` is the pre-match or in-play cap. The cap is checked at the limit price: a LAY
    is sized there (the highest fill it may take, S75), because its liability grows with the fill price, so a LAY
    filled above its decision price moves the balance more than planned, by at most the difference to the limit. The
    shrunk stake, and the green-up stake, are exact and not rounded. A trade below ``max(SIGNAL_MIN_STAKE,
    min_trade_stake)`` is dropped.

Price
    Every intent's ``price_limit`` is the worst price at which half the minimum edge remains (never better than the
    decision price; hedges keep the decision price). The fill model rejects the order if the price has moved against
    it by the fill time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Optional, Sequence

from shared.constants import SIGNAL_MIN_STAKE
from strategy import staking
from strategy.base import MatchedBet, OrderIntent, bet_gross_pnl

BACK = staking.BACK
LAY = staking.LAY
HOME = "home"
AWAY = "away"
REASONS = ("enter", "add", "reduce", "flip", "green_up")
NEW_RISK_REASONS = frozenset({"enter", "add", "flip", "tail"})
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
    placebo: str = ""  # S85: "" for a strategy; "random" or "favourite" for a placebo control (see placebo_ticket)
    ticket_budget: int = 30  # tickets per match per variant, every reason counts (operator, 2026-11-03: 20 to 30)
    ticket_reserve: int = 2  # S90: when this many or fewer are left, only risk-reducing tickets
    no_trade_share: float = 0.4  # S90: no-trade band = this share of max_trade_risk (in balance units)
    prematch_budget_share: float = 0.5  # S90: pre-match worst case may not exceed this share of max_match_loss
    min_tradable_odds: float = 1.35  # S109: minimum decimal odds for new-risk LAY
    max_tradable_odds: float = 4.00  # S109: maximum decimal odds for new-risk BACK
    max_adverse_drift: float = 0.30  # S110: maximum adverse price drift allowed when adding to a position

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
        _require(self.ticket_budget >= 0, f"ticket_budget must be >= 0, got {self.ticket_budget!r}")
        _require(self.ticket_reserve >= 0, f"ticket_reserve must be >= 0, got {self.ticket_reserve!r}")
        _require(
            math.isfinite(self.no_trade_share) and self.no_trade_share >= 0.0,
            f"no_trade_share must be >= 0, got {self.no_trade_share!r}",
        )
        _require(
            0.0 <= self.prematch_budget_share <= 1.0,
            f"prematch_budget_share must be in [0, 1], got {self.prematch_budget_share!r}",
        )
        _require(self.placebo in ("", "random", "favourite"), f"placebo must be '', 'random' or 'favourite', got {self.placebo!r}")
        _require(math.isfinite(self.min_tradable_odds) and self.min_tradable_odds > 1.0, f"min_tradable_odds must be > 1.0, got {self.min_tradable_odds!r}")
        _require(math.isfinite(self.max_tradable_odds) and self.max_tradable_odds > self.min_tradable_odds, f"max_tradable_odds must be > min_tradable_odds, got {self.max_tradable_odds!r}")
        _require(math.isfinite(self.max_adverse_drift) and self.max_adverse_drift >= 0.0, f"max_adverse_drift must be >= 0.0, got {self.max_adverse_drift!r}")

    @property
    def no_trade_band(self) -> float:
        """Gap between target and current balance that is too small to trade (balance units)."""
        return self.no_trade_share * self.max_trade_risk

    def match_cap(self, in_play: bool) -> float:
        """Worst-case loss allowed for one match: the full cap in play, the pre-match share before the off."""
        return self.max_match_loss if in_play else self.max_match_loss * self.prematch_budget_share


@dataclass
class Book:
    """Net P&L of one match's matched bets if home wins and if away wins, plus the filled ticket count."""

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


def _sign(x: float) -> float:
    return 1.0 if x > 0.0 else -1.0


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


def _human(raw: float) -> float:
    """A human-friendly stake rounded down (0 below the minimum). Local import: value_policy pulls in the model stack."""
    from strategy.value_policy import round_stake

    return round_stake(raw)


def _kelly_stake(variant: Variant, cand: _Cand) -> float:
    """Fractional Kelly on the bankroll, rounded down to a human stake (0 when there is no edge)."""
    if cand.edge <= 0.0:
        return 0.0
    kelly = staking.kelly_of_bankroll(cand.side, cand.p, cand.price)
    risk = variant.kelly_fraction * kelly * variant.bankroll  # money at risk: stake for BACK, liability for LAY
    raw = risk if cand.side == BACK else risk / (cand.price - 1.0)
    return _human(raw)


def _kelly_target(variant: Variant, cand: _Cand) -> float:
    """Target balance: the fractional-Kelly stake on ``cand`` times its balance effect, at the decision price."""
    return (cand.vh - cand.va) * _kelly_stake(variant, cand)


def _cap_room(home: float, away: float, vh: float, va: float, cap: float) -> float:
    """Largest stake for which both outcome P&Ls stay at or above ``-cap`` after the trade. An outcome the trade does
    not worsen never limits it, even when it already sits below ``-cap``."""
    room = math.inf
    for current, effect in ((home, vh), (away, va)):
        if effect < 0.0:
            room = min(room, (current + cap) / -effect)
    return max(0.0, room)


def _reprice(cand: _Cand, price: float) -> _Cand:
    """The same candidate at another price: the outcome effects change, the probability does not."""
    vh, va = _effect(cand.side, cand.selection, price)
    return replace(cand, price=price, vh=vh, va=va)


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


def _sized(variant: Variant, cand: _Cand) -> tuple[_Cand, float]:
    """The candidate as it is sized, and its limit price. A LAY is sized at its limit: its liability grows with the
    fill price, and the fill may be as high as the limit, so the worst case holds whatever price fills."""
    limit = _limit_price(variant, cand)
    if cand.side == LAY and limit > cand.price:
        return _reprice(cand, limit), limit
    return cand, limit


def _ticket(
    book: Book,
    sized: _Cand,
    limit: float,
    stake: float,
    reason: str,
    cap: float,
    variant: Variant,
) -> Optional[OrderIntent]:
    """One intent: the stake shrunk to the cap, dropped below the minimum."""
    room = _cap_room(book.home_pnl, book.away_pnl, sized.vh, sized.va, cap)
    size = min(stake, room)
    if not size >= max(SIGNAL_MIN_STAKE, variant.min_trade_stake):
        return None
    return OrderIntent(sized.side, sized.selection, limit, size, reason)


def _move_to(
    variant: Variant,
    book: Book,
    target: float,
    options: Sequence[_Cand],
    cap: float,
    new_risk: bool,
) -> Optional[OrderIntent]:
    """One ticket that moves the balance to ``target``, on the best-edge option that moves it that way.

    Nothing is traded when the gap is inside the no-trade band or no option moves the balance toward the target.
    ``new_risk`` is False when enter, add and flip are not allowed now: an add is then dropped, and a flip is cut to
    its close-out part (a reduce to flat).
    """
    balance = book.balance
    gap = target - balance
    if abs(gap) <= variant.no_trade_band:
        return None
    direction = _sign(gap)
    same = [c for c in options if _sign(c.vh - c.va) == direction]
    if not same:
        return None
    cand = max(same, key=lambda c: c.edge)  # first of the best on ties
    sized, limit = _sized(variant, cand)
    per_unit = abs(cand.vh - cand.va)  # balance per unit of stake at the decision price, the price the target uses
    risk_per_unit = 1.0 if sized.side == BACK else sized.price - 1.0  # risk at the limit: the worst fill
    risk_room = variant.max_trade_risk / risk_per_unit
    if abs(balance) <= _EPS or _sign(balance) == direction:
        # Enter from flat or extend the balance's side: new risk, capped per ticket.
        if not new_risk:
            return None
        if sized.side == LAY and sized.price < variant.min_tradable_odds:
            return None
        if sized.side == BACK and sized.price >= variant.max_tradable_odds:
            return None
        reason = "enter" if abs(balance) <= _EPS else "add"
        if reason == "add":
            if balance > _EPS and book.away_pnl < -_EPS:
                entry_odds = 1.0 + (book.home_pnl / -book.away_pnl)
                if sized.side == BACK and entry_odds > 1.0 and sized.price > entry_odds * (1.0 + variant.max_adverse_drift):
                    return None
            elif balance < -_EPS and book.home_pnl < -_EPS:
                entry_odds = 1.0 + (book.away_pnl / -book.home_pnl)
                if sized.side == BACK and entry_odds > 1.0 and sized.price > entry_odds * (1.0 + variant.max_adverse_drift):
                    return None
        stake = min(_human(abs(gap) / per_unit), risk_room)
        return _ticket(book, sized, limit, stake, reason, cap, variant)
    # Moves toward zero: the part that closes the current balance is a reduce; any part past zero is new risk.
    close = _human(min(abs(balance), abs(gap)) / per_unit)
    opened = 0.0
    corridor_ok = not ((sized.side == LAY and sized.price < variant.min_tradable_odds) or (sized.side == BACK and sized.price >= variant.max_tradable_odds))
    if new_risk and corridor_ok and abs(gap) > abs(balance):
        opened = min(_human((abs(gap) - abs(balance)) / per_unit), risk_room)
    if opened > 0.0:
        return _ticket(book, sized, limit, close + opened, "flip", cap, variant)
    if close > 0.0:
        return _ticket(book, sized, limit, close, "reduce", cap, variant)
    return None


def _green_up(variant: Variant, book: Book, cands: list[_Cand], cap: float) -> Optional[OrderIntent]:
    """Equalise the outcome P&Ls with the candidate that leaves the highest common P&L."""
    balance = book.balance
    best: Optional[tuple[float, _Cand, float]] = None
    for cand in cands:
        effect = cand.vh - cand.va  # change of the balance per unit of stake, at the decision price
        if effect * balance >= 0.0:
            continue  # this trade would not reduce the balance
        stake = -balance / effect
        common = book.home_pnl + stake * cand.vh
        if best is None or common > best[0]:
            best = (common, cand, stake)
    if best is None:
        return None
    _common, cand, stake = best
    sized, limit = _sized(variant, cand)
    return _ticket(book, sized, limit, stake, "green_up", cap, variant)


def decide(
    variant: Variant,
    p_model: Optional[float],
    p_market: float,
    book_prices: Mapping[str, object],
    position: Book,
    *,
    now: float,
    last_trade_at: Optional[float],
    in_play: bool = False,
) -> list[OrderIntent]:
    """At most one order for one match, from the rules in the module docstring. Pure: the inputs are not changed.

    ``book_prices`` uses the keys ``back_home``, ``lay_home``, ``back_away``, ``lay_away``; missing or invalid prices
    are skipped. ``position`` holds the matched bets only: do not call this while an earlier order is unfilled.
    ``in_play`` selects the worst-case cap: the pre-match share before the off, the full cap in play.
    """
    p, new_risk_ok = _blend(variant, p_model, p_market)
    cands = _candidates(_takeable(book_prices), p)
    book = position
    balance = book.balance
    cooling = last_trade_at is not None and now - last_trade_at < variant.cooldown_s
    gated = cooling or book.trades >= variant.max_trades_per_match
    budget_open = variant.ticket_budget - book.trades > variant.ticket_reserve
    new_risk = new_risk_ok and not gated and budget_open
    cap = variant.match_cap(in_play)

    if abs(balance) > _EPS:
        favoured = HOME if balance > 0.0 else AWAY
        hold = _find(cands, favoured, BACK)
        if hold is not None and hold.edge <= -variant.hedge_edge:
            # Reduce or flip: the favoured outcome is now overpriced, so the target is the opposite side.
            opposite = _find(cands, favoured, LAY)
            if opposite is None or opposite.edge <= 0.0:
                return []
            intent = _move_to(variant, book, _kelly_target(variant, opposite), [opposite], cap, new_risk)
            return [intent] if intent is not None else []
        if hold is not None and hold.edge < variant.hedge_edge and book.locked > _EPS:
            if abs(balance) <= variant.no_trade_band:
                return []
            intent = _green_up(variant, book, cands, cap)
            return [intent] if intent is not None else []

    # Target: the Kelly position on the best candidate; the ticket moves the balance toward it (either direction).
    best: Optional[_Cand] = None
    for cand in cands:
        if cand.edge <= 0.0 or cand.edge < variant.min_edge or (cand.side == LAY and cand.price < variant.min_tradable_odds) or (cand.side == BACK and cand.price >= variant.max_tradable_odds):
            continue
        if best is None or cand.edge > best.edge:
            best = cand
    if best is None:
        return []
    intent = _move_to(variant, book, _kelly_target(variant, best), cands, cap, new_risk)
    return [intent] if intent is not None else []


# ----------------------------------------------------------------------------- placebo controls (S85)
def favourite(book_prices: Mapping[str, object]) -> Optional[str]:
    """The market favourite: the selection with the lower back price (home on a tie). None without both back prices."""
    prices = _takeable(book_prices)
    home = prices.get((HOME, BACK))
    away = prices.get((AWAY, BACK))
    if home is None or away is None:
        return None
    return HOME if home <= away else AWAY


def placebo_ticket(
    variant: Variant,
    p_market: float,
    book_prices: Mapping[str, object],
    position: Book,
    *,
    side: str,
    selection: str,
    stake: float,
    in_play: bool,
    new_risk: bool = True,
) -> Optional[OrderIntent]:
    """One control ticket on a fixed side and selection, priced and capped like a strategy ticket (S85).

    The probability is the de-vigged market alone (no model). The price limit is ``_limit_price`` and a LAY is sized at
    its limit (``_sized``), the same as ``decide``. The ticket is ``enter`` from flat, ``add`` when it extends the
    balance's side, and ``reduce`` when it moves toward zero: a reduce is cut to the close-out part, so a control never
    flips. New risk (enter, add) needs ``new_risk`` (False for a suspended control) and is capped per ticket by
    ``max_trade_risk`` and per match by the worst-case cap (``_ticket``). A control does not apply the strategy's
    cooldown, trade-count and ticket-budget gates: it trades at the decision times of its reference, which passed them.
    Returns None when the price is missing, the new risk is not allowed, or the stake falls below the minimum.
    """
    price = _takeable(book_prices).get((selection, side))
    if price is None:
        return None
    p_sel = _prob(p_market, "p_market")
    if selection == AWAY:
        p_sel = 1.0 - p_sel
    vh, va = _effect(side, selection, price)
    cand = _Cand(side, selection, price, p_sel, staking.edge(side, p_sel, price), vh, va)
    sized, limit = _sized(variant, cand)
    cap = variant.match_cap(in_play)
    effect = vh - va  # change of the balance per unit of stake, at the decision price
    book = position
    balance = position.balance
    if abs(balance) <= _EPS or _sign(balance) == _sign(effect):
        if not new_risk:
            return None
        if sized.side == LAY and sized.price < variant.min_tradable_odds:
            return None
        if sized.side == BACK and sized.price >= variant.max_tradable_odds:
            return None
        reason = "enter" if abs(balance) <= _EPS else "add"
        if reason == "add":
            if balance > _EPS and book.away_pnl < -_EPS:
                entry_odds = 1.0 + (book.home_pnl / -book.away_pnl)
                if sized.side == BACK and entry_odds > 1.0 and sized.price > entry_odds * (1.0 + variant.max_adverse_drift):
                    return None
            elif balance < -_EPS and book.home_pnl < -_EPS:
                entry_odds = 1.0 + (book.away_pnl / -book.home_pnl)
                if sized.side == BACK and entry_odds > 1.0 and sized.price > entry_odds * (1.0 + variant.max_adverse_drift):
                    return None
        risk_per_unit = 1.0 if sized.side == BACK else sized.price - 1.0
        size = min(stake, variant.max_trade_risk / risk_per_unit)
    else:
        reason = "reduce"
        size = min(stake, abs(balance) / abs(effect))
    return _ticket(position, sized, limit, size, reason, cap, variant)
