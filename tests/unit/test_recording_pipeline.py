"""Recording pipeline for the 30-day collection run: pre-match prices, score state for every
recorded match, and no default retention on odds_ticks.

All database access uses fake sessions; no Redis, DB or browser is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.match import MatchContextRecord
from backend.models.odds import OddsTick
from tests.unit.test_s37_recording import (  # noqa: F401  (manager_env is a fixture)
    _new_manager,
    live_event,
    manager_env,
)

UTC = timezone.utc


def _upcoming(match_id: str = "up1", back_home: float = 1.92):
    event = live_event(match_id=match_id)  # lay_home 1.94: keep back_home below it (a crossed book is rejected)
    return event.model_copy(update={"is_live": False, "back_home": back_home})


def _ticks(session) -> list[OddsTick]:
    return [o for o in session.added if isinstance(o, OddsTick)]


async def test_prematch_prices_are_recorded_only_on_change(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    await mgr._on_events([_upcoming()])
    assert len(_ticks(manager_env.session)) == 1
    assert manager_env.redis.published == []  # pre-match ticks never reach live consumers

    # Same price again, immediately and 60 s later: nothing new (heartbeat is 15 min)
    await mgr._on_events([_upcoming()])
    mgr._prematch_last["up1"] = (mgr._prematch_last["up1"][0], mgr._prematch_last["up1"][1] - timedelta(seconds=60))
    await mgr._on_events([_upcoming()])
    assert len(_ticks(manager_env.session)) == 1

    # A price change after the 30 s minimum interval is stored
    await mgr._on_events([_upcoming(back_home=1.88)])
    assert len(_ticks(manager_env.session)) == 2
    assert mgr._quality_stats["recorded_prematch"] == 2


async def test_prematch_change_inside_min_interval_waits(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    await mgr._on_events([_upcoming()])
    await mgr._on_events([_upcoming(back_home=1.86)])  # changed, but < 30 s later
    assert len(_ticks(manager_env.session)) == 1


async def test_prematch_recording_can_be_switched_off(monkeypatch, manager_env):
    monkeypatch.setenv("SCRAPER_RECORD_PREMATCH", "0")
    mgr = _new_manager(monkeypatch, record_all=None)
    await mgr._on_events([_upcoming()])
    assert _ticks(manager_env.session) == []


async def test_unapproved_live_match_stores_score_state_but_publishes_nothing(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)  # not approved, record-all on
    event = live_event(match_id="m9").model_copy(update={"score_text": "45/2 (8.3)"})
    await mgr._on_events([event])

    contexts = [o for o in manager_env.session.added if isinstance(o, MatchContextRecord)]
    assert len(_ticks(manager_env.session)) == 1
    assert len(contexts) == 1 and contexts[0].score == 45 and contexts[0].wickets == 2
    assert manager_env.redis.published == []  # recording only; live consumers see approved matches


async def test_live_match_without_score_stores_no_placeholder_context(monkeypatch, manager_env):
    """The LotusBook listing often shows no score; no 0/0 placeholder row may be stored then."""
    mgr = _new_manager(monkeypatch, record_all=None)
    await mgr._on_events([live_event(match_id="m10").model_copy(update={"score_text": ""})])

    assert len(_ticks(manager_env.session)) == 1
    assert [o for o in manager_env.session.added if isinstance(o, MatchContextRecord)] == []


def test_db_init_has_no_retention_by_default(monkeypatch):
    """Recorded LotusBook ticks are the only real price history: no retention unless opted in."""
    import inspect

    import backend.db_init as db_init

    src = inspect.getsource(db_init)
    assert 'os.getenv("ODDS_TICKS_RETENTION", "off")' in src


async def test_rejected_prematch_tick_does_not_reset_the_throttle(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    await mgr._on_events([_upcoming()])
    first = mgr._prematch_last["up1"]
    mgr._prematch_last["up1"] = (first[0], first[1] - timedelta(seconds=60))
    await mgr._on_events([_upcoming(back_home=1.99)])  # crossed book (lay_home 1.94): rejected by the quality gate
    assert len(_ticks(manager_env.session)) == 1
    assert mgr._prematch_last["up1"][0] == first[0]  # still the last *stored* prices


def test_keep_awake_holds_only_while_container_runs(tmp_path, monkeypatch):
    from scripts import keep_awake_while_recording as ka

    monkeypatch.setattr(ka, "LOG_PATH", tmp_path / "keep_awake.log")
    states = iter([True, True, False])
    calls: list[bool] = []
    ka.run(0, is_running=lambda: next(states), apply=lambda hold: calls.append(hold) or True,
           sleep=lambda s: None, max_cycles=3)
    assert calls == [True, True, False, False]  # hold, refresh, release on stop, release on exit


async def test_registry_competition_is_filled_once_the_listing_menu_is_known(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    first = live_event(match_id="m11").model_copy(update={"competition": ""})
    await mgr._on_events([first])
    assert mgr._upsert_training_status.await_count == 1
    await mgr._on_events([first])  # still unknown: no second registry write
    assert mgr._upsert_training_status.await_count == 1
    await mgr._on_events([first.model_copy(update={"competition": "NCL GT20"})])
    assert mgr._upsert_training_status.await_count == 2
    assert mgr._upsert_training_status.await_args.args[0].competition == "NCL GT20"


async def test_unchanged_live_price_is_stored_again_as_a_heartbeat(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    event = live_event(match_id="m12")
    await mgr._on_events([event])
    await mgr._on_events([event])  # unchanged, immediately: not stored
    assert len(_ticks(manager_env.session)) == 1
    mgr._last_stored_at["m12"] -= timedelta(seconds=mgr.LIVE_HEARTBEAT_S)
    await mgr._on_events([event])  # unchanged, 5 min later: heartbeat
    assert len(_ticks(manager_env.session)) == 2
