"""Match data endpoints: live odds, historical data, training approval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, text, update

from backend.models.match_status import MatchTrainingStatus
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


# ============================================================
# Training Approval Endpoints
# ============================================================


@router.get("/training-status")
async def get_training_status(
    status_filter: Optional[str] = Query(None, alias="status"),
) -> dict[str, Any]:
    """List all matches with their training approval status.

    Optional query param ``status`` filters by: pending, approved, rejected.
    Also returns tick counts for each match.
    """
    try:
        async with get_session() as session:
            # Build query - join with odds_ticks to get tick counts
            where_clause = ""
            params: dict[str, Any] = {}
            if status_filter and status_filter in ("pending", "approved", "rejected"):
                where_clause = "WHERE mts.training_status = :status"
                params["status"] = status_filter

            result = await session.execute(
                text(f"""
                    SELECT
                        mts.match_id,
                        mts.team_home,
                        mts.team_away,
                        mts.competition,
                        mts.training_status,
                        mts.auto_approved,
                        mts.approved_at,
                        mts.created_at,
                        COALESCE(tc.tick_count, 0) as tick_count
                    FROM match_training_status mts
                    LEFT JOIN (
                        SELECT match_id, COUNT(*) as tick_count
                        FROM odds_ticks
                        GROUP BY match_id
                    ) tc ON tc.match_id = mts.match_id
                    {where_clause}
                    ORDER BY mts.created_at DESC
                """),
                params,
            )
            rows = result.fetchall()

            matches = [
                {
                    "match_id": row[0],
                    "team_home": row[1],
                    "team_away": row[2],
                    "competition": row[3],
                    "training_status": row[4],
                    "auto_approved": row[5],
                    "approved_at": row[6].isoformat() if row[6] else None,
                    "created_at": row[7].isoformat() if row[7] else None,
                    "tick_count": row[8],
                }
                for row in rows
            ]

            # Summary counts
            summary = {"pending": 0, "approved": 0, "rejected": 0}
            for m in matches:
                s = m["training_status"]
                if s in summary:
                    summary[s] += 1

            return {
                "matches": matches,
                "total": len(matches),
                "summary": summary,
            }
    except Exception as e:
        logger.error("training_status_query_error", error=str(e))
        return {"matches": [], "total": 0, "summary": {}, "error": str(e)}


@router.patch("/{match_id}/approve")
async def approve_match(match_id: str) -> dict[str, Any]:
    """Approve a match for RL training."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail="Match not found")

            record.training_status = "approved"
            record.approved_at = datetime.now(timezone.utc)

        logger.info("match_approved", match_id=match_id)
        return {"match_id": match_id, "training_status": "approved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("approve_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/reject")
async def reject_match(match_id: str) -> dict[str, Any]:
    """Reject a match from RL training."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail="Match not found")

            record.training_status = "rejected"
            record.approved_at = None

        logger.info("match_rejected", match_id=match_id)
        return {"match_id": match_id, "training_status": "rejected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("reject_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
