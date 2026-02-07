"""Match data endpoints: live odds, historical data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, text

from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("router_matches")


@router.get("/active")
async def get_active_matches() -> dict[str, Any]:
    """Get currently active/live matches."""
    try:
        redis = await get_redis()
        data = await redis.get_json("active_matches")
        if data:
            return data
    except Exception as e:
        logger.error("active_matches_error", error=str(e))

    return {"matches": []}


@router.get("/odds/{match_id}")
async def get_match_odds(
    match_id: str,
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Get recent odds history for a match."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, back_home, lay_home, back_away, lay_away,
                           back_draw, lay_draw, implied_prob_home, implied_prob_away,
                           overround, is_live
                    FROM odds_ticks
                    WHERE match_id = :match_id
                    ORDER BY time DESC
                    LIMIT :limit
                """),
                {"match_id": match_id, "limit": limit},
            )
            rows = result.fetchall()
            return {
                "match_id": match_id,
                "count": len(rows),
                "ticks": [
                    {
                        "time": row[0].isoformat() if row[0] else None,
                        "back_home": row[1],
                        "lay_home": row[2],
                        "back_away": row[3],
                        "lay_away": row[4],
                        "back_draw": row[5],
                        "lay_draw": row[6],
                        "implied_prob_home": row[7],
                        "implied_prob_away": row[8],
                        "overround": row[9],
                        "is_live": row[10],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("odds_query_error", match_id=match_id, error=str(e))
        return {"match_id": match_id, "count": 0, "ticks": [], "error": str(e)}


@router.get("/context/{match_id}")
async def get_match_context(
    match_id: str,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Get match context history."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, score, wickets, overs, run_rate, req_run_rate,
                           innings, balls_remaining, batting_team, bowling_team, status
                    FROM match_context
                    WHERE match_id = :match_id
                    ORDER BY time DESC
                    LIMIT :limit
                """),
                {"match_id": match_id, "limit": limit},
            )
            rows = result.fetchall()
            return {
                "match_id": match_id,
                "count": len(rows),
                "context": [
                    {
                        "time": row[0].isoformat() if row[0] else None,
                        "score": row[1],
                        "wickets": row[2],
                        "overs": row[3],
                        "run_rate": row[4],
                        "req_run_rate": row[5],
                        "innings": row[6],
                        "balls_remaining": row[7],
                        "batting_team": row[8],
                        "bowling_team": row[9],
                        "status": row[10],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("context_query_error", match_id=match_id, error=str(e))
        return {"match_id": match_id, "count": 0, "context": [], "error": str(e)}
