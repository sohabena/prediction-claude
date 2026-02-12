"""
Bet persister: writes virtual bets to TimescaleDB for graduation evaluation.

Virtual bets must be persisted so _run_graduation_check can query win_rate,
ROI, Sharpe, etc. from the virtual_bets table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from shared.logging import setup_logging
from shared.schemas import BettingAction, VirtualBet

logger = setup_logging("bet_persister")


async def persist_bet(
    bet: VirtualBet,
    agent_version: str = "",
) -> None:
    """
    Persist a virtual bet to the virtual_bets table.

    Called when a bet is placed (outcome=pending) and when it is settled.

    Args:
        bet: The VirtualBet (Pydantic model).
        agent_version: Current agent version string.
    """
    from backend.models.bets import VirtualBetRecord
    from shared.db import get_session

    try:
        action_str = bet.action.name if isinstance(bet.action, BettingAction) else str(bet.action)
        outcome_str = bet.outcome.value if hasattr(bet.outcome, "value") else str(bet.outcome)

        async with get_session() as session:
            record = VirtualBetRecord(
                placed_at=bet.placed_at,
                match_id=bet.match_id,
                action=action_str,
                team=bet.team,
                odds=bet.odds,
                stake=bet.stake,
                confidence=bet.confidence,
                settled_at=bet.settled_at,
                outcome=outcome_str,
                profit_loss=bet.profit_loss,
                closing_odds=bet.closing_odds,
                clv=bet.clv,
                agent_version=agent_version or bet.agent_version,
                observation_hash=bet.observation_hash or "",
                reward=bet.reward,
            )
            session.add(record)

        logger.debug(
            "bet_persisted",
            match_id=bet.match_id,
            action=action_str,
            outcome=getattr(bet.outcome, "value", str(bet.outcome)),
        )
    except Exception as e:
        logger.error("bet_persist_failed", match_id=bet.match_id, error=str(e))
        raise


async def update_settled_bet(
    match_id: str,
    placed_at: datetime,
    outcome: str,
    profit_loss: float,
    closing_odds: Optional[float] = None,
    clv: Optional[float] = None,
) -> None:
    """
    Update an existing bet record when it is settled.

    Used when we need to update settlement info for a bet that was
    persisted at placement time.

    Args:
        match_id: Match identifier.
        placed_at: When the bet was placed (part of PK).
        outcome: 'win', 'loss', or 'void'.
        profit_loss: Realized P&L.
        closing_odds: Odds at match end (for CLV).
        clv: Closing Line Value.
    """
    from sqlalchemy import text

    from shared.db import get_session

    try:
        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE virtual_bets
                    SET settled_at = NOW(),
                        outcome = :outcome,
                        profit_loss = :profit_loss,
                        closing_odds = :closing_odds,
                        clv = :clv
                    WHERE match_id = :match_id
                      AND placed_at = :placed_at
                """),
                {
                    "match_id": match_id,
                    "placed_at": placed_at,
                    "outcome": outcome,
                    "profit_loss": profit_loss,
                    "closing_odds": closing_odds,
                    "clv": clv,
                },
            )

        logger.debug("bet_settlement_updated", match_id=match_id, outcome=outcome)
    except Exception as e:
        logger.error("bet_settlement_update_failed", match_id=match_id, error=str(e))
        raise
