"""
Group 8: Match Category Features (8 features)

Encodes the structural context of a match so the RL agent can learn
format-specific and tier-specific betting strategies.

Feature layout:
    0: is_t20              (1 if T20 format, else 0)
    1: is_odi              (1 if ODI format, else 0)
    2: is_test             (1 if Test/FC format, else 0)
    3: is_icc_event        (1 if ICC tournament, else 0)
    4: is_intl_bilateral   (1 if international bilateral, else 0)
    5: is_major_franchise  (1 if IPL/BBL/PSL/CPL/Hundred/SA20/MLC, else 0)
    6: is_minor_domestic   (1 if none of the above tiers, else 0)
    7: is_womens           (1 if women's match, else 0)

Rationale:
- Different formats have fundamentally different odds dynamics.
  T20s are volatile with quick swings; ODIs are slower; Tests span days.
- Tier determines market depth and liquidity. An IPL match has 10-100x the
  volume of a Ranji Trophy or minor league match, affecting spread reliability.
- Women's matches have thinner markets and different liquidity profiles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from shared.constants import (
    FRANCHISE_TEAMS,
    INTERNATIONAL_TEAMS,
)


# ============================================================
# Match Category Enums
# ============================================================


class MatchFormat(StrEnum):
    """Cricket match format."""
    T20 = "t20"
    ODI = "odi"
    TEST = "test"


class MatchTier(StrEnum):
    """Tournament/competition tier by market depth."""
    ICC_EVENT = "icc_event"              # World Cups, Champions Trophy, WTC
    INTERNATIONAL_BILATERAL = "intl_bilateral"  # Country vs Country tours
    MAJOR_FRANCHISE = "major_franchise"  # IPL, BBL, PSL, CPL, etc.
    MINOR_DOMESTIC = "minor_domestic"    # Everything else


# ============================================================
# MatchCategory Dataclass
# ============================================================


@dataclass(frozen=True, slots=True)
class MatchCategory:
    """Structured match classification for feature encoding."""
    format: MatchFormat = MatchFormat.T20
    tier: MatchTier = MatchTier.MINOR_DOMESTIC
    is_womens: bool = False


# ============================================================
# Classification Patterns
# ============================================================

# ICC event patterns (highest tier)
_ICC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"icc", r"world\s*cup", r"champions\s*trophy",
        r"\bwtc\b", r"world\s*test\s*championship",
    ]
]

# Format detection patterns
_ODI_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bodi\b", r"one\s*day", r"50\s*over",
    ]
]

_TEST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\btest\b", r"sheffield\s*shield", r"ranji\s*trophy",
        r"county\s*championship", r"first\s*class",
        r"\bfc\b", r"\bwtc\b", r"world\s*test",
    ]
]

_T20_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bt20\b", r"t20i", r"twenty\s*20", r"\bipl\b",
        r"\bbbl\b", r"\bpsl\b", r"\bcpl\b", r"\bhundred\b",
        r"\bsa20\b", r"\bmlc\b", r"\bilt20\b", r"\bbpl\b",
        r"\blpl\b", r"\bapl\b", r"super\s*smash",
        r"vitality\s*blast", r"premier\s*league",
        r"big\s*bash",
    ]
]

# Women's match patterns
_WOMENS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"women", r"\bwomen's\b", r"\bWPL\b",
        r"\bWBBL\b", r"\bW\s*$",  # trailing "W" suffix
    ]
]

# Women's team name suffixes (e.g. "Delhi Capitals W", "India Women")
_WOMENS_TEAM_RE = re.compile(
    r"\bwomen\b|\bW$|\bW\b$", re.IGNORECASE,
)

# Major franchise league names -> set of their team names (imported)
# Build a reverse lookup: team_name_lower -> league_name
_TEAM_TO_LEAGUE: dict[str, str] = {}
for _league, _teams in FRANCHISE_TEAMS.items():
    for _team in _teams:
        _TEAM_TO_LEAGUE[_team.lower()] = _league


# ============================================================
# Classifier
# ============================================================


class MatchCategoryClassifier:
    """
    Classifies a match into format, tier, and gender based on
    competition name and team names.

    Uses the same team/competition databases as `MatchClassifier`
    in `scraper/match_filter.py` but returns structured categories
    instead of a boolean.
    """

    def classify(
        self,
        competition: str,
        team_home: str,
        team_away: str,
    ) -> MatchCategory:
        """
        Classify a match.

        Args:
            competition: Competition/tournament name (may be empty).
            team_home: Home team name.
            team_away: Away team name.

        Returns:
            MatchCategory with format, tier, and gender.
        """
        fmt = self._detect_format(competition, team_home, team_away)
        tier = self._detect_tier(competition, team_home, team_away)
        is_womens = self._detect_womens(competition, team_home, team_away)

        return MatchCategory(format=fmt, tier=tier, is_womens=is_womens)

    # --- Format detection ---

    def _detect_format(
        self, competition: str, team_home: str, team_away: str,
    ) -> MatchFormat:
        """Infer match format from competition name and teams."""
        combined = f"{competition} {team_home} {team_away}"

        # Test/First-Class first (narrower match)
        for pattern in _TEST_PATTERNS:
            if pattern.search(combined):
                return MatchFormat.TEST

        # ODI
        for pattern in _ODI_PATTERNS:
            if pattern.search(combined):
                return MatchFormat.ODI

        # T20 (explicit match)
        for pattern in _T20_PATTERNS:
            if pattern.search(combined):
                return MatchFormat.T20

        # Check if teams belong to a known franchise league (all are T20)
        home_league = _TEAM_TO_LEAGUE.get(team_home.lower().strip())
        away_league = _TEAM_TO_LEAGUE.get(team_away.lower().strip())
        if home_league or away_league:
            return MatchFormat.T20

        # Default to T20 -- most common format on LotusBook
        return MatchFormat.T20

    # --- Tier detection ---

    @staticmethod
    def _strip_gender_suffix(name: str) -> str:
        """Remove trailing gender markers like ' W', ' Women' for lookup."""
        return re.sub(r"\s+(?:W|Women|Men)$", "", name, flags=re.IGNORECASE).strip()

    def _detect_tier(
        self, competition: str, team_home: str, team_away: str,
    ) -> MatchTier:
        """Determine market tier from competition and teams."""
        # ICC events (highest tier)
        for pattern in _ICC_PATTERNS:
            if pattern.search(competition):
                return MatchTier.ICC_EVENT

        home_lower = team_home.lower().strip()
        away_lower = team_away.lower().strip()

        # Also try with gender suffix stripped (e.g. "Delhi Capitals W" -> "Delhi Capitals")
        home_base = self._strip_gender_suffix(team_home).lower().strip()
        away_base = self._strip_gender_suffix(team_away).lower().strip()

        # Check if both teams are international
        home_intl = home_lower in INTERNATIONAL_TEAMS or home_base in INTERNATIONAL_TEAMS
        away_intl = away_lower in INTERNATIONAL_TEAMS or away_base in INTERNATIONAL_TEAMS
        if home_intl and away_intl:
            return MatchTier.INTERNATIONAL_BILATERAL

        # Check if teams belong to a major franchise league
        # Try both original and gender-stripped names
        home_league = (
            _TEAM_TO_LEAGUE.get(home_lower)
            or _TEAM_TO_LEAGUE.get(home_base)
        )
        away_league = (
            _TEAM_TO_LEAGUE.get(away_lower)
            or _TEAM_TO_LEAGUE.get(away_base)
        )
        if home_league or away_league:
            return MatchTier.MAJOR_FRANCHISE

        # Competition name might indicate a major league even if
        # team names aren't in our database yet
        major_league_patterns = [
            re.compile(p, re.IGNORECASE) for p in [
                r"\bipl\b", r"\bbbl\b", r"\bpsl\b", r"\bcpl\b",
                r"\bhundred\b", r"\bsa20\b", r"\bmlc\b",
                r"indian\s*premier", r"big\s*bash",
                r"pakistan\s*super", r"caribbean\s*premier",
            ]
        ]
        for pattern in major_league_patterns:
            if pattern.search(competition):
                return MatchTier.MAJOR_FRANCHISE

        # Fallback
        return MatchTier.MINOR_DOMESTIC

    # --- Gender detection ---

    def _detect_womens(
        self, competition: str, team_home: str, team_away: str,
    ) -> bool:
        """Detect if this is a women's match."""
        for pattern in _WOMENS_PATTERNS:
            if pattern.search(competition):
                return True

        if _WOMENS_TEAM_RE.search(team_home) or _WOMENS_TEAM_RE.search(team_away):
            return True

        return False


