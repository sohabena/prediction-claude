"""
Multi-component reward function for the cricket betting RL agent.

Key insight: Most reward signal comes from bet outcomes, but we add
shaping rewards to accelerate learning of patience and risk management.

NO domain-specific shaping (no 'death over penalty', no 'phase-based' adjustments).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from shared.schemas import BettingAction


class RewardFunction:
    """
    Multi-component reward that teaches profitable betting behavior.

    Components:
    1. Bet Outcome (dominant signal, ~10x scale)
    2. Patience (tiny positive for correct HOLD)
    3. Overtrading Penalty (> 15 bets/hour)
    4. Risk Breach Penalty (exposure > 50%)
    5. Drawdown Penalty (quadratic, > 10%)
    6. Sharpe Bonus (episodic, at end of match)
    7. (Removed — win rate uncapped; multi-account distribution handles detection)
    8. CLV Bonus (intermediate: odds moved in our favor before settlement)
    """

    def __init__(
        self,
        pnl_scale: float = 10.0,
        patience_reward: float = 0.001,
        overtrading_threshold: int = 15,
        overtrading_penalty: float = 0.01,
        exposure_threshold: float = 0.5,
        exposure_penalty: float = 0.1,
        drawdown_threshold: float = 0.10,
        drawdown_penalty_scale: float = 50.0,
        sharpe_bonus_scale: float = 0.5,
        clv_bonus_scale: float = 0.5,
    ) -> None:
        self.pnl_scale = pnl_scale
        self.patience_reward = patience_reward
        self.overtrading_threshold = overtrading_threshold
        self.overtrading_penalty = overtrading_penalty
        self.exposure_threshold = exposure_threshold
        self.exposure_penalty = exposure_penalty
        self.drawdown_threshold = drawdown_threshold
        self.drawdown_penalty_scale = drawdown_penalty_scale
        self.sharpe_bonus_scale = sharpe_bonus_scale
        self.clv_bonus_scale = clv_bonus_scale

    def compute(self, action: int, step_info: dict[str, Any]) -> float:
        """
        Compute the total reward for a single step.

        Args:
            action: Integer action taken.
            step_info: Dict with keys:
                - bankroll: current balance
                - profit_loss: P&L from settled bets
                - bet_settled: bool
                - odds_change: abs change in odds
                - bets_last_hour: int
                - exposure_pct: float
                - current_drawdown: float
                - episode_done: bool
                - episode_returns: list of returns
        """
        reward = 0.0

        # Component 1: Bet Outcome (dominant signal)
        if action != BettingAction.HOLD and step_info.get("bet_settled"):
            pnl = step_info.get("profit_loss", 0.0)
            bankroll = step_info.get("bankroll", 1.0)
            if bankroll > 0:
                reward += (pnl / bankroll) * self.pnl_scale

        # Component 2: Patience Reward
        # Only reward patience when odds are stable AND there's no clear opportunity
        # (avoid over-holding when there's high CLV potential)
        if action == BettingAction.HOLD:
            odds_change = abs(step_info.get("odds_change", 0.0))
            clv_potential = abs(step_info.get("clv_improvement", 0.0))
            # Don't reward holding if there's significant odds movement (potential opportunity)
            if odds_change < 0.01 and clv_potential < 0.02:
                reward += self.patience_reward

        # Component 3: Overtrading Penalty
        bets_last_hour = step_info.get("bets_last_hour", 0)
        if bets_last_hour > self.overtrading_threshold:
            reward -= self.overtrading_penalty * (bets_last_hour - self.overtrading_threshold)

        # Component 4: Risk Breach Penalty
        exposure_pct = step_info.get("exposure_pct", 0.0)
        if exposure_pct > self.exposure_threshold:
            reward -= self.exposure_penalty * (exposure_pct - self.exposure_threshold)

        # Component 5: Drawdown Penalty (quadratic)
        drawdown = step_info.get("current_drawdown", 0.0)
        if drawdown > self.drawdown_threshold:
            reward -= (drawdown - self.drawdown_threshold) ** 2 * self.drawdown_penalty_scale

        # Component 6: Sharpe Bonus (episodic)
        if step_info.get("episode_done"):
            returns = step_info.get("episode_returns", [])
            if len(returns) >= 5:
                returns_arr = np.array(returns)
                sharpe = float(np.mean(returns_arr) / (np.std(returns_arr) + 1e-8))
                reward += max(0.0, sharpe) * self.sharpe_bonus_scale

        # Component 8: CLV bonus (intermediate reward when odds move in our favor)
        clv = step_info.get("clv_improvement", 0.0)
        if clv > 0:
            reward += clv * self.clv_bonus_scale

        return reward
