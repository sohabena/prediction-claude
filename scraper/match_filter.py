"""
International match classifier.
Two-layer filtering: competition name patterns, then team name recognition.
Only qualifying matches are stored and published.
"""

from __future__ import annotations

import re
from typing import Any

from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("match_filter")

# ============================================================
# Layer 1: Competition / Tournament Patterns
# ============================================================

# Whitelisted competition patterns (case-insensitive)
COMPETITION_WHITELIST: list[str] = [
    # ICC events
    r"icc", r"world\s*cup", r"champions\s*trophy", r"wtc",
    # International formats
    r"t20i", r"odi", r"\btest\b", r"bilateral",
    # Major franchise leagues
    r"\bipl\b", r"indian\s*premier", r"tata\s*ipl",
    r"\bbbl\b", r"big\s*bash",
    r"\bpsl\b", r"pakistan\s*super",
    r"\bcpl\b", r"caribbean\s*premier",
    r"the\s*hundred", r"\bhundred\b",
    r"\bsa20\b", r"sa\s*20",
    r"\bmlc\b", r"major\s*league\s*cricket",
    r"\bilt20\b", r"international\s*league\s*t20",
    r"\bbpl\b", r"bangladesh\s*premier",
    r"\blpl\b", r"lanka\s*premier",
    r"\bapl\b", r"afghanistan\s*premier",
    r"super\s*smash",  # New Zealand
    r"vitality\s*blast",  # England T20
    r"county\s*championship",  # England Tests
    r"sheffield\s*shield",  # Australia FC
    r"ranji\s*trophy",  # India FC
]

# Blacklisted patterns (always reject, even if whitelist matches)
COMPETITION_BLACKLIST: list[str] = [
    r"\bsrl\b", r"simulated", r"virtual", r"esports", r"e-?sports",
    r"cyber", r"fantasy", r"practice", r"warm.?up",
]

# ============================================================
# Layer 2: Team Name Database
# ============================================================

# ICC Full Member national teams + common aliases
INTERNATIONAL_TEAMS: set[str] = {
    # Full Members (12)
    "india", "ind", "team india",
    "australia", "aus",
    "england", "eng",
    "pakistan", "pak",
    "south africa", "sa", "rsa", "proteas",
    "new zealand", "nz", "black caps", "blackcaps",
    "west indies", "wi", "windies",
    "sri lanka", "sl",
    "bangladesh", "ban", "bd",
    "afghanistan", "afg",
    "ireland", "ire",
    "zimbabwe", "zim",
    # Associate Members (commonly seen on betting sites)
    "nepal", "nep",
    "usa", "united states",
    "netherlands", "ned",
    "scotland", "sco",
    "namibia", "nam",
    "oman", "oma",
    "uae", "united arab emirates",
    "canada", "can",
    "hong kong", "hk",
    "papua new guinea", "png",
    "jersey", "jer",
    "uganda", "uga",
    "kenya", "ken",
    "bermuda",
}

# Major franchise team names (IPL, BBL, PSL, CPL, Hundred, SA20, etc.)
FRANCHISE_TEAMS: dict[str, set[str]] = {
    "IPL": {
        "mumbai indians", "mi",
        "chennai super kings", "csk",
        "royal challengers", "rcb", "royal challengers bengaluru",
        "kolkata knight riders", "kkr",
        "sunrisers hyderabad", "srh",
        "rajasthan royals", "rr",
        "delhi capitals", "dc",
        "punjab kings", "pbks",
        "lucknow super giants", "lsg",
        "gujarat titans", "gt",
    },
    "BBL": {
        "sydney sixers", "sixers",
        "sydney thunder", "thunder",
        "melbourne stars", "stars",
        "melbourne renegades", "renegades",
        "brisbane heat", "heat",
        "perth scorchers", "scorchers",
        "hobart hurricanes", "hurricanes",
        "adelaide strikers", "strikers",
    },
    "PSL": {
        "karachi kings",
        "lahore qalandars", "qalandars",
        "islamabad united",
        "peshawar zalmi", "zalmi",
        "quetta gladiators", "gladiators",
        "multan sultans", "sultans",
    },
    "CPL": {
        "trinbago knight riders", "tkr",
        "guyana amazon warriors", "amazon warriors",
        "jamaica tallawahs", "tallawahs",
        "barbados royals", "royals",
        "st kitts and nevis patriots", "patriots",
        "st lucia kings",
    },
    "Hundred": {
        "oval invincibles", "invincibles",
        "trent rockets", "rockets",
        "southern brave", "brave",
        "birmingham phoenix", "phoenix",
        "manchester originals", "originals",
        "london spirit", "spirit",
        "northern superchargers", "superchargers",
        "welsh fire", "fire",
    },
    "SA20": {
        "sunrisers eastern cape",
        "mi cape town",
        "joburg super kings",
        "paarl royals",
        "durban super giants",
        "pretoria capitals",
    },
    "MLC": {
        "los angeles knight riders", "la knight riders",
        "mi new york",
        "san francisco unicorns",
        "seattle orcas",
        "texas super kings",
        "washington freedom",
    },
}

