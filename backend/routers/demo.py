"""
Demo / Watch mode endpoints.

Allows users to select a live match to watch virtual bets in real-time,
regardless of graduation. Graduation continues to gate when users can follow.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from shared.constants import KEY_WATCHED_MATCH_ID
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("router_demo")


@router.put("/watch")
async def set_watched_match(
    match_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """Set the match to watch. When set, live loop only places bets on this match."""
    try:
        redis = await get_redis()
        await redis.client.set(KEY_WATCHED_MATCH_ID, match_id)
        logger.info("watched_match_set", match_id=match_id)
        return {"success": True, "match_id": match_id}
    except Exception as e:
        logger.error("set_watched_match_error", error=str(e))
        return {"success": False, "error": str(e)}


@router.delete("/watch")
async def clear_watched_match() -> dict[str, Any]:
    """Clear the watched match. Live loop will process all matches again."""
    try:
        redis = await get_redis()
        await redis.client.delete(KEY_WATCHED_MATCH_ID)
        logger.info("watched_match_cleared")
        return {"success": True, "match_id": None}
    except Exception as e:
        logger.error("clear_watched_match_error", error=str(e))
        return {"success": False, "error": str(e)}


@router.get("/watch")
async def get_watched_match() -> dict[str, Any]:
    """Get the currently watched match (if any)."""
    try:
        redis = await get_redis()
        match_id = await redis.client.get(KEY_WATCHED_MATCH_ID)
        return {"match_id": match_id, "watching": match_id is not None}
    except Exception as e:
        logger.error("get_watched_match_error", error=str(e))
        return {"match_id": None, "watching": False, "error": str(e)}
