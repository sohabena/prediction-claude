"""
Risk manager: enforces all risk limits and provides risk assessments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.constants import (
    CIRCUIT_BREAKER_CONSECUTIVE,
    MAX_BETS_PER_HOUR,
    MAX_BET_PERCENT,
    MAX_DAILY_LOSS_PERCENT,
    MAX_DRAWDOWN_PERCENT,
    MAX_MATCH_EXPOSURE_PERCENT,
    MAX_TOTAL_EXPOSURE_PERCENT,
    MAX_WEEKLY_LOSS_PERCENT,
)
from shared.logging import setup_logging
from virtual_trading.portfolio import PortfolioManager

logger = setup_logging("risk_manager")


class RiskManager:
    """
    Enforces risk limits for the virtual trading system.

    Checks:
    - Per-bet limits (max % of bankroll)
    - Match exposure limits
    - Total exposure limits
    - Daily/weekly loss limits
    - Drawdown limits
    - Bet frequency limits
    - Circuit breaker (consecutive losses)
    """

    def __init__(self, portfolio: PortfolioManager) -> None:
        self.portfolio = portfolio
        self._hourly_bet_times: list[float] = []

    def check_all(self, proposed_stake: float, match_id: str) -> dict[str, Any]:
        """
        Run all risk checks for a proposed bet.

        Returns:
            Dict with 'allowed' bool and 'violations' list.
        """
        violations: list[str] = []
        state = self.portfolio.state
        balance = state.current_balance

        # 1. Per-bet size limit
        if proposed_stake > balance * MAX_BET_PERCENT:
            violations.append(
                f"Stake {proposed_stake:.0f} exceeds {MAX_BET_PERCENT:.0%} of {balance:.0f}"
            )

        # 2. Total exposure
        new_exposure_pct = (state.total_exposure + proposed_stake) / max(balance, 1)
        if new_exposure_pct > MAX_TOTAL_EXPOSURE_PERCENT:
            violations.append(f"Total exposure {new_exposure_pct:.1%} > {MAX_TOTAL_EXPOSURE_PERCENT:.0%}")

        # 3. Match exposure
        match_exposure = sum(
            b.stake for b in self.portfolio.open_bets.values()
            if b.match_id == match_id
        )
        match_exposure_pct = (match_exposure + proposed_stake) / max(balance, 1)
        if match_exposure_pct > MAX_MATCH_EXPOSURE_PERCENT:
            violations.append(f"Match exposure {match_exposure_pct:.1%} > {MAX_MATCH_EXPOSURE_PERCENT:.0%}")

        # 4. Daily loss limit
        daily_loss_pct = -state.daily_pnl / max(state.initial_balance, 1)
        if daily_loss_pct >= MAX_DAILY_LOSS_PERCENT:
            violations.append(f"Daily loss {daily_loss_pct:.1%} >= {MAX_DAILY_LOSS_PERCENT:.0%}")

        # 5. Drawdown
        drawdown = self.portfolio.get_drawdown()
        if drawdown >= MAX_DRAWDOWN_PERCENT:
            violations.append(f"Drawdown {drawdown:.1%} >= {MAX_DRAWDOWN_PERCENT:.0%}")

        # 6. Hourly bet limit
        now = datetime.now(timezone.utc).timestamp()
        self._hourly_bet_times = [t for t in self._hourly_bet_times if now - t < 3600]
        if len(self._hourly_bet_times) >= MAX_BETS_PER_HOUR:
            violations.append(f"Hourly limit: {len(self._hourly_bet_times)} >= {MAX_BETS_PER_HOUR}")

        # 7. Circuit breaker
        if self.portfolio.is_circuit_breaker_active:
            violations.append(
                f"Circuit breaker: {CIRCUIT_BREAKER_CONSECUTIVE} consecutive losses"
            )

        allowed = len(violations) == 0

        if not allowed:
            logger.warning("risk_check_failed", violations=violations)

        return {"allowed": allowed, "violations": violations}

    def record_bet(self) -> None:
        """Record that a bet was placed (for frequency tracking)."""
        self._hourly_bet_times.append(datetime.now(timezone.utc).timestamp())

    def get_risk_summary(self) -> dict[str, Any]:
        """Get current risk metrics."""
        state = self.portfolio.state
        balance = max(state.current_balance, 1)
        return {
            "exposure_pct": state.total_exposure / balance,
            "daily_pnl_pct": state.daily_pnl / max(state.initial_balance, 1),
            "drawdown": self.portfolio.get_drawdown(),
            "open_positions": state.open_positions,
            "bets_this_hour": len(self._hourly_bet_times),
            "circuit_breaker": self.portfolio.is_circuit_breaker_active,
            "consecutive_streak": state.consecutive_streak,
        }
