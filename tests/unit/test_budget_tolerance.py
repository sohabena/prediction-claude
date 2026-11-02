"""Unit tests for the 20% Opportunity Tolerance Buffer in VirtualBetEngine and CricketBettingEnv."""

from datetime import datetime, timezone
import pytest

from rl.environment import CricketBettingEnv
from shared.constants import (
    MAX_BETS_PER_MATCH,
    MAX_BETS_WITH_TOLERANCE,
    PER_MATCH_BUDGET,
    MAX_PER_MATCH_BUDGET,
    TOLERANCE_MIN_CONFIDENCE,
)
from shared.schemas import BettingAction, OddsEvent, VirtualBet
from virtual_trading.engine import VirtualBetEngine
from virtual_trading.portfolio import PortfolioManager


def _make_event(match_id: str = "test_match_tol") -> OddsEvent:
    return OddsEvent(
        event_id="ev_tol_1",
        match_id=match_id,
        team_home="India",
        team_away="Australia",
        competition="ICC World Cup",
        timestamp=datetime.now(timezone.utc),
        back_home=2.10,
        lay_home=2.14,
        back_away=1.85,
        lay_away=1.90,
        is_live=True,
    )


def test_constants_tolerance_math():
    assert MAX_BETS_PER_MATCH == 25
    assert MAX_BETS_WITH_TOLERANCE == 30
    assert PER_MATCH_BUDGET == 100_000
    assert MAX_PER_MATCH_BUDGET == 120_000
    assert TOLERANCE_MIN_CONFIDENCE == 0.75


def test_virtual_engine_bet_limit_tolerance():
    portfolio = PortfolioManager(initial_balance=500_000.0)
    engine = VirtualBetEngine(portfolio=portfolio, rejection_rate=0.0)
    event = _make_event()
    match_id = event.match_id

    # Simulate 25 bets already placed (base limit reached)
    engine._match_bet_counts[match_id] = 25
    engine._match_total_staked[match_id] = 50_000.0

    # Low confidence bet (0.50) without hedge should be REJECTED
    bet_low = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.50)
    assert bet_low is None

    # High confidence opportunity (0.80 >= 0.75) should UNLOCK tolerance buffer
    bet_high = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.80)
    assert bet_high is not None
    assert engine._match_bet_counts[match_id] == 26

    # Once 30 bets (MAX_BETS_WITH_TOLERANCE) are reached, even high confidence must be REJECTED
    engine._match_bet_counts[match_id] = 30
    bet_exhausted = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.95)
    assert bet_exhausted is None


def test_virtual_engine_budget_tolerance():
    portfolio = PortfolioManager(initial_balance=500_000.0)
    engine = VirtualBetEngine(portfolio=portfolio, rejection_rate=0.0, per_match_budget=100_000.0)
    event = _make_event("test_match_budget_tol")
    match_id = event.match_id

    # Simulate base budget exhausted ($100,000 staked, 10 bets)
    engine._match_bet_counts[match_id] = 10
    engine._match_total_staked[match_id] = 100_000.0
    engine._match_potential_payouts[match_id] = 100_000.0

    # Low confidence bet should be REJECTED
    bet_low = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.50)
    assert bet_low is None

    # High confidence opportunity (0.85) should UNLOCK 20% budget tolerance (up to $120,000)
    bet_high = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.85)
    assert bet_high is not None
    assert engine._match_total_staked[match_id] > 100_000.0
    assert engine._match_total_staked[match_id] <= 120_000.0

    # Once $120,000 is staked, even high confidence must be REJECTED
    engine._match_total_staked[match_id] = 120_000.0
    bet_max_budget = engine.execute_action(BettingAction.BACK_HOME_SM.value, event, confidence=0.95)
    assert bet_max_budget is None


def test_env_bet_volume_tolerance():
    ticks = [
        {"timestamp": "2026-01-01T10:00:00Z", "back_home": 2.0, "lay_home": 2.05, "is_live": True, "overs": 10.0, "score": 80, "wickets": 2, "target": 180, "_meta": {"winner": "home", "team_home": "India", "team_away": "Australia"}}
        for _ in range(50)
    ]
    env = CricketBettingEnv(data=[ticks], max_bets_per_episode=25)
    env.reset()
    assert env.max_bets_per_episode == 25
    assert env.max_bets_with_tolerance == 30

    # Simulate reaching 25 bets
    env._bets_this_episode = 25
    env._net_exposure_home = 4000.0  # Open Back on home

    # Counter action (Lay Home SM) is a hedge candidate, so should be permitted within tolerance!
    obs, reward, terminated, truncated, info = env.step(BettingAction.LAY_HOME_SM.value)
    assert env._bets_this_episode == 26

    # Once 30 bets reached, forced HOLD (action becomes 0)
    env._bets_this_episode = 30
    obs, reward, terminated, truncated, info = env.step(BettingAction.LAY_HOME_SM.value)
    assert env._bets_this_episode == 30

