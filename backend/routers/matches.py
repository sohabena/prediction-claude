"""Match data endpoints: live odds, historical data, training approval.

Thin HTTP layer — all business logic delegated to MatchService.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.match_service import MatchService
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_matches")
svc = MatchService()


class ScraperSettingsUpdate(BaseModel):
    auto_approve: Optional[bool] = None
    international_only: Optional[bool] = None


@router.get("/settings")
async def get_scraper_settings() -> dict[str, Any]:
    """Get dynamic scraper settings."""
    try:
        return await svc.get_scraper_settings()
    except Exception as e:
        logger.error("get_scraper_settings_error", error=str(e))
        return {"auto_approve": False, "international_only": True, "error": str(e)}


@router.post("/settings")
async def update_scraper_settings(settings: ScraperSettingsUpdate) -> dict[str, Any]:
    """Update dynamic scraper settings."""
    try:
        updates = settings.model_dump(exclude_unset=True)
        return await svc.update_scraper_settings(updates)
    except Exception as e:
        logger.error("update_scraper_settings_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
async def get_active_matches() -> dict[str, Any]:
    """Get currently active/live matches."""
    return await svc.get_active_matches()


@router.get("/odds/{match_id}")
async def get_match_odds(
    match_id: str,
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Get recent odds history for a match."""
    try:
        return await svc.get_match_odds(match_id, limit)
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
        return await svc.get_match_context(match_id, limit)
    except Exception as e:
        logger.error("context_query_error", match_id=match_id, error=str(e))
        return {"match_id": match_id, "count": 0, "context": [], "error": str(e)}


# ============================================================
# Training Approval Endpoints
# ============================================================


@router.get("/training-status")
async def get_training_status(
    status_filter: Optional[str] = Query(None, alias="status"),
) -> dict[str, Any]:
    """List all matches with their training approval status."""
    try:
        return await svc.get_training_status(status_filter)
    except Exception as e:
        logger.error("training_status_query_error", error=str(e))
        return {"matches": [], "total": 0, "summary": {}, "error": str(e)}


@router.patch("/{match_id}/approve-scrape")
async def approve_scrape(match_id: str) -> dict[str, Any]:
    """Approve a match for data scraping."""
    try:
        return await svc.approve_scrape(match_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Match not found")
    except Exception as e:
        logger.error("approve_scrape_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/reject-scrape")
async def reject_scrape(match_id: str) -> dict[str, Any]:
    """Reject a match from data scraping."""
    try:
        return await svc.reject_scrape(match_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Match not found")
    except Exception as e:
        logger.error("reject_scrape_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{match_id}/validate")
async def validate_match_data(match_id: str) -> dict[str, Any]:
    """Run full data quality validation on a match before approving for training."""
    try:
        return await svc.validate_match_data(match_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("validate_match_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/approve")
async def approve_match(
    match_id: str,
    skip_validation: bool = Query(False, description="Skip quality validation"),
) -> dict[str, Any]:
    """Approve a match for RL training."""
    try:
        return await svc.approve_match(match_id, skip_validation)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("approve_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/reject")
async def reject_match(match_id: str) -> dict[str, Any]:
    """Reject a match from RL training."""
    try:
        return await svc.reject_match(match_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Match not found")
    except Exception as e:
        logger.error("reject_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cleanup/empty")
async def cleanup_empty_matches() -> dict[str, Any]:
    """Delete all old matches with 0 ticks."""
    try:
        return await svc.delete_empty_matches()
    except Exception as e:
        logger.error("cleanup_empty_matches_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{match_id}")
async def delete_match_data(match_id: str) -> dict[str, Any]:
    """Delete a rejected match and all its data."""
    try:
        return await svc.delete_match(match_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Match not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("delete_match_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{match_id}/result")
async def submit_manual_result(
    match_id: str,
    winner: str,
    loser: str,
    result_type: str = Query("win", pattern="^(win|tie|draw|no_result|abandoned)$"),
    margin: str = "",
) -> dict[str, Any]:
    """Manually submit a match result when automatic collection fails."""
    try:
        return await svc.submit_manual_result(
            match_id, winner, loser, result_type, margin,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("manual_result_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{match_id}/closing_odds")
async def get_closing_odds(match_id: str) -> dict[str, Any]:
    """Get the closing odds for a match."""
    try:
        return await svc.get_closing_odds(match_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("closing_odds_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
