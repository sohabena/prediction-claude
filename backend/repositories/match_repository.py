"""Data access layer for match-related DB operations.

Encapsulates all raw SQL queries and ORM operations for odds_ticks,
match_context, match_training_status, match_results, and virtual_bets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.match_status import MatchTrainingStatus
from backend.models.results import MatchResultRecord
from shared.db import get_session


class MatchRepository:
    """Repository for match data access operations."""

    # ------------------------------------------------------------------
    # Odds Ticks
    # ------------------------------------------------------------------

    @staticmethod
    async def get_odds_ticks(match_id: str, limit: int) -> list[dict[str, Any]]:
        """Fetch recent odds ticks for a match, newest first."""
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
            return [
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
                for row in result.fetchall()
            ]

    @staticmethod
    async def get_full_odds_episode(match_id: str) -> list[Any]:
        """Fetch all ticks for a match ordered by time ASC (for quality gate)."""
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
            return result.fetchall()

    @staticmethod
    async def get_closing_odds(match_id: str) -> dict[str, Any] | None:
        """Get the last recorded odds tick for a match (closing odds)."""
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, back_home, lay_home, back_away, lay_away,
                           back_draw, lay_draw
                    FROM odds_ticks
                    WHERE match_id = :match_id
                    ORDER BY time DESC
                    LIMIT 1
                """),
                {"match_id": match_id},
            )
            row = result.fetchone()
            if row is None:
                return None
            return {
                "closing_time": row[0].isoformat() if row[0] else None,
                "back_home": row[1],
                "lay_home": row[2],
                "back_away": row[3],
                "lay_away": row[4],
                "back_draw": row[5],
                "lay_draw": row[6],
            }

    # ------------------------------------------------------------------
    # Match Context
    # ------------------------------------------------------------------

    @staticmethod
    async def get_match_context(match_id: str, limit: int) -> list[dict[str, Any]]:
        """Fetch match context history."""
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
            return [
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
                for row in result.fetchall()
            ]

    # ------------------------------------------------------------------
    # Training Status
    # ------------------------------------------------------------------

    @staticmethod
    async def get_all_training_statuses(
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all matches with training status, joined with tick counts."""
        async with get_session() as session:
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
                        mts.match_start_time,
                        COALESCE(mts.tick_count, 0) as tick_count,
                        COALESCE(mts.scrape_status, 'discovered') as scrape_status
                    FROM match_training_status mts
                    {where_clause}
                    ORDER BY mts.created_at DESC
                """),
                params,
            )
            return [
                {
                    "match_id": row[0],
                    "team_home": row[1],
                    "team_away": row[2],
                    "competition": row[3],
                    "training_status": row[4],
                    "auto_approved": row[5],
                    "approved_at": row[6].isoformat() if row[6] else None,
                    "created_at": row[7].isoformat() if row[7] else None,
                    "match_start_time": row[8].isoformat() if row[8] else None,
                    "tick_count": row[9],
                    "scrape_status": row[10],
                }
                for row in result.fetchall()
            ]

    @staticmethod
    async def get_training_status_record(
        match_id: str,
    ) -> MatchTrainingStatus | None:
        """Fetch a single MatchTrainingStatus record by match_id."""
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def update_scrape_status(match_id: str, status: str) -> None:
        """Update the scrape_status for a match."""
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise LookupError(f"Match {match_id} not found")
            record.scrape_status = status

    @staticmethod
    async def update_training_status(
        match_id: str,
        status: str,
        approved_at: datetime | None = None,
    ) -> None:
        """Update the training_status (and optionally approved_at) for a match."""
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise LookupError(f"Match {match_id} not found")
            record.training_status = status
            record.approved_at = approved_at

    # ------------------------------------------------------------------
    # Delete Match Data
    # ------------------------------------------------------------------

    @staticmethod
    async def delete_match_data(match_id: str) -> None:
        """Delete all data for a rejected match across all tables.

        Only works for matches with training_status='rejected'.
        Raises LookupError if not found, ValueError if not rejected.
        """
        async with get_session() as session:
            result = await session.execute(
                select(MatchTrainingStatus).where(
                    MatchTrainingStatus.match_id == match_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise LookupError(f"Match {match_id} not found")
            if record.training_status != "rejected":
                raise ValueError(
                    "Only rejected matches can be deleted. Reject the match first."
                )

            _ALLOWED_DELETE_TABLES = frozenset({
                "odds_ticks", "match_context", "match_results", "virtual_bets",
            })
            params = {"match_id": match_id}
            for table in _ALLOWED_DELETE_TABLES:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE match_id = :match_id"),
                    params,
                )
            await session.delete(record)

    @staticmethod
    async def delete_empty_matches() -> int:
        """Delete old matches with 0 ticks (older than 24 hours)."""
        async with get_session() as session:
            result = await session.execute(text("""
                DELETE FROM match_training_status 
                WHERE match_id IN (
                    SELECT m.match_id 
                    FROM match_training_status m 
                    LEFT JOIN odds_ticks o ON m.match_id = o.match_id 
                    GROUP BY m.match_id 
                    HAVING count(o.time) = 0
                ) 
                AND created_at < NOW() - INTERVAL '1 day'
            """))
            return result.rowcount

    # ------------------------------------------------------------------
    # Match Results
    # ------------------------------------------------------------------

    @staticmethod
    async def get_match_teams_from_odds(match_id: str) -> tuple[str, str] | None:
        """Get (team_home, team_away) from the first odds tick."""
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
            return (row[0], row[1]) if row else None

    @staticmethod
    async def result_exists(match_id: str) -> bool:
        """Check if a match result already exists."""
        async with get_session() as session:
            result = await session.execute(
                text("SELECT 1 FROM match_results WHERE match_id = :match_id"),
                {"match_id": match_id},
            )
            return result.fetchone() is not None

    @staticmethod
    async def get_result(match_id: str) -> MatchResultRecord | None:
        """Fetch a match result record by match_id."""
        async with get_session() as session:
            result = await session.execute(
                select(MatchResultRecord).where(
                    MatchResultRecord.match_id == match_id
                )
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def update_result(record: MatchResultRecord) -> None:
        """Update an existing match result record."""
        async with get_session() as session:
            existing = await session.execute(
                select(MatchResultRecord).where(
                    MatchResultRecord.match_id == record.match_id
                )
            )
            obj = existing.scalar_one_or_none()
            if obj:
                obj.winner = record.winner
                obj.loser = record.loser
                obj.result_type = record.result_type
                obj.completed_at = record.completed_at
                obj.source = record.source
                obj.margin = record.margin

    @staticmethod
    async def store_result(record: MatchResultRecord) -> None:
        """Persist a match result record."""
        async with get_session() as session:
            session.add(record)

    @staticmethod
    async def resettle_bets_for_match(
        match_id: str,
        winner: str,
        result_type: str,
    ) -> int:
        """Re-calculate and update outcomes for all bets for a specific match."""
        async with get_session() as session:
            # 1. Fetch all bets for this match
            result = await session.execute(
                text("""
                    SELECT placed_at, match_id, action, team, odds, stake
                    FROM virtual_bets
                    WHERE match_id = :match_id
                """),
                {"match_id": match_id},
            )
            bets = result.fetchall()
            
            count = 0
            for row in bets:
                placed_at, mid, action, team, odds, stake = row
                
                # Logic for P&L calculation (sync with SettlementEngine)
                if result_type == "win":
                    is_back = "BACK" in action
                    team_won = (str(team).strip().lower() == str(winner).strip().lower())
                    
                    if is_back:
                        if team_won:
                            outcome = "win"
                            pnl = float(stake) * (float(odds) - 1.0)
                        else:
                            outcome = "loss"
                            pnl = -float(stake)
                    else:  # LAY
                        if team_won:
                            outcome = "loss"
                            pnl = -float(stake) * (float(odds) - 1.0)
                        else:
                            outcome = "win"
                            pnl = float(stake)
                else:
                    # no_result, tie, abandoned, etc.
                    outcome = "void"
                    pnl = 0.0

                # 2. Update the bet
                await session.execute(
                    text("""
                        UPDATE virtual_bets
                        SET outcome = :outcome,
                            profit_loss = :pnl,
                            settled_at = :now
                        WHERE match_id = :match_id AND placed_at = :placed_at
                    """),
                    {
                        "outcome": outcome,
                        "pnl": pnl,
                        "now": datetime.now(timezone.utc),
                        "match_id": mid,
                        "placed_at": placed_at,
                    },
                )
                count += 1
            
            return count
