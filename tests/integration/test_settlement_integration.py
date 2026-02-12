"""Integration tests for bet settlement with real match results."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from rl.environment import CricketBettingEnv
from shared.schemas import BettingAction


def _make_episode_with_result(winner: str = "India", result_type: str = "win") -> dict:
    """Create episode data with match result metadata.
    
    Returns dict format: {"_meta": {...}, "ticks": [...]}
    """
    from datetime import timedelta
    base_time = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    
    ticks = []
    for i in range(20):
        ticks.append({
            "match_id": "test_match_real",
            "timestamp": base_time + timedelta(seconds=i * 3),
            "team_home": "India",
            "team_away": "Australia",
            "competition": "Test Cup",
            "back_home": 1.85 + np.random.normal(0, 0.02),
            "lay_home": 1.90 + np.random.normal(0, 0.02),
            "back_away": 2.10 + np.random.normal(0, 0.02),
            "lay_away": 2.15 + np.random.normal(0, 0.02),
            "is_live": True,
        })
    
    return {
        "_meta": {
            "winner": winner,
            "team_home": "India",
            "team_away": "Australia",
            "result_type": result_type,
        },
        "ticks": ticks,
    }


class TestRealSettlement:
    """Tests for settlement with actual match results."""

    def test_back_home_wins_when_home_wins(self) -> None:
        """BACK_HOME bet wins when home team wins."""
        data = _make_episode_with_result(winner="India")
        env = CricketBettingEnv(data=[data], settlement_mode="real")
        env.reset()
        
        initial_balance = env._portfolio.current_balance
        
        # Place BACK_HOME bet
        env.step(1)  # BACK_HOME_SM
        
        # Run to end of episode (settlement happens at episode end)
        done = False
        while not done:
            _, _, done, _, _ = env.step(0)  # HOLD
        
        # Should have positive P&L since we backed India and India won
        final_balance = env._portfolio.current_balance
        assert final_balance > initial_balance, "Back home should win when home team wins"

    def test_back_home_loses_when_away_wins(self) -> None:
        """BACK_HOME bet loses when away team wins."""
        data = _make_episode_with_result(winner="Australia")
        env = CricketBettingEnv(data=[data], settlement_mode="real")
        env.reset()
        
        initial_balance = env._portfolio.current_balance
        
        # Place BACK_HOME bet
        env.step(1)  # BACK_HOME_SM
        
        # Run to end of episode
        done = False
        while not done:
            _, _, done, _, _ = env.step(0)
        
        # Should have negative P&L since we backed India but Australia won
        final_balance = env._portfolio.current_balance
        assert final_balance < initial_balance, "Back home should lose when away team wins"

    def test_back_away_wins_when_away_wins(self) -> None:
        """BACK_AWAY bet wins when away team wins."""
        data = _make_episode_with_result(winner="Australia")
        env = CricketBettingEnv(data=[data], settlement_mode="real")
        env.reset()
        
        initial_balance = env._portfolio.current_balance
        
        # Place BACK_AWAY bet
        env.step(3)  # BACK_AWAY_SM
        
        # Run to end of episode
        done = False
        while not done:
            _, _, done, _, _ = env.step(0)
        
        final_balance = env._portfolio.current_balance
        assert final_balance > initial_balance, "Back away should win when away team wins"

    def test_lay_home_wins_when_away_wins(self) -> None:
        """LAY_HOME bet wins when home team loses."""
        data = _make_episode_with_result(winner="Australia")
        env = CricketBettingEnv(data=[data], settlement_mode="real")
        env.reset()
        
        initial_balance = env._portfolio.current_balance
        
        # Place LAY_HOME bet
        env.step(5)  # LAY_HOME_SM
        
        # Run to end of episode
        done = False
        while not done:
            _, _, done, _, _ = env.step(0)
        
        final_balance = env._portfolio.current_balance
        assert final_balance > initial_balance, "Lay home should win when home team loses"

    def test_void_on_tie(self) -> None:
        """Bets are voided on tie result."""
        data = _make_episode_with_result(winner="", result_type="tie")
        
        env = CricketBettingEnv(data=[data], settlement_mode="real")
        env.reset()
        
        initial_balance = env._portfolio.current_balance
        
        # Place a bet
        env.step(1)  # BACK_HOME_SM
        
        # Run to end
        done = False
        while not done:
            _, _, done, _, _ = env.step(0)
        
        # Balance should be unchanged (stake returned)
        final_balance = env._portfolio.current_balance
        assert abs(final_balance - initial_balance) < 0.01, "Bets should void on tie"

    def test_default_settlement_mode_is_real(self) -> None:
        """Verify default settlement mode is 'real' not 'simulated'."""
        env = CricketBettingEnv(data=[_make_episode_with_result()])
        assert env.settlement_mode == "real"
