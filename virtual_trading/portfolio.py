"""
Portfolio manager: tracks the agent's financial state, open positions, and P&L.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from shared.constants import (
    CIRCUIT_BREAKER_CONSECUTIVE,
    MAX_DAILY_LOSS_PERCENT,
    MAX_TOTAL_EXPOSURE_PERCENT,
)
from shared.logging import setup_logging
from shared.schemas import PortfolioState, VirtualBet

logger = setup_logging("portfolio")


class PortfolioManager:
    """
    Manages the RL agent's virtual portfolio.

    Tracks:
    - Balance, P&L, exposure
    - Open positions
    - Win/loss streaks
    - Risk limit enforcement
    """

    def __init__(self, initial_balance: float = 100_000.0) -> None:
        self._state = PortfolioState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
        )
        self._open_bets: dict[str, VirtualBet] = {}
        self._settled_bets: list[VirtualBet] = []
        self._daily_start_balance: float = initial_balance
        self._peak_balance: float = initial_balance
        self._last_bet_time: float = 0.0
        self._circuit_breaker_active: bool = False

    @property
    def state(self) -> PortfolioState:
        """Get current portfolio state."""
        self._state.time_since_last_bet = time.time() - self._last_bet_time if self._last_bet_time else 0.0
        return self._state

    @property
    def open_bets(self) -> dict[str, VirtualBet]:
        """Get currently open bets."""
        return self._open_bets

    @property
    def is_circuit_breaker_active(self) -> bool:
        """Check if circuit breaker is triggered."""
        return self._circuit_breaker_active

    def can_place_bet(self, stake: float) -> tuple[bool, str]:
        """
        Check if a new bet can be placed given risk constraints.

        Returns:
            (allowed, reason) tuple.
        """
        if self._circuit_breaker_active:
            return False, "Circuit breaker active"

        # Check daily loss limit
        daily_loss = (self._daily_start_balance - self._state.current_balance) / self._daily_start_balance
        if daily_loss >= MAX_DAILY_LOSS_PERCENT:
            return False, f"Daily loss limit reached: {daily_loss:.1%}"

        # Check total exposure
        new_exposure = (self._state.total_exposure + stake) / self._state.current_balance
        if new_exposure > MAX_TOTAL_EXPOSURE_PERCENT:
            return False, f"Exposure limit: {new_exposure:.1%} > {MAX_TOTAL_EXPOSURE_PERCENT:.0%}"

        # Check if enough balance
        if stake > self._state.current_balance * 0.1:
            return False, f"Stake too large: {stake:.0f}"

        return True, "OK"

    def open_position(self, bet: VirtualBet) -> bool:
        """Record a new open bet position."""
        allowed, reason = self.can_place_bet(bet.stake)
        if not allowed:
            logger.warning("bet_rejected", reason=reason, match_id=bet.match_id)
            return False

        bet_id = f"{bet.match_id}_{bet.placed_at.timestamp()}"
        bet.id = bet_id
        self._open_bets[bet_id] = bet

        self._state.open_positions += 1
        self._state.total_exposure += bet.stake
        self._state.total_bets += 1
        self._last_bet_time = time.time()

        logger.info(
            "position_opened",
            bet_id=bet_id,
            action=bet.action.name,
            odds=bet.odds,
            stake=bet.stake,
        )
        return True

    def close_position(self, bet_id: str, pnl: float) -> None:
        """Close a position with realized P&L."""
        if bet_id not in self._open_bets:
            logger.warning("bet_not_found", bet_id=bet_id)
            return

        bet = self._open_bets.pop(bet_id)
        bet.profit_loss = pnl
        bet.settled_at = datetime.now(timezone.utc)

        self._settled_bets.append(bet)

        # Update state
        self._state.open_positions -= 1
        self._state.total_exposure -= bet.stake
        self._state.current_balance += pnl
        self._state.session_pnl += pnl
        self._state.daily_pnl += pnl

        # Update streak
        if pnl > 0:
            self._state.total_wins += 1
            self._state.consecutive_streak = max(1, self._state.consecutive_streak + 1)
        elif pnl < 0:
            self._state.consecutive_streak = min(-1, self._state.consecutive_streak - 1)

        # Check circuit breaker
        if abs(self._state.consecutive_streak) >= CIRCUIT_BREAKER_CONSECUTIVE and self._state.consecutive_streak < 0:
            self._circuit_breaker_active = True
            logger.warning(
                "circuit_breaker_triggered",
                streak=self._state.consecutive_streak,
            )

        # Update peak
        self._peak_balance = max(self._peak_balance, self._state.current_balance)

        logger.info(
            "position_closed",
            bet_id=bet_id,
            pnl=pnl,
            balance=self._state.current_balance,
        )

    def get_drawdown(self) -> float:
        """Current drawdown from peak."""
        if self._peak_balance <= 0:
            return 0.0
        return 1.0 - (self._state.current_balance / self._peak_balance)

    def reset_daily(self) -> None:
        """Reset daily tracking (call at start of each day)."""
        self._daily_start_balance = self._state.current_balance
        self._state.daily_pnl = 0.0
        self._circuit_breaker_active = False
        logger.info("daily_reset", balance=self._state.current_balance)

    def get_recent_win_rate(self, n: int = 20) -> float:
        """Win rate over last N settled bets."""
        recent = self._settled_bets[-n:]
        if not recent:
            return 0.0
        wins = sum(1 for b in recent if b.profit_loss > 0)
        return wins / len(recent)
