"""Benchmark value-betting policy: bet when price beats calibrated fair value (S14)."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from shared.constants import (
    KELLY_FRACTION,
    MAX_BET_LIABILITY_PCT,
    SIGNAL_MIN_STAKE,
    STAKE_ROUND_BUCKETS,
    STRATEGY_COMMISSION,
    STRATEGY_GREEN_UP_AT,
    STRATEGY_MAX_BETS_PER_MATCH,
    STRATEGY_MIN_EDGE,
    STRATEGY_NO_SIGNAL_LAST_OVERS,
    STRATEGY_PRICE_TICK,
    STRATEGY_QUIET_AFTER_CHANGE_SECONDS,
)
from shared.schemas import BettingAction as A
from strategy.base import OrderIntent, Position, Strategy
from strategy.fair_value import FairValueModel
from strategy.staking import shrink_factor

_BACKS = (A.BACK_HOME_SM, A.BACK_HOME_LG, A.BACK_AWAY_SM, A.BACK_AWAY_LG)
_HOMES = (A.BACK_HOME_SM, A.BACK_HOME_LG, A.LAY_HOME_SM, A.LAY_HOME_LG)


class ValuePolicy:
    """Back when ``p*back-1 > min_edge + commission*(edge)+``, lay when ``1-p*lay > min_edge``.

    One position at a time; closes it (opposite side, small size) once the locked
    green-up profit reaches ``green_up_at`` x the entry edge. Interface matches
    ``backtesting.baselines`` policies: ``predict(obs, tick, env) -> int`` and ``reset()``.
    """

    name = "value"

    def __init__(
        self,
        fair_model: FairValueModel,
        min_edge: float = 0.02,
        commission: float = 0.02,
        max_bets: int = 25,
        green_up_at: float = 0.5,
    ) -> None:
        self.fair = fair_model
        self.min_edge = min_edge
        self.commission = commission
        self.max_bets = max_bets
        self.green_up_at = green_up_at
        self._bets = 0
        self._pos: Optional[Dict[str, Any]] = None

    def reset(self) -> None:
        self._bets = 0
        self._pos = None

    def win_prob(self, tick: Dict[str, Any], action: int) -> float:
        """Fair probability that the selection backed/laid by ``action`` wins."""
        p_home = self.fair.predict_home(tick)
        return p_home if int(action) in _HOMES else 1.0 - p_home

    @staticmethod
    def _price(tick: Dict[str, Any], key: str) -> Optional[float]:
        v = tick.get(key)
        return float(v) if v and float(v) > 1.0 else None

    def _edges(self, tick: Dict[str, Any]) -> list[tuple[float, int, int]]:
        """Candidate (edge, small_action, large_action) tuples that clear the threshold."""
        p_h = self.fair.predict_home(tick)
        out: list[tuple[float, int, int]] = []
        for p, bkey, lkey, back_a, lay_a in (
            (p_h, "back_home", "lay_home", (A.BACK_HOME_SM, A.BACK_HOME_LG), (A.LAY_HOME_SM, A.LAY_HOME_LG)),
            (1.0 - p_h, "back_away", "lay_away", (A.BACK_AWAY_SM, A.BACK_AWAY_LG), (A.LAY_AWAY_SM, A.LAY_AWAY_LG)),
        ):
            b, l = self._price(tick, bkey), self._price(tick, lkey)
            if b is not None:
                ev = p * b - 1.0
                if ev > self.min_edge + self.commission * max(0.0, ev):
                    out.append((ev, int(back_a[0]), int(back_a[1])))
            if l is not None:
                ev = 1.0 - p * l
                if ev > self.min_edge:
                    out.append((ev, int(lay_a[0]), int(lay_a[1])))
        return out

    def _green_up(self, tick: Dict[str, Any]) -> Optional[int]:
        pos = self._pos
        if pos is None:
            return None
        home = pos["home"]
        if pos["side"] == "back":
            close = self._price(tick, "lay_home" if home else "lay_away")
            locked = pos["price"] / close - 1.0 if close else -1.0
            action = A.LAY_HOME_SM if home else A.LAY_AWAY_SM
        else:
            close = self._price(tick, "back_home" if home else "back_away")
            locked = close / pos["price"] - 1.0 if close else -1.0
            action = A.BACK_HOME_SM if home else A.BACK_AWAY_SM
        if locked > 0 and locked >= self.green_up_at * pos["edge"]:
            self._pos = None
            return int(action)
        return None

    def predict(self, obs: Any, tick: Dict[str, Any], env: Any = None) -> int:
        exit_a = self._green_up(tick)
        if exit_a is not None:
            return exit_a
        if self._pos is not None or self._bets >= self.max_bets:
            return int(A.HOLD)
        cands = self._edges(tick)
        if not cands:
            return int(A.HOLD)
        edge, small, large = max(cands)
        action = large if edge > 2.0 * self.min_edge else small
        is_back = action in _BACKS
        home = action in _HOMES
        key = ("back_" if is_back else "lay_") + ("home" if home else "away")
        self._pos = {"side": "back" if is_back else "lay", "home": home,
                     "price": self._price(tick, key), "edge": edge}
        self._bets += 1
        return int(action)


def round_stake(raw: float) -> float:
    """Human-friendly stake, rounded DOWN to a round unit (never above ``raw``). 0 below ``SIGNAL_MIN_STAKE``."""
    if raw < SIGNAL_MIN_STAKE:
        return 0.0
    unit = 10.0
    for bucket in STAKE_ROUND_BUCKETS:
        if raw >= 2 * bucket:
            unit = float(bucket)
    rounded = math.floor(raw / unit + 1e-9) * unit
    return float(rounded) if rounded >= SIGNAL_MIN_STAKE else 0.0


def _floor_tick(x: float) -> float:
    return math.floor(x / STRATEGY_PRICE_TICK + 1e-9) * STRATEGY_PRICE_TICK


def _ceil_tick(x: float) -> float:
    return math.ceil(x / STRATEGY_PRICE_TICK - 1e-9) * STRATEGY_PRICE_TICK


class ValueStrategy(Strategy):
    """The value strategy as a backtestable and live signal source (S40).

    Maths (per unit stake, net of commission ``c`` and half spread ``h = (lay - back) / 2`` on the selection):
    - BACK at quote ``o``: effective odds ``o - h``; gain per unit risked ``b = (o - h - 1)(1 - c)``;
      ``ev = p(1 + b) - 1``; Kelly fraction of bankroll risked ``ev / b``.
    - LAY at quote ``l``: win ``(1 - c)`` when the selection loses, lose ``(l + h - 1)``;
      ``ev = (1 - p)(1 - c) - p(l + h - 1)``; Kelly fraction of bankroll risked (liability) ``ev / (1 - c)``.
    An entry needs ``ev >= min_edge``. Its ``price_limit`` is the worst price at which ``ev`` still clears
    ``min_edge`` (rounded in the conservative direction), and it is only emitted when the quote is already
    past that limit. Stake = fractional Kelly on liability, capped at ``MAX_BET_LIABILITY_PCT`` of bankroll and
    rounded down to a human amount (``round_stake``); this is deterministic, unlike ``engine.propose``.

    One open position at a time. Exits (``exit_green_up``: lay or back the selection so the locked profit
    reaches ``green_up_at`` x the entry edge; ``exit_cut``: the holding's expected value turned negative and the
    market allows closing at or better than the entry price). No signal in the quiet window after an innings,
    wicket or over change, and none when ``no_signal_last_overs`` is set and the innings is ending.

    ``last_details`` holds, for the intents of the last ``on_tick`` call, the fields a signal needs
    (``kind``, ``fair_prob``, ``market_price``, ``edge``).
    """

    name = "value"

    def __init__(
        self,
        fair_model: FairValueModel,
        *,
        min_edge: float = STRATEGY_MIN_EDGE,
        commission: float = STRATEGY_COMMISSION,
        max_bets: int = STRATEGY_MAX_BETS_PER_MATCH,
        green_up_at: float = STRATEGY_GREEN_UP_AT,
        kelly_fraction: float = KELLY_FRACTION,
        quiet_after_change_s: float = STRATEGY_QUIET_AFTER_CHANGE_SECONDS,
        no_signal_last_overs: float = STRATEGY_NO_SIGNAL_LAST_OVERS,
        p_sd: Optional[float] = None,
    ) -> None:
        if p_sd is not None and (not math.isfinite(p_sd) or p_sd < 0.0):
            raise ValueError(f"p_sd must be None or finite and >= 0, got {p_sd!r}")
        self.fair = fair_model
        self.min_edge = min_edge
        self.commission = commission
        self.max_bets = max_bets
        self.green_up_at = green_up_at
        self.kelly = kelly_fraction
        self.quiet_s = quiet_after_change_s
        self.no_signal_last_overs = no_signal_last_overs
        # S62: posterior sd of the fair probability. None keeps the sizing above unchanged; a value shrinks each
        # Kelly stake by strategy.staking.shrink_factor (uncertain edges get smaller stakes).
        self.p_sd = p_sd
        self.version = str(getattr(fair_model, "version", "v1"))
        self.last_details: list[dict[str, Any]] = []
        self._reset_match()

    def reset(self, match_meta_without_result: Dict[str, Any]) -> None:
        self._reset_match()

    def _reset_match(self) -> None:
        self._bets = 0
        self._done: set[str] = set()  # selections already closed out this match: no re-entry (no churn)
        self._pos: Optional[Dict[str, Any]] = None
        self._sig: Optional[tuple] = None
        self._quiet_until = -math.inf
        self.last_details = []

    # ------------------------------------------------------------------ maths
    @staticmethod
    def _spread(tick: Dict[str, Any], selection: str) -> Optional[tuple[float, float, float]]:
        back = ValuePolicy._price(tick, f"back_{selection}")
        lay = ValuePolicy._price(tick, f"lay_{selection}")
        if back is None or lay is None:
            return None
        return back, lay, max(0.0, (lay - back) / 2.0)

    def _ev(self, side: str, p: float, quote: float, half_spread: float) -> tuple[float, float]:
        """(edge per unit stake, Kelly fraction of bankroll risked) for a bet on a selection with win prob ``p``."""
        c = self.commission
        if side == "BACK":
            gain = (quote - half_spread - 1.0) * (1.0 - c)
            ev = p * (1.0 + gain) - 1.0
            return ev, (ev / gain if gain > 0 else 0.0)
        ev = (1.0 - p) * (1.0 - c) - p * (quote + half_spread - 1.0)
        return ev, ev / (1.0 - c)

    def _stake(
        self, side: str, quote: float, half_spread: float, fraction: float, bankroll: float, p: float = 0.0
    ) -> float:
        if self.p_sd is not None:
            fraction *= shrink_factor(p, self.p_sd, quote, side)
        liability = min(self.kelly * fraction * bankroll, MAX_BET_LIABILITY_PCT * bankroll)
        if liability <= 0:
            return 0.0
        raw = liability if side == "BACK" else liability / (quote + half_spread - 1.0)
        return round_stake(raw)

    def _entry(self, tick: Dict[str, Any], p_home: float, bankroll: float) -> Optional[tuple[OrderIntent, Dict[str, Any]]]:
        best: Optional[tuple[float, OrderIntent, Dict[str, Any]]] = None
        for sel in ("home", "away"):
            if sel in self._done:
                continue
            p = p_home if sel == "home" else 1.0 - p_home
            spread = self._spread(tick, sel)
            if spread is None:
                continue
            back, lay, h = spread
            for side, quote in (("BACK", back), ("LAY", lay)):
                ev, fraction = self._ev(side, p, quote, h)
                if ev < self.min_edge:
                    continue
                if side == "BACK":
                    limit = _ceil_tick(h + 1.0 + ((1.0 + self.min_edge) / p - 1.0) / (1.0 - self.commission))
                    if quote < limit:
                        continue
                else:
                    limit = _floor_tick(1.0 - h + ((1.0 - p) * (1.0 - self.commission) - self.min_edge) / p)
                    if quote > limit or limit <= 1.0:
                        continue
                stake = self._stake(side, quote, h, fraction, bankroll, p)
                if stake <= 0:
                    continue
                if best is None or ev > best[0]:
                    intent = OrderIntent(side, sel, limit, stake, "value_entry")
                    best = (ev, intent, {"kind": "entry", "fair_prob": p, "market_price": quote, "edge": ev})
        return None if best is None else (best[1], best[2])

    def _exit(self, tick: Dict[str, Any], p_home: float, position: Position) -> Optional[tuple[OrderIntent, Dict[str, Any]]]:
        pos = self._pos or {}
        side, sel, edge0 = pos["side"], pos["selection"], pos["edge"]
        entry = next(
            (b for b in position.matched if b.reason == "value_entry" and b.side == side and b.selection == sel),
            None,
        )
        spread = self._spread(tick, sel)
        if entry is None or spread is None:
            return None
        back, lay, _h = spread
        p = p_home if sel == "home" else 1.0 - p_home
        c = self.commission
        g = self.green_up_at * edge0
        if side == "BACK":
            p0 = entry.price
            quote = lay
            locked = p0 / quote - 1.0
            green_limit = _floor_tick(p0 / (1.0 + g))
            hold_ev = p * (1.0 + (p0 - 1.0) * (1.0 - c)) - 1.0
            green = locked >= g and quote <= green_limit
            cut = hold_ev < 0 and quote <= p0
            exit_side, limit = "LAY", green_limit if green else p0
            stake = entry.stake * p0 / quote
            edge = locked if green else hold_ev
        else:
            l0 = entry.price
            quote = back
            locked = 1.0 - l0 / quote
            green_limit = _ceil_tick(l0 / (1.0 - g)) if g < 1.0 else math.inf
            green = quote >= green_limit and locked >= g
            hold_ev = (1.0 - p) * (1.0 - c) - p * (l0 - 1.0)
            cut = hold_ev < 0 and quote >= l0
            exit_side, limit = "BACK", green_limit if green else l0
            stake = entry.stake * l0 / quote
            edge = locked if green else hold_ev
        if not green and not cut:
            return None
        stake = math.floor(stake * 100.0) / 100.0
        if stake <= 0 or limit <= 1.0 or not math.isfinite(limit):
            return None
        reason = "exit_green_up" if green else "exit_cut"
        intent = OrderIntent(exit_side, sel, limit, stake, reason)
        return intent, {"kind": "exit", "fair_prob": p, "market_price": quote, "edge": edge}

    # ------------------------------------------------------------------ Strategy interface
    def on_tick(self, tick: Dict[str, Any], position: Position) -> list[OrderIntent]:
        self.last_details = []
        if position.feed is None:
            raise ValueError("ValueStrategy needs position.feed for the current time")
        now = position.feed.now
        sig = (int(tick.get("innings") or 1), int(tick.get("wickets") or 0), int(float(tick.get("overs") or 0.0)))
        if self._sig is not None and sig != self._sig:
            self._quiet_until = now + self.quiet_s
        self._sig = sig
        if now < self._quiet_until:
            return []
        if self.no_signal_last_overs > 0:
            balls_left = tick.get("balls_remaining")
            if balls_left is not None and float(balls_left) / 6.0 < self.no_signal_last_overs:
                return []

        # An entry that never filled (missed or lapsed) releases the position slot.
        if self._pos is not None and not position.open_orders and not any(
            b.reason == "value_entry" and b.side == self._pos["side"] and b.selection == self._pos["selection"]
            for b in position.matched
        ):
            self._pos = None

        p_home = self.fair.predict_home(tick)
        if self._pos is not None:
            if position.open_orders:
                return []
            exit_ = self._exit(tick, p_home, position)
            if exit_ is None:
                return []
            intent, detail = exit_
            self._done.add(self._pos["selection"])
            self._pos = None
            self.last_details = [detail]
            return [intent]

        if position.open_orders or self._bets >= self.max_bets:
            return []
        entry = self._entry(tick, p_home, position.bankroll)
        if entry is None:
            return []
        intent, detail = entry
        self._bets += 1
        self._pos = {"side": intent.side, "selection": intent.selection, "edge": detail["edge"]}
        self.last_details = [detail]
        return [intent]
