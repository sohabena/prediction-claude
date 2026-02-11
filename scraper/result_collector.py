"""
Match result collector: fetches final match outcomes after completion.
Monitors for completed matches and stores verified results in TimescaleDB.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from shared.constants import CHANNEL_MATCH_RESULTS, KEY_MATCH_RESULTS
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import MatchResult

logger = setup_logging("result_collector")

CRICBUZZ_MATCH_URL = "https://www.cricbuzz.com/api/cricket-match"


class MatchResultCollector:
    """
    Collects final match results from Cricbuzz after matches complete.

    Strategy:
    1. Monitors active_matches in Redis for completed matches
    2. Queries Cricbuzz for the final result
    3. Parses winner/loser and margin
    4. Stores in match_results table
    5. Publishes to Redis for downstream consumers (orchestrator, shadow trader)
    """

    def __init__(self, poll_interval: int = 60) -> None:
        self.poll_interval = poll_interval
        self._client = httpx.AsyncClient(
            timeout=10.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )
        self._running = False
        self._collected_ids: set[str] = set()  # Already-collected match IDs

    async def start(self) -> None:
        """Start the result collection loop."""
        self._running = True
        logger.info("result_collector_started", poll_interval=self.poll_interval)

        # Load already-collected IDs from DB to avoid duplicates
        await self._load_existing_results()

        while self._running:
            try:
                await self._check_for_completed_matches()
            except Exception as e:
                logger.error("result_collector_error", error=str(e))

            await asyncio.sleep(self.poll_interval)

    async def _load_existing_results(self) -> None:
        """Load match IDs that already have results in the DB."""
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("SELECT match_id FROM match_results")
                )
                rows = result.fetchall()
                self._collected_ids = {row[0] for row in rows}
                logger.info("existing_results_loaded", count=len(self._collected_ids))
        except Exception as e:
            logger.warning("load_existing_results_error", error=str(e))

    async def _check_for_completed_matches(self) -> None:
        """Check for matches that have completed but don't have results yet."""
        try:
            async with get_session() as session:
                # Find matches with no recent odds ticks (completed) that lack results
                result = await session.execute(
                    text("""
                        SELECT DISTINCT o.match_id, o.team_home, o.team_away, o.competition
                        FROM odds_ticks o
                        WHERE o.match_id NOT IN (SELECT match_id FROM match_results)
                          AND o.time < NOW() - INTERVAL '30 minutes'
                          AND o.match_id IN (
                              SELECT match_id FROM odds_ticks
                              GROUP BY match_id
                              HAVING MAX(time) < NOW() - INTERVAL '30 minutes'
                                 AND COUNT(*) >= 10
                          )
                        LIMIT 10
                    """)
                )
                rows = result.fetchall()

                for row in rows:
                    match_id = row[0]
                    if match_id in self._collected_ids:
                        continue

                    team_home = row[1]
                    team_away = row[2]

                    logger.info(
                        "checking_completed_match",
                        match_id=match_id,
                        teams=f"{team_home} vs {team_away}",
                    )

                    match_result = await self._fetch_result_from_cricbuzz(
                        match_id, team_home, team_away
                    )

                    if match_result:
                        await self._store_result(match_result)
                        await self._publish_result(match_result)
                        self._collected_ids.add(match_id)
                        logger.info(
                            "result_collected",
                            match_id=match_id,
                            winner=match_result.winner,
                            result_type=match_result.result_type,
                            margin=match_result.margin,
                        )
                    else:
                        # Try fallback: use match_context status if available
                        fallback = await self._try_context_fallback(
                            match_id, team_home, team_away
                        )
                        if fallback:
                            await self._store_result(fallback)
                            await self._publish_result(fallback)
                            self._collected_ids.add(match_id)

        except Exception as e:
            logger.error("completed_match_check_error", error=str(e))

    async def _fetch_result_from_cricbuzz(
        self,
        match_id: str,
        team_home: str,
        team_away: str,
    ) -> MatchResult | None:
        """Fetch match result from Cricbuzz API."""
        try:
            # Search for the match in Cricbuzz recent matches
            resp = await self._client.get(
                "https://www.cricbuzz.com/api/cricket-match/recent"
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            matches = data.get("matchList", data.get("matches", []))

            # Find our match by team names
            for match_group in matches:
                match_list = match_group.get("matches", [match_group])
                if not isinstance(match_list, list):
                    match_list = [match_list]

                for match in match_list:
                    match_info = match.get("matchInfo", match)

                    team1 = match_info.get("team1", {}).get("teamName", "")
                    team2 = match_info.get("team2", {}).get("teamName", "")

                    # Check if teams match (fuzzy)
                    if not self._teams_match(team_home, team_away, team1, team2):
                        continue

                    # Extract result from status string
                    status = match_info.get("status", "")
                    state = match_info.get("state", "")

                    if state.lower() not in ("complete", "completed"):
                        continue

                    return self._parse_result_string(
                        status, match_id, team_home, team_away, team1, team2
                    )

        except Exception as e:
            logger.debug("cricbuzz_result_fetch_error", match_id=match_id, error=str(e))

        return None

    async def _try_context_fallback(
        self,
        match_id: str,
        team_home: str,
        team_away: str,
    ) -> MatchResult | None:
        """
        Fallback: try to determine result from match_context table.
        If the last context record shows status='completed' and we can
        infer the winner from the final score data.
        """
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT status, score, wickets, innings, batting_team, bowling_team
                        FROM match_context
                        WHERE match_id = :match_id
                        ORDER BY time DESC
                        LIMIT 1
                    """),
                    {"match_id": match_id},
                )
                row = result.fetchone()

                if row and row[0] == "completed":
                    # If we have second innings context, the batting team in
                    # 2nd innings is the chaser -- if they have fewer wickets
                    # than 10, they likely won (completed the chase)
                    innings = row[3] or 1
                    wickets = row[2] or 0
                    batting_team = row[4] or ""
                    bowling_team = row[5] or ""

                    if innings >= 2 and batting_team and bowling_team:
                        if wickets < 10:
                            # Chaser won (still had wickets remaining)
                            winner = batting_team
                            loser = bowling_team
                            margin = f"{10 - wickets} wickets"
                        else:
                            # Chaser all out -- bowling team (first innings) won
                            winner = bowling_team
                            loser = batting_team
                            margin = "runs"  # Can't determine exact margin
                    else:
                        return None

                    return MatchResult(
                        match_id=match_id,
                        completed_at=datetime.now(timezone.utc),
                        winner=winner,
                        loser=loser,
                        result_type="win",
                        margin=margin,
                        team_home=team_home,
                        team_away=team_away,
                        source="context_fallback",
                    )

        except Exception as e:
            logger.debug("context_fallback_error", match_id=match_id, error=str(e))

        return None

    def _parse_result_string(
        self,
        status: str,
        match_id: str,
        team_home: str,
        team_away: str,
        cb_team1: str,
        cb_team2: str,
    ) -> MatchResult | None:
        """Parse a Cricbuzz result status string like 'India won by 5 wickets'."""
        status_lower = status.lower()

        # Handle special cases
        if "no result" in status_lower or "abandoned" in status_lower:
            return MatchResult(
                match_id=match_id,
                completed_at=datetime.now(timezone.utc),
                winner="",
                loser="",
                result_type="no_result" if "no result" in status_lower else "abandoned",
                margin="",
                team_home=team_home,
                team_away=team_away,
                source="cricbuzz",
            )

        if "tied" in status_lower or "draw" in status_lower:
            return MatchResult(
                match_id=match_id,
                completed_at=datetime.now(timezone.utc),
                winner="",
                loser="",
                result_type="tie" if "tied" in status_lower else "draw",
                margin="",
                team_home=team_home,
                team_away=team_away,
                source="cricbuzz",
            )

        # Pattern: "TEAM won by X runs/wickets"
        won_pattern = re.compile(r"(.+?)\s+won\s+by\s+(.+)", re.IGNORECASE)
        match = won_pattern.search(status)

        if match:
            winner_name = match.group(1).strip()
            margin = match.group(2).strip()

            # Map Cricbuzz team name back to our team names
            if self._name_matches(winner_name, cb_team1):
                winner = team_home if self._name_matches(cb_team1, team_home) else team_away
                loser = team_away if winner == team_home else team_home
            elif self._name_matches(winner_name, cb_team2):
                winner = team_home if self._name_matches(cb_team2, team_home) else team_away
                loser = team_away if winner == team_home else team_home
            else:
                # Can't map -- use Cricbuzz names directly
                winner = winner_name
                loser = cb_team2 if winner_name == cb_team1 else cb_team1

            return MatchResult(
                match_id=match_id,
                completed_at=datetime.now(timezone.utc),
                winner=winner,
                loser=loser,
                result_type="win",
                margin=margin,
                team_home=team_home,
                team_away=team_away,
                source="cricbuzz",
            )

        return None

    def _teams_match(
        self, home: str, away: str, team1: str, team2: str
    ) -> bool:
        """Check if two sets of team names refer to the same match."""
        return (
            (self._name_matches(home, team1) and self._name_matches(away, team2))
            or (self._name_matches(home, team2) and self._name_matches(away, team1))
        )

    def _name_matches(self, name1: str, name2: str) -> bool:
        """Fuzzy team name matching."""
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()
        if n1 == n2:
            return True
        # Check if one contains the other
        if n1 in n2 or n2 in n1:
            return True
        # Common abbreviations
        abbrevs = {
            "ind": "india", "aus": "australia", "eng": "england",
            "pak": "pakistan", "sa": "south africa", "nz": "new zealand",
            "wi": "west indies", "sl": "sri lanka", "ban": "bangladesh",
            "afg": "afghanistan", "zim": "zimbabwe", "ire": "ireland",
        }
        n1_full = abbrevs.get(n1, n1)
        n2_full = abbrevs.get(n2, n2)
        return n1_full == n2_full

    async def _store_result(self, match_result: MatchResult) -> None:
        """Store match result to TimescaleDB."""
        from backend.models.results import MatchResultRecord

        try:
            async with get_session() as session:
                record = MatchResultRecord(
                    match_id=match_result.match_id,
                    completed_at=match_result.completed_at,
                    winner=match_result.winner,
                    loser=match_result.loser,
                    result_type=match_result.result_type,
                    margin=match_result.margin,
                    team_home=match_result.team_home,
                    team_away=match_result.team_away,
                    source=match_result.source,
                )
                session.add(record)
                logger.info("result_stored", match_id=match_result.match_id)
        except Exception as e:
            logger.error("result_store_error", match_id=match_result.match_id, error=str(e))

    async def _publish_result(self, match_result: MatchResult) -> None:
        """Publish match result to Redis for real-time consumers."""
        try:
            redis = await get_redis()
            result_data = match_result.model_dump(mode="json")
            await redis.publish_event(CHANNEL_MATCH_RESULTS, result_data)
        except Exception as e:
            logger.error("result_publish_error", match_id=match_result.match_id, error=str(e))

    def stop(self) -> None:
        """Stop the collector loop."""
        self._running = False

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
