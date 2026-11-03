"""
LotusBook DOM/WebSocket parser.
Converts raw scraped data into normalized OddsEvent objects.

Tailored for LotusBook's DOM where:
  - Match ID comes from the URL (lotus_id) for stable identification
  - Odds may be None for suspended markets (still emit the event)
  - Team names are already clean from the JS extractor
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from shared.constants import FRANCHISE_TEAMS, VALID_ODDS_MAX, VALID_ODDS_MIN
from shared.logging import setup_logging
from shared.naming import canonical_team_name
from shared.schemas import OddsEvent

logger = setup_logging("lotusbook_parser")

# LotusBook writes listing and match-page times in the site clock, which is India Standard Time
# (verified 2026-11-03: "Today at 6:45 AM" for Biratnagar Kings v Janakpur Bolts is stored as 01:15 UTC,
# and the Toronto Kings v Montreal Warriors page start "03 Nov 04:30" matches its LIVE flip at 23:27 UTC
# on 02 Nov). Times are converted to UTC here; nothing downstream sees site-local time.
SITE_TZ = timezone(timedelta(hours=5, minutes=30))

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Competition for listing rows. The LotusBook listing does not label each row with its competition
# (the left-menu competition names have no link to a row), so the competition is taken from the team
# names. Only teams that play in exactly one listed competition are mapped; anything else stays empty.
# Keys are canonical team names exactly as the listing shows them (see shared.naming).
COMPETITION_BY_TEAM: dict[str, str] = {
    # Nepal Premier League (listing rows verified 2026-11-03)
    "biratnagar kings": "Nepal Premier League",
    "janakpur bolts": "Nepal Premier League",
    "karnali yaks": "Nepal Premier League",
    "lumbini lions": "Nepal Premier League",
    "pokhara avengers": "Nepal Premier League",
    "sudurpaschim royals": "Nepal Premier League",
    # Caribbean Premier League, simulated listing name
    "st lucia kings srl": "Caribbean Premier League SRL",
    "trinbago knight riders srl": "Caribbean Premier League SRL",
    # Big Bash League, Women
    "hobart hurricanes w": "Big Bash League, Women",
    "melbourne renegades w": "Big Bash League, Women",
    "adelaide strikers w": "Big Bash League, Women",
    "brisbane heat w": "Big Bash League, Women",
    "sydney thunder w": "Big Bash League, Women",
    "perth scorchers w": "Big Bash League, Women",
}


def competition_for_teams(team_home: str, team_away: str) -> str:
    """Competition for a pair of listing teams, or "" when the teams do not share one known competition."""
    home = COMPETITION_BY_TEAM.get(canonical_team_name(team_home).lower())
    away = COMPETITION_BY_TEAM.get(canonical_team_name(team_away).lower())
    if home and home == away:
        return home
    return ""


def parse_site_time(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse LotusBook listing/match-page start text into an aware UTC datetime.

    Site-local text ("Today at 10:00 PM", "Tomorrow 12:15 PM", "03 NOV 04:30", "05-11-2026 3:30 AM")
    is read in ``SITE_TZ``. ``now`` is the reference clock (defaults to the current time); tests pass it
    explicitly. Naive ISO datetimes are also read in ``SITE_TZ``; aware ones are converted. Returns None
    when nothing matches.
    """
    if value is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_ist = now.astimezone(SITE_TZ)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=SITE_TZ).astimezone(timezone.utc)
        return value.astimezone(timezone.utc)

    s = re.sub(r"\s+", " ", str(value).replace("|", " ")).strip().lower()
    if not s:
        return None

    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SITE_TZ)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        # "today 10:00 pm", "today at 10:00 pm"
        m = re.search(r"today(?:\s+at)?\s+(\d{1,2}):(\d{2})\s*(am|pm)?", s)
        if m:
            return _site_datetime(now_ist.date(), m.group(1), m.group(2), m.group(3)).astimezone(timezone.utc)

        # "tomorrow 12:15 pm"
        m = re.search(r"tomorrow(?:\s+at)?\s+(\d{1,2}):(\d{2})\s*(am|pm)?", s)
        if m:
            day = now_ist.date() + timedelta(days=1)
            return _site_datetime(day, m.group(1), m.group(2), m.group(3)).astimezone(timezone.utc)

        # "05-11-2026 3:30 am" or "05/11/2026 3:30 am" (day first)
        m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})\s*(am|pm)?", s)
        if m:
            day_n, month_n, year_n, hour, minute, ampm = m.groups()
            return _site_datetime_from_parts(int(year_n), int(month_n), int(day_n), hour, minute, ampm)

        # "03 nov 04:30", "03 nov 2026 04:30" (day, month name, optional year, time)
        m = re.search(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s*(?:(\d{4})\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?", s)
        if m and m.group(2) in _MONTHS:
            day_n, mon, year_n, hour, minute, ampm = m.groups()
            if year_n is None:
                # No year in the text: take the nearest year to the reference clock.
                candidate = _site_datetime_from_parts(now_ist.year, _MONTHS[mon], int(day_n), hour, minute, ampm)
                if candidate - now > timedelta(days=183):
                    return _site_datetime_from_parts(now_ist.year - 1, _MONTHS[mon], int(day_n), hour, minute, ampm)
                if now - candidate > timedelta(days=183):
                    return _site_datetime_from_parts(now_ist.year + 1, _MONTHS[mon], int(day_n), hour, minute, ampm)
                return candidate
            return _site_datetime_from_parts(int(year_n), _MONTHS[mon], int(day_n), hour, minute, ampm)

        # "dd/mm" or "dd-mm" with optional time, no year (legacy fallback)
        m = re.search(r"(\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
        if m:
            day_n, month_n, hour, minute = m.groups()
            return _site_datetime_from_parts(
                now_ist.year, int(month_n), int(day_n), hour or "0", minute or "0", None
            ).astimezone(timezone.utc)
    except ValueError:
        return None

    return None


def _hour24(hour: str, ampm: Optional[str]) -> int:
    h = int(hour)
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return h


def _site_datetime(day: Any, hour: str, minute: str, ampm: Optional[str]) -> datetime:
    return datetime(day.year, day.month, day.day, _hour24(hour, ampm), int(minute), tzinfo=SITE_TZ)


def _site_datetime_from_parts(year: int, month: int, day: int, hour: str, minute: str,
                              ampm: Optional[str]) -> datetime:
    return datetime(year, month, day, _hour24(hour, ampm), int(minute), tzinfo=SITE_TZ).astimezone(timezone.utc)


class LotusBookParser:
    """
    Parse raw scraper data into normalized OddsEvent format.

    Handles:
    - DOM data (HTML elements -> structured data)
    - WebSocket frames (JSON -> structured data)
    - Data validation (required fields, valid ranges)
    - Match ID generation (prefers LotusBook URL ID when available)
    - Odds format normalization (ensure decimal)
    """

    def __init__(self) -> None:
        self._match_id_cache: dict[str, str] = {}
        self._last_events: dict[str, str] = {}  # fingerprint dedup

    def parse_dom_data(self, raw_matches: list[dict[str, Any]], dedup: bool = False) -> list[OddsEvent]:
        """
        Parse DOM-scraped match data into OddsEvent list.

        Args:
            raw_matches: List of dicts extracted from LotusBook JS evaluator.
                Keys: team_home, team_away, competition, is_live, lotus_id,
                      back_home, lay_home, back_draw, lay_draw, back_away, lay_away,
                      volume_back_home, volume_lay_home, etc.

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
                    logger.warning("missing_team_names", raw=str(raw)[:200])
                    continue

                competition = self._infer_competition_if_empty(
                    team_home, team_away, str(raw.get("competition", "") or "").strip()
                )

                # Prefer LotusBook URL ID for stable match identification
                lotus_id = raw.get("lotus_id", "")
                if lotus_id:
                    match_id = f"lb_{lotus_id}"
                else:
                    match_id = self._generate_match_id(
                        team_home, team_away, competition
                    )

                event = OddsEvent(
                    match_id=match_id,
                    timestamp=now,
                    team_home=team_home,
                    team_away=team_away,
                    competition=competition,
                    back_home=self._parse_odds(raw.get("back_home")),
                    lay_home=self._parse_odds(raw.get("lay_home")),
                    back_draw=self._parse_odds(raw.get("back_draw")),
                    lay_draw=self._parse_odds(raw.get("lay_draw")),
                    back_away=self._parse_odds(raw.get("back_away")),
                    lay_away=self._parse_odds(raw.get("lay_away")),
                    volume_back_home=self._parse_volume(raw.get("volume_back_home")),
                    volume_lay_home=self._parse_volume(raw.get("volume_lay_home")),
                    volume_back_away=self._parse_volume(raw.get("volume_back_away")),
                    volume_lay_away=self._parse_volume(raw.get("volume_lay_away")),
                    is_live=bool(raw.get("is_live", False)),
                    scheduled_time=self._parse_time(raw.get("scheduled_time")),
                    score_text=raw.get("score_text", ""),
                    source="lotusbook",
                    scrape_method="dom",
                    scrape_latency_ms=raw.get("scrape_latency_ms", 0),
                )

                if not self.validate_event(event):
                    continue

                # Dedup: skip if identical to last event for this match (only when dedup=True)
                if dedup:
                    fingerprint = self._fingerprint(event)
                    if self._last_events.get(match_id) == fingerprint:
                        continue
                    self._last_events[match_id] = fingerprint

                events.append(event)

                logger.debug(
                    "match_parsed",
                    match_id=match_id,
                    home=team_home,
                    away=team_away,
                    back_home=event.back_home,
                    lay_home=event.lay_home,
                    back_away=event.back_away,
                    lay_away=event.lay_away,
                    is_live=event.is_live,
                )

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
        Validate an OddsEvent passes quality checks.

        For LotusBook, markets can be suspended (odds = None) during certain
        periods. We accept events if at least ONE back price is present,
        since partial data is still valuable for tracking match status.

        Checks:
        1. At least one back price present (home or away)
        2. Any present odds within valid range
        3. Back <= Lay where both exist (spread consistency)
        """
        # Must have at least one back price OR a scheduled time
        if event.back_home is None and event.back_away is None and event.scheduled_time is None:
            logger.debug("validation_fail_no_odds", match_id=event.match_id)
            return False

        # Check odds ranges for all non-None values
        for odds_val in [
            event.back_home, event.lay_home,
            event.back_draw, event.lay_draw,
            event.back_away, event.lay_away,
        ]:
            if odds_val is not None and not (VALID_ODDS_MIN <= odds_val <= VALID_ODDS_MAX):
                logger.debug(
                    "validation_fail_range",
                    match_id=event.match_id,
                    odds=odds_val,
                )
                return False

        # Back <= Lay check (only when both sides present)
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

    def _infer_competition_if_empty(
        self, team_home: str, team_away: str, competition: str
    ) -> str:
        """When LotusBook omits the competition row, infer IPL from both franchise names."""
        if competition:
            return competition
        mapped = competition_for_teams(team_home, team_away)
        if mapped:
            return mapped
        ipl = FRANCHISE_TEAMS.get("IPL", set())

        def franchise_key(name: str) -> str:
            n = name.lower().strip()
            if n.endswith(" w"):
                n = n[:-2].strip()
            return n

        if franchise_key(team_home) in ipl and franchise_key(team_away) in ipl:
            return "Indian Premier League"
        return ""

    def _clean_team_name(self, name: str) -> str:
        """Normalize team name for consistency."""
        return canonical_team_name(name)

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
            str(event.scheduled_time),
        ]
        return "|".join(parts)

    def _parse_time(self, value: Any) -> datetime | None:
        """Parse listing or match-page start text (site clock, IST) into an aware UTC datetime."""
        return parse_site_time(value)

    def _parse_volume(self, value: Any) -> float | None:
        """Parse a volume value, handling strings, floats, None."""
        if value is None:
            return None
        try:
            vol = float(str(value).strip().replace(",", "").replace("₹", "").replace("$", ""))
            if vol >= 0:
                return vol
            return None
        except (ValueError, TypeError):
            return None
