"""
Virtual bet execution engine: places and manages virtual bets based on RL agent actions.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Optional

from shared.constants import (
    CHANNEL_RL_ACTIONS,
    CHANNEL_VIRTUAL_OUTCOMES,
    LARGE_STAKE_PERCENT,
    SMALL_STAKE_PERCENT,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import BetOutcome, BettingAction, OddsEvent, VirtualBet
from virtual_trading.portfolio import PortfolioManager

logger = setup_logging("virtual_engine")


class VirtualBetEngine:
    """
    Executes virtual bets for the RL agent.

    Simulates bet placement with realistic conditions:
    - Slippage modelling
    - Bet rejection probability
    - Execution delay simulation
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        slippage_pct: float = 0.005,
        rejection_rate: float = 0.05,
    ) -> None:
        self.portfolio = portfolio
        self.slippage_pct = slippage_pct
        self.rejection_rate = rejection_rate

    def execute_action(
        self, action: int, event: OddsEvent
    ) -> Optional[VirtualBet]:
        """
        Execute an RL agent action as a virtual bet.

        Args:
            action: Integer action from the agent.
            event: Current odds event.

        Returns:
            VirtualBet if placed, None if HOLD or rejected.
        """
        betting_action = BettingAction(action)

        if betting_action == BettingAction.HOLD:
            return None

        # Simulate rejection
        if random.random() < self.rejection_rate:
            logger.debug("bet_rejected_simulation", action=betting_action.name)
            return None

        # Determine team and base odds
        team, base_odds = self._resolve_action(betting_action, event)
        if base_odds is None or base_odds <= 1.0:
            return None

        # Apply slippage
        slippage = random.gauss(0, self.slippage_pct)
        adjusted_odds = base_odds * (1.0 + slippage)
        adjusted_odds = max(1.01, adjusted_odds)

        # Determine stake
        if betting_action in (
            BettingAction.BACK_HOME_SM, BettingAction.BACK_AWAY_SM,
            BettingAction.LAY_HOME_SM, BettingAction.LAY_AWAY_SM,
        ):
            stake_pct = SMALL_STAKE_PERCENT
        else:
            stake_pct = LARGE_STAKE_PERCENT

        stake = self.portfolio.state.current_balance * stake_pct

        # Create bet
        bet = VirtualBet(
            match_id=event.match_id,
            placed_at=datetime.now(timezone.utc),
            action=betting_action,
            team=team,
            odds=adjusted_odds,
            stake=stake,
        )

        # Try to open position
        if self.portfolio.open_position(bet):
            return bet

        return None

    def _resolve_action(
        self, action: BettingAction, event: OddsEvent
    ) -> tuple[str, Optional[float]]:
        """Resolve an action to team name and odds."""
        if action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
            return event.team_home, event.back_home
        elif action in (BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
            return event.team_away, event.back_away
        elif action == BettingAction.LAY_HOME_SM:
            return event.team_home, event.lay_home
        elif action == BettingAction.LAY_AWAY_SM:
            return event.team_away, event.lay_away
        return "", None

    async def publish_action(self, action: int, match_id: str) -> None:
        """Publish agent action to Redis for dashboard tracking."""
        redis = await get_redis()
        await redis.publish_event(CHANNEL_RL_ACTIONS, {
            "action": action,
            "action_name": BettingAction(action).name,
            "match_id": match_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def publish_outcome(self, bet: VirtualBet) -> None:
        """Publish bet outcome to Redis."""
        redis = await get_redis()
        await redis.publish_event(CHANNEL_VIRTUAL_OUTCOMES, bet.model_dump(mode="json"))
