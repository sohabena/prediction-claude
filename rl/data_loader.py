"""
Data loader: queries TimescaleDB and groups odds ticks into training episodes.
Each completed match becomes one episode (list of tick dicts) for the RL environment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from features.data_quality import EpisodeQualityGate, EpisodeQualityReport
from shared.config import get_settings
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("data_loader")


class MatchDataLoader:
    """
    Loads historical match data from TimescaleDB for RL training.

    Groups odds_ticks by match_id, left-joins match_context,
    and returns episodes suitable for CricketBettingEnv.

    Includes an episode-level quality gate: episodes that fail quality
    checks (low completeness, excessive jumps, etc.) are rejected
    before they reach the RL environment.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._quality_gate = EpisodeQualityGate()
        self._quality_reports: list[EpisodeQualityReport] = []

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

        # Step 1b: Load match results for these matches
        results = await self._load_match_results(match_ids)

        # Step 2: Load ticks for each match, run quality gate, attach metadata
        episodes: list[list[dict[str, Any]]] = []
        self._quality_reports = []
        rejected_count = 0

        for match_id in match_ids:
            episode = await self._load_match_episode(match_id)
            if not episode:
                continue

            # ── Quality gate ──────────────────────────────────
            report = self._quality_gate.evaluate(episode)
            self._quality_reports.append(report)

            if not report.passed:
                rejected_count += 1
                logger.warning(
                    "episode_rejected_quality",
                    match_id=match_id,
                    score=report.quality_score,
                    issues=report.issues,
                    ticks=report.total_ticks,
                )
                continue
            # ──────────────────────────────────────────────────

            # Attach match result metadata to the first tick
            result = results.get(match_id)
            if result:
                episode[0]["_meta"] = {
                    "winner": result["winner"],
                    "loser": result["loser"],
                    "team_home": result["team_home"],
                    "team_away": result["team_away"],
                    "result_type": result["result_type"],
                    "margin": result["margin"],
                    "has_real_outcome": True,
                }
            else:
                # No verified result -- mark as simulated
                episode[0]["_meta"] = {
                    "winner": "",
                    "team_home": episode[0].get("team_home", ""),
                    "team_away": episode[0].get("team_away", ""),
                    "result_type": "",
                    "has_real_outcome": False,
                }
            episodes.append(episode)

        logger.info(
            "matches_loaded",
            total_episodes=len(episodes),
            total_ticks=sum(len(ep) for ep in episodes),
            quality_rejected=rejected_count,
            quality_reports=len(self._quality_reports),
        )
        return episodes

    @property
    def quality_reports(self) -> list[EpisodeQualityReport]:
        """Access the quality reports from the most recent load."""
        return self._quality_reports

    async def _get_qualifying_match_ids(
        self, min_ticks: int, days_back: int
    ) -> list[str]:
        """Get match_ids that have enough data points AND are approved for training."""
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT ot.match_id, COUNT(*) as tick_count
                        FROM odds_ticks ot
                        INNER JOIN match_training_status mts
                            ON ot.match_id = mts.match_id
                        WHERE ot.time > NOW() - MAKE_INTERVAL(days => :days_back)
                          AND mts.training_status = 'approved'
                        GROUP BY ot.match_id
                        HAVING COUNT(*) >= :min_ticks
                        ORDER BY MIN(ot.time) ASC
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

    async def load_episode_for_settled_match(
        self,
        match_id: str,
        result_meta: dict[str, Any],
        min_ticks: int = 10,
        run_quality_gate: bool = False,
    ) -> list[dict[str, Any]] | None:
        """
        Load one episode for a match that just settled (live virtual trading).
        Uses result_meta from Redis; does not require match_training_status.
        """
        episode = await self._load_match_episode(match_id)
        if not episode or len(episode) < min_ticks:
            return None

        if run_quality_gate:
            report = self._quality_gate.evaluate(episode)
            if not report.passed:
                return None

        episode[0]["_meta"] = {
            "winner": result_meta.get("winner", ""),
            "loser": result_meta.get("loser", ""),
            "team_home": result_meta.get("team_home", ""),
            "team_away": result_meta.get("team_away", ""),
            "result_type": result_meta.get("result_type", "win"),
            "margin": result_meta.get("margin"),
            "has_real_outcome": True,
        }
        return episode

    async def _load_match_results(
        self, match_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Load verified match results from the match_results table."""
        results: dict[str, dict[str, Any]] = {}
        if not match_ids:
            return results

        try:
            async with get_session() as session:
                # Use a parameterized IN query
                placeholders = ", ".join(f":id_{i}" for i in range(len(match_ids)))
                params = {f"id_{i}": mid for i, mid in enumerate(match_ids)}

                result = await session.execute(
                    text(f"""
                        SELECT match_id, winner, loser, result_type, margin,
                               team_home, team_away
                        FROM match_results
                        WHERE match_id IN ({placeholders})
                    """),
                    params,
                )
                rows = result.fetchall()
                for row in rows:
                    results[row[0]] = {
                        "winner": row[1],
                        "loser": row[2],
                        "result_type": row[3],
                        "margin": row[4],
                        "team_home": row[5],
                        "team_away": row[6],
                    }

                logger.info(
                    "match_results_loaded",
                    requested=len(match_ids),
                    found=len(results),
                )
        except Exception as e:
            logger.warning("match_results_load_error", error=str(e))

        return results

    async def get_accumulation_stats(self) -> dict[str, Any]:
        """Get statistics about accumulated data for the dashboard.

        Only counts *approved* matches as qualifying for training.
        """
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            COUNT(DISTINCT ot.match_id) as total_matches,
                            COUNT(*) as total_ticks,
                            MIN(ot.time) as earliest,
                            MAX(ot.time) as latest,
                            COUNT(DISTINCT ot.match_id) FILTER (
                                WHERE ot.match_id IN (
                                    SELECT ot2.match_id
                                    FROM odds_ticks ot2
                                    INNER JOIN match_training_status mts
                                        ON ot2.match_id = mts.match_id
                                    WHERE mts.training_status = 'approved'
                                    GROUP BY ot2.match_id
                                    HAVING COUNT(*) >= :min_ticks
                                )
                            ) as qualifying_matches
                        FROM odds_ticks ot
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
