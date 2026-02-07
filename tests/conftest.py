"""Shared test fixtures for PHOENIX tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from shared.schemas import MatchContext, OddsEvent, PortfolioState


@pytest.fixture
def sample_odds_event() -> OddsEvent:
    """Create a sample OddsEvent for testing."""
    return OddsEvent(
        match_id="test_match_001",
        timestamp=datetime.now(timezone.utc),
        team_home="India",
        team_away="Australia",
        competition="ICC T20 World Cup",
        back_home=1.85,
        lay_home=1.90,
        back_draw=15.0,
        lay_draw=18.0,
        back_away=2.10,
        lay_away=2.15,
        is_live=True,
        source="lotusbook",
        scrape_method="dom",
    )


@pytest.fixture
def sample_match_context() -> MatchContext:
    """Create a sample MatchContext for testing."""
    return MatchContext(
        match_id="test_match_001",
        timestamp=datetime.now(timezone.utc),
        is_live=True,
        score=85,
        wickets=3,
        overs=10.2,
        run_rate=8.25,
        required_run_rate=0.0,
        innings=1,
        balls_remaining=58,
        max_overs=20.0,
        max_balls=120,
        batting_team="India",
        bowling_team="Australia",
    )


@pytest.fixture
def sample_portfolio() -> PortfolioState:
    """Create a sample PortfolioState for testing."""
    return PortfolioState(
        initial_balance=100000.0,
        current_balance=102500.0,
        open_positions=1,
        total_exposure=1000.0,
        session_pnl=2500.0,
        daily_pnl=500.0,
        total_bets=25,
        total_wins=14,
        consecutive_streak=2,
        time_since_last_bet=45.0,
    )


@pytest.fixture
def sample_odds_history() -> list[OddsEvent]:
    """Create a sample odds history for feature testing."""
    from datetime import timedelta

    base_time = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    events = []
    np.random.seed(42)

    for i in range(50):
        t = base_time + timedelta(seconds=i * 3)  # 3-second intervals
        home_drift = np.random.normal(0, 0.02)
        away_drift = np.random.normal(0, 0.02)

        events.append(OddsEvent(
            match_id="test_match_001",
            timestamp=t,
            team_home="India",
            team_away="Australia",
            competition="ICC T20 World Cup",
            back_home=max(1.01, 1.85 + home_drift * i * 0.1),
            lay_home=max(1.02, 1.90 + home_drift * i * 0.1),
            back_away=max(1.01, 2.10 + away_drift * i * 0.1),
            lay_away=max(1.02, 2.15 + away_drift * i * 0.1),
            back_draw=15.0,
            lay_draw=18.0,
            is_live=True,
        ))

    return events
