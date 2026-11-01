"""Training metrics, progress, and data quality endpoints.

Thin HTTP layer — all business logic delegated to TrainingService.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.services.training_service import TrainingService
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_training")
svc = TrainingService()


@router.get("/steps")
async def get_training_progress() -> dict[str, Any]:
    """Get current offline training progress."""
    return await svc.get_training_progress()


@router.get("/metrics")
async def get_training_metrics(
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Get recent training metrics."""
    try:
        return await svc.get_training_metrics(limit)
    except Exception as e:
        logger.error("training_metrics_error", error=str(e))
        return {"count": 0, "metrics": [], "error": str(e)}


@router.get("/summary")
async def get_training_summary() -> dict[str, Any]:
    """Get a summary of the latest training state."""
    return await svc.get_training_summary()


@router.get("/data-quality")
async def get_data_quality(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Run the episode quality gate on recent approved matches."""
    try:
        return await svc.get_data_quality(limit)
    except Exception as e:
        logger.error("data_quality_error", error=str(e))
        return {
            "total_episodes": 0, "passed": 0, "failed": 0,
            "avg_quality_score": 0.0, "reports": [], "error": str(e),
        }
