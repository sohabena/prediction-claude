"""
LiveMatchTracker: derives MatchContext from LotusBook scraped data.

Builds match context (score, wickets, overs, run_rate, required_run_rate,
innings, balls_remaining) directly from the score_text that LotusBook
already displays on its cricket page.

Publishes to Redis keys consumed by FeaturePipeline, LiveTradingLoop,
and context_resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from features.extractors.category_features import MatchCategoryClassifier, MatchFormat
from shared.constants import CHANNEL_MATCH_CONTEXT, KEY_MATCH_CONTEXT
from shared.logging import setup_logging
from shared.schemas import MatchContext, MatchStatus, OddsEvent

logger = setup_logging("live_match_tracker")

# Regex patterns for parsing score_text variants from LotusBook
# Examples: "45/2 (8.3)", "123/5", "45/2(8.3)", "156-3 (18.2 Ov)"
_SCORE_RE = re.compile(r"(\d+)[/\-](\d+)")
_OVERS_RE = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*(?:ov(?:ers?)?)?\.?\s*\)?", re.IGNORECASE)


@dataclass
class _InningsState:
    """Tracks state within a single innings."""

    score: int = 0
    wickets: int = 0
    overs: float = 0.0
    final_score: int | None = None  # Set when innings ends


@dataclass
class _MatchState:
    """Per-match accumulated state for deriving full context."""

    match_id: str
    team_home: str = ""
    team_away: str = ""
    competition: str = ""
    match_format: str = "T20"
    max_overs: float = 20.0
    max_balls: int = 120
    current_innings: int = 1
    innings_history: list[_InningsState] = field(default_factory=lambda: [_InningsState()])
    is_live: bool = False
    last_score: int = 0
    last_wickets: int = 0
    last_overs: float = 0.0
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def current(self) -> _InningsState:
        return self.innings_history[-1]

    def first_innings_total(self) -> int | None:
        """Return 1st innings final score if available."""
        if len(self.innings_history) >= 2:
            return self.innings_history[0].final_score
        return None


class LiveMatchTracker:
    """
    Builds MatchContext from LotusBook OddsEvent data every tick.

    For each live match, tracks:
    - Score, wickets, overs (parsed from score_text)
    - Current run rate (calculated: score / overs)
    - Required run rate (calculated in 2nd innings from 1st innings total)
    - Innings transitions (detected via score/wickets reset)
    - Balls remaining (calculated from overs and format)
    - Match format (inferred from competition name)

    All context is derived from the same LotusBook data we already scrape.
    """

    def __init__(self) -> None:
        self._matches: dict[str, _MatchState] = {}
        self._classifier = MatchCategoryClassifier()

    def update(self, event: OddsEvent) -> MatchContext | None:
        """
        Update match state from a new OddsEvent and return MatchContext.

        Args:
            event: Latest OddsEvent from LotusBook scraper.

        Returns:
            MatchContext if the match is live and has score data,
            None if not live or no score data available.
        """
        match_id = event.match_id
        state = self._matches.get(match_id)

        if state is None:
            # First time seeing this match — initialize
            category = self._classifier.classify(
                event.competition, event.team_home, event.team_away,
            )
            fmt = category.format.value.upper()
            if fmt == "ODI":
                max_overs, max_balls = 50.0, 300
            elif fmt == "TEST":
                max_overs, max_balls = 90.0, 540  # per day
            else:
                max_overs, max_balls = 20.0, 120

            state = _MatchState(
                match_id=match_id,
                team_home=event.team_home,
                team_away=event.team_away,
                competition=event.competition,
                match_format=fmt,
                max_overs=max_overs,
                max_balls=max_balls,
            )
            self._matches[match_id] = state
            logger.info(
                "match_tracked",
                match_id=match_id,
                teams=f"{event.team_home} vs {event.team_away}",
                format=fmt,
            )

        state.is_live = event.is_live
        state.last_update = datetime.now(timezone.utc)

        # Parse score_text from LotusBook
        score, wickets, overs = self._parse_score_text(event.score_text)

        if score is not None:
            # Detect innings transition: score dropped significantly
            # (e.g., 156/8 → 12/0 means new innings started)
            if (
                state.last_score > 30
                and score < state.last_score * 0.3
                and wickets < state.last_wickets
            ):
                # New innings detected
                state.current.final_score = state.last_score
                state.innings_history.append(_InningsState())
                state.current_innings = len(state.innings_history)
                logger.info(
                    "innings_transition",
                    match_id=match_id,
                    new_innings=state.current_innings,
                    prev_score=state.last_score,
                    new_score=score,
                )

            # Update current innings state
            state.current.score = score
            state.current.wickets = wickets
            if overs is not None:
                state.current.overs = overs

            state.last_score = score
            state.last_wickets = wickets
            if overs is not None:
                state.last_overs = overs

        # Build MatchContext even without score_text (use last known state)
        if not state.is_live and state.last_score == 0:
            # Not live and no score data yet — skip context generation
            return None

        return self._build_context(state)

    def _parse_score_text(
        self, score_text: str,
    ) -> tuple[int | None, int, float | None]:
        """
        Parse score_text from LotusBook into (score, wickets, overs).

        Handles formats:
        - "45/2 (8.3)"
        - "123/5"
        - "45-2 (8.3 Ov)"
        - "156/3 (18.2)"

        Returns:
            Tuple of (score, wickets, overs). score/overs may be None.
        """
        if not score_text or not score_text.strip():
            return None, 0, None

        score: int | None = None
        wickets = 0
        overs: float | None = None

        # Parse score/wickets
        score_match = _SCORE_RE.search(score_text)
        if score_match:
            score = int(score_match.group(1))
            wickets = int(score_match.group(2))

        # Parse overs — look after the score pattern
        if score_match:
            remainder = score_text[score_match.end():]
            overs_match = _OVERS_RE.search(remainder)
            if overs_match:
                overs = float(overs_match.group(1))

        return score, wickets, overs

    def _build_context(self, state: _MatchState) -> MatchContext:
        """Build a MatchContext from accumulated match state."""
        innings = state.current
        overs = innings.overs or state.last_overs

        # Calculate run rate
        if overs > 0:
            run_rate = round(innings.score / overs, 2)
        else:
            run_rate = 0.0

        # Calculate required run rate (2nd innings only)
        req_rr = 0.0
        first_total = state.first_innings_total()
        if first_total is not None and state.current_innings >= 2:
            target = first_total + 1
            runs_needed = target - innings.score
            # Calculate balls remaining in this innings
            balls_bowled = self._overs_to_balls(overs)
            balls_left = max(0, state.max_balls - balls_bowled)
            overs_left = balls_left / 6.0
            if overs_left > 0 and runs_needed > 0:
                req_rr = round(runs_needed / overs_left, 2)

        # Calculate balls remaining
        balls_bowled = self._overs_to_balls(overs)
        balls_remaining = max(0, state.max_balls - balls_bowled)

        # Determine status
        if state.is_live:
            status = MatchStatus.LIVE
        else:
            status = MatchStatus.SCHEDULED

        # Determine batting team (simple heuristic: home bats first in odd innings)
        if state.current_innings % 2 == 1:
            batting_team = state.team_home
            bowling_team = state.team_away
        else:
            batting_team = state.team_away
            bowling_team = state.team_home

        return MatchContext(
            match_id=state.match_id,
            timestamp=state.last_update,
            is_live=state.is_live,
            score=innings.score,
            wickets=innings.wickets,
            overs=overs,
            run_rate=run_rate,
            required_run_rate=req_rr,
            innings=state.current_innings,
            balls_remaining=balls_remaining,
            max_overs=state.max_overs,
            max_balls=state.max_balls,
            batting_team=batting_team,
            bowling_team=bowling_team,
            status=status,
            match_format=state.match_format,
        )

    @staticmethod
    def _overs_to_balls(overs: float) -> int:
        """Convert overs (e.g. 8.3) to balls (e.g. 51)."""
        complete = int(overs)
        partial = round((overs - complete) * 10)
        return complete * 6 + partial

    def remove_match(self, match_id: str) -> None:
        """Remove a match from tracking (e.g. when it completes)."""
        self._matches.pop(match_id, None)

    def get_state(self, match_id: str) -> _MatchState | None:
        """Get the internal state for a match (for debugging)."""
        return self._matches.get(match_id)

    @property
    def tracked_matches(self) -> list[str]:
        """List of currently tracked match IDs."""
        return list(self._matches.keys())
