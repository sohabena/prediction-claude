"""Baseline trading policies used to benchmark trained specialists.

All policies expose ``predict(obs, tick) -> int`` returning a ``BettingAction`` value, so
they can be driven through exactly the same evaluation loop as a trained PPO model.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from shared.schemas import BettingAction

DEFAULT_RATING = 100


def estimate_in_play_prob(
    tick: Dict[str, Any],
    cfg: Dict[str, Any],
    ratings: Optional[Dict[str, int]] = None,
) -> float:
    """Causal in-play home win probability from the state visible at ``tick``.

    Never reads the winner or any future tick. ``ratings`` are static, hand-assigned
    pre-match team ratings (a weak prior, not fitted on the corpus).
    """
    ratings = ratings or {}
    max_overs = float(cfg.get("max_overs", 20.0))
    par_1 = float(cfg.get("par_1", 168.0))
    wicket_decay = float(cfg.get("wicket_decay_power", 0.65))
    div_factor = float(cfg.get("div", 18.0))

    th = tick.get("team_home", "")
    ta = tick.get("team_away", "")
    r_home = ratings.get(th, DEFAULT_RATING)
    r_away = ratings.get(ta, DEFAULT_RATING)
    p_pre = 1.0 / (1.0 + 10.0 ** ((r_away - r_home) / 400.0))

    innings = tick.get("innings", 1) or 1
    overs = float(tick.get("overs", 0.0) or 0.0)
    score = float(tick.get("score", 0) or 0)
    wickets = int(tick.get("wickets", 0) or 0)
    batting_is_home = tick.get("batting_team") == th

    overs_rem = max(0.1, max_overs - overs)
    wickets_rem = max(0, 10 - wickets)
    res_factor = (wickets_rem / 10.0) ** wicket_decay * (overs_rem / max_overs) ** 0.35
    proj_extra = res_factor * (max_overs * (par_1 / max_overs))
    proj_total = score + proj_extra

    if innings == 1:
        diff = proj_total - par_1
    else:
        # Prefer the real chase target when the tick carries it. Otherwise rebuild it from
        # the required run rate (runs per over) and overs remaining (in overs, no /6).
        target = tick.get("target")
        if not target:
            req_r = tick.get("req_run_rate")
            if req_r is None:
                req_r = par_1 / max_overs
            target = score + float(req_r) * overs_rem
        diff = proj_total - float(target)
    p_inplay_bat = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, diff / div_factor))))
    p_inplay_home = p_inplay_bat if batting_is_home else (1.0 - p_inplay_bat)

    total_balls = max_overs * 6.0 * (2 if innings == 2 else 1)
    current_balls = (max_overs * 6.0 if innings == 2 else 0.0) + overs * 6.0
    progress = min(1.0, current_balls / max(1.0, total_balls))
    w_inplay = progress ** 0.75

    p_final_home = (1.0 - w_inplay) * p_pre + w_inplay * p_inplay_home
    return max(0.02, min(0.98, p_final_home))


class HoldPolicy:
    """Never trades."""

    name = "hold"

    def reset(self) -> None:
        pass

    def predict(self, obs: Any, tick: Dict[str, Any], env: Any = None) -> int:
        return int(BettingAction.HOLD)


class RandomPolicy:
    """Uniformly random over the discrete action space (seeded)."""

    name = "random"

    def __init__(self, seed: int = 0, n_actions: int = len(BettingAction)) -> None:
        self._seed = seed
        self._n = n_actions
        self._rng = np.random.default_rng(seed)

    def reset(self) -> None:
        self._rng = np.random.default_rng(self._seed)

    def predict(self, obs: Any, tick: Dict[str, Any], env: Any = None) -> int:
        return int(self._rng.integers(0, self._n))


class HeuristicPolicy:
    """Rule-based edge trader driven by :func:`estimate_in_play_prob`.

    Acts every ``interval`` ticks, capped at ``max_bets``; ``env`` (optional) supplies the
    bet count and net exposure for green-up decisions.
    """

    name = "heuristic"

    def __init__(
        self,
        cfg: Dict[str, Any],
        ratings: Optional[Dict[str, int]] = None,
        n_ticks: int = 100,
        max_bets: int = 25,
    ) -> None:
        self.cfg = cfg
        self.ratings = ratings or {}
        self.max_bets = max_bets
        self.interval = max(2, n_ticks // max(1, max_bets))
        self._step = 0
        self._bets = 0

    def reset(self) -> None:
        self._step = 0
        self._bets = 0

    def predict(self, obs: Any, tick: Dict[str, Any], env: Any = None) -> int:
        step = self._step
        self._step += 1
        bets = env._bets_this_episode if env is not None else self._bets
        if bets >= self.max_bets or step % self.interval != 0:
            return int(BettingAction.HOLD)

        b_home = tick.get("back_home") or 2.0
        l_home = tick.get("lay_home") or 2.0
        b_away = tick.get("back_away") or 2.0
        l_away = tick.get("lay_away") or 2.0
        p_h = estimate_in_play_prob(tick, self.cfg, self.ratings)
        p_a = 1.0 - p_h
        edge_h = p_h - (1.0 / b_home if b_home > 1.0 else 0.5)
        edge_a = p_a - (1.0 / b_away if b_away > 1.0 else 0.5)

        net_home = getattr(env, "_net_exposure_home", 0.0) if env is not None else 0.0
        net_away = getattr(env, "_net_exposure_away", 0.0) if env is not None else 0.0

        action = BettingAction.HOLD
        if net_home > 2000.0 and l_home <= 1.80 and p_h > 0.60:
            action = BettingAction.LAY_HOME_SM
        elif net_away > 2000.0 and l_away <= 1.80 and p_a > 0.60:
            action = BettingAction.LAY_AWAY_SM
        elif edge_h > 0.035:
            action = BettingAction.BACK_HOME_SM
        elif edge_a > 0.035:
            action = BettingAction.BACK_AWAY_SM
        elif l_home <= 2.10 and edge_a > 0.02:
            action = BettingAction.LAY_HOME_SM
        elif l_away <= 2.10 and edge_h > 0.02:
            action = BettingAction.LAY_AWAY_SM
        if action != BettingAction.HOLD:
            self._bets += 1
        return int(action)
