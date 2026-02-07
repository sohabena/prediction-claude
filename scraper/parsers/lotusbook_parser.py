"""
LotusBook DOM/WebSocket parser.
Converts raw scraped data into normalized OddsEvent objects.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from shared.constants import VALID_ODDS_MAX, VALID_ODDS_MIN
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("lotusbook_parser")


class LotusBookParser:
    """
    Parse raw scraper data into normalized OddsEvent format.

    Handles:
    - DOM data (HTML elements -> structured data)
    - WebSocket frames (JSON -> structured data)
    - Data validation (required fields, valid ranges)
    - Match ID generation and caching
    - Odds format normalization (ensure decimal)
    """

    def __init__(self) -> None:
        self._match_id_cache: dict[str, str] = {}
        self._last_events: dict[str, str] = {}  # fingerprint dedup

    def parse_dom_data(self, raw_matches: list[dict[str, Any]]) -> list[OddsEvent]:
        """
        Parse DOM-scraped match data into OddsEvent list.

        Args:
            raw_matches: List of dicts extracted from DOM, each with keys:
                team_home, team_away, competition, is_live,
                back_home, lay_home, back_draw, lay_draw, back_away, lay_away

        Returns:
            List of validated OddsEvent objects.
        """
        events: list[OddsEvent] = []
        now = datetime.now(timezone.utc)

        for raw in raw_matches:
            try:
                team_home = self._clean_team_name(raw.get("team_home", ""))
                team_away = self._clean_team_name(raw.get("team_away", ""))

                if not team_home or not team_away:
                    logger.warning("missing_team_names", raw=raw)
                    continue

                match_id = self._generate_match_id(team_home, team_away, raw.get("competition", ""))

                event = OddsEvent(
                    match_id=match_id,
                    timestamp=now,
                    team_home=team_home,
                    team_away=team_away,
                    competition=raw.get("competition", ""),
                    back_home=self._parse_odds(raw.get("back_home")),
                    lay_home=self._parse_odds(raw.get("lay_home")),
                    back_draw=self._parse_odds(raw.get("back_draw")),
                    lay_draw=self._parse_odds(raw.get("lay_draw")),
                    back_away=self._parse_odds(raw.get("back_away")),
                    lay_away=self._parse_odds(raw.get("lay_away")),
                    is_live=bool(raw.get("is_live", False)),
                    scheduled_time=self._parse_time(raw.get("scheduled_time")),
                    source="lotusbook",
                    scrape_method="dom",
                    scrape_latency_ms=raw.get("scrape_latency_ms", 0),
                )

                if not self.validate_event(event):
                    continue

                # Dedup: skip if identical to last event for this match
                fingerprint = self._fingerprint(event)
                if self._last_events.get(match_id) == fingerprint:
                    continue
                self._last_events[match_id] = fingerprint

                events.append(event)

            except Exception as e:
                logger.error("parse_dom_error", error=str(e), raw=str(raw)[:200])

        return events

    def parse_websocket_data(self, frame: dict[str, Any]) -> list[OddsEvent]:
        """
        Parse a WebSocket frame into OddsEvent list.

        Args:
            frame: JSON-decoded WebSocket message.

        Returns:
            List of validated OddsEvent objects.
        """
        events: list[OddsEvent] = []
        now = datetime.now(timezone.utc)

        # WebSocket frames may contain multiple match updates
        matches = frame.get("matches", [frame]) if isinstance(frame, dict) else []

        for match_data in matches:
            try:
                team_home = self._clean_team_name(match_data.get("team_home", ""))
                team_away = self._clean_team_name(match_data.get("team_away", ""))

                if not team_home or not team_away:
                    continue

                match_id = match_data.get("match_id") or self._generate_match_id(
                    team_home, team_away, match_data.get("competition", "")
                )

                event = OddsEvent(
                    match_id=match_id,
                    timestamp=now,
                    team_home=team_home,
                    team_away=team_away,
                    competition=match_data.get("competition", ""),
                    back_home=self._parse_odds(match_data.get("back_home")),
                    lay_home=self._parse_odds(match_data.get("lay_home")),
                    back_draw=self._parse_odds(match_data.get("back_draw")),
                    lay_draw=self._parse_odds(match_data.get("lay_draw")),
                    back_away=self._parse_odds(match_data.get("back_away")),
                    lay_away=self._parse_odds(match_data.get("lay_away")),
                    is_live=bool(match_data.get("is_live", False)),
                    source="lotusbook",
                    scrape_method="websocket",
                )

                if self.validate_event(event):
                    events.append(event)

            except Exception as e:
                logger.error("parse_ws_error", error=str(e))

        return events

    def validate_event(self, event: OddsEvent) -> bool:
        """
        Validate an OddsEvent passes all quality checks.

        Checks:
        1. At least home and away back prices present
        2. Odds within valid range
        3. Back <= Lay (spread consistency)
        """
        # Must have at least home and away back prices
        if event.back_home is None or event.back_away is None:
            logger.debug("validation_fail_missing_odds", match_id=event.match_id)
            return False

        # Check odds ranges
        for odds_val in [
            event.back_home, event.lay_home,
            event.back_draw, event.lay_draw,
            event.back_away, event.lay_away,
        ]:
            if odds_val is not None and not (VALID_ODDS_MIN <= odds_val <= VALID_ODDS_MAX):
                logger.debug("validation_fail_range", match_id=event.match_id, odds=odds_val)
                return False

        # Back <= Lay check
        if event.back_home and event.lay_home and event.back_home > event.lay_home:
            logger.debug("validation_fail_spread_home", match_id=event.match_id)
            return False
        if event.back_away and event.lay_away and event.back_away > event.lay_away:
            logger.debug("validation_fail_spread_away", match_id=event.match_id)
            return False

        return True

    def _parse_odds(self, value: Any) -> float | None:
        """Parse an odds value, handling strings, floats, None."""
        if value is None:
            return None
        try:
            odds = float(str(value).strip().replace(",", ""))
            if VALID_ODDS_MIN <= odds <= VALID_ODDS_MAX:
                return odds
            return None
        except (ValueError, TypeError):
            return None

    def _clean_team_name(self, name: str) -> str:
        """Normalize team name for consistency."""
        if not name:
            return ""
        # Remove extra whitespace, strip
        cleaned = re.sub(r"\s+", " ", str(name).strip())
        return cleaned

    def _generate_match_id(self, home: str, away: str, competition: str) -> str:
        """Generate a deterministic match ID from team names and competition."""
        cache_key = f"{home}|{away}|{competition}"
        if cache_key in self._match_id_cache:
            return self._match_id_cache[cache_key]

        raw = f"{home.lower()}_{away.lower()}_{competition.lower()}"
        match_id = hashlib.md5(raw.encode()).hexdigest()[:12]
        self._match_id_cache[cache_key] = match_id
        return match_id

    def _fingerprint(self, event: OddsEvent) -> str:
        """Create a fingerprint of an event for dedup."""
        parts = [
            str(event.back_home), str(event.lay_home),
            str(event.back_away), str(event.lay_away),
            str(event.back_draw), str(event.lay_draw),
            str(event.is_live),
        ]
        return "|".join(parts)

    def _parse_time(self, value: Any) -> datetime | None:
        """Parse a datetime from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
