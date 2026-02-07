"""Health check endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from shared.db import check_database_health
from shared.redis_client import get_redis
from shared.schemas import AgentState, HealthStatus

router = APIRouter()


@router.get("/health", response_model=HealthStatus)
async def health_check() -> HealthStatus:
    """System health check."""
    redis_ok = False
    db_ok = False

    try:
        redis = await get_redis()
        redis_ok = await redis.health_check()
    except Exception:
        pass

    try:
        db_ok = await check_database_health()
    except Exception:
        pass

    status = "healthy"
    if not redis_ok or not db_ok:
        status = "degraded"
    if not redis_ok and not db_ok:
        status = "unhealthy"

    return HealthStatus(
        status=status,
        redis="connected" if redis_ok else "disconnected",
        database="connected" if db_ok else "disconnected",
        agent_state=AgentState.PAUSED,
        timestamp=datetime.now(timezone.utc),
    )
