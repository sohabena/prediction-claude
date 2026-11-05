"""
Reward function for the cricket betting RL agent.

The reward is the (scaled) change in equity per step, expressed as a fraction of
the initial bankroll, so the undiscounted episode return equals net P&L / bankroll
(equity already includes realised P&L, commission and the back/lay spread).

    r_t = reward_scale * (
            equity_delta_t / B
            - lambda_dd   * (dd_pot_t - dd_pot_{t-1})      # drawdown potential difference
            - lambda_tail * max(0, -worst_case_pnl_t / B - tail_thr)
            + lambda_clv  * clv_delta_t )

There are no hedge, patience, per-bet-cost, Sharpe or overtrading terms: none of
them correspond to money, and each could be farmed by PPO.
"""

from __future__ import annotations

from typing import Any

from shared.logging import setup_logging

logger = setup_logging("reward")


class RewardFunction:
    """Equity-based reward with incremental drawdown and tail-liability penalties."""

    def __init__(
        self,
        initial_bankroll: float = 100_000.0,
        reward_scale: float = 100.0,
        lambda_dd: float = 2.0,
        dd_threshold: float = 0.05,
        lambda_tail: float = 0.05,
        tail_threshold: float = 0.10,
        lambda_clv: float = 0.0,
    ) -> None:
        self.initial_bankroll = max(float(initial_bankroll), 1e-9)
        self.reward_scale = reward_scale
        self.lambda_dd = lambda_dd
        self.dd_threshold = dd_threshold
        self.lambda_tail = lambda_tail
        self.tail_threshold = tail_threshold
        self.lambda_clv = lambda_clv
        self._prev_dd_term = 0.0

    def reset(self) -> None:
        """Reset per-episode state (drawdown potential)."""
        self._prev_dd_term = 0.0

    def _dd_potential(self, drawdown: float) -> float:
        return self.lambda_dd * max(0.0, drawdown - self.dd_threshold) ** 2

    def components(self, step_info: dict[str, Any]) -> dict[str, float]:
        """Return each (scaled) reward term; their sum equals ``compute``.

        Pure with respect to state: does not advance the drawdown potential.
        """
        b = self.initial_bankroll
        s = self.reward_scale
        equity = s * float(step_info.get("equity_delta", 0.0)) / b
        dd_now = self._dd_potential(float(step_info.get("current_drawdown", 0.0)))
        drawdown = -s * (dd_now - self._prev_dd_term)
        worst = float(step_info.get("worst_case_pnl", 0.0))
        tail = -s * self.lambda_tail * max(0.0, -worst / b - self.tail_threshold)
        clv = s * self.lambda_clv * float(step_info.get("clv_delta", 0.0))
        return {"equity": equity, "drawdown": drawdown, "tail": tail, "clv": clv}

    def compute(self, action: int, step_info: dict[str, Any]) -> float:
        """Compute the reward for a single step (advances drawdown potential)."""
        total = float(sum(self.components(step_info).values()))
        if step_info.get("episode_done", False):
            self._prev_dd_term = 0.0
        else:
            self._prev_dd_term = self._dd_potential(float(step_info.get("current_drawdown", 0.0)))
        return total
