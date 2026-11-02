"""Benchmark value-betting policy: bet when price beats calibrated fair value (S14)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from shared.schemas import BettingAction as A
from strategy.fair_value import FairValueModel

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
