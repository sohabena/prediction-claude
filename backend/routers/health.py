"""Health check endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from shared.constants import KEY_ACTIVE_MATCHES, KEY_AGENT_STATE, KEY_AGENT_VERSION, KEY_ORCHESTRATOR_STATE
from shared.db import check_database_health
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import AgentState, HealthStatus

router = APIRouter()
logger = setup_logging("router_health")


@router.get("/health", response_model=HealthStatus)
async def health_check() -> HealthStatus:
    """System health check."""
    redis_ok = False
    db_ok = False
    agent_state = AgentState.PAUSED
    agent_version = ""
    scraper_status = "unknown"
    scraper_last_update: float | None = None

    try:
        redis = await get_redis()
        redis_ok = await redis.health_check()

        if redis_ok:
            # Primary: read orchestrator state (always up-to-date)
            orch_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
            if isinstance(orch_data, dict):
                orch_state = orch_data.get("state", "")
                # Map orchestrator state to AgentState enum
                state_map = {
                    "accumulating": AgentState.PAUSED,
                    "offline_training": AgentState.TRAINING,
                    "online_training": AgentState.TRAINING,
                    "virtual_trading": AgentState.EVALUATING,
                    "graduated": AgentState.LIVE,
                }
                agent_state = state_map.get(orch_state, AgentState.PAUSED)
                agent_version = str(orch_data.get("model_version", ""))
            else:
                # Fallback: read agent:state for backward compat
                state_data = await redis.get_json(KEY_AGENT_STATE)
                if isinstance(state_data, dict):
                    mode = state_data.get("mode", "paused")
                    try:
                        agent_state = AgentState(mode)
                    except ValueError:
                        agent_state = AgentState.PAUSED
                version_data = await redis.get_json(KEY_AGENT_VERSION)
                if isinstance(version_data, dict):
                    agent_version = str(version_data.get("version", ""))

            # Check scraper status via active_matches timestamp
            active = await redis.get_json(KEY_ACTIVE_MATCHES)
            if isinstance(active, dict):
                updated = active.get("updated_at")
                if updated:
                    try:
                        updated_dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                        age = (datetime.now(timezone.utc) - updated_dt).total_seconds()
                        scraper_last_update = round(age, 1)
                        scraper_status = "active" if age < 60 else "stale"
                    except (ValueError, TypeError):
                        scraper_status = "unknown"
                else:
                    scraper_status = "no_data"
    except Exception as e:
        logger.warning("health_redis_error", error=str(e))

    try:
        db_ok = await check_database_health()
    except Exception as e:
        logger.warning("health_db_error", error=str(e))

    status = "healthy"
    if not redis_ok or not db_ok:
        status = "degraded"
    if not redis_ok and not db_ok:
        status = "unhealthy"

    return HealthStatus(
        status=status,
        redis="connected" if redis_ok else "disconnected",
        database="connected" if db_ok else "disconnected",
        scraper_status=scraper_status,
        scraper_last_update_seconds=scraper_last_update,
        agent_state=agent_state,
        agent_version=agent_version,
        timestamp=datetime.now(timezone.utc),
    )
