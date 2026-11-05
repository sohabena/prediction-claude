"""Decision-tick gate: live policy queried at the same cadence as in training (S12)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from shared.schemas import MatchContext, OddsEvent

SCORE_RISE_THRESHOLD = 4


@dataclass
class _MatchState:
    last_ts: float
    innings: Optional[int] = None
    wickets: Optional[int] = None
    over: Optional[int] = None
    score: Optional[int] = None


class DecisionGate:
    """Pure per-match gate deciding whether a tick is a decision tick.

    A tick is a decision tick when it is the first for the match, innings changed,
    wickets increased, floor(overs) changed, score rose by >= 4 since the last decision
    tick, or no decision tick happened for ``max_quiet_seconds``. Without context only
    the time rule applies (plus the first tick).
    """

    def __init__(self, max_quiet_seconds: float = 180.0) -> None:
        self.max_quiet_seconds = max_quiet_seconds
        self._state: dict[str, _MatchState] = {}

    def reset(self, match_id: str) -> None:
        """Forget per-match state."""
        self._state.pop(match_id, None)

    def is_decision_tick(self, event: OddsEvent, context: Optional[MatchContext]) -> bool:
        ts = event.timestamp.timestamp()
        st = self._state.get(event.match_id)
        if st is None:
            self._state[event.match_id] = self._snapshot(ts, context)
            return True

        decision = ts - st.last_ts >= self.max_quiet_seconds
        if context is not None:
            over = int(math.floor(context.overs))
            if st.innings is not None and context.innings != st.innings:
                decision = True
            if st.wickets is not None and context.wickets > st.wickets:
                decision = True
            if st.over is not None and over != st.over:
                decision = True
            if st.score is not None and context.score - st.score >= SCORE_RISE_THRESHOLD:
                decision = True
            if st.innings is None:  # context appeared late: baseline it
                decision = True

        if decision:
            self._state[event.match_id] = self._snapshot(ts, context)
        return decision

    @staticmethod
    def _snapshot(ts: float, context: Optional[MatchContext]) -> _MatchState:
        if context is None:
            return _MatchState(last_ts=ts)
        return _MatchState(
            last_ts=ts,
            innings=context.innings,
            wickets=context.wickets,
            over=int(math.floor(context.overs)),
            score=context.score,
        )
