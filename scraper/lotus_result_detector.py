"""
LotusBook-based match result detector.

Infers match results from LotusBook odds data alone:

1. A match is "completed" when it was live but disappears from the scrape
   batch for a configurable duration (default 5 minutes).
2. The winner is inferred from the last odds tick:
   - If one team's back odds ≤ 1.05, that team won.
   - If both teams have similar odds, it's likely a tie/no-result.
3. The result + closing odds are stored in match_results and published
   to Redis CHANNEL_MATCH_RESULTS for downstream settlement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.constants import CHANNEL_MATCH_RESULTS
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("lotus_result_detector")

# Odds threshold: if a team's back odds are at or below this, they "won"
WINNER_ODDS_THRESHOLD = 1.10
# If both teams' odds are above this, the match is ambiguous (possible tie/NR)
AMBIGUOUS_ODDS_FLOOR = 1.30
# Seconds a match must be absent from scrape results before we declare it completed
ABSENCE_THRESHOLD_SECONDS = 300  # 5 minutes


@dataclass
class _TrackedMatch:
    """Internal state for a match being monitored for completion."""

    match_id: str
    team_home: str
    team_away: str
    competition: str
    was_live: bool = False
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Last known odds (updated every tick)
    last_back_home: float | None = None
    last_back_away: float | None = None
    last_lay_home: float | None = None
    last_lay_away: float | None = None
    # Score tracking for cross-reference (from LiveMatchTracker)
    last_innings: int = 1
    last_score: int = 0
    first_innings_total: int | None = None
    # Has this match already been resolved?
    resolved: bool = False


class LotusResultDetector:
    """
    Detects match completion from LotusBook odds disappearance patterns.

    Usage:
        detector = LotusResultDetector()

        # Call on every scrape cycle with the current batch of match IDs
        await detector.update_tick(match_id, odds_event)  # for each live event
        await detector.check_completed(current_batch_ids)  # after processing batch
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

        now = datetime.now(timezone.utc)
        tracked = self._matches.get(match_id)

        if tracked is None:
            tracked = _TrackedMatch(
                match_id=match_id,
                team_home=team_home,
                team_away=team_away,
                competition=competition,
            )
            self._matches[match_id] = tracked

        tracked.last_seen = now
        if is_live:
            tracked.was_live = True
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
            List of result dicts for newly completed matches.
        """
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []

        for match_id, tracked in list(self._matches.items()):
            if tracked.resolved:
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

            # Match has been absent long enough — infer result
            result = self._infer_result(tracked, now)
            if result is not None:
                tracked.resolved = True
                self._resolved_ids.add(match_id)
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

    def _infer_result(
        self, tracked: _TrackedMatch, now: datetime,
    ) -> dict[str, Any] | None:
        """Infer match result from last known odds."""
        bh = tracked.last_back_home
        ba = tracked.last_back_away

        if bh is None or ba is None:
            # No odds data — can't infer
            logger.warning(
                "cannot_infer_result_no_odds",
                match_id=tracked.match_id,
            )
            return None

        # --- Cross-reference: score-based winner detection (most reliable) ---
        # If 2nd innings and score exceeds 1st innings total, chasing team won
        score_winner = None
        if (
            tracked.last_innings >= 2
            and tracked.first_innings_total is not None
            and tracked.last_score > tracked.first_innings_total
        ):
            # Chasing team won — in cricket, home bats first (odd innings),
            # away chases (even innings) by convention in our tracker
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

        # --- Odds-based winner detection ---
        odds_winner = None
        if bh <= WINNER_ODDS_THRESHOLD and ba > AMBIGUOUS_ODDS_FLOOR:
            odds_winner = tracked.team_home
        elif ba <= WINNER_ODDS_THRESHOLD and bh > AMBIGUOUS_ODDS_FLOOR:
            odds_winner = tracked.team_away
        elif bh <= WINNER_ODDS_THRESHOLD and ba <= WINNER_ODDS_THRESHOLD:
            odds_winner = tracked.team_home if bh <= ba else tracked.team_away

        # --- Log mismatch if both signals exist but disagree ---
        if score_winner and odds_winner and score_winner != odds_winner:
            logger.warning(
                "score_odds_winner_mismatch",
                match_id=tracked.match_id,
                score_winner=score_winner,
                odds_winner=odds_winner,
            )

        # --- Final decision: score wins over odds if available ---
        if score_winner:
            winner = score_winner
            loser = tracked.team_away if winner == tracked.team_home else tracked.team_home
            result_type = "win"
        elif odds_winner:
            winner = odds_winner
            loser = tracked.team_away if winner == tracked.team_home else tracked.team_home
            result_type = "win"
        else:
            # Neither score nor odds indicate a clear winner
            winner = ""
            loser = ""
            result_type = "no_result"

        return {
            "match_id": tracked.match_id,
            "completed_at": now.isoformat(),
            "winner": winner,
            "loser": loser,
            "result_type": result_type,
            "margin": "",
            "team_home": tracked.team_home,
            "team_away": tracked.team_away,
            "source": "lotusbook_odds",
            "closing_back_home": bh,
            "closing_back_away": ba,
            "closing_lay_home": tracked.last_lay_home,
            "closing_lay_away": tracked.last_lay_away,
        }

    async def _store_and_publish(self, result: dict[str, Any]) -> None:
        """Store result in DB and publish to Redis."""
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
                session.add(record)

            logger.info("result_stored_db", match_id=result["match_id"])
        except Exception as e:
            logger.error(
                "result_store_error",
                match_id=result["match_id"],
                error=str(e),
            )

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
        """Remove old resolved matches from memory."""
        now = datetime.now(timezone.utc)
        to_remove = []
        for mid, tracked in self._matches.items():
            if tracked.resolved:
                age = (now - tracked.last_seen).total_seconds()
                if age > max_age_hours * 3600:
                    to_remove.append(mid)
        for mid in to_remove:
            del self._matches[mid]
