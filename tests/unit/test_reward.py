"""Unit tests for the reward function."""

from __future__ import annotations

import pytest

from rl.reward import RewardFunction
from shared.schemas import BettingAction


class TestRewardFunction:
    """Tests for the multi-component reward function."""

    def setup_method(self) -> None:
        self.reward_fn = RewardFunction()

    def test_hold_small_positive(self) -> None:
        """HOLD with no odds change gives tiny positive reward."""
        step_info = {
            "bankroll": 100000,
            "odds_change": 0.001,
            "bets_last_hour": 0,
            "exposure_pct": 0.0,
            "current_drawdown": 0.0,
        }
        reward = self.reward_fn.compute(BettingAction.HOLD, step_info)
        assert reward > 0
        assert reward < 0.01  # Tiny

    def test_winning_bet_positive(self) -> None:
        """Winning bet gives positive reward."""
        step_info = {
            "bankroll": 100000,
            "profit_loss": 1000,
            "bet_settled": True,
            "odds_change": 0.0,
            "bets_last_hour": 1,
            "exposure_pct": 0.01,
            "current_drawdown": 0.0,
        }
        reward = self.reward_fn.compute(BettingAction.BACK_HOME_SM, step_info)
        assert reward > 0

    def test_losing_bet_negative(self) -> None:
        """Losing bet gives negative reward."""
        step_info = {
            "bankroll": 100000,
            "profit_loss": -1000,
            "bet_settled": True,
            "odds_change": 0.0,
            "bets_last_hour": 1,
            "exposure_pct": 0.01,
            "current_drawdown": 0.01,
        }
        reward = self.reward_fn.compute(BettingAction.BACK_HOME_SM, step_info)
        assert reward < 0

    def test_overtrading_penalty(self) -> None:
        """Overtrading is penalized."""
        step_info = {
            "bankroll": 100000,
            "odds_change": 0.0,
            "bets_last_hour": 20,  # Over 15 threshold
            "exposure_pct": 0.0,
            "current_drawdown": 0.0,
        }
        reward = self.reward_fn.compute(BettingAction.HOLD, step_info)
        # Should be less than normal HOLD reward due to penalty
        normal_info = {**step_info, "bets_last_hour": 0}
        normal_reward = self.reward_fn.compute(BettingAction.HOLD, normal_info)
        assert reward < normal_reward

    def test_drawdown_penalty(self) -> None:
        """Large drawdown gives quadratic penalty."""
        step_info = {
            "bankroll": 100000,
            "odds_change": 0.0,
            "bets_last_hour": 0,
            "exposure_pct": 0.0,
            "current_drawdown": 0.20,  # 20% drawdown, over 10% threshold
        }
        reward = self.reward_fn.compute(BettingAction.HOLD, step_info)
        assert reward < 0  # Penalty should dominate

    def test_sharpe_bonus_at_episode_end(self) -> None:
        """Positive Sharpe gives bonus at episode end."""
        step_info = {
            "bankroll": 100000,
            "odds_change": 0.0,
            "bets_last_hour": 0,
            "exposure_pct": 0.0,
            "current_drawdown": 0.0,
            "episode_done": True,
            "episode_returns": [0.01, 0.02, -0.005, 0.015, 0.01, 0.008],
        }
        reward = self.reward_fn.compute(BettingAction.HOLD, step_info)
        assert reward > 0.001  # Should include patience + Sharpe bonus
