"""S12: live decision cadence (decision-tick gate)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np

import virtual_trading.live_trading_loop as loop_mod
from shared.schemas import BettingAction, MatchContext, OddsEvent
from virtual_trading.decision_gate import DecisionGate
from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ev(i: int, back_home: float = 2.0) -> OddsEvent:
    return OddsEvent(
        match_id="m1", timestamp=T0 + timedelta(seconds=3 * i), team_home="A", team_away="B",
        back_home=back_home, lay_home=back_home + 0.02, back_away=3.0, lay_away=3.05,
        is_live=True, source="lotusbook", scrape_method="dom",
    )


def _ctx(i: int, score=0, wickets=0, overs=0.0, innings=1) -> MatchContext:
    return MatchContext(match_id="m1", timestamp=T0 + timedelta(seconds=3 * i), is_live=True,
                        score=score, wickets=wickets, overs=overs, innings=innings)


def test_over_replay_yields_contract_decision_ticks():
    gate = DecisionGate()
    # (score, wickets, overs) per 3 s tick
    states = [
        (0, 0, 0.0),   # first tick -> decision
        (0, 0, 0.0), (0, 0, 0.1),
        (1, 0, 0.2),   # single: no
        (1, 0, 0.2),
        (5, 0, 0.3),   # boundary (+4 since last decision) -> decision
        (5, 0, 0.3),
        (6, 0, 0.4),   # +1 -> no
        (6, 0, 0.5),
        (6, 1, 0.5),   # wicket -> decision
        (6, 1, 0.5),
        (7, 1, 1.0),   # over boundary -> decision
        (7, 1, 1.0),
    ]
    got = [gate.is_decision_tick(_ev(i), _ctx(i, s, w, o)) for i, (s, w, o) in enumerate(states)]
    assert [i for i, d in enumerate(got) if d] == [0, 5, 9, 11]


def test_innings_change_and_quiet_timer_and_no_context():
    gate = DecisionGate(max_quiet_seconds=180.0)
    assert gate.is_decision_tick(_ev(0), None)
    assert not gate.is_decision_tick(_ev(1), None)
    assert gate.is_decision_tick(_ev(60), None)  # 180 s later
    g2 = DecisionGate()
    assert g2.is_decision_tick(_ev(0), _ctx(0, 150, 5, 19.5, 1))
    assert not g2.is_decision_tick(_ev(1), _ctx(1, 150, 5, 19.5, 1))
    assert g2.is_decision_tick(_ev(2), _ctx(2, 0, 0, 0.0, 2))
    g2.reset("m1")
    assert g2.is_decision_tick(_ev(3), _ctx(3, 0, 0, 0.0, 2))


def _make_loop(monkeypatch) -> LiveVirtualTradingLoop:
    loop = LiveVirtualTradingLoop(agent=None)
    loop._running = True
    monkeypatch.setattr(loop_mod, "persist_bet", AsyncMock())
    monkeypatch.setattr(loop, "_get_watched_match_id", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._feature_store, "store", AsyncMock())
    monkeypatch.setattr(loop_mod, "get_match_context", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._feature_pipeline, "compute",
                        lambda **kw: np.zeros(4, dtype=np.float32))
    return loop


async def test_predict_called_at_most_twice_in_static_over(monkeypatch):
    loop = _make_loop(monkeypatch)
    agent = MagicMock()
    agent.predict.return_value = (BettingAction.HOLD, None)
    loop._agent = agent
    for i in range(200):
        j = i / 3  # sub-second ticks: 200 ticks stay within the 180 s quiet window
        data = _ev(j).model_dump(mode="json")
        data["context"] = _ctx(j, 10, 0, 2.3).model_dump(mode="json")
        await loop._handle_match_event(data)
    assert agent.predict.call_count <= 2


async def test_non_decision_ticks_still_update_latest_event(monkeypatch):
    loop = _make_loop(monkeypatch)
    agent = MagicMock()
    agent.predict.return_value = (BettingAction.HOLD, None)
    loop._agent = agent
    for i, price in enumerate([2.0, 2.2, 2.4]):
        data = _ev(i, back_home=price).model_dump(mode="json")
        data["context"] = _ctx(i, 10, 0, 2.3).model_dump(mode="json")
        await loop._handle_match_event(data)
    assert agent.predict.call_count == 1
    assert loop._latest_events["m1"].back_home == 2.4
