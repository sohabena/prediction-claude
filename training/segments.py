"""S63 pre-registered evaluation segments.

The segment list is frozen at module import: it is a tuple of immutable :class:`Segment` objects and cannot be
extended at runtime. Every gate report records :data:`SEGMENTS_VERSION`. Changing a predicate, a threshold or the
list means bumping the version and writing a new story, so the harness cannot be tuned into finding an edge.

A segment predicate receives one evaluation row: ``family`` (t20/odi/test/unknown), ``competition``,
``team_home`` and ``team_away`` (optional, used by the gender rule), ``is_simulated`` and ``p_market`` (the de-vigged
market probability at the snapshot, or None).

Version history: ``2026.11.03-2`` (S67) moved the gender predicates to the rule shared with ``strategy.live_context``,
which also reads team names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

SEGMENTS_VERSION = "2026.11.03-2"

FAVOURITE_MIN_P = 0.65
LONGSHOT_MAX_P = 0.35
# The one signal rule the policy simulation applies, for every segment (pre-registered).
POLICY_MIN_EDGE = 0.02
# Segment whose minimum edge the policy uses. Only "all" decides bets; other segments only slice the results.
POLICY_SEGMENT = "all"

# One gender rule for the whole project (S67). A competition is women's when it carries a women's token; a team is
# women's when its name ends with " W", " (W)", " W." or " Women". live_context.gender_of uses this same rule.
_WOMEN_COMPETITION_RE = re.compile(
    r"\bwomen|\bwbbl\b|\bwpl\b|\bwodi\b|\bwt20i?\b|\bw-?(?:t20|odi)\b", re.IGNORECASE
)
_WOMEN_TEAM_SUFFIX_RE = re.compile(r"\s+(?:\(w\)|w\.?|women)$", re.IGNORECASE)
_TIER_1_RE = re.compile(
    r"\bipl\b|indian premier league|big bash|\bbbl\b|\bpsl\b|pakistan super league|caribbean premier league"
    r"|\bcpl\b|vitality blast|the hundred|\bsa20\b|international|world cup|\bt20i\b",
    re.IGNORECASE,
)


def is_womens_team(name: Any) -> bool:
    """True for a women's side name such as ``Sydney Thunder W`` or ``Delhi Capitals Women``."""
    return bool(_WOMEN_TEAM_SUFFIX_RE.search(str(name or "").strip()))


def strip_womens_team(name: Any) -> str:
    """Men's rating name for a women's side: ``Sydney Thunder W`` -> ``Sydney Thunder``. Other names are unchanged."""
    text = str(name or "").strip()
    return _WOMEN_TEAM_SUFFIX_RE.sub("", text).strip() if is_womens_team(text) else text


def gender_of(competition: Any, team_home: Any = None, team_away: Any = None) -> str:
    """``women`` when the competition has a women's token or either side is a women's side, else ``men``."""
    if _WOMEN_COMPETITION_RE.search(str(competition or "")):
        return "women"
    if is_womens_team(team_home) or is_womens_team(team_away):
        return "women"
    return "men"


def tier_of(competition: Any) -> str:
    """``tier_1`` for the established leagues and internationals, else ``tier_2``."""
    return "tier_1" if _TIER_1_RE.search(str(competition or "")) else "tier_2"


@dataclass(frozen=True)
class Segment:
    name: str
    description: str
    predicate: Callable[[Mapping[str, Any]], bool]

    def contains(self, row: Mapping[str, Any]) -> bool:
        return bool(self.predicate(row))


def _family(name: str) -> Callable[[Mapping[str, Any]], bool]:
    return lambda row: row.get("family") == name


def _gender(row: Mapping[str, Any]) -> str:
    return gender_of(row.get("competition"), row.get("team_home"), row.get("team_away"))


def _p_market(row: Mapping[str, Any]) -> float | None:
    value = row.get("p_market")
    return None if value is None else float(value)


SEGMENTS: tuple[Segment, ...] = (
    Segment("all", "every settled match", lambda row: True),
    Segment("family_t20", "family t20", _family("t20")),
    Segment("family_odi", "family odi", _family("odi")),
    Segment("family_test", "family test", _family("test")),
    Segment("gender_men", "men's competition and sides", lambda row: _gender(row) == "men"),
    Segment("gender_women", "women's competition or side", lambda row: _gender(row) == "women"),
    Segment("tier_1", "established leagues and internationals", lambda row: tier_of(row.get("competition")) == "tier_1"),
    Segment("tier_2", "all other competitions", lambda row: tier_of(row.get("competition")) == "tier_2"),
    Segment(
        "market_favourite",
        f"market home-probability >= {FAVOURITE_MIN_P}",
        lambda row: _p_market(row) is not None and _p_market(row) >= FAVOURITE_MIN_P,
    ),
    Segment(
        "market_mid",
        f"market home-probability in ({LONGSHOT_MAX_P}, {FAVOURITE_MIN_P})",
        lambda row: _p_market(row) is not None and LONGSHOT_MAX_P < _p_market(row) < FAVOURITE_MIN_P,
    ),
    Segment(
        "market_longshot",
        f"market home-probability <= {LONGSHOT_MAX_P}",
        lambda row: _p_market(row) is not None and _p_market(row) <= LONGSHOT_MAX_P,
    ),
    Segment("real", "real (non-simulated) matches", lambda row: not row.get("is_simulated")),
    Segment("simulated", "simulated (SRL) matches", lambda row: bool(row.get("is_simulated"))),
)

SEGMENT_NAMES: tuple[str, ...] = tuple(segment.name for segment in SEGMENTS)
_BY_NAME: dict[str, Segment] = {segment.name: segment for segment in SEGMENTS}


def get_segment(name: str) -> Segment:
    """Return the pre-registered segment called ``name``; unknown names raise ValueError."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown segment {name!r}; pre-registered segments are {list(SEGMENT_NAMES)}") from None


def validate_segment_names(names: Any) -> tuple[str, ...]:
    """Check a list of segment names against the frozen list. Returns them as a tuple in the given order."""
    checked = tuple(names)
    for name in checked:
        get_segment(name)
    return checked


def segments_for(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Names of every pre-registered segment the row belongs to, in registry order."""
    return tuple(segment.name for segment in SEGMENTS if segment.contains(row))


def policy_min_edges() -> dict[str, float]:
    """The ``min_edge_by_segment`` mapping passed to ``strategy.staking.select``: one value per segment."""
    return {name: POLICY_MIN_EDGE for name in SEGMENT_NAMES}


__all__ = [
    "SEGMENTS_VERSION",
    "SEGMENTS",
    "SEGMENT_NAMES",
    "Segment",
    "POLICY_MIN_EDGE",
    "POLICY_SEGMENT",
    "gender_of",
    "is_womens_team",
    "strip_womens_team",
    "tier_of",
    "get_segment",
    "validate_segment_names",
    "segments_for",
    "policy_min_edges",
]
