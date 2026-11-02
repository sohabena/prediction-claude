"""
Virtual bet execution engine: places and manages virtual bets based on RL agent actions.

Pipeline: ``propose`` (size by liability + reserve) -> ``RiskManager.check`` -> ``commit``.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from shared.constants import (
    BUDGET_TOLERANCE_PERCENT,
    CHANNEL_RL_ACTIONS,
    CHANNEL_VIRTUAL_OUTCOMES,
    KELLY_FRACTION,
    LARGE_STAKE_BUDGET_FRACTION,
    MAX_BET_LIABILITY_PCT,
    MAX_BETS_PER_MATCH,
    MAX_BETS_WITH_TOLERANCE,
    MIN_BET_INTERVAL_SECONDS,
    PER_MATCH_BUDGET_PCT,
    SMALL_STAKE_BUDGET_FRACTION,
    STAKE_NOISE_PERCENT,
    STAKE_ROUND_BUCKETS,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import BettingAction, OddsEvent, VirtualBet
from virtual_trading.portfolio import (
    BetProposal,
    PortfolioManager,
    action_side,
    liability_for,
    outcome_pnls,
    worst_loss,
)
from virtual_trading.risk_manager import RiskManager

__all__ = ["BetProposal", "BetRejectedError", "VirtualBetEngine", "humanize_stake"]

logger = setup_logging("virtual_engine")

_LARGE_ACTIONS = frozenset({
    BettingAction.BACK_HOME_LG, BettingAction.BACK_AWAY_LG,
    BettingAction.LAY_HOME_LG, BettingAction.LAY_AWAY_LG,
})


class BetRejectedError(RuntimeError):
    """Raised by ``commit`` when the portfolio refuses a proposed bet."""


def _floor_cents(x: float) -> float:
    return math.floor(max(x, 0.0) * 100.0) / 100.0


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
    Executes virtual bets for the RL agent in three steps:

    ``propose`` (size + reserve liability) -> ``RiskManager.check`` -> ``commit``
    (or ``release``). The stake that is risk-checked is the stake that is committed.

    Simulates realistic conditions: slippage, random rejection, human-like stake
    rounding, per-match liability budget (percentage of bankroll) and cooldowns.
    The +20% bet-count / budget tolerance buffer is usable only by proposals that
    reduce the match's worst-case loss (hedges / green-ups).
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        slippage_pct: float = 0.005,
        rejection_rate: float = 0.05,
        per_match_budget: Optional[float] = None,
        risk_manager: Optional[RiskManager] = None,
    ) -> None:
        self.portfolio = portfolio
        self.slippage_pct = slippage_pct
        self.rejection_rate = rejection_rate
        # Absolute override (liability); None -> PER_MATCH_BUDGET_PCT of current bankroll
        self.per_match_budget = per_match_budget
        self.risk = risk_manager or RiskManager(portfolio)
        self._match_bet_counts: dict[str, int] = {}
        self._match_last_bet_time: dict[str, float] = {}
        # Committed worst-case LIABILITY per match (attribute name kept for compatibility)
        self._match_total_staked: dict[str, float] = {}
        self._match_potential_payouts: dict[str, float] = {}
        self._pending_ts: dict[str, float] = {}

    # ------------------------------------------------------------ helpers
    def _match_budget(self) -> float:
        if self.per_match_budget is not None:
            return self.per_match_budget
        return PER_MATCH_BUDGET_PCT * self.portfolio.state.current_balance

    def _reduces(self, match_id: str, action: BettingAction, odds: float, stake: float) -> bool:
        """True if adding this bet lowers the match's worst-case loss."""
        before = self.portfolio.match_books().get(match_id, (0.0, 0.0))
        h, a = outcome_pnls(action, odds, stake)
        after = (before[0] + h, before[1] + a)
        return worst_loss(after) < worst_loss(before) - 1e-9

    @staticmethod
    def _target_liability(
        action: BettingAction,
        side: str,
        odds: float,
        bankroll: float,
        budget: float,
        confidence: Optional[float],
        win_prob: Optional[float],
    ) -> Optional[float]:
        """Target liability before noise/rounding/caps; None means no edge.

        ``confidence=None`` means no confidence scaling (factor 1.0).
        """
        large = action in _LARGE_ACTIONS
        if win_prob is not None:
            # Fractional Kelly on the calibrated probability that the selection wins.
            if side == "back":
                edge = (win_prob * odds - 1.0) / (odds - 1.0)
            else:
                edge = 1.0 - win_prob * odds
            if edge <= 0:
                return None
            target = KELLY_FRACTION * edge * bankroll * (1.0 if large else 0.5)
        else:
            # Fixed fractional sizing (no probability model available)
            frac = LARGE_STAKE_BUDGET_FRACTION if large else SMALL_STAKE_BUDGET_FRACTION
            target = budget * frac
        # Confidence may only scale the stake down; None = no scaling
        if confidence is None:
            return target
        return target * (0.25 + 0.75 * max(0.0, min(1.0, confidence)))

    # ------------------------------------------------------------ pipeline
    def propose(
        self,
        action: int,
        event: OddsEvent,
        confidence: Optional[float] = None,
        win_prob: Optional[float] = None,
    ) -> Optional[BetProposal]:
        """Size a bet by liability and reserve it; returns None if nothing to place.

        Args:
            action: Integer action from the agent.
            event: Current odds event.
            confidence: Policy confidence (0..1) or None; only scales stake down.
                None applies no scaling and is stored as NULL (S19: callers that
                relied on the old 0.5 default now get factor 1.0 instead of 0.625).
            win_prob: Optional calibrated P(selection wins); enables fractional Kelly.
        """
        betting_action = BettingAction(action)
        if betting_action == BettingAction.HOLD:
            return None

        if random.random() < self.rejection_rate:
            logger.debug("bet_rejected_simulation", action=betting_action.name)
            return None

        team, base_odds = self._resolve_action(betting_action, event)
        if base_odds is None or base_odds <= 1.0:
            return None

        side = action_side(betting_action)
        if side == "lay" and base_odds < 1.10:
            logger.debug("bet_rejected_low_lay_odds", odds=base_odds, match_id=event.match_id)
            return None
        if side == "back" and base_odds > 50.0:
            logger.debug("bet_rejected_high_back_odds", odds=base_odds, match_id=event.match_id)
            return None

        odds = max(1.01, base_odds * (1.0 + random.gauss(0, self.slippage_pct)))
        pf = self.portfolio
        match_id = event.match_id
        pf.register_match(match_id, event.team_home, event.team_away)
        bankroll = pf.state.current_balance
        budget = self._match_budget()
        ceiling = budget * (1.0 + BUDGET_TOLERANCE_PERCENT)
        div = (odds - 1.0) if side == "lay" else 1.0

        target = self._target_liability(
            betting_action, side, odds, bankroll, budget, confidence, win_prob
        )
        if target is None or target <= 0:
            return None

        # Noise and rounding first, caps last
        noise = 1.0 + random.uniform(-(STAKE_NOISE_PERCENT / 2), STAKE_NOISE_PERCENT / 2)
        stake = humanize_stake((target / div) * noise)
        stake = min(stake, MAX_BET_LIABILITY_PCT * bankroll * (1.0 - 1e-9) / div)

        pending = [p for p in pf.reservations.values() if p.match_id == match_id]
        count = self._match_bet_counts.get(match_id, 0) + len(pending)
        used = self._match_total_staked.get(match_id, 0.0) + sum(p.liability for p in pending)

        # Tolerance buffer: only liability-reducing bets may use it
        reducing = self._reduces(match_id, betting_action, odds, stake)
        count_limit = MAX_BETS_WITH_TOLERANCE if reducing else MAX_BETS_PER_MATCH
        budget_limit = ceiling if reducing else budget
        if MAX_BETS_PER_MATCH > 0 and count >= count_limit:
            logger.debug("bet_rejected_max_bets", match_id=match_id, count=count, reducing=reducing)
            return None
        remaining = budget_limit - used
        if remaining <= 0:
            logger.debug("bet_rejected_budget", match_id=match_id, used=used, reducing=reducing)
            return None
        stake = _floor_cents(min(stake, remaining / div))

        reducing = stake > 0 and self._reduces(match_id, betting_action, odds, stake)
        if not reducing:
            if count >= MAX_BETS_PER_MATCH or used >= budget:
                return None
            stake = _floor_cents(min(stake, (budget - used) / div))
        if stake < 1.0:
            return None

        # Cooldown (hedges are exempt); in-flight proposals count as recent bets
        if not reducing:
            now_ts = datetime.now(timezone.utc).timestamp()
            last_ts = max(
                [self._match_last_bet_time.get(match_id, 0.0)]
                + [self._pending_ts.get(p.reservation_id, 0.0) for p in pending]
            )
            if (now_ts - last_ts) < MIN_BET_INTERVAL_SECONDS:
                return None

        proposal = BetProposal(
            match_id=match_id,
            action=betting_action,
            team=team,
            side=side,
            odds=odds,
            stake=stake,
            liability=liability_for(side, stake, odds),
            reduces_liability=reducing,
            confidence=confidence,
            reservation_id=uuid.uuid4().hex,
        )
        pf.reserve(proposal)
        self._pending_ts[proposal.reservation_id] = datetime.now(timezone.utc).timestamp()
        return proposal

    def commit(self, proposal: BetProposal, fill_odds: Optional[float] = None) -> VirtualBet:
        """Convert a reservation into an open bet (optionally at a different fill price).

        Raises:
            BetRejectedError: the portfolio backstop refused the bet.
        """
        pf = self.portfolio
        odds = fill_odds if fill_odds is not None else proposal.odds
        stake = proposal.stake
        cap = MAX_BET_LIABILITY_PCT * pf.state.current_balance * (1.0 - 1e-9)
        if liability_for(proposal.side, stake, odds) > cap:
            div = (odds - 1.0) if proposal.side == "lay" else 1.0
            stake = _floor_cents(cap / div)

        self.release(proposal)  # free the reservation so it is not double counted
        bet = VirtualBet(
            match_id=proposal.match_id,
            placed_at=datetime.now(timezone.utc),
            action=proposal.action,
            team=proposal.team,
            odds=odds,
            stake=stake,
            confidence=proposal.confidence,
        )
        if not pf.open_position(bet, reduces_liability=proposal.reduces_liability):
            raise BetRejectedError(f"portfolio rejected bet on {proposal.match_id}")

        mid = proposal.match_id
        liab = liability_for(proposal.side, stake, odds)
        self._match_bet_counts[mid] = self._match_bet_counts.get(mid, 0) + 1
        self._match_last_bet_time[mid] = datetime.now(timezone.utc).timestamp()
        self._match_total_staked[mid] = self._match_total_staked.get(mid, 0.0) + liab
        self._match_potential_payouts[mid] = self._match_potential_payouts.get(mid, 0.0) + stake * odds
        return bet

    def release(self, proposal: BetProposal) -> None:
        """Cancel a reservation."""
        self.portfolio.release(proposal.reservation_id)
        self._pending_ts.pop(proposal.reservation_id, None)

    def execute_action(
        self,
        action: int,
        event: OddsEvent,
        confidence: Optional[float] = None,
        win_prob: Optional[float] = None,
    ) -> Optional[VirtualBet]:
        """Thin wrapper: propose -> risk check -> commit."""
        proposal = self.propose(action, event, confidence, win_prob)
        if proposal is None:
            return None
        decision = self.risk.check(proposal)
        if not decision.allowed:
            self.release(proposal)
            return None
        try:
            bet = self.commit(proposal)
        except BetRejectedError:
            return None
        self.risk.record_bet()
        return bet

    async def rebuild_state_from_db(self) -> None:
        """Rebuild per-match tracking state from DB after a restart.

        Queries unsettled virtual bets to restore:
        - _match_bet_counts (total bets placed per match)
        - _match_total_staked (approximated by summed stake; exact liability for lay
          bets needs per-bet odds and is restored by S06 when it rehydrates the portfolio)
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
        for p in list(self.portfolio.reservations.values()):
            if p.match_id == match_id:
                self.release(p)

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
