"""S86: when a match actually started (the off), from three passive signals.

``match_training_status.actual_start_time`` and ``actual_start_source`` are set ONCE, by the first signal that
arrives. The signals and their names (the value stored in ``actual_start_source``):

1. ``json_in_play``: the match page's market JSON carries ``"inPlay": true`` (scraper/parsers/lotusbook_json.py).
2. ``listing_live``: the listing shows the live badge (``OddsEvent.is_live``, scraper/manager.py ``_on_events``).
3. ``score_first_ball``: the score feed shows the first ball of the first innings (scraper/parsers/lotusbook_score.py).
4. ``listing_backfill`` / ``score_backfill``: scripts/backfill_actual_start.py, from recorded rows. Never live.

Precedence: when several signals are known together, the one listed first wins (1, then 2, then 3). The live
writes are separate asynchronous callbacks, so the precedence applies only where the signals are passed together
(``pick_actual_start``, used by the backfill planner). Between live callbacks the first write wins, and the
``actual_start_time IS NULL`` guard in the UPDATE keeps it set once, across restarts too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Sequence

SOURCE_JSON_IN_PLAY = "json_in_play"
SOURCE_LISTING_LIVE = "listing_live"
SOURCE_SCORE_FIRST_BALL = "score_first_ball"
SOURCE_LISTING_BACKFILL = "listing_backfill"
SOURCE_SCORE_BACKFILL = "score_backfill"

# Live signals in precedence order (earlier wins when several are known together).
LIVE_PRECEDENCE: tuple[str, ...] = (SOURCE_JSON_IN_PLAY, SOURCE_LISTING_LIVE, SOURCE_SCORE_FIRST_BALL)
BACKFILL_PRECEDENCE: tuple[str, ...] = (SOURCE_LISTING_BACKFILL, SOURCE_SCORE_BACKFILL)


def pick_actual_start(
    signals: Iterable[tuple[str, Optional[datetime]]],
    precedence: Sequence[str] = LIVE_PRECEDENCE,
) -> Optional[tuple[str, datetime]]:
    """The winning ``(source, time)`` among the signals that have a time, by precedence; None when none does.

    A source missing from ``precedence`` ranks last. Times are not compared: the precedence decides.
    """
    rank = {source: i for i, source in enumerate(precedence)}
    known = [(source, when) for source, when in signals if when is not None]
    if not known:
        return None
    source, when = min(known, key=lambda item: rank.get(item[0], len(rank)))
    return source, when


def is_first_ball(innings: Optional[int], balls: Optional[int]) -> bool:
    """True when the score state shows a ball bowled in the first innings (the match has started)."""
    return innings == 1 and balls is not None and balls >= 1
