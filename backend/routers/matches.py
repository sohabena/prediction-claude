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
                        COALESCE(tc.tick_count, 0) as tick_count,
                        COALESCE(mts.scrape_status, 'discovered') as scrape_status
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
                    "scrape_status": row[9],
                }
                for row in rows
            ]

            # Summary counts
            summary = {"pending": 0, "approved": 0, "rejected": 0}
            scrape_summary = {"discovered": 0, "scrape_approved": 0, "scrape_rejected": 0}
            for m in matches:
                s = m["training_status"]
                if s in summary:
                    summary[s] += 1
                ss = m["scrape_status"]
                if ss in scrape_summary:
                    scrape_summary[ss] += 1

            return {
                "matches": matches,
                "total": len(matches),
                "summary": summary,
                "scrape_summary": scrape_summary,
            }
    except Exception as e:
        logger.error("training_status_query_error", error=str(e))
        return {"matches": [], "total": 0, "summary": {}, "error": str(e)}


@router.patch("/{match_id}/approve-scrape")
async def approve_scrape(match_id: str) -> dict[str, Any]:
    """Approve a match for data scraping. Scraper will start collecting ticks."""
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

            record.scrape_status = "scrape_approved"

        logger.info("scrape_approved", match_id=match_id)
        return {"match_id": match_id, "scrape_status": "scrape_approved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("approve_scrape_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/reject-scrape")
async def reject_scrape(match_id: str) -> dict[str, Any]:
    """Reject a match from data scraping. No tick data will be collected."""
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

            record.scrape_status = "scrape_rejected"

        logger.info("scrape_rejected", match_id=match_id)
        return {"match_id": match_id, "scrape_status": "scrape_rejected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("reject_scrape_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{match_id}/validate")
async def validate_match_data(match_id: str) -> dict[str, Any]:
    """Run full data quality validation on a match before approving for training.

    Returns detailed quality metrics so the user can make an informed decision:
    - Quality score (0-1)
    - Completeness, odds jumps, duplicates, overround violations
    - Tick distribution: live vs pre-match, time gaps, unique price levels
    - Odds range summary
    - Recommendation: approve / review / reject
    """
    from dataclasses import asdict
    from features.data_quality import EpisodeQualityGate

    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, match_id, back_home, lay_home,
                           back_away, lay_away, back_draw, lay_draw,
                           implied_prob_home, implied_prob_away,
                           overround, is_live, team_home, team_away,
                           competition, volume_back_home, volume_back_away,
                           score, wickets, overs
                    FROM odds_ticks
                    WHERE match_id = :match_id
                    ORDER BY time ASC
                """),
                {"match_id": match_id},
            )
            rows = result.fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="No tick data found for this match")

        # Build episode for quality gate
        episode = [
            {
                "timestamp": r[0],
                "match_id": r[1],
                "back_home": r[2],
                "lay_home": r[3],
                "back_away": r[4],
                "lay_away": r[5],
                "back_draw": r[6],
                "lay_draw": r[7],
                "implied_prob_home": r[8],
                "implied_prob_away": r[9],
                "overround": r[10],
                "is_live": r[11],
            }
            for r in rows
        ]

        gate = EpisodeQualityGate()
        report = gate.evaluate(episode)

        # Extra detailed stats for manual review
        total = len(rows)
        live_count = sum(1 for r in rows if r[11])
        prematch_count = total - live_count
        has_volume = sum(1 for r in rows if r[15] is not None or r[16] is not None)
        has_score = sum(1 for r in rows if r[17] is not None)

        # Time span and gaps
        first_time = rows[0][0]
        last_time = rows[-1][0]
        duration_min = (last_time - first_time).total_seconds() / 60.0

        # Odds range
        home_prices = [r[2] for r in rows if r[2] is not None]
        away_prices = [r[4] for r in rows if r[4] is not None]

        # Missing lay prices
        missing_lay_home = sum(1 for r in rows if r[2] is not None and r[3] is None)
        missing_lay_away = sum(1 for r in rows if r[4] is not None and r[5] is None)

        # Recommendation logic
        score = report.quality_score
        if score >= 0.85 and total >= 50 and live_count >= 30:
            recommendation = "approve"
            reason = "Good quality data with sufficient live coverage"
        elif score >= 0.60 and total >= 20:
            recommendation = "review"
            reason = "Moderate quality — review issues before approving"
        else:
            recommendation = "reject"
            reason = "Poor quality or insufficient data for training"

        return {
            "match_id": match_id,
            "team_home": rows[0][12],
            "team_away": rows[0][13],
            "competition": rows[0][14],
            "quality_report": asdict(report),
            "recommendation": recommendation,
            "recommendation_reason": reason,
            "detailed_stats": {
                "total_ticks": total,
                "live_ticks": live_count,
                "prematch_ticks": prematch_count,
                "duration_minutes": round(duration_min, 1),
                "has_volume_data": has_volume,
                "has_score_data": has_score,
                "missing_lay_home": missing_lay_home,
                "missing_lay_away": missing_lay_away,
                "home_odds_range": {
                    "min": round(min(home_prices), 2) if home_prices else None,
                    "max": round(max(home_prices), 2) if home_prices else None,
                    "unique_prices": len(set(round(p, 2) for p in home_prices)),
                },
                "away_odds_range": {
                    "min": round(min(away_prices), 2) if away_prices else None,
                    "max": round(max(away_prices), 2) if away_prices else None,
                    "unique_prices": len(set(round(p, 2) for p in away_prices)),
                },
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("validate_match_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{match_id}/approve")
async def approve_match(
    match_id: str,
    skip_validation: bool = Query(False, description="Skip quality validation (force approve)"),
) -> dict[str, Any]:
    """Approve a match for RL training.

    By default, runs data quality validation first and rejects if quality score < 0.50.
    Pass skip_validation=true to force approve without quality check.
    """
    try:
        # Run validation unless explicitly skipped
        if not skip_validation:
            validation = await validate_match_data(match_id)
            quality_score = validation["quality_report"]["quality_score"]
            if quality_score < 0.50:
                return {
                    "match_id": match_id,
                    "training_status": "pending",
                    "error": f"Quality score too low: {quality_score:.2f}. "
                             f"Use skip_validation=true to force approve.",
                    "recommendation": validation["recommendation"],
                    "quality_report": validation["quality_report"],
                }

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


@router.delete("/{match_id}")
async def delete_match_data(match_id: str) -> dict[str, Any]:
    """Delete a rejected match and all its data (odds, context, results, training status).

    Only matches with training_status='rejected' can be deleted.
    Permanently removes odds_ticks, match_context, match_results, virtual_bets,
    and the match_training_status record.
    """
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
            if record.training_status != "rejected":
                raise HTTPException(
                    status_code=400,
                    detail="Only rejected matches can be deleted. Reject the match first.",
                )

            # Delete in order: child tables first, then parent
            _ALLOWED_DELETE_TABLES = frozenset({
                "odds_ticks",
                "match_context",
                "match_results",
                "virtual_bets",
            })
            tables = [
                "odds_ticks",
                "match_context",
                "match_results",
                "virtual_bets",
            ]
            params = {"match_id": match_id}
            for table in tables:
                assert table in _ALLOWED_DELETE_TABLES, f"Unexpected table: {table}"
                await session.execute(
                    text(f"DELETE FROM {table} WHERE match_id = :match_id"),
                    params,
                )

            # Delete the training status record
            await session.delete(record)

        logger.info("match_data_deleted", match_id=match_id)
        return {"match_id": match_id, "deleted": True}
    except HTTPException:
        raise
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
    """
    Manually submit a match result when automatic collection fails.

    This is a fallback mechanism for when automatic result detection fails
    or the match result cannot be automatically determined from odds data.

    Args:
        match_id: The match identifier.
        winner: Name of the winning team (empty for tie/draw/no_result).
        loser: Name of the losing team (empty for tie/draw/no_result).
        result_type: One of 'win', 'tie', 'draw', 'no_result', 'abandoned'.
        margin: Victory margin (e.g., '5 wickets', '23 runs').

    Returns:
        Confirmation of the stored result.
    """
    from backend.models.results import MatchResultRecord
    from shared.constants import CHANNEL_MATCH_RESULTS
    from shared.schemas import MatchResult

    try:
        # Validate match exists
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT team_home, team_away FROM odds_ticks
                    WHERE match_id = :match_id
                    LIMIT 1
                """),
                {"match_id": match_id},
            )
            row = result.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Match not found in odds data")

            team_home = row[0]
            team_away = row[1]

            # Check if result already exists
            existing = await session.execute(
                text("SELECT 1 FROM match_results WHERE match_id = :match_id"),
                {"match_id": match_id},
            )
            if existing.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail="Result already exists for this match"
                )

            # Create and store the result
            now = datetime.now(timezone.utc)
            record = MatchResultRecord(
                match_id=match_id,
                completed_at=now,
                winner=winner if result_type == "win" else "",
                loser=loser if result_type == "win" else "",
                result_type=result_type,
                margin=margin,
                team_home=team_home,
                team_away=team_away,
                source="manual",
            )
            session.add(record)

        # Publish to Redis for real-time consumers (settlement)
        match_result = MatchResult(
            match_id=match_id,
            completed_at=now,
            winner=winner if result_type == "win" else "",
            loser=loser if result_type == "win" else "",
            result_type=result_type,
            margin=margin,
            team_home=team_home,
            team_away=team_away,
            source="manual",
        )
        redis = await get_redis()
        await redis.publish_event(CHANNEL_MATCH_RESULTS, match_result.model_dump(mode="json"))

        logger.info(
            "manual_result_submitted",
            match_id=match_id,
            winner=winner,
            result_type=result_type,
        )
        return {
            "match_id": match_id,
            "result_type": result_type,
            "winner": winner,
            "loser": loser,
            "margin": margin,
            "source": "manual",
            "stored": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("manual_result_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{match_id}/closing_odds")
async def get_closing_odds(match_id: str) -> dict[str, Any]:
    """
    Get the closing odds for a match (last recorded tick before completion).

    This is used for CLV (Closing Line Value) calculation.
    """
    try:
        async with get_session() as session:
            # Get the last tick before match result was recorded
            result = await session.execute(
                text("""
                    SELECT o.time, o.back_home, o.lay_home, o.back_away, o.lay_away,
                           o.back_draw, o.lay_draw
                    FROM odds_ticks o
                    WHERE o.match_id = :match_id
                    ORDER BY o.time DESC
                    LIMIT 1
                """),
                {"match_id": match_id},
            )
            row = result.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="No odds data for this match")

            return {
                "match_id": match_id,
                "closing_time": row[0].isoformat() if row[0] else None,
                "back_home": row[1],
                "lay_home": row[2],
                "back_away": row[3],
                "lay_away": row[4],
                "back_draw": row[5],
                "lay_draw": row[6],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("closing_odds_error", match_id=match_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