# Flatten all franchise teams into a single lookup set
_ALL_FRANCHISE_TEAMS: set[str] = set()
for _teams in FRANCHISE_TEAMS.values():
    _ALL_FRANCHISE_TEAMS.update(_teams)

# Combined known teams (international + franchise)
ALL_KNOWN_TEAMS: set[str] = INTERNATIONAL_TEAMS | _ALL_FRANCHISE_TEAMS


class MatchClassifier:
    """
    Decides whether a scraped match qualifies for data collection.

    Layer 1: Competition name pattern matching (whitelist/blacklist).
    Layer 2: Team name recognition against known teams database.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._whitelist_re = [re.compile(p, re.IGNORECASE) for p in COMPETITION_WHITELIST]
        self._blacklist_re = [re.compile(p, re.IGNORECASE) for p in COMPETITION_BLACKLIST]
        self._stats = {"accepted": 0, "rejected": 0, "unknown_teams_logged": 0}

    def qualifies(self, event: OddsEvent) -> bool:
        """
        Check if an OddsEvent qualifies for collection.

        Returns True if the match is an international or major franchise match.
        """
        if not self.enabled:
            return True

        # Layer 0: Blacklist always wins
        if self._is_blacklisted(event.competition):
            logger.debug(
                "match_rejected_blacklist",
                competition=event.competition,
                teams=f"{event.team_home} vs {event.team_away}",
            )
            self._stats["rejected"] += 1
            return False

        # Layer 1: Competition name whitelist
        if event.competition and self._is_whitelisted(event.competition):
            self._stats["accepted"] += 1
            return True

        # Layer 2: Team name recognition
        home_known = self._is_known_team(event.team_home)
        away_known = self._is_known_team(event.team_away)

        if home_known and away_known:
            self._stats["accepted"] += 1
            return True

        # Neither layer matched -- reject but log unknown teams
        if not home_known:
            logger.info("unknown_team", team=event.team_home, side="home",
                        competition=event.competition)
            self._stats["unknown_teams_logged"] += 1
        if not away_known:
            logger.info("unknown_team", team=event.team_away, side="away",
                        competition=event.competition)
            self._stats["unknown_teams_logged"] += 1

        self._stats["rejected"] += 1
        return False

    def _is_whitelisted(self, competition: str) -> bool:
        """Check if competition matches any whitelist pattern."""
        for pattern in self._whitelist_re:
            if pattern.search(competition):
                return True
        return False

    def _is_blacklisted(self, competition: str) -> bool:
        """Check if competition matches any blacklist pattern."""
        if not competition:
            return False
        for pattern in self._blacklist_re:
            if pattern.search(competition):
                return True
        return False

    def _is_known_team(self, team_name: str) -> bool:
        """Check if a team name is in the known teams database."""
        normalized = team_name.lower().strip()
        return normalized in ALL_KNOWN_TEAMS

    @property
    def stats(self) -> dict[str, int]:
        """Get filter statistics."""
        return dict(self._stats)
