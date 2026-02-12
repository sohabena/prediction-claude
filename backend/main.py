"""
PHOENIX FastAPI Backend
Main application entry point with WebSocket support.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import advisor, agent, demo, graduation, health, matches, training, websocket_router
from shared.config import get_settings
from shared.db import close_database
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("backend")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    logger.info("backend_starting", port=settings.api.port)

    # Connect to Redis on startup
    try:
        redis = await get_redis()
        logger.info("redis_connected")
    except Exception as e:
        logger.warning("redis_connection_failed", error=str(e))

    yield

    # Shutdown
    logger.info("backend_shutting_down")
    try:
        redis = await get_redis()
        await redis.close()
    except Exception:
        pass
    await close_database()


app = FastAPI(
    title="PHOENIX Cricket Betting RL",
    description="Automated cricket betting system powered by Reinforcement Learning",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(matches.router, prefix="/api/matches", tags=["Matches"])
app.include_router(training.router, prefix="/api/training", tags=["Training"])
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
app.include_router(graduation.router, prefix="/api/graduation", tags=["Graduation"])
app.include_router(advisor.router, prefix="/api/advisor", tags=["Advisor"])
app.include_router(demo.router, prefix="/api/demo", tags=["Demo"])
app.include_router(websocket_router.router, tags=["WebSocket"])


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": "PHOENIX Backend",
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
