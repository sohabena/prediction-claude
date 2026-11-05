"""Unit tests for the 20% opportunity tolerance buffer in VirtualBetEngine.

The tolerance buffer (bet count and budget) is usable ONLY by proposals that reduce
the match's worst-case loss; confidence no longer unlocks it. (Env-side tolerance is
re-tested by S03.)
"""

from datetime import datetime, timezone

import pytest

from shared.constants import (
    BUDGET_TOLERANCE_PERCENT,
    MAX_BETS_PER_MATCH,
    MAX_BETS_WITH_TOLERANCE,
    PER_MATCH_BUDGET_PCT,
)
from shared.schemas import BettingAction, OddsEvent, VirtualBet
from virtual_trading.engine import VirtualBetEngine
from virtual_trading.portfolio import PortfolioManager


def _make_event(match_id: str = "test_match_tol") -> OddsEvent:
    return OddsEvent(
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


def _setup(match_id: str):
    """500k bankroll, 100k match budget, and an open 15k back-home bet to hedge."""
    portfolio = PortfolioManager(initial_balance=500_000.0)
    engine = VirtualBetEngine(portfolio=portfolio, rejection_rate=0.0, per_match_budget=100_000.0)
    back = VirtualBet(
        match_id=match_id, placed_at=datetime.now(timezone.utc),
        action=BettingAction.BACK_HOME_SM, team="India", odds=2.10, stake=15_000.0,
    )
    assert portfolio.open_position(back)
    return portfolio, engine


def test_constants_tolerance_math():
    assert MAX_BETS_PER_MATCH == 25
    assert MAX_BETS_WITH_TOLERANCE == 30
    assert PER_MATCH_BUDGET_PCT == 0.10
    assert BUDGET_TOLERANCE_PERCENT == 0.20


def test_virtual_engine_bet_limit_tolerance():
    event = _make_event()
    mid = event.match_id
    _, engine = _setup(mid)
    engine._match_bet_counts[mid] = 25  # base limit reached

    # Non-reducing bet is rejected, even at very high confidence
    assert engine.propose(BettingAction.BACK_HOME_SM.value, event, confidence=0.99) is None

    # A liability-reducing hedge may use the tolerance buffer, at any confidence
    hedge = engine.propose(BettingAction.LAY_HOME_SM.value, event, confidence=0.30)
    assert hedge is not None and hedge.reduces_liability
    engine.commit(hedge)
    assert engine._match_bet_counts[mid] == 26

    # At the tolerance ceiling even a hedge is rejected
    engine._match_bet_counts[mid] = 30
    assert engine.propose(BettingAction.LAY_HOME_SM.value, event, confidence=0.95) is None


def test_virtual_engine_budget_tolerance():
    event = _make_event("test_match_budget_tol")
    mid = event.match_id
    _, engine = _setup(mid)

    # Base liability budget exhausted
    engine._match_bet_counts[mid] = 10
    engine._match_total_staked[mid] = 100_000.0

    assert engine.propose(BettingAction.BACK_HOME_SM.value, event, confidence=0.95) is None

    hedge = engine.propose(BettingAction.LAY_HOME_SM.value, event, confidence=0.5)
    assert hedge is not None and hedge.reduces_liability
    engine.commit(hedge)
    assert 100_000.0 < engine._match_total_staked[mid] <= 120_000.0

    # Once the 120k ceiling is reached, even hedges are rejected
    engine._match_total_staked[mid] = 120_000.0
    assert engine.propose(BettingAction.LAY_HOME_SM.value, event, confidence=0.95) is None
