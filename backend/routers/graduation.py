"""Graduation status and history endpoints.

Thin HTTP layer — graduation status from Redis, history from TrainingService.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services.training_service import TrainingService
from shared.constants import KEY_GRADUATION_STATUS
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("router_graduation")
svc = TrainingService()


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
        return await svc.get_graduation_history(limit)
    except Exception as e:
        logger.error("graduation_history_error", error=str(e))
        return {"count": 0, "snapshots": [], "error": str(e)}
