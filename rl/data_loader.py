"""
Data loader: queries TimescaleDB and groups odds ticks into training episodes.
Each completed match becomes one episode (list of tick dicts) for the RL environment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from shared.config import get_settings
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("data_loader")


class MatchDataLoader:
    """
    Loads historical match data from TimescaleDB for RL training.

    Groups odds_ticks by match_id, left-joins match_context,
    and returns episodes suitable for CricketBettingEnv.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def load_completed_matches(
        self,
        min_ticks: int | None = None,
        days_back: int | None = None,
    ) -> list[list[dict[str, Any]]]:
        """
        Load completed matches as training episodes.

        Args:
            min_ticks: Minimum ticks per match (default from config).
            days_back: How many days of data to load (default from config).

        Returns:
            List of episodes. Each episode is a list of tick dicts
            sorted by time ascending.
        """
        min_ticks = min_ticks or self.settings.rl.min_ticks_per_match
        days_back = days_back or self.settings.rl.data_lookback_days

        logger.info("loading_matches", min_ticks=min_ticks, days_back=days_back)

        # Step 1: Find match_ids with enough ticks
        match_ids = await self._get_qualifying_match_ids(min_ticks, days_back)
        logger.info("qualifying_matches_found", count=len(match_ids))

        if not match_ids:
            return []

        # Step 2: Load ticks for each match
        episodes: list[list[dict[str, Any]]] = []
        for match_id in match_ids:
            episode = await self._load_match_episode(match_id)
            if episode:
                episodes.append(episode)

        logger.info(
            "matches_loaded",
            total_episodes=len(episodes),
            total_ticks=sum(len(ep) for ep in episodes),
        )
        return episodes

    async def _get_qualifying_match_ids(
        self, min_ticks: int, days_back: int
    ) -> list[str]:
        """Get match_ids that have enough data points."""
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT match_id, COUNT(*) as tick_count
                        FROM odds_ticks
                        WHERE time > NOW() - MAKE_INTERVAL(days => :days_back)
                        GROUP BY match_id
                        HAVING COUNT(*) >= :min_ticks
                        ORDER BY MIN(time) ASC
                    """),
                    {"min_ticks": min_ticks, "days_back": days_back},
                )
                rows = result.fetchall()
                return [row[0] for row in rows]
        except Exception as e:
            logger.error("match_id_query_error", error=str(e))
            return []

    async def _load_match_episode(self, match_id: str) -> list[dict[str, Any]]:
        """Load all ticks for a single match, enriched with context data."""
        try:
            async with get_session() as session:
                # Load odds ticks
                result = await session.execute(
                    text("""
                        SELECT
                            o.time, o.match_id, o.team_home, o.team_away,
                            o.competition, o.back_home, o.lay_home,
                            o.back_draw, o.lay_draw, o.back_away, o.lay_away,
                            o.implied_prob_home, o.implied_prob_away,
                            o.overround, o.is_live,
                            c.score, c.wickets, c.overs, c.run_rate,
                            c.req_run_rate, c.innings, c.balls_remaining,
                            c.batting_team, c.bowling_team, c.status,
                            c.match_format
                        FROM odds_ticks o
                        LEFT JOIN LATERAL (
                            SELECT * FROM match_context mc
                            WHERE mc.match_id = o.match_id
                              AND mc.time <= o.time
                            ORDER BY mc.time DESC
                            LIMIT 1
                        ) c ON true
                        WHERE o.match_id = :match_id
                        ORDER BY o.time ASC
                    """),
                    {"match_id": match_id},
                )
                rows = result.fetchall()

                episode: list[dict[str, Any]] = []
                for row in rows:
                    tick: dict[str, Any] = {
                        "timestamp": row[0],
                        "match_id": row[1],
                        "team_home": row[2],
                        "team_away": row[3],
                        "competition": row[4],
                        "back_home": row[5],
                        "lay_home": row[6],
                        "back_draw": row[7],
                        "lay_draw": row[8],
                        "back_away": row[9],
                        "lay_away": row[10],
                        "implied_prob_home": row[11],
                        "implied_prob_away": row[12],
                        "overround": row[13],
                        "is_live": row[14],
                        # Match context (may be None)
                        "score": row[15] or 0,
                        "wickets": row[16] or 0,
                        "overs": row[17] or 0.0,
                        "run_rate": row[18] or 0.0,
                        "req_run_rate": row[19] or 0.0,
                        "innings": row[20] or 1,
                        "balls_remaining": row[21] or 0,
                        "batting_team": row[22] or "",
                        "bowling_team": row[23] or "",
                        "status": row[24] or "scheduled",
                        "match_format": row[25] or "T20",
                    }
                    episode.append(tick)

                return episode

        except Exception as e:
            logger.error("episode_load_error", match_id=match_id, error=str(e))
            return []

    async def get_accumulation_stats(self) -> dict[str, Any]:
        """Get statistics about accumulated data for the dashboard."""
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            COUNT(DISTINCT match_id) as total_matches,
                            COUNT(*) as total_ticks,
                            MIN(time) as earliest,
                            MAX(time) as latest,
                            COUNT(DISTINCT match_id) FILTER (
                                WHERE match_id IN (
                                    SELECT match_id FROM odds_ticks
                                    GROUP BY match_id
                                    HAVING COUNT(*) >= :min_ticks
                                )
                            ) as qualifying_matches
                        FROM odds_ticks
                    """),
                    {"min_ticks": self.settings.rl.min_ticks_per_match},
                )
                row = result.fetchone()
                if row:
                    return {
                        "total_matches": row[0] or 0,
                        "total_ticks": row[1] or 0,
                        "earliest_data": row[2].isoformat() if row[2] else None,
                        "latest_data": row[3].isoformat() if row[3] else None,
                        "qualifying_matches": row[4] or 0,
                        "min_required": self.settings.rl.min_matches_to_train,
                        "ready_to_train": (row[4] or 0) >= self.settings.rl.min_matches_to_train,
                    }
        except Exception as e:
            logger.error("accumulation_stats_error", error=str(e))

        return {
            "total_matches": 0,
            "total_ticks": 0,
            "earliest_data": None,
            "latest_data": None,
            "qualifying_matches": 0,
            "min_required": self.settings.rl.min_matches_to_train,
            "ready_to_train": False,
        }
