"""
Link one real match across Betfair, LotusBook and Cricsheet.

A Betfair market is matched on the unordered pair of canonical team names
(``scripts.cricsheet_harmonizer.canonicalize_team``, falling back to the raw name)
and on the start time within a window. A candidate counts only if it is the only
one that matches. Ambiguous matches link to nothing, so closing prices are never
written to the wrong match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from scraper.parsers.betfair_parser import betfair_match_id
from scripts.cricsheet_harmonizer import canonicalize_team

DEFAULT_START_WINDOW = timedelta(hours=6)
_DRAW_NAME_RE = re.compile(r"^(the\s+)?draw$", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"\s+v(?:s)?\.?\s+", re.IGNORECASE)


@dataclass(frozen=True)
class MatchLink:
    betfair_market_id: str
    betfair_match_id: str
    lotusbook_match_id: Optional[str]
    cricsheet_match_id: Optional[str]
    status: str  # linked | ambiguous | unmatched


def normalise_team(name: str) -> str:
    """Canonical team name when known, else the whitespace-collapsed raw name, case-folded."""
    raw = " ".join(str(name or "").split())
    return (canonicalize_team(raw) or raw).casefold()


def team_pair(team_a: str, team_b: str) -> frozenset[str]:
    return frozenset({normalise_team(team_a), normalise_team(team_b)})


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def market_teams(catalogue_entry: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Home and away names from a catalogue entry: the first two runners that are not the draw."""
    others = [
        str(r.get("runnerName") or "").strip()
        for r in (catalogue_entry.get("runners") or [])
        if not _DRAW_NAME_RE.match(str(r.get("runnerName") or "").strip())
    ]
    others = [name for name in others if name]
    if len(others) >= 2:
        return others[0], others[1]
    parts = _SEPARATOR_RE.split(str((catalogue_entry.get("event") or {}).get("name") or ""), maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def _find_candidates(
    candidates: Iterable[dict[str, Any]],
    pair: frozenset[str],
    start: Optional[datetime],
    window: timedelta,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for candidate in candidates:
        if team_pair(str(candidate.get("team_home", "")), str(candidate.get("team_away", ""))) != pair:
            continue
        other_start = _parse_time(candidate.get("start_time"))
        if start is not None and other_start is not None and abs(other_start - start) > window:
            continue
        found.append(candidate)
    return found


def link_market(
    catalogue_entry: dict[str, Any],
    lotusbook_matches: Iterable[dict[str, Any]],
    cricsheet_matches: Iterable[dict[str, Any]],
    window: timedelta = DEFAULT_START_WINDOW,
) -> MatchLink:
    """
    Link one Betfair catalogue entry to LotusBook and Cricsheet matches.

    Candidates are dicts with ``match_id``, ``team_home``, ``team_away`` and optionally
    ``start_time``. Each source yields at most one link. If a source has more than one
    matching candidate, that source gets no link and the status is ``ambiguous``.
    """
    market_id = str(catalogue_entry.get("marketId") or "")
    bf_id = betfair_match_id(market_id)
    teams = market_teams(catalogue_entry)
    if not market_id or teams is None:
        return MatchLink(market_id, bf_id, None, None, "unmatched")

    pair = team_pair(*teams)
    start = _parse_time(catalogue_entry.get("marketStartTime"))
    lotus = _find_candidates(lotusbook_matches, pair, start, window)
    cricsheet = _find_candidates(cricsheet_matches, pair, start, window)
    ambiguous = len(lotus) > 1 or len(cricsheet) > 1
    lotus_id = str(lotus[0]["match_id"]) if len(lotus) == 1 and not ambiguous else None
    cricsheet_id = str(cricsheet[0]["match_id"]) if len(cricsheet) == 1 and not ambiguous else None

    if ambiguous:
        status = "ambiguous"
    elif lotus_id or cricsheet_id:
        status = "linked"
    else:
        status = "unmatched"
    return MatchLink(market_id, bf_id, lotus_id, cricsheet_id, status)
