"""Business logic service for agent state and bet settlement."""

from __future__ import annotations

from typing import Any

from backend.repositories.agent_repository import AgentRepository
from shared.constants import KEY_AGENT_STATE, KEY_AGENT_VERSION, KEY_ORCHESTRATOR_STATE
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import AgentState

logger = setup_logging("service_agent")
repo = AgentRepository()


class AgentService:
    """Business logic for agent state and virtual betting."""

    @staticmethod
    async def get_agent_state() -> dict[str, Any]:
        """Get current agent state from Redis."""
        try:
            redis = await get_redis()
            orch_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
            if isinstance(orch_data, dict):
                state_map = {
                    "accumulating": AgentState.PAUSED.value,
                    "offline_training": AgentState.TRAINING.value,
                    "online_training": AgentState.TRAINING.value,
                    "virtual_trading": AgentState.EVALUATING.value,
                    "graduated": AgentState.LIVE.value,
                }
                return {
                    "state": state_map.get(
                        orch_data.get("state", ""), AgentState.PAUSED.value,
                    ),
                    "version": str(orch_data.get("model_version", "none")),
                    "orchestrator_state": orch_data.get("state", "unknown"),
                }
            state_data = await redis.get_json(KEY_AGENT_STATE)
            version_data = await redis.get_json(KEY_AGENT_VERSION)
            return {
                "state": state_data.get("mode", AgentState.PAUSED.value)
                    if isinstance(state_data, dict)
                    else (state_data or AgentState.PAUSED.value),
                "version": version_data.get("version", "none")
                    if isinstance(version_data, dict)
                    else (version_data or "none"),
            }
        except Exception as e:
            logger.error("agent_state_error", error=str(e))
            return {"state": AgentState.PAUSED.value, "version": "none"}

    @staticmethod
    async def get_virtual_bets(
        limit: int, match_id: str | None = None,
    ) -> dict[str, Any]:
        """Get recent virtual bets."""
        bets = await repo.get_virtual_bets(limit, match_id)
        return {"count": len(bets), "bets": bets}

    @staticmethod
    async def get_performance() -> dict[str, Any]:
        """Get agent performance summary."""
        return await repo.get_performance_summary()

    @staticmethod
    async def settle_pending_bets() -> dict[str, Any]:
        """Retroactively settle all pending virtual bets with match results.

        Joins virtual_bets (unsettled) with match_results and computes
        P&L based on BACK/LAY logic and the match winner.
        """
        settled_count = 0
        total_pnl = 0.0
        errors = 0

        rows = await repo.get_pending_bets_with_results()

        async with get_session() as session:
            for row in rows:
                bet_id = row[0]
                placed_at = row[1]
                match_id = row[2]
                action = row[3]
                team = row[4]
                odds = float(row[5])
                stake = float(row[6])
                winner = row[7]
                result_type = row[8]

                # Determine outcome
                if result_type in ("tie", "no_result", "draw", "abandoned"):
                    outcome = "void"
                    pnl = 0.0
                else:
                    is_back = "BACK" in action
                    team_won = (team == winner)
                    if is_back:
                        if team_won:
                            outcome = "win"
                            pnl = stake * (odds - 1.0)
                        else:
                            outcome = "loss"
                            pnl = -stake
                    else:  # LAY
                        if team_won:
                            outcome = "loss"
                            pnl = -stake * (odds - 1.0)
                        else:
                            outcome = "win"
                            pnl = stake

                # Fetch closing odds for CLV
                closing = await repo.get_closing_odds_for_bet(session, match_id)
                closing_odds = None
                clv = None
                if closing:
                    team_home = row[9]
                    bet_on_home = (team == team_home)
                    closing_odds = closing[0] if bet_on_home else closing[1]
                    if closing_odds and closing_odds > 1.0 and odds > 1.0:
                        clv = (1.0 / closing_odds) / (1.0 / odds) - 1.0

                try:
                    await repo.settle_bet(
                        session, bet_id, placed_at, outcome, pnl,
                        closing_odds, clv,
                    )
                    settled_count += 1
                    total_pnl += pnl
                except Exception as e:
                    errors += 1
                    logger.warning("settle_bet_error", bet_id=bet_id, error=str(e))

        logger.info("pending_bets_settled", count=settled_count, pnl=round(total_pnl, 2))
        return {
            "settled": settled_count,
            "total_pnl": round(total_pnl, 2),
            "errors": errors,
        }
