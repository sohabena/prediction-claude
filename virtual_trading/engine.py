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
    MAX_BETS_PER_MATCH,
    MIN_BET_INTERVAL_SECONDS,
    PAYOUT_HEADROOM_FACTOR,
    PER_MATCH_BUDGET,
    SMALL_STAKE_PERCENT,
    STAKE_NOISE_PERCENT,
    STAKE_ROUND_BUCKETS,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import BetOutcome, BettingAction, OddsEvent, VirtualBet
from virtual_trading.portfolio import PortfolioManager

logger = setup_logging("virtual_engine")


def humanize_stake(raw_stake: float) -> float:
    """Round a raw stake to a realistic human-like amount.

    Real bettors use round numbers: 100, 200, 500, 1000, 2000, 5000.
    A raw stake of 2946 becomes 3000; 487 becomes 500; 8312 becomes 8000.

    Strategy:
    - Find the nearest bucket boundary from STAKE_ROUND_BUCKETS
    - Round to the nearest multiple of that bucket
    - Add slight jitter (+/- one bucket step) 20% of the time
    """
    if raw_stake < 50:
        return max(50.0, round(raw_stake / 10) * 10)  # Round to nearest 10

    # Find the best rounding unit for this stake size
    # e.g. stake=2946 -> round_unit=500 -> 3000
    # e.g. stake=487  -> round_unit=100 -> 500
    round_unit = STAKE_ROUND_BUCKETS[0]
    for bucket in STAKE_ROUND_BUCKETS:
        if raw_stake >= bucket * 2:
            round_unit = bucket

    rounded = round(raw_stake / round_unit) * round_unit
    rounded = max(round_unit, rounded)  # Ensure at least one unit

    # 20% chance of slight jitter (one step up or down)
    if random.random() < 0.20:
        jitter = random.choice([-round_unit, round_unit])
        rounded = max(round_unit, rounded + jitter)

    return float(rounded)


class VirtualBetEngine:
    """
    Executes virtual bets for the RL agent.

    Simulates bet placement with realistic conditions:
    - Slippage modelling
    - Bet rejection probability
    - Execution delay simulation
    - Human-like stake rounding
    - Per-match budget (1,00,000 default) with payout-aware headroom
    - Dynamic cooldown (shorter during high-volatility moments)
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        slippage_pct: float = 0.005,
        rejection_rate: float = 0.05,
        per_match_budget: float = PER_MATCH_BUDGET,
    ) -> None:
        self.portfolio = portfolio
        self.slippage_pct = slippage_pct
        self.rejection_rate = rejection_rate
        self.per_match_budget = per_match_budget
        self._match_bet_counts: dict[str, int] = {}
        self._match_last_bet_time: dict[str, float] = {}
        self._match_total_staked: dict[str, float] = {}
        self._match_potential_payouts: dict[str, float] = {}

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

        # Per-match bet limit check (0 = unlimited)
        match_id = event.match_id
        if MAX_BETS_PER_MATCH > 0 and self._match_bet_counts.get(match_id, 0) >= MAX_BETS_PER_MATCH:
            logger.debug("bet_rejected_match_limit", match_id=match_id,
                         count=self._match_bet_counts[match_id])
            return None

        # Dynamic cooldown: baseline MIN_BET_INTERVAL_SECONDS (15s)
        # but no hard block — just a minimum spacing to avoid bot patterns
        now_ts = datetime.now(timezone.utc).timestamp()
        last_bet_ts = self._match_last_bet_time.get(match_id, 0)
        if (now_ts - last_bet_ts) < MIN_BET_INTERVAL_SECONDS:
            logger.debug("bet_rejected_cooldown", match_id=match_id,
                         seconds_since_last=round(now_ts - last_bet_ts, 1))
            return None

        # Per-match budget check with payout-aware headroom
        staked_so_far = self._match_total_staked.get(match_id, 0.0)
        potential_payouts = self._match_potential_payouts.get(match_id, 0.0)
        effective_budget = self.per_match_budget + (potential_payouts * (PAYOUT_HEADROOM_FACTOR - 1.0))

        # Determine stake from per-match budget (SM = ~10%, LG = ~25% of budget)
        if betting_action in (
            BettingAction.BACK_HOME_SM, BettingAction.BACK_AWAY_SM,
            BettingAction.LAY_HOME_SM, BettingAction.LAY_AWAY_SM,
        ):
            raw_stake = self.per_match_budget * 0.10
        else:
            raw_stake = self.per_match_budget * 0.25

        # Anti-detection: stake noise +/-20% (avoids robotic consistency)
        raw_stake *= 1.0 + random.uniform(-STAKE_NOISE_PERCENT, STAKE_NOISE_PERCENT)

        # Cap stake to remaining budget (with payout headroom)
        remaining = max(0, effective_budget - staked_so_far)
        if remaining <= 0:
            logger.debug("bet_rejected_budget_exhausted", match_id=match_id,
                         staked=staked_so_far, budget=effective_budget)
            return None
        raw_stake = min(raw_stake, remaining)

        # Round to human-like amount (e.g. 2946 -> 3000)
        stake = humanize_stake(raw_stake)
        stake = min(stake, remaining)  # Ensure rounding didn't exceed remaining

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
            self._match_bet_counts[match_id] = self._match_bet_counts.get(match_id, 0) + 1
            self._match_last_bet_time[match_id] = now_ts
            self._match_total_staked[match_id] = staked_so_far + stake
            self._match_potential_payouts[match_id] = potential_payouts + (stake * adjusted_odds)
            return bet

        return None

    async def rebuild_state_from_db(self) -> None:
        """Rebuild per-match tracking state from DB after a restart.

        Queries unsettled virtual bets to restore:
        - _match_bet_counts (total bets placed per match)
        - _match_total_staked (total stake per match)
        """
        from shared.db import get_session
        from sqlalchemy import text

        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT match_id, COUNT(*) as bet_count, SUM(stake) as total_staked
                        FROM virtual_bets
                        WHERE settled_at IS NULL
                        GROUP BY match_id
                    """)
                )
                rows = result.fetchall()
                for row in rows:
                    mid = row[0]
                    self._match_bet_counts[mid] = int(row[1] or 0)
                    self._match_total_staked[mid] = float(row[2] or 0)
                if rows:
                    logger.info("engine_state_rebuilt", matches=len(rows))
        except Exception as e:
            logger.warning("engine_state_rebuild_failed", error=str(e))

    def clear_match(self, match_id: str) -> None:
        """Clear tracking state for a completed match."""
        self._match_bet_counts.pop(match_id, None)
        self._match_last_bet_time.pop(match_id, None)
        self._match_total_staked.pop(match_id, None)
        self._match_potential_payouts.pop(match_id, None)

    def _resolve_action(
        self, action: BettingAction, event: OddsEvent
    ) -> tuple[str, Optional[float]]:
        """Resolve an action to team name and odds."""
        if action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
            return event.team_home, event.back_home
        elif action in (BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
            return event.team_away, event.back_away
        elif action in (BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG):
            return event.team_home, event.lay_home
        elif action in (BettingAction.LAY_AWAY_SM, BettingAction.LAY_AWAY_LG):
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
