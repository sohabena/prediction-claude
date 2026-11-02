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


@router.get("/cricsheet-sync/status")
async def get_cricsheet_sync_status() -> dict[str, Any]:
    """Get the status of the weekly Cricsheet sync & retraining pipeline."""
    import json
    from pathlib import Path
    status_file = Path("models/specialists/weekly_sync_status.json")
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("error_reading_sync_status", error=str(e))
    return {
        "success": True,
        "new_matches_found": 0,
        "updated_specialists": [],
        "timestamp": None,
        "message": "Weekly sync has not run yet or status file is absent.",
    }


@router.post("/cricsheet-sync")
async def trigger_cricsheet_sync() -> dict[str, Any]:
    """
    Trigger on-demand Cricsheet synchronization and specialist model fine-tuning.
    Runs asynchronously in the background.
    """
    import asyncio
    from scripts.weekly_cricsheet_sync_and_retrain import run_weekly_sync_and_retrain

    async def _run():
        try:
            await run_weekly_sync_and_retrain(timesteps=4000)
        except Exception as e:
            logger.error("manual_cricsheet_sync_error", error=str(e))

    asyncio.create_task(_run())
    return {
        "status": "initiated",
        "message": "Weekly Cricsheet sync & retraining task initiated in background.",
    }

