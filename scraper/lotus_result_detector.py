"""
LotusBook-based match result detector.

Results are only written when they can be trusted:

1. A match is "completed" when it was live but disappears from the scrape
   batch for a configurable duration (default 5 minutes).
2. A winner is inferred only from decisive prices (or a completed chase in
   the score tracker). Decisive means, within the last
   DECISIVE_WINDOW_SECONDS before the match disappeared, one side's back
   price is at or below DECISIVE_BACK, the other side's back is at or above
   DECISIVE_LONG_BACK, or one side's lay is at or below DECISIVE_LAY.
   Otherwise the match is stored as 'unresolved' (empty winner) so a later,
   better source can settle it. A disappearance is never a 'no_result'.
3. The official scoreboard comment (update_scoreboard) is the only source of
   'no_result', 'abandoned' and 'tie'. A scoreboard result overrides a price
   inference; a price inference never overrides a scoreboard result.
4. Results are stored in match_results and published to Redis
   CHANNEL_MATCH_RESULTS for downstream settlement. 'unresolved' rows are
   stored but not published.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.constants import CHANNEL_MATCH_RESULTS
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("lotus_result_detector")

# Legacy odds constants (no longer used for inference; kept for importers)
WINNER_ODDS_THRESHOLD = 1.10
AMBIGUOUS_ODDS_FLOOR = 1.30
AMBIGUOUS_THRESHOLD_SECONDS = 3600  # 1 hour

# Seconds a match must be absent from scrape results before we declare it completed
ABSENCE_THRESHOLD_SECONDS = 300  # 5 minutes

# Decisive price rules (S54)
DECISIVE_BACK = 1.05        # a side's back at or below this -> that side is winning
DECISIVE_LONG_BACK = 15.0   # the other side's back at or above this -> this side is winning
DECISIVE_LAY = 1.06         # a side's lay at or below this -> that side is winning
DECISIVE_WINDOW_SECONDS = 600  # look-back before the match disappeared
MAX_TICK_HISTORY = 500

SOURCE_ODDS = "lotusbook_odds"
SOURCE_SCOREBOARD = "lotusbook_scoreboard"

RESULT_WIN = "win"
RESULT_TIE = "tie"
RESULT_DRAW = "draw"
RESULT_NO_RESULT = "no_result"
RESULT_ABANDONED = "abandoned"
RESULT_UNRESOLVED = "unresolved"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_ABANDONED_RE = re.compile(r"\babandoned\b", re.IGNORECASE)
_NO_RESULT_RE = re.compile(r"\bno[\s-]+result\b", re.IGNORECASE)
_TIE_RE = re.compile(r"\b(?:match\s+)?(?:tied|tie)\b", re.IGNORECASE)
_DRAWN_RE = re.compile(r"\bdrawn\b", re.IGNORECASE)
_WON_RE = re.compile(
    r"^\s*(?P<team>.+?)\s+won(?:\s+by\s+(?P<margin>.+?))?\s*\.?\s*$",
    re.IGNORECASE,
)


try:  # S51 ships the scoreboard parser; fall back to the local forms if absent
    from scraper.parsers.lotusbook_scoreboard import parse_result_comment as _s51_parse_comment
except ImportError:  # pragma: no cover - depends on checkout contents
    _s51_parse_comment = None


def _local_parse_comment(text: str) -> tuple[str, str, str] | None:
    if _NO_RESULT_RE.search(text):
        return RESULT_NO_RESULT, "", ""
    if _TIE_RE.search(text):
        return RESULT_TIE, "", ""
    m = _WON_RE.match(text)
    if m:
        margin = (m.group("margin") or "").strip().rstrip(".").strip()
        return RESULT_WIN, m.group("team").strip(), margin
    return None


def parse_result_comment(comment: str) -> tuple[str, str, str] | None:
    """
    Parse an official scoreboard comment.

    Returns (result_type, winner_text, margin) or None when unrecognised.
    winner_text is the team name as written in the comment (empty unless win).
    margin is text such as "25 runs" or "6 wickets" (empty when absent).
    """
    text = " ".join((comment or "").split())
    if not text:
        return None
    if _ABANDONED_RE.search(text):
        return RESULT_ABANDONED, "", ""
    if _DRAWN_RE.search(text):
        return RESULT_DRAW, "", ""

    if _s51_parse_comment is not None:
        parsed = _s51_parse_comment(text)
        if parsed is not None:
            winner, kind, margin_value = parsed
            if kind == "tie":
                return RESULT_TIE, "", ""
            if kind == "no_result":
                return RESULT_NO_RESULT, "", ""
            if kind in ("runs", "wickets") and winner:
                unit = kind[:-1] if margin_value == 1 else kind  # "1 run", "2 runs"
                margin = f"{margin_value} {unit}" if margin_value is not None else ""
                return RESULT_WIN, winner, margin
            return None

    # S51 has no form for a win without a margin, and no parser at all in some checkouts
    return _local_parse_comment(text)


def _norm_team(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _side_from_prices(
    back_home: float | None,
    back_away: float | None,
    lay_home: float | None,
    lay_away: float | None,
) -> str | None:
    """Return 'home' or 'away' if one tick's prices are decisive, else None."""
    home = (
        (back_home is not None and back_home <= DECISIVE_BACK)
        or (back_away is not None and back_away >= DECISIVE_LONG_BACK)
        or (lay_home is not None and lay_home <= DECISIVE_LAY)
    )
    away = (
        (back_away is not None and back_away <= DECISIVE_BACK)
        or (back_home is not None and back_home >= DECISIVE_LONG_BACK)
        or (lay_away is not None and lay_away <= DECISIVE_LAY)
    )
    if home == away:  # neither, or contradictory
        return None
    return "home" if home else "away"


