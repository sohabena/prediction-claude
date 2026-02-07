"""Agent state and virtual betting endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from shared.constants import KEY_AGENT_STATE, KEY_AGENT_VERSION
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import AgentState

router = APIRouter()
logger = setup_logging("router_agent")


@router.get("/state")
async def get_agent_state() -> dict[str, Any]:
    """Get current agent state."""
    try:
        redis = await get_redis()
        state = await redis.client.get(KEY_AGENT_STATE)
        version = await redis.client.get(KEY_AGENT_VERSION)
        return {
            "state": state or AgentState.PAUSED.value,
            "version": version or "none",
        }
    except Exception as e:
        logger.error("agent_state_error", error=str(e))
        return {"state": AgentState.PAUSED.value, "version": "none"}


@router.get("/bets")
async def get_virtual_bets(
    limit: int = Query(50, ge=1, le=500),
    match_id: str | None = None,
) -> dict[str, Any]:
    """Get recent virtual bets."""
    try:
        async with get_session() as session:
            query = """
                SELECT id, placed_at, match_id, action, team, odds, stake,
                       settled_at, outcome, profit_loss, agent_version
                FROM virtual_bets
            """
            params: dict[str, Any] = {"limit": limit}

            if match_id:
                query += " WHERE match_id = :match_id"
                params["match_id"] = match_id

            query += " ORDER BY placed_at DESC LIMIT :limit"

            result = await session.execute(text(query), params)
            rows = result.fetchall()

            return {
                "count": len(rows),
                "bets": [
                    {
                        "id": row[0],
                        "placed_at": row[1].isoformat() if row[1] else None,
                        "match_id": row[2],
                        "action": row[3],
                        "team": row[4],
                        "odds": row[5],
                        "stake": row[6],
                        "settled_at": row[7].isoformat() if row[7] else None,
                        "outcome": row[8],
                        "profit_loss": row[9],
                        "agent_version": row[10],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("bets_query_error", error=str(e))
        return {"count": 0, "bets": [], "error": str(e)}


@router.get("/performance")
async def get_agent_performance() -> dict[str, Any]:
    """Get agent performance summary."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT
                        COUNT(*) as total_bets,
                        COUNT(*) FILTER (WHERE outcome = 'win') as wins,
                        COUNT(*) FILTER (WHERE outcome = 'loss') as losses,
                        SUM(profit_loss) as total_pnl,
                        AVG(odds) as avg_odds,
                        AVG(stake) as avg_stake
                    FROM virtual_bets
                    WHERE outcome IS NOT NULL
                """)
            )
            row = result.fetchone()
            if row and row[0]:
                total = row[0]
                wins = row[1] or 0
                return {
                    "total_bets": total,
                    "wins": wins,
                    "losses": row[2] or 0,
                    "win_rate": wins / max(total, 1),
                    "total_pnl": float(row[3] or 0),
                    "avg_odds": float(row[4] or 0),
                    "avg_stake": float(row[5] or 0),
                }
    except Exception as e:
        logger.error("performance_error", error=str(e))

    return {
        "total_bets": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "total_pnl": 0.0, "avg_odds": 0.0, "avg_stake": 0.0,
    }
