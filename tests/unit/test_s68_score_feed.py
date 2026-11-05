"""S68: the live score feed of the match pages (``match-center/score/v3``).

Covers the pure parser on the real livescore-v2 fixture and a legacy body, the match_context row mapping, the
worker's passive listener (own score responses only, dedupe by updated_at and content, legacy hand-off, no
request of any kind), the manager's storage, toss and result hand-off, the registry columns and the migration.

Fakes only: no DB, Redis, browser or network. The DB session is replaced in the manager tests.
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.models.match import MatchContextRecord
from backend.models.match_status import MatchTrainingStatus
from scraper import manager as manager_module
from scraper.market_pages import MarketPageWorker
from scraper.parsers.lotusbook_score import (
    PIPELINE_LEGACY,
    PIPELINE_V2,
    ScoreState,
    parse_score_v3,
    score_event_id_from_url,
    score_pipeline_of,
    to_context_row,
)
from scraper.parsers.lotusbook_scoreboard import parse_result_comment
from shared.schemas import OddsEvent

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lotusbook"
REAL_PAYLOAD = json.loads((FIX / "score_v3_real_live_36267578_20261103.json").read_text(encoding="utf-8"))
LEGACY_PAYLOAD = {"result": {"stats": {}, "scorePipeline": "legacy"}}

REAL_EVENT_ID = "36267578"
REAL_MID = "lb_36267578"
OTHER_EVENT_ID = "99999999"
SCORE_URL = f"https://b2b.max247.co/api/public/match-center/score/v3/4/{REAL_EVENT_ID}"
NOW = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
BATTING = "Biratnagar Kings"
BOWLING = "Janakpur Bolts"

FORBIDDEN_PAGE_ATTRS = ("request", "fetch", "route", "unroute", "set_extra_http_headers", "set_user_agent")


# ── fakes ─────────────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, url, payload=None, error=None):
        self.url = url
        self._payload = payload
        self._error = error

    async def json(self):
        if self._error is not None:
            raise self._error
        return copy.deepcopy(self._payload)


class ListenPage:
    """Records ``on`` listeners. Any request, route or header change is a test failure."""

    def __init__(self):
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def __getattr__(self, name):
        if name in FORBIDDEN_PAGE_ATTRS:
            raise AssertionError(f"forbidden page access: {name}")
        raise AttributeError(name)

    async def content(self):
        return ""

    async def reload(self, **kwargs):
        pass

    async def close(self):
        pass


class FloatClock:
    def __call__(self):
        return 0.0


class Store:
    async def __call__(self, match_id, records):
        pass


class _Rows:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class _Session:
    def __init__(self, rowcount=1):
        self.added = []
        self.executed = []
        self._rowcount = rowcount

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return _Rows(self._rowcount)


def _patch_session(monkeypatch, session):
    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr(manager_module, "get_session", fake_get_session)


def _worker(on_score=None, page=None):
    return MarketPageWorker(
        REAL_MID, page or ListenPage(), Store(), clock=FloatClock(), reload_every=10**9, on_score=on_score,
    )


def _finished_payload(text="Biratnagar Kings won by 5 runs"):
    payload = copy.deepcopy(REAL_PAYLOAD)
    payload["result"]["stats"]["result"] = text
    return payload


def _event():
    return OddsEvent(
        match_id=REAL_MID, timestamp=NOW, team_home=BATTING, team_away=BOWLING, competition="NPL",
    )


# ── 1. parser on the real livescore-v2 fixture ────────────────────────────────────


class TestParserFixture:
    def test_parsed_values_equal_the_fixture(self):
        s = parse_score_v3(REAL_PAYLOAD)
        assert isinstance(s, ScoreState)
        assert s.pipeline == PIPELINE_V2
        assert s.batting_team == BATTING
        assert (s.runs, s.wickets, s.overs, s.balls) == (2, 1, 1.0, 6)
        assert s.innings == 1
        assert s.target is None  # the feed sends 0 in the first innings
        assert s.run_rate == 2.0 and s.required_run_rate == 0.0 and s.required_runs is None
        assert s.remaining_balls == 114 and s.total_balls == 120
        assert s.max_overs == 20.0 and s.match_format == "T20"
        assert s.is_powerplay is True and s.session == "In Play"
        assert s.toss_winner == "Janakpur Bolts" and s.toss_decision == "opt to Bowl"
        assert s.last_over_balls == ("0", "1", "1", "W", "0", "0")
        assert s.teams == (BATTING, BOWLING)

    def test_updated_at_is_the_feed_time_in_utc(self):
        s = parse_score_v3(REAL_PAYLOAD)
        assert s.updated_at == datetime(2026, 11, 3, 6, 51, 36, 565511, tzinfo=timezone.utc)

    def test_live_fixture_is_not_finished_and_has_no_result(self):
        s = parse_score_v3(REAL_PAYLOAD)
        assert s.result_text == ""
        assert s.is_finished is False

    def test_fraction_digits_are_normalised(self):
        payload = copy.deepcopy(REAL_PAYLOAD)
        payload["result"]["stats"]["updatedAt"] = "2026-11-03T06:51:36.5Z"
        assert parse_score_v3(payload).updated_at == datetime(2026, 11, 3, 6, 51, 36, 500000, tzinfo=timezone.utc)

    def test_pipeline_is_read_from_the_body(self):
        assert score_pipeline_of(REAL_PAYLOAD) == PIPELINE_V2
        assert score_pipeline_of(LEGACY_PAYLOAD) == PIPELINE_LEGACY
        assert score_pipeline_of({}) is None


# ── 2. refused bodies ─────────────────────────────────────────────────────────────


class TestRefusedBodies:
    def test_legacy_body_has_no_state(self):
        assert parse_score_v3(LEGACY_PAYLOAD) is None

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            "not a dict",
            {},
            {"success": False, "result": REAL_PAYLOAD["result"]},
            {"result": "x"},
            {"result": {"stats": {}}},
        ],
    )
    def test_empty_or_unreadable_bodies_have_no_state(self, payload):
        assert parse_score_v3(payload) is None

    def test_missing_runs_is_refused_not_estimated(self):
        payload = copy.deepcopy(REAL_PAYLOAD)
        del payload["result"]["stats"]["battingTeam"]["score"]
        assert parse_score_v3(payload) is None

    def test_ball_digit_above_five_is_refused(self):
        payload = copy.deepcopy(REAL_PAYLOAD)
        payload["result"]["stats"]["battingTeam"]["overs"] = "1.6"
        assert parse_score_v3(payload) is None

    def test_event_url_matcher(self):
        assert score_event_id_from_url(SCORE_URL) == REAL_EVENT_ID
        assert score_event_id_from_url(SCORE_URL + "?x=1") == REAL_EVENT_ID
        assert score_event_id_from_url(
            "https://b2b.max247.co/api/public/exchange/odds/sma-event/events/4/36267578"
        ) is None
        assert score_event_id_from_url("https://b2b.max247.co/api/public/match-center/score/v3/5/36267578") is None


# ── 3. mapping to match_context columns ───────────────────────────────────────────


class TestContextRow:
    def test_row_matches_the_match_context_columns(self):
        s = parse_score_v3(REAL_PAYLOAD)
        row = to_context_row(s, REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert row == {
            "time": NOW,
            "match_id": REAL_MID,
            "is_live": True,
            "score": 2,
            "wickets": 1,
            "overs": 1.0,
            "run_rate": 2.0,
            "req_run_rate": 0.0,
            "innings": 1,
            "balls_remaining": 114,
            "batting_team": BATTING,
            "bowling_team": BOWLING,
            "status": "live",
            "match_format": "T20",
            "players": s.players,  # S107 part A: checked field by field in test_s107a_players.py
        }
        assert set(row) == {c.name for c in MatchContextRecord.__table__.columns}

    def test_bowling_team_is_the_other_side_whichever_side_bats(self):
        s = parse_score_v3(REAL_PAYLOAD)
        row = to_context_row(s, REAL_MID, BOWLING, BATTING, observed_at=NOW)
        assert row["bowling_team"] == BOWLING and row["batting_team"] == BATTING

    def test_bowling_team_falls_back_to_the_feed_team_list(self):
        s = parse_score_v3(REAL_PAYLOAD)
        row = to_context_row(s, REAL_MID, "Some Other Name", "Another Name", observed_at=NOW)
        assert row["bowling_team"] == BOWLING

    def test_time_defaults_to_the_feed_time(self):
        s = parse_score_v3(REAL_PAYLOAD)
        assert to_context_row(s, REAL_MID, BATTING, BOWLING)["time"] == s.updated_at

    def test_no_time_at_all_gives_no_row(self):
        s = parse_score_v3(REAL_PAYLOAD)
        s_without_time = ScoreState(**{**s.__dict__, "updated_at": None})
        assert to_context_row(s_without_time, REAL_MID, BATTING, BOWLING) is None

    def test_balls_remaining_is_derived_when_the_feed_omits_it(self):
        payload = copy.deepcopy(REAL_PAYLOAD)
        del payload["result"]["stats"]["remainingBalls"]
        row = to_context_row(parse_score_v3(payload), REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert row["balls_remaining"] == 114  # 20 overs = 120 balls, 6 bowled

    def test_finished_result_maps_to_a_completed_row(self):
        s = parse_score_v3(_finished_payload())
        assert s.is_finished is True
        assert parse_result_comment(s.result_text) == (BATTING, "runs", 5)
        row = to_context_row(s, REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert row["status"] == "completed" and row["is_live"] is False


# ── 4. worker: passive listener, dedupe, legacy hand-off ──────────────────────────


class TestPassiveScoreListener:
    @pytest.mark.asyncio
    async def test_own_score_response_is_handed_on_and_other_events_are_ignored(self):
        calls = []

        async def on_score(match_id, state, pipeline, observed_at):
            calls.append((match_id, state, pipeline))

        page = ListenPage()
        worker = _worker(on_score=on_score, page=page)
        worker.attach()
        handler = page.handlers["response"][0]
        handler(FakeResponse(SCORE_URL.replace(REAL_EVENT_ID, OTHER_EVENT_ID), REAL_PAYLOAD))
        handler(FakeResponse(SCORE_URL, REAL_PAYLOAD))
        await asyncio_drain(worker)
        assert len(calls) == 1
        match_id, state, pipeline = calls[0]
        assert match_id == REAL_MID and pipeline == PIPELINE_V2
        assert state.runs == 2 and state.wickets == 1

    @pytest.mark.asyncio
    async def test_unreadable_body_is_logged_and_skipped(self):
        calls = []

        async def on_score(*args):
            calls.append(args)

        worker = _worker(on_score=on_score)
        await worker.handle_score_response(FakeResponse(SCORE_URL, error=ValueError("bad json")))
        assert calls == []

    @pytest.mark.asyncio
    async def test_dedupe_by_updated_at_and_content(self):
        calls = []

        async def on_score(match_id, state, pipeline, observed_at):
            calls.append(state)

        worker = _worker(on_score=on_score)
        base = copy.deepcopy(REAL_PAYLOAD)

        assert await worker.ingest_score_json(base) is True  # first state
        assert await worker.ingest_score_json(copy.deepcopy(base)) is False  # same time, same score

        newer = copy.deepcopy(base)
        newer["result"]["stats"]["updatedAt"] = "2026-11-03T06:51:40.000000Z"
        newer["result"]["stats"]["battingTeam"]["score"] = "4"
        assert await worker.ingest_score_json(newer) is True  # newer and changed

        older = copy.deepcopy(base)  # the feed already moved past this one
        older["result"]["stats"]["battingTeam"]["score"] = "6"
        assert await worker.ingest_score_json(older) is False

        same_time_new_wicket = copy.deepcopy(newer)
        same_time_new_wicket["result"]["stats"]["battingTeam"]["wickets"] = "2"
        assert await worker.ingest_score_json(same_time_new_wicket) is True  # same time, content changed

        later_same_content = copy.deepcopy(same_time_new_wicket)
        later_same_content["result"]["stats"]["updatedAt"] = "2026-11-03T06:51:50.000000Z"
        assert await worker.ingest_score_json(later_same_content) is False  # later time, nothing changed

        assert [s.runs for s in calls] == [2, 4, 4]
        assert [s.wickets for s in calls] == [1, 1, 2]

    @pytest.mark.asyncio
    async def test_legacy_pipeline_is_handed_on_once_without_a_state(self):
        calls = []

        async def on_score(match_id, state, pipeline, observed_at):
            calls.append((state, pipeline))

        worker = _worker(on_score=on_score)
        assert await worker.ingest_score_json(LEGACY_PAYLOAD) is False
        assert await worker.ingest_score_json(copy.deepcopy(LEGACY_PAYLOAD)) is False
        assert calls == [(None, PIPELINE_LEGACY)]
        assert worker._score_legacy_logged is True

    @pytest.mark.asyncio
    async def test_failing_callback_never_stops_the_recording(self):
        async def on_score(*args):
            raise RuntimeError("registry down")

        worker = _worker(on_score=on_score)
        assert await worker.ingest_score_json(REAL_PAYLOAD) is True


class TestNoRequestsEver:
    def test_score_code_has_no_request_click_or_header_calls(self):
        forbidden = [
            "page.request", "page.route", ".route(", "set_extra_http_headers", "set_user_agent",
            ".click(", ".fill(", ".type(", "aiohttp", "httpx", "requests.", "await fetch(",
        ]
        for path in (ROOT / "scraper" / "parsers" / "lotusbook_score.py", ROOT / "scraper" / "market_pages.py"):
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in source, (path.name, token)

    def test_the_listener_never_touches_request_or_route(self):
        page = ListenPage()
        worker = _worker(page=page)
        worker.attach()  # any access to request/route/headers would raise AssertionError here
        assert "response" in page.handlers


# ── 5. manager: storage, pipeline, toss and result ────────────────────────────────


def _manager(monkeypatch, session=None):
    session = session or _Session()
    _patch_session(monkeypatch, session)
    mgr = manager_module.ScraperManager.__new__(manager_module.ScraperManager)
    mgr._score_pipeline_by_match = {}
    mgr._score_result_sent = set()
    mgr._toss_written = set()
    mgr._result_detector = SimpleNamespace(update_scoreboard=AsyncMock(return_value=None))
    return mgr, session


class TestManagerScoreStep:
    @pytest.mark.asyncio
    async def test_changed_state_stores_one_context_row(self, monkeypatch):
        mgr, session = _manager(monkeypatch)
        state = parse_score_v3(REAL_PAYLOAD)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        rows = [o for o in session.added if isinstance(o, MatchContextRecord)]
        assert len(rows) == 1
        assert (rows[0].score, rows[0].wickets, rows[0].match_id, rows[0].batting_team) == (2, 1, REAL_MID, BATTING)
        assert rows[0].time == NOW

    @pytest.mark.asyncio
    async def test_no_state_stores_no_placeholder_row(self, monkeypatch):
        mgr, session = _manager(monkeypatch)
        await mgr._on_market_score(_event(), None, None, NOW)
        assert session.added == [] and session.executed == []

    @pytest.mark.asyncio
    async def test_db_error_is_logged_not_raised(self, monkeypatch):
        class Broken(_Session):
            async def execute(self, statement, params=None):
                raise RuntimeError("db down")

            def add(self, obj):
                raise RuntimeError("db down")

        mgr, _ = _manager(monkeypatch, session=Broken())
        await mgr._on_market_score(_event(), parse_score_v3(REAL_PAYLOAD), PIPELINE_V2, NOW)

    @pytest.mark.asyncio
    async def test_pipeline_is_written_once_to_the_registry(self, monkeypatch):
        mgr, session = _manager(monkeypatch)
        await mgr._on_market_score(_event(), None, PIPELINE_LEGACY, NOW)
        await mgr._on_market_score(_event(), None, PIPELINE_LEGACY, NOW)
        pipeline_updates = [p for sql, p in session.executed if "score_pipeline" in sql]
        assert pipeline_updates == [{"pipeline": PIPELINE_LEGACY, "mid": REAL_MID}]
        assert session.added == []

    @pytest.mark.asyncio
    async def test_toss_is_written_once(self, monkeypatch):
        mgr, session = _manager(monkeypatch)
        state = parse_score_v3(REAL_PAYLOAD)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        toss = [(sql, p) for sql, p in session.executed if "toss_winner" in sql]
        assert len(toss) == 1
        assert toss[0][1] == {"winner": BOWLING, "decision": "opt to Bowl", "mid": REAL_MID}
        assert REAL_MID in mgr._toss_written

    @pytest.mark.asyncio
    async def test_toss_is_retried_while_the_registry_row_is_missing(self, monkeypatch):
        mgr, session = _manager(monkeypatch, session=_Session(rowcount=0))
        state = parse_score_v3(REAL_PAYLOAD)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        toss = [sql for sql, _ in session.executed if "toss_winner" in sql]
        assert len(toss) == 2
        assert REAL_MID not in mgr._toss_written

    @pytest.mark.asyncio
    async def test_finished_result_goes_to_the_detector_once(self, monkeypatch):
        mgr, _ = _manager(monkeypatch)
        state = parse_score_v3(_finished_payload())
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        mgr._result_detector.update_scoreboard.assert_awaited_once_with(
            REAL_MID, "Biratnagar Kings won by 5 runs", finished=True,
        )

    @pytest.mark.asyncio
    async def test_unparsed_result_text_is_logged_and_not_handed_on(self, monkeypatch):
        mgr, _ = _manager(monkeypatch)
        state = parse_score_v3(_finished_payload("Some unexpected wording"))
        await mgr._on_market_score(_event(), state, PIPELINE_V2, NOW)
        mgr._result_detector.update_scoreboard.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_state_does_not_touch_the_detector(self, monkeypatch):
        mgr, _ = _manager(monkeypatch)
        await mgr._on_market_score(_event(), parse_score_v3(REAL_PAYLOAD), PIPELINE_V2, NOW)
        mgr._result_detector.update_scoreboard.assert_not_awaited()


# ── 6. registry columns and migration ─────────────────────────────────────────────


class TestSchemaAndMigration:
    def test_model_has_nullable_toss_and_pipeline_columns(self):
        cols = MatchTrainingStatus.__table__.columns
        for name in ("toss_winner", "toss_decision", "score_pipeline"):
            assert name in cols and cols[name].nullable is True

    def test_db_init_adds_the_columns_in_one_entry(self):
        from backend.db_init import MIGRATION_STATEMENTS

        entries = [s for s in MIGRATION_STATEMENTS if "match_training_status" in s and "toss_winner" in s]
        assert len(entries) == 1
        for name in ("toss_winner TEXT", "toss_decision TEXT", "score_pipeline TEXT"):
            assert name in entries[0]

    def test_alembic_007_follows_006_and_adds_the_same_columns(self):
        text = (ROOT / "alembic" / "versions" / "007_toss_fields.py").read_text(encoding="utf-8")
        assert 'revision: str = "007_toss_fields"' in text
        assert 'down_revision: Union[str, None] = "006_odds_ticks_quarantine"' in text
        for name in ("toss_winner TEXT", "toss_decision TEXT", "score_pipeline TEXT"):
            assert name in text


async def asyncio_drain(worker):
    """Wait for the listener's pending response tasks."""
    import asyncio

    tasks = list(worker._tasks)
    if tasks:
        await asyncio.wait(tasks)
