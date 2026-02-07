"""Graduation status and history endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from shared.constants import KEY_GRADUATION_STATUS
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("router_graduation")


@router.get("/status")
async def get_graduation_status() -> dict[str, Any]:
    """Get current graduation status."""
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_GRADUATION_STATUS)
        if data:
            return data
    except Exception as e:
        logger.error("graduation_status_error", error=str(e))

    return {
        "ready": False,
        "consecutive_days": 0,
        "required_days": 14,
        "criteria": [],
    }


@router.get("/history")
async def get_graduation_history(
    limit: int = Query(30, ge=1, le=90),
) -> dict[str, Any]:
    """Get graduation snapshot history."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, win_rate, roi, sharpe_ratio, max_drawdown,
                           profitable_days, total_bets, all_criteria_met,
                           consecutive_days
                    FROM graduation_snapshots
                    ORDER BY time DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            rows = result.fetchall()
            return {
                "count": len(rows),
                "snapshots": [
                    {
                        "time": row[0].isoformat() if row[0] else None,
                        "win_rate": row[1],
                        "roi": row[2],
                        "sharpe_ratio": row[3],
                        "max_drawdown": row[4],
                        "profitable_days": row[5],
                        "total_bets": row[6],
                        "all_criteria_met": row[7],
                        "consecutive_days": row[8],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("graduation_history_error", error=str(e))
        return {"count": 0, "snapshots": [], "error": str(e)}