@dataclass
class _TrackedMatch:
    """Internal state for a match being monitored for completion."""

    match_id: str
    team_home: str
    team_away: str
    competition: str
    was_live: bool = False
    last_seen: datetime = field(default_factory=_utcnow)
    # Last known odds (updated every tick)
    last_back_home: float | None = None
    last_back_away: float | None = None
    last_lay_home: float | None = None
    last_lay_away: float | None = None
    # Price history: (timestamp, back_home, back_away, lay_home, lay_away); None = not quoted
    ticks: deque = field(default_factory=lambda: deque(maxlen=MAX_TICK_HISTORY))
    # Score tracking for cross-reference (from LiveMatchTracker)
    last_innings: int = 1
    last_score: int = 0
    first_innings_total: int | None = None
    # Which source wrote the stored result for this match (None = nothing written yet)
    written_source: str | None = None


class LotusResultDetector:
    """
    Detects match completion from LotusBook odds disappearance patterns.

    Usage:
        detector = LotusResultDetector()

        # Call on every scrape cycle with the current batch of match IDs
        detector.update_tick(...)  # for each live event
        await detector.check_completed(current_batch_ids)  # after processing batch

        # When the match page shows the official scoreboard comment
        await detector.update_scoreboard(match_id, comment, finished=True)
    """

    def __init__(
        self,
        absence_threshold: int = ABSENCE_THRESHOLD_SECONDS,
    ) -> None:
        self._matches: dict[str, _TrackedMatch] = {}
        self._absence_threshold = absence_threshold
        self._resolved_ids: set[str] = set()

    def load_already_resolved(self, match_ids: set[str]) -> None:
        """Pre-load match IDs that already have results (from DB at startup)."""
        self._resolved_ids = set(match_ids)

    def update_tick(
        self,
        match_id: str,
        team_home: str,
        team_away: str,
        competition: str,
        is_live: bool,
        back_home: float | None,
        back_away: float | None,
        lay_home: float | None = None,
        lay_away: float | None = None,
        innings: int | None = None,
        score: int | None = None,
        first_innings_total: int | None = None,
    ) -> None:
        """Update tracker with latest tick data for a match."""
        if match_id in self._resolved_ids:
            return

        now = _utcnow()
        tracked = self._matches.get(match_id)

        if tracked is None:
            tracked = _TrackedMatch(
                match_id=match_id,
                team_home=team_home,
                team_away=team_away,
                competition=competition,
                last_seen=now,
            )
            self._matches[match_id] = tracked

        # Team names are copied from the ticks so winners join exactly
        if team_home:
            tracked.team_home = team_home
        if team_away:
            tracked.team_away = team_away
        if competition:
            tracked.competition = competition

        # A match marked unresolved from prices that shows up again is not finished: let it be re-judged
        if tracked.written_source == SOURCE_ODDS:
            tracked.written_source = None

        tracked.last_seen = now
        if is_live:
            tracked.was_live = True
        if any(v is not None for v in (back_home, back_away, lay_home, lay_away)):
            tracked.ticks.append((now, back_home, back_away, lay_home, lay_away))
        if back_home is not None:
            tracked.last_back_home = back_home
        if back_away is not None:
            tracked.last_back_away = back_away
        if lay_home is not None:
            tracked.last_lay_home = lay_home
        if lay_away is not None:
            tracked.last_lay_away = lay_away
        if innings is not None:
            tracked.last_innings = innings
        if score is not None:
            tracked.last_score = score
        if first_innings_total is not None:
            tracked.first_innings_total = first_innings_total

    async def check_completed(
        self, current_batch_ids: set[str],
    ) -> list[dict[str, Any]]:
        """
        Check for matches that have completed (absent from current batch).

        Args:
            current_batch_ids: Match IDs present in the latest scrape batch.

        Returns:
            List of result dicts (win or unresolved) for newly completed matches.
        """
        now = _utcnow()
        results: list[dict[str, Any]] = []

        for match_id, tracked in list(self._matches.items()):
            if tracked.written_source is not None:
                continue
            if match_id in self._resolved_ids:
                continue

            # Only consider matches that were live at some point
            if not tracked.was_live:
                continue

            # Match is still in the current batch — not completed yet
            if match_id in current_batch_ids:
                continue

            # Match absent — check if it's been gone long enough
            absence = (now - tracked.last_seen).total_seconds()
            if absence < self._absence_threshold:
                continue

            result = self._infer_result(tracked, now)
            tracked.written_source = SOURCE_ODDS
            if result["result_type"] == RESULT_WIN:
                self._resolved_ids.add(match_id)
            else:
                # Unresolved: stays eligible for a scoreboard result
                logger.warning(
                    "match_unresolved",
                    match_id=match_id,
                    absence_seconds=round(absence),
                )

            results.append(result)

            # Persist to DB and publish to Redis
            await self._store_and_publish(result)

            logger.info(
                "match_result_detected",
                match_id=match_id,
                winner=result["winner"],
                result_type=result["result_type"],
                closing_back_home=tracked.last_back_home,
                closing_back_away=tracked.last_back_away,
                absence_seconds=round(absence),
            )

        return results

    async def update_scoreboard(
        self, match_id: str, comment: str, finished: bool,
    ) -> dict[str, Any] | None:
        """
        Apply the official scoreboard comment ("X won by 25 runs", "Match tied",
        "No result"). Overrides a price inference; never overridden by one.

        Returns the stored result dict, or None when nothing was written.
        """
        if not finished:
            return None

        tracked = self._matches.get(match_id)
        if tracked is None:
            logger.warning("scoreboard_result_untracked", match_id=match_id)
            return None
        if tracked.written_source == SOURCE_SCOREBOARD:
            return None

        parsed = parse_result_comment(comment)
        if parsed is None:
            logger.warning("scoreboard_comment_unparsed", match_id=match_id, comment=comment)
            return None
        result_type, winner_text, margin = parsed

        winner = ""
        loser = ""
        if result_type == RESULT_WIN:
            winner_name = _norm_team(winner_text)
            if winner_name == _norm_team(tracked.team_home):
                winner = tracked.team_home
            elif winner_name == _norm_team(tracked.team_away):
                winner = tracked.team_away
            else:
                logger.warning(
                    "scoreboard_winner_unmatched",
                    match_id=match_id,
                    comment=comment,
                    team_home=tracked.team_home,
                    team_away=tracked.team_away,
                )
                return None
            loser = tracked.team_away if winner == tracked.team_home else tracked.team_home

        result = self._build_result(
            tracked, _utcnow(), winner, loser, result_type, margin, SOURCE_SCOREBOARD,
        )
        tracked.written_source = SOURCE_SCOREBOARD
        self._resolved_ids.add(match_id)

        await self._store_and_publish(result)

        logger.info(
            "match_result_scoreboard",
            match_id=match_id,
            winner=winner,
            result_type=result_type,
            margin=margin,
        )
        return result

    async def update_settled(
        self, match_id: str, winner: str, source: str = "lotusbook_settled",
    ) -> dict[str, Any] | None:
        """Apply a settled winner from LotusBook's own market JSON (S56): a CLOSED Winner market with one
        WINNER/WON runner. ``winner`` is that runner's name, matched to a team exactly as a scoreboard winner.

        Behaves like a scoreboard result: it overrides a price inference and is never overridden by one.
        A scoreboard result is never replaced, and a settled result is written once.
        Returns the stored result dict, or None when nothing was written.
        """
        tracked = self._matches.get(match_id)
        if tracked is None:
            logger.warning("settled_result_untracked", match_id=match_id)
            return None
        if tracked.written_source in (SOURCE_SCOREBOARD, source):
            return None

        winner_name = _norm_team(winner)
        if winner_name == _norm_team(tracked.team_home):
            winner_team = tracked.team_home
        elif winner_name == _norm_team(tracked.team_away):
            winner_team = tracked.team_away
        else:
            logger.warning(
                "settled_winner_unmatched",
                match_id=match_id,
                winner=winner,
                team_home=tracked.team_home,
                team_away=tracked.team_away,
            )
            return None
        loser = tracked.team_away if winner_team == tracked.team_home else tracked.team_home

        result = self._build_result(tracked, _utcnow(), winner_team, loser, RESULT_WIN, "", source)
        tracked.written_source = source
        self._resolved_ids.add(match_id)

        await self._store_and_publish(result)

        logger.info("match_result_settled", match_id=match_id, winner=winner_team, source=source)
        return result

    def _decisive_side(self, tracked: _TrackedMatch) -> str | None:
        """Newest decisive side within the window before the match disappeared."""
        cutoff = tracked.last_seen - timedelta(seconds=DECISIVE_WINDOW_SECONDS)
        for ts, bh, ba, lh, la in reversed(tracked.ticks):
            if ts < cutoff:
                break
            side = _side_from_prices(bh, ba, lh, la)
            if side is not None:
                return side
        return None

    def _infer_result(
        self, tracked: _TrackedMatch, now: datetime,
    ) -> dict[str, Any]:
        """Infer a win from score or decisive prices; otherwise 'unresolved'."""
        # --- Cross-reference: score-based winner detection (most reliable) ---
        # If 2nd innings and score exceeds 1st innings total, chasing team won
        score_winner = None
        if (
            tracked.last_innings >= 2
            and tracked.first_innings_total is not None
            and tracked.last_score > tracked.first_innings_total
        ):
            # In cricket, home bats first (odd innings), away chases (even innings)
            if tracked.last_innings % 2 == 0:
                score_winner = tracked.team_away
            else:
                score_winner = tracked.team_home
            logger.info(
                "winner_from_score",
                match_id=tracked.match_id,
                winner=score_winner,
                chase_score=tracked.last_score,
                target=tracked.first_innings_total + 1,
            )

        # --- Price-based winner detection: decisive prices only ---
        side = self._decisive_side(tracked)
        odds_winner = None
        if side == "home":
            odds_winner = tracked.team_home
        elif side == "away":
            odds_winner = tracked.team_away

        if score_winner and odds_winner and score_winner != odds_winner:
            logger.warning(
                "score_odds_winner_mismatch",
                match_id=tracked.match_id,
                score_winner=score_winner,
                odds_winner=odds_winner,
            )

        winner = score_winner or odds_winner
        if not winner:
            logger.info(
                "match_absent_no_decisive_prices",
                match_id=tracked.match_id,
                closing_back_home=tracked.last_back_home,
                closing_back_away=tracked.last_back_away,
            )
            return self._build_result(tracked, now, "", "", RESULT_UNRESOLVED, "", SOURCE_ODDS)

        loser = tracked.team_away if winner == tracked.team_home else tracked.team_home
        return self._build_result(tracked, now, winner, loser, RESULT_WIN, "", SOURCE_ODDS)

    def _build_result(
        self,
        tracked: _TrackedMatch,
        now: datetime,
        winner: str,
        loser: str,
        result_type: str,
        margin: str,
        source: str,
    ) -> dict[str, Any]:
        return {
            "match_id": tracked.match_id,
            "completed_at": now.isoformat(),
            "winner": winner,
            "loser": loser,
            "result_type": result_type,
            "margin": margin,
            "team_home": tracked.team_home,
            "team_away": tracked.team_away,
            "source": source,
            "closing_back_home": tracked.last_back_home,
            "closing_back_away": tracked.last_back_away,
            "closing_lay_home": tracked.last_lay_home,
            "closing_lay_away": tracked.last_lay_away,
        }

    async def _store_and_publish(self, result: dict[str, Any]) -> None:
        """Store result in DB (upsert) and publish final results to Redis."""
        from backend.models.results import MatchResultRecord

        try:
            async with get_session() as session:
                record = MatchResultRecord(
                    match_id=result["match_id"],
                    completed_at=datetime.fromisoformat(result["completed_at"]),
                    winner=result["winner"],
                    loser=result["loser"],
                    result_type=result["result_type"],
                    margin=result["margin"],
                    team_home=result["team_home"],
                    team_away=result["team_away"],
                    source=result["source"],
                    closing_back_home=result.get("closing_back_home"),
                    closing_back_away=result.get("closing_back_away"),
                    closing_lay_home=result.get("closing_lay_home"),
                    closing_lay_away=result.get("closing_lay_away"),
                )
                # merge: a scoreboard result replaces an earlier price row for the same match
                await session.merge(record)

            logger.info("result_stored_db", match_id=result["match_id"])
        except Exception as e:
            logger.error(
                "result_store_error",
                match_id=result["match_id"],
                error=str(e),
            )

        # Unresolved rows are stored but never sent to settlement
        if result["result_type"] == RESULT_UNRESOLVED:
            return

        # Simulated (SRL) results are recorded for later study but never reach live consumers (S50)
        from scraper.match_filter import is_simulated_name

        if is_simulated_name(result.get("competition", ""), result["team_home"], result["team_away"]):
            return

        # Publish to Redis for downstream consumers (settlement, orchestrator)
        try:
            redis = await get_redis()
            await redis.publish_event(CHANNEL_MATCH_RESULTS, result)
            logger.info("result_published_redis", match_id=result["match_id"])
        except Exception as e:
            logger.error(
                "result_publish_error",
                match_id=result["match_id"],
                error=str(e),
            )

    def cleanup(self, max_age_hours: int = 48) -> None:
        """Remove old finished matches (stored results) from memory."""
        now = _utcnow()
        to_remove = []
        for mid, tracked in self._matches.items():
            if tracked.written_source is not None:
                age = (now - tracked.last_seen).total_seconds()
                if age > max_age_hours * 3600:
                    to_remove.append(mid)
        for mid in to_remove:
            del self._matches[mid]
