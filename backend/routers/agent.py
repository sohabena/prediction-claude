"""Agent state and virtual betting endpoints.

Thin HTTP layer — all business logic delegated to AgentService.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services.agent_service import AgentService
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_agent")
svc = AgentService()


@router.get("/state")
async def get_agent_state() -> dict[str, Any]:
    """Get current agent state."""
    return await svc.get_agent_state()


@router.get("/bets")
async def get_virtual_bets(
    limit: int = Query(50, ge=1, le=500),
    match_id: str | None = None,
) -> dict[str, Any]:
    """Get recent virtual bets."""
    try:
        return await svc.get_virtual_bets(limit, match_id)
    except Exception as e:
        logger.error("bets_query_error", error=str(e))
        return {"count": 0, "bets": [], "error": str(e)}


@router.get("/performance")
async def get_agent_performance() -> dict[str, Any]:
    """Get agent performance summary."""
    try:
        return await svc.get_performance()
    except Exception as e:
        logger.error("performance_error", error=str(e))
        return {
            "total_bets": 0, "settled_bets": 0, "pending_bets": 0,
            "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "avg_odds": 0.0, "avg_stake": 0.0,
        }


@router.post("/settle-pending")
async def settle_pending_bets() -> dict[str, Any]:
    """Retroactively settle all pending virtual bets that have match results."""
    try:
        return await svc.settle_pending_bets()
    except Exception as e:
        logger.error("settle_pending_error", error=str(e))
        return {"settled": 0, "total_pnl": 0.0, "errors": 1, "error": str(e)}
