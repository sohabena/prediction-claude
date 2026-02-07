"""Unit tests for the portfolio manager."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.schemas import BettingAction, VirtualBet
from virtual_trading.portfolio import PortfolioManager


class TestPortfolioManager:
    """Tests for portfolio management."""

    def setup_method(self) -> None:
        self.portfolio = PortfolioManager(initial_balance=100_000.0)

    def test_initial_state(self) -> None:
        """Initial state is correct."""
        state = self.portfolio.state
        assert state.initial_balance == 100_000.0
        assert state.current_balance == 100_000.0
        assert state.open_positions == 0
        assert state.total_bets == 0

    def test_open_position(self) -> None:
        """Can open a position."""
        bet = VirtualBet(
            match_id="test",
            placed_at=datetime.now(timezone.utc),
            action=BettingAction.BACK_HOME_SM,
            team="India",
            odds=1.85,
            stake=1000.0,
        )
        result = self.portfolio.open_position(bet)
        assert result is True
        assert self.portfolio.state.open_positions == 1
        assert self.portfolio.state.total_bets == 1

    def test_close_position_win(self) -> None:
        """Closing a winning position updates balance."""
        bet = VirtualBet(
            match_id="test",
            placed_at=datetime.now(timezone.utc),
            action=BettingAction.BACK_HOME_SM,
            team="India",
            odds=1.85,
            stake=1000.0,
        )
        self.portfolio.open_position(bet)
        bet_id = bet.id
        assert bet_id is not None

        self.portfolio.close_position(bet_id, pnl=850.0)
        assert self.portfolio.state.current_balance == 100_850.0
        assert self.portfolio.state.open_positions == 0
        assert self.portfolio.state.total_wins == 1

    def test_close_position_loss(self) -> None:
        """Closing a losing position updates balance."""
        bet = VirtualBet(
            match_id="test",
            placed_at=datetime.now(timezone.utc),
            action=BettingAction.BACK_HOME_SM,
            team="India",
            odds=1.85,
            stake=1000.0,
        )
        self.portfolio.open_position(bet)
        bet_id = bet.id
        assert bet_id is not None

        self.portfolio.close_position(bet_id, pnl=-1000.0)
        assert self.portfolio.state.current_balance == 99_000.0
        assert self.portfolio.state.consecutive_streak == -1

    def test_circuit_breaker(self) -> None:
        """Circuit breaker triggers after consecutive losses."""
        for i in range(6):
            bet = VirtualBet(
                match_id=f"test_{i}",
                placed_at=datetime.now(timezone.utc),
                action=BettingAction.BACK_HOME_SM,
                team="India",
                odds=1.85,
                stake=100.0,
            )
            self.portfolio.open_position(bet)
            self.portfolio.close_position(bet.id, pnl=-100.0)  # type: ignore[arg-type]

        assert self.portfolio.is_circuit_breaker_active

    def test_drawdown_calculation(self) -> None:
        """Drawdown is calculated correctly."""
        # Initial: peak = 100k
        bet = VirtualBet(
            match_id="test",
            placed_at=datetime.now(timezone.utc),
            action=BettingAction.BACK_HOME_SM,
            team="India",
            odds=1.85,
            stake=1000.0,
        )
        self.portfolio.open_position(bet)
        self.portfolio.close_position(bet.id, pnl=-10000.0)  # type: ignore[arg-type]

        drawdown = self.portfolio.get_drawdown()
        assert abs(drawdown - 0.10) < 0.01  # ~10% drawdown