# ============================================================
# Feature Computation
# ============================================================


def compute_category_features(category: MatchCategory) -> list[float]:
    """
    Encode a MatchCategory as 8 binary features.

    Features:
        0: is_t20              (format one-hot)
        1: is_odi              (format one-hot)
        2: is_test             (format one-hot)
        3: is_icc_event        (tier one-hot)
        4: is_intl_bilateral   (tier one-hot)
        5: is_major_franchise  (tier one-hot)
        6: is_minor_domestic   (tier one-hot)
        7: is_womens           (binary)
    """
    # Format one-hot (3)
    is_t20 = 1.0 if category.format == MatchFormat.T20 else 0.0
    is_odi = 1.0 if category.format == MatchFormat.ODI else 0.0
    is_test = 1.0 if category.format == MatchFormat.TEST else 0.0

    # Tier one-hot (4)
    is_icc = 1.0 if category.tier == MatchTier.ICC_EVENT else 0.0
    is_intl = 1.0 if category.tier == MatchTier.INTERNATIONAL_BILATERAL else 0.0
    is_franchise = 1.0 if category.tier == MatchTier.MAJOR_FRANCHISE else 0.0
    is_domestic = 1.0 if category.tier == MatchTier.MINOR_DOMESTIC else 0.0

    # Gender (1)
    is_womens = 1.0 if category.is_womens else 0.0

    return [
        is_t20, is_odi, is_test,
        is_icc, is_intl, is_franchise, is_domestic,
        is_womens,
    ]
