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

from backend.routers import advisor, agent, data, demo, graduation, health, matches, paper, training, websocket_router
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
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics (graceful no-op if prometheus_client not installed)
from backend.observability.metrics import add_metrics_middleware
add_metrics_middleware(app)


from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception processing %s %s: %s", request.method, request.url, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )

# Include routers
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(matches.router, prefix="/api/matches", tags=["Matches"])
app.include_router(training.router, prefix="/api/training", tags=["Training"])
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
app.include_router(graduation.router, prefix="/api/graduation", tags=["Graduation"])
app.include_router(advisor.router, prefix="/api/advisor", tags=["Advisor"])
app.include_router(demo.router, prefix="/api/demo", tags=["Demo"])
app.include_router(paper.router, prefix="/api/paper", tags=["Paper"])
app.include_router(data.router, prefix="/api/data", tags=["Data"])
app.include_router(websocket_router.router, tags=["WebSocket"])


@app.get("/api/signals")
async def get_signals_alias(limit: int = 150, match_id: str | None = None):
    return await paper.get_signals(limit=limit, match_id=match_id)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": "PHOENIX Backend",
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
