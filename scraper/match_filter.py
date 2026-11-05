"""
International match classifier.
Two-layer filtering: competition name patterns, then team name recognition.
Only qualifying matches are stored and published.
"""

from __future__ import annotations

import re
from typing import Any

from shared.constants import FRANCHISE_TEAMS, INTERNATIONAL_TEAMS
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("match_filter")

# ============================================================
# Layer 1: Competition / Tournament Patterns
# ============================================================

# Whitelisted competition patterns (case-insensitive)
# Only ICC events, international bilateral, and top-tier franchise leagues.
COMPETITION_WHITELIST: list[str] = [
    # ICC events
    r"icc", r"world\s*cup", r"champions\s*trophy", r"wtc",
    # International formats
    r"t20i", r"odi", r"\btest\b", r"bilateral",
    # Major franchise leagues (top-tier only)
    # Note: "Indian Premier League" does not contain the substring "ipl"; use indian\s*premier + explicit ipl/tata
    r"\bipl\b", r"indian\s*premier", r"tata\s*ipl",
    r"\bwpl\b", r"women'?s\s*premier", r"women\s+premier",
    r"\bbbl\b", r"big\s*bash",
    r"\bpsl\b", r"pakistan\s*super",
    r"\bcpl\b", r"caribbean\s*premier",
    r"the\s*hundred", r"\bhundred\b",
    r"\bsa20\b", r"sa\s*20",
    r"\bmlc\b", r"major\s*league\s*cricket",
    r"\bilt20\b", r"international\s*league\s*t20",
]

# Blacklisted patterns (always reject, even if whitelist matches)
COMPETITION_BLACKLIST: list[str] = [
    r"\bsrl\b", r"simulated", r"virtual", r"esports", r"e-?sports",
    r"cyber", r"fantasy", r"practice", r"warm.?up",
    # Minor / junk leagues
    r"jagran", r"\bu19\b", r"under.?19", r"legends?",
    r"invitational", r"provincial", r"domestic",
    # Minor domestic leagues (removed from whitelist)
    r"super\s*smash", r"vitality\s*blast", r"county\s*championship",
    r"sheffield\s*shield", r"ranji\s*trophy",
    r"bangladesh\s*premier", r"lanka\s*premier", r"afghanistan\s*premier",
]

# Simulated / computer-generated cricket (S37). Token matches, case-insensitive, checked on
# competition and team names. These are never real cricket and must never be recorded.
SIMULATED_PATTERNS: list[str] = [
    r"\bsrl\b", r"\bsimulated\b", r"\bvirtual\b", r"\be-?cricket\b",
]
_SIMULATED_RE = [re.compile(p, re.IGNORECASE) for p in SIMULATED_PATTERNS]


def is_simulated_name(*names: str | None) -> bool:
    """True when any name carries a simulated-league token (SRL, Simulated, Virtual, E-Cricket)."""
    for name in names:
        if not name:
            continue
        if any(p.search(name) for p in _SIMULATED_RE):
            return True
    return False


# ============================================================
# Layer 2: Team Name Database (imported from shared.constants)
# ============================================================

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
        self._stats = {"accepted": 0, "rejected": 0, "unknown_teams_logged": 0, "simulated_rejected": 0}

    def reject_if_simulated(self, event: OddsEvent) -> bool:
        """Return True and count the event when it is a simulated (SRL/virtual) match.

        Independent of ``enabled``: the recording gate calls it even when the filter is off.
        """
        if not is_simulated_name(event.competition, event.team_home, event.team_away):
            return False
        self._stats["simulated_rejected"] += 1
        logger.debug(
            "match_rejected_simulated",
            competition=event.competition,
            teams=f"{event.team_home} vs {event.team_away}",
        )
        return True

    def qualifies(self, event: OddsEvent) -> bool:
        """
        Check if an OddsEvent qualifies for collection.

        Returns True if the match is an international or major franchise match.
        """
        if not self.enabled:
            return True

        # Layer 0a (S37): simulated leagues are rejected and counted separately
        if self.reject_if_simulated(event):
            self._stats["rejected"] += 1
            return False

        # Layer 0: Blacklist always wins (checks competition, team_home, and team_away)
        if (
            self._is_blacklisted(event.competition)
            or self._is_blacklisted(event.team_home)
            or self._is_blacklisted(event.team_away)
        ):
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

    def classify(self, event: OddsEvent) -> "MatchCategory":
        """
        Classify a match into format, tier, and gender.

        Returns a structured MatchCategory. This is independent of the
        accept/reject decision in ``qualifies()``.

        Lazy-imports to avoid circular dependency (category_features
        imports constants from this module).
        """
        from features.extractors.category_features import MatchCategoryClassifier

        if not hasattr(self, "_category_classifier"):
            self._category_classifier = MatchCategoryClassifier()

        return self._category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )

    def is_international_or_major(self, event: OddsEvent) -> bool:
        """
        Strictly verify whether a match is official international or major franchise.
        Returns False for all SRL, simulated, cyber, minor, and unverified matches.
        """
        # 1. Any blacklist pattern in competition or team names immediately fails
        if (
            self._is_blacklisted(event.competition)
            or self._is_blacklisted(event.team_home)
            or self._is_blacklisted(event.team_away)
        ):
            return False

        # 2. Whitelisted competition name
        if event.competition and self._is_whitelisted(event.competition):
            return True

        # 3. Known team name recognition (both teams must be recognised)
        return self._is_known_team(event.team_home) and self._is_known_team(event.team_away)

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
        if normalized in ALL_KNOWN_TEAMS:
            return True
        # LotusBook often appends " W" / " Women" for women's franchise sides (WPL).
        stripped = self._strip_womens_suffix(normalized)
        return stripped != normalized and stripped in ALL_KNOWN_TEAMS

    @staticmethod
    def _strip_womens_suffix(normalized: str) -> str:
        """Remove common women's-side suffixes so WPL teams match men's franchise aliases."""
        n = normalized
        for suffix in (" (w)", " w", " women", " w."):
            if n.endswith(suffix):
                n = n[: -len(suffix)].strip()
                break
        return n

    @property
    def stats(self) -> dict[str, int]:
        """Get filter statistics."""
        return dict(self._stats)
