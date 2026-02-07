"""RL environment sanity tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from rl.environment import CricketBettingEnv
from shared.constants import ACTION_SPACE_SIZE, OBSERVATION_SIZE


def _make_episode_data(n_steps: int = 50) -> list[dict]:
    """Create fake episode data for testing."""
    from datetime import timedelta
    base_time = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    data = []
    for i in range(n_steps):
        data.append({
            "match_id": "test_match",
            "timestamp": base_time + timedelta(seconds=i * 3),
            "team_home": "India",
            "team_away": "Australia",
            "competition": "Test Cup",
            "back_home": 1.85 + np.random.normal(0, 0.05),
            "lay_home": 1.90 + np.random.normal(0, 0.05),
            "back_away": 2.10 + np.random.normal(0, 0.05),
            "lay_away": 2.15 + np.random.normal(0, 0.05),
            "back_draw": 15.0,
            "lay_draw": 18.0,
            "is_live": True,
        })
    return data


class TestCricketBettingEnv:
    """Sanity tests for the custom Gymnasium environment."""

    def test_reset_returns_valid_observation(self) -> None:
        """Reset returns observation of correct shape."""
        env = CricketBettingEnv(data=[_make_episode_data()])
        obs, info = env.reset()
        assert obs.shape == (OBSERVATION_SIZE,)
        assert obs.dtype == np.float32
        assert not np.any(np.isnan(obs))

    def test_step_returns_valid(self) -> None:
        """Step returns correct tuple format."""
        env = CricketBettingEnv(data=[_make_episode_data()])
        env.reset()
        obs, reward, terminated, truncated, info = env.step(0)  # HOLD
        assert obs.shape == (OBSERVATION_SIZE,)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_hold_dominant(self) -> None:
        """HOLD action doesn't change balance significantly."""
        env = CricketBettingEnv(data=[_make_episode_data()])
        obs, _ = env.reset()
        initial_balance = env._portfolio.current_balance

        for _ in range(10):
            obs, reward, done, _, _ = env.step(0)  # HOLD
            if done:
                break

        # Balance should be unchanged after HOLDs
        assert env._portfolio.current_balance == initial_balance

    def test_episode_terminates(self) -> None:
        """Episode terminates after all data consumed."""
        data = _make_episode_data(10)
        env = CricketBettingEnv(data=[data])
        env.reset()

        done = False
        steps = 0
        while not done:
            _, _, done, _, _ = env.step(0)
            steps += 1
            if steps > 100:
                break

        assert done
        assert steps <= len(data) + 1

    def test_action_space_valid(self) -> None:
        """Action space has correct size."""
        env = CricketBettingEnv(data=[_make_episode_data()])
        assert env.action_space.n == ACTION_SPACE_SIZE

    def test_observation_space_valid(self) -> None:
        """Observation space has correct shape."""
        env = CricketBettingEnv(data=[_make_episode_data()])
        assert env.observation_space.shape == (OBSERVATION_SIZE,)

    def test_bet_action_changes_portfolio(self) -> None:
        """Betting action modifies portfolio state."""
        env = CricketBettingEnv(data=[_make_episode_data(20)])
        env.reset()

        # Take a back bet
        obs, reward, done, _, info = env.step(1)  # BACK_HOME_SM

        # Portfolio should have recorded the bet
        assert env._portfolio.total_bets >= 1
