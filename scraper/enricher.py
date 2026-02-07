"""
Cricbuzz enricher: fetches live match statistics to enrich odds data.
Polls Cricbuzz for score, wickets, overs, run rate, etc.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from shared.constants import CHANNEL_MATCH_CONTEXT
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import MatchContext, MatchStatus

logger = setup_logging("cricbuzz_enricher")

CRICBUZZ_API_BASE = "https://www.cricbuzz.com/api/cricket-match"
CRICBUZZ_MATCHES_URL = "https://www.cricbuzz.com/api/cricket-match/commentary"


class MatchMapper:
    """
    Maps LotusBook match IDs to Cricbuzz match IDs.

    Strategy:
    1. Fuzzy match team names
    2. Match scheduled time (+/- 30 minutes)
    3. Cache mapping for duration of match
    """

    def __init__(self) -> None:
        self._mapping: dict[str, str] = {}  # lotusbook_id -> cricbuzz_id
        self._team_aliases: dict[str, list[str]] = {
            "India": ["IND", "India", "INDIA"],
            "Australia": ["AUS", "Australia", "AUSTRALIA"],
            "England": ["ENG", "England", "ENGLAND"],
            "Pakistan": ["PAK", "Pakistan", "PAKISTAN"],
            "South Africa": ["SA", "South Africa", "RSA"],
            "New Zealand": ["NZ", "New Zealand"],
            "West Indies": ["WI", "West Indies", "Windies"],
            "Sri Lanka": ["SL", "Sri Lanka"],
            "Bangladesh": ["BAN", "Bangladesh"],
            "Afghanistan": ["AFG", "Afghanistan"],
        }

    def normalize_team_name(self, name: str) -> str:
        """Normalize a team name for matching."""
        name_lower = name.lower().strip()
        for canonical, aliases in self._team_aliases.items():
            for alias in aliases:
                if alias.lower() == name_lower:
                    return canonical
        return name.strip()

    def set_mapping(self, lotusbook_id: str, cricbuzz_id: str) -> None:
        """Cache a LotusBook -> Cricbuzz match ID mapping."""
        self._mapping[lotusbook_id] = cricbuzz_id

    def get_cricbuzz_id(self, lotusbook_id: str) -> str | None:
        """Get Cricbuzz match ID for a LotusBook match."""
        return self._mapping.get(lotusbook_id)


class CricbuzzEnricher:
    """
    Enriches odds data with live cricket match context.

    Data source: Cricbuzz (public cricket scores)
    Poll interval: 10 seconds
    """

    def __init__(self, poll_interval: int = 10) -> None:
        self.poll_interval = poll_interval
        self.mapper = MatchMapper()
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

    async def fetch_live_matches(self) -> list[dict[str, Any]]:
        """Fetch list of live cricket matches from Cricbuzz."""
        try:
            resp = await self._client.get(
                "https://www.cricbuzz.com/api/cricket-match/live"
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("matches", data.get("matchList", []))
        except Exception as e:
            logger.warning("cricbuzz_fetch_error", error=str(e))
        return []

    async def fetch_match_score(self, cricbuzz_match_id: str) -> dict[str, Any] | None:
        """Fetch detailed score for a specific match."""
        try:
            resp = await self._client.get(
                f"{CRICBUZZ_API_BASE}/{cricbuzz_match_id}/score"
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("score_fetch_error", match_id=cricbuzz_match_id, error=str(e))
        return None

    def parse_match_context(
        self, raw: dict[str, Any], match_id: str
    ) -> MatchContext | None:
        """Parse raw Cricbuzz data into MatchContext."""
        try:
            score_data = raw.get("score", raw.get("scorecard", {}))
            if isinstance(score_data, list) and score_data:
                score_data = score_data[-1]  # Latest innings

            score = self._safe_int(score_data.get("runs", score_data.get("score", 0)))
            wickets = self._safe_int(score_data.get("wickets", 0))
            overs = self._safe_float(score_data.get("overs", 0.0))
            run_rate = self._safe_float(score_data.get("runRate", score_data.get("crr", 0.0)))
            req_rr = self._safe_float(score_data.get("requiredRunRate", score_data.get("rrr", 0.0)))
            innings = self._safe_int(score_data.get("innings", score_data.get("inningsId", 1)))

            # Determine match format and calculate balls remaining
            match_format = raw.get("format", raw.get("matchFormat", "T20")).upper()
            if "T20" in match_format:
                max_overs = 20.0
                max_balls = 120
            elif "ODI" in match_format or "50" in match_format:
                max_overs = 50.0
                max_balls = 300
            else:
                max_overs = 20.0  # Default to T20
                max_balls = 120

            balls_bowled = int(overs) * 6 + int((overs % 1) * 10)
            balls_remaining = max(0, max_balls - balls_bowled)

            is_live = raw.get("state", "").lower() in ("live", "inprogress", "in progress")

            status = MatchStatus.LIVE if is_live else MatchStatus.SCHEDULED
            if raw.get("state", "").lower() in ("complete", "completed"):
                status = MatchStatus.COMPLETED

            return MatchContext(
                match_id=match_id,
                timestamp=datetime.now(timezone.utc),
                is_live=is_live,
                score=score,
                wickets=wickets,
                overs=overs,
                run_rate=run_rate,
                required_run_rate=req_rr,
                innings=innings,
                balls_remaining=balls_remaining,
                max_overs=max_overs,
                max_balls=max_balls,
                batting_team=str(raw.get("battingTeam", raw.get("batTeamName", ""))),
                bowling_team=str(raw.get("bowlingTeam", raw.get("bowlTeamName", ""))),
                status=status,
                match_format=match_format if match_format in ("T20", "ODI", "TEST") else "T20",
            )
        except Exception as e:
            logger.error("parse_context_error", match_id=match_id, error=str(e))
            return None

    async def enrich_loop(self, active_match_ids: list[str] | None = None) -> None:
        """
        Continuous enrichment loop.

        Polls Cricbuzz for match stats and publishes to Redis.
        """
        self._running = True
        logger.info("enricher_started", poll_interval=self.poll_interval)

        while self._running:
            try:
                redis = await get_redis()

                # Get active matches from Redis if not provided
                if active_match_ids is None:
                    active_data = await redis.get_json("active_matches")
                    if active_data and "matches" in active_data:
                        active_match_ids_loop = [
                            m["match_id"] for m in active_data["matches"] if m.get("is_live")
                        ]
                    else:
                        active_match_ids_loop = []
                else:
                    active_match_ids_loop = active_match_ids

                for match_id in active_match_ids_loop:
                    cricbuzz_id = self.mapper.get_cricbuzz_id(match_id)
                    if cricbuzz_id:
                        raw = await self.fetch_match_score(cricbuzz_id)
                        if raw:
                            context = self.parse_match_context(raw, match_id)
                            if context:
                                await redis.publish_event(
                                    CHANNEL_MATCH_CONTEXT,
                                    context.model_dump(mode="json"),
                                )
                                await self._store_context(context)

            except Exception as e:
                logger.error("enricher_error", error=str(e))

            await asyncio.sleep(self.poll_interval)

    async def _store_context(self, context: MatchContext) -> None:
        """Store match context to TimescaleDB."""
        from backend.models.match import MatchContextRecord

        try:
            async with get_session() as session:
                record = MatchContextRecord(
                    time=context.timestamp,
                    match_id=context.match_id,
                    is_live=context.is_live,
                    score=context.score,
                    wickets=context.wickets,
                    overs=context.overs,
                    run_rate=context.run_rate,
                    req_run_rate=context.required_run_rate,
                    innings=context.innings,
                    balls_remaining=context.balls_remaining,
                    batting_team=context.batting_team,
                    bowling_team=context.bowling_team,
                    status=context.status.value,
                    match_format=context.match_format,
                )
                session.add(record)
        except Exception as e:
            logger.error("db_context_store_error", match_id=context.match_id, error=str(e))

    def stop(self) -> None:
        """Stop the enrichment loop."""
        self._running = False

    def _safe_int(self, value: Any) -> int:
        """Safely convert to int."""
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _safe_float(self, value: Any) -> float:
        """Safely convert to float."""
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
