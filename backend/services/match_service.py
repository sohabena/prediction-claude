"""Business logic service for match operations.

Orchestrates repository calls, validation, and side-effects for match
data management, training approval, and manual result submission.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from backend.models.results import MatchResultRecord
from backend.repositories.match_repository import MatchRepository
from features.data_quality import EpisodeQualityGate
from shared.constants import CHANNEL_MATCH_RESULTS, KEY_ACTIVE_MATCHES
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import MatchResult

logger = setup_logging("service_matches")
repo = MatchRepository()


class MatchService:
    """Business logic for match operations."""

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @staticmethod
    async def get_active_matches() -> dict[str, Any]:
        """Get currently active/live matches from Redis, enriched with DB status and tick count."""
        try:
            redis = await get_redis()
            data = await redis.get_json(KEY_ACTIVE_MATCHES)
            if not data or not data.get("matches"):
                return {"matches": []}

            matches = data.get("matches", [])
            match_ids = [m["match_id"] for m in matches if isinstance(m, dict) and "match_id" in m]

            if match_ids:
                async with get_session() as session:
                    from sqlalchemy import text
                    res = await session.execute(
                        text("""
                            SELECT mts.match_id, mts.scrape_status, mts.training_status,
                                   mts.auto_approved, mts.match_start_time,
                                   COALESCE(tc.tick_count, 0) as tick_count
                            FROM match_training_status mts
                            LEFT JOIN (
                                SELECT match_id, COUNT(*) as tick_count
                                FROM odds_ticks
                                GROUP BY match_id
                            ) tc ON tc.match_id = mts.match_id
                            WHERE mts.match_id = ANY(:ids)
                        """),
                        {"ids": match_ids},
                    )
                    status_map = {
                        r[0]: {
                            "scrape_status": r[1],
                            "training_status": r[2],
                            "auto_approved": r[3],
                            "match_start_time": r[4].isoformat() if r[4] else None,
                            "tick_count": r[5],
                        }
                        for r in res.fetchall()
                    }
                    for m in matches:
                        mid = m.get("match_id")
                        if mid in status_map:
                            m.update(status_map[mid])
                        else:
                            m.setdefault("scrape_status", "discovered")
                            m.setdefault("training_status", "pending")
                            m.setdefault("auto_approved", False)
                            m.setdefault("tick_count", 0)

            return {"matches": matches, "updated_at": data.get("updated_at")}
        except Exception as e:
            logger.error("active_matches_error", error=str(e))
        return {"matches": []}

    @staticmethod
    async def get_match_odds(match_id: str, limit: int) -> dict[str, Any]:
        """Get recent odds history for a match."""
        ticks = await repo.get_odds_ticks(match_id, limit)
        return {"match_id": match_id, "count": len(ticks), "ticks": ticks}

    @staticmethod
    async def get_match_context(match_id: str, limit: int) -> dict[str, Any]:
        """Get match context history."""
        context = await repo.get_match_context(match_id, limit)
        return {"match_id": match_id, "count": len(context), "context": context}

    @staticmethod
    async def get_training_status(
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """List all matches with their training approval status."""
        matches = await repo.get_all_training_statuses(status_filter)

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

    @staticmethod
    async def get_closing_odds(match_id: str) -> dict[str, Any]:
        """Get the closing odds for a match."""
        data = await repo.get_closing_odds(match_id)
        if data is None:
            raise LookupError("No odds data for this match")
        return {"match_id": match_id, **data}

    # ------------------------------------------------------------------
    # Scrape approval
    # ------------------------------------------------------------------

    @staticmethod
    async def approve_scrape(match_id: str) -> dict[str, Any]:
        """Approve a match for data scraping."""
        await repo.update_scrape_status(match_id, "scrape_approved")
        logger.info("scrape_approved", match_id=match_id)
        return {"match_id": match_id, "scrape_status": "scrape_approved"}

    @staticmethod
    async def reject_scrape(match_id: str) -> dict[str, Any]:
        """Reject a match from data scraping."""
        await repo.update_scrape_status(match_id, "scrape_rejected")
        logger.info("scrape_rejected", match_id=match_id)
        return {"match_id": match_id, "scrape_status": "scrape_rejected"}

    # ------------------------------------------------------------------
    # Validation & Training approval
    # ------------------------------------------------------------------

    @staticmethod
    async def validate_match_data(match_id: str) -> dict[str, Any]:
        """Run full data quality validation on a match."""
        rows = await repo.get_full_odds_episode(match_id)
        if not rows:
            raise LookupError("No tick data found for this match")

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

        total = len(rows)
        live_count = sum(1 for r in rows if r[11])
        prematch_count = total - live_count
        has_volume = sum(1 for r in rows if r[15] is not None or r[16] is not None)
        has_score = sum(1 for r in rows if r[17] is not None)

        first_time = rows[0][0]
        last_time = rows[-1][0]
        duration_min = (last_time - first_time).total_seconds() / 60.0

        home_prices = [r[2] for r in rows if r[2] is not None]
        away_prices = [r[4] for r in rows if r[4] is not None]
        missing_lay_home = sum(1 for r in rows if r[2] is not None and r[3] is None)
        missing_lay_away = sum(1 for r in rows if r[4] is not None and r[5] is None)

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

    @staticmethod
    async def approve_match(
        match_id: str, skip_validation: bool = False,
    ) -> dict[str, Any]:
        """Approve a match for RL training, optionally with validation."""
        if not skip_validation:
            validation = await MatchService.validate_match_data(match_id)
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

        await repo.update_training_status(
            match_id, "approved", approved_at=datetime.now(timezone.utc),
        )
        logger.info("match_approved", match_id=match_id)
        return {"match_id": match_id, "training_status": "approved"}

    @staticmethod
    async def reject_match(match_id: str) -> dict[str, Any]:
        """Reject a match from RL training."""
        await repo.update_training_status(match_id, "rejected", approved_at=None)
        logger.info("match_rejected", match_id=match_id)
        return {"match_id": match_id, "training_status": "rejected"}

    @staticmethod
    async def delete_match(match_id: str) -> dict[str, Any]:
        """Delete a rejected match and all its data."""
        await repo.delete_match_data(match_id)
        logger.info("match_data_deleted", match_id=match_id)
        return {"match_id": match_id, "deleted": True}

    @staticmethod
    async def delete_empty_matches() -> dict[str, Any]:
        """Delete all old matches with 0 ticks."""
        count = await repo.delete_empty_matches()
        logger.info("empty_matches_deleted", count=count)
        return {"deleted_count": count}

    # ------------------------------------------------------------------
    # Manual result submission
    # ------------------------------------------------------------------

    @staticmethod
    async def submit_manual_result(
        match_id: str,
        winner: str,
        loser: str,
        result_type: str,
        margin: str,
    ) -> dict[str, Any]:
        """Submit and publish a manual match result, and re-settle existing bets."""
        teams = await repo.get_match_teams_from_odds(match_id)
        if teams is None:
            raise LookupError("Match not found in odds data")
        team_home, team_away = teams

        # If result already exists, we might want to overwrite it if it's 'no_result'
        existing = await repo.get_result(match_id)
        if existing and existing.result_type != "no_result":
            raise ValueError(f"Match already has a definitive result: {existing.result_type}")

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
        
        if existing:
            await repo.update_result(record)
            logger.info("manual_result_updated", match_id=match_id)
        else:
            await repo.store_result(record)
            logger.info("manual_result_stored", match_id=match_id)

        # Re-settle ANY bets in the DB for this match (un-voiding if necessary)
        # This fixes the 'VOID' stuck bets issue
        settled_count = await repo.resettle_bets_for_match(
            match_id=match_id,
            winner=winner if result_type == "win" else "",
            result_type=result_type,
        )

        # Publish to Redis for real-time consumers
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
        await redis.publish_event(
            CHANNEL_MATCH_RESULTS, match_result.model_dump(mode="json"),
        )

        logger.info(
            "manual_result_processed",
            match_id=match_id,
            winner=winner,
            settled_bets=settled_count,
        )
        return {
            "match_id": match_id,
            "result_type": result_type,
            "winner": winner,
            "loser": loser,
            "margin": margin,
            "source": "manual",
            "stored": True,
            "settled_bets": settled_count,
        }
