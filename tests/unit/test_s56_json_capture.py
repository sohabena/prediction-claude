"""S56: passive capture of the match pages' own market JSON.

Covers the pure parser on both sma-event fixtures, the cricket menu mapping, the worker's passive listener
(own sma-event responses only, no page.request, fetch, route or headers), JSON-first polling with the 30 s DOM
fallback, the Winner settlement hand-off, the registry refresh, the migration fields and the static rules.

Fakes only: no DB, Redis, browser or network. The DB session and Redis are replaced in the manager tests.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scraper import lotus_result_detector as detector_module
from scraper import manager as manager_module
from scraper.lotus_result_detector import LotusResultDetector
from scraper.market_pages import JSON_FRESH_S, MarketPageWorker
from scraper.parsers.lotusbook_json import (
    EventMeta,
    event_id_from_url,
    event_meta,
    event_records,
    is_menu_url,
    menu_competitions,
    settlement_of,
)
from scraper.parsers.lotusbook_markets import parse_market_html
from backend.models.match_status import MatchTrainingStatus
from shared.schemas import OddsEvent

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lotusbook"
REAL_EVENT = json.loads((FIX / "sma_event_real_76321652_20261103.json").read_text(encoding="utf-8"))
SRL_EVENT = json.loads((FIX / "sma_event_srl_76108412_20261103.json").read_text(encoding="utf-8"))
MENU = json.loads((FIX / "sma_menu_cricket_20261103.json").read_text(encoding="utf-8"))
SRL_PAGE = (FIX / "match_page_srl_live_20261103.html").read_text(encoding="utf-8")

NOW = datetime(2026, 11, 3, 2, 50, tzinfo=timezone.utc)
SITE_API = "https://b2b.max247.co/api/public/exchange/odds"
SRL_MID = "lb_76108412"
REAL_MID = "lb_76321652"
SRL_URL = f"{SITE_API}/sma-event/events/4/76108412"


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
    """Records ``on`` listeners. Any attempt to make a request, route, or change headers is a failure."""

    def __init__(self, html=SRL_PAGE):
        self.html = html
        self.handlers: dict[str, list] = {}
        self.reloads = 0

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def content(self):
        return self.html

    async def reload(self, **kwargs):
        self.reloads += 1

    async def close(self):
        pass

    def __getattr__(self, name):
        if name in ("request", "fetch", "route", "unroute", "set_extra_http_headers", "set_user_agent"):
            raise AssertionError(f"forbidden page access: {name}")
        raise AttributeError(name)


class FloatClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now


class Store:
    def __init__(self):
        self.calls: list[list] = []

    async def __call__(self, match_id, records):
        self.calls.append(list(records))


async def _drain(worker):
    tasks = list(worker._tasks)
    if tasks:
        await asyncio.wait(tasks)


def _worker(page=None, clock=None, store=None, **kwargs):
    clock = clock or FloatClock()
    store = store or Store()
    page = page or ListenPage()
    return MarketPageWorker(
        SRL_MID, page, store, clock=clock, reload_every=10**9, **kwargs
    ), store, clock


def _market(payload, market_id):
    return next(m for m in payload["result"] if m["id"] == market_id)


# ── 1. parser on the two fixtures ─────────────────────────────────────────────────


class TestParserFixtures:
    def test_real_event_record_counts_and_breakdown(self):
        records = event_records(REAL_EVENT, REAL_MID, NOW)
        assert len(REAL_EVENT["result"]) == 92
        assert len({r.market_id for r in records}) == 92
        assert len(records) == 267
        counts = {}
        for r in records:
            counts[r.market_type] = counts.get(r.market_type, 0) + 1
        assert counts == {"other": 219, "over_runs": 48}

    def test_real_event_is_all_suspended_and_unpriced(self):
        records = event_records(REAL_EVENT, REAL_MID, NOW)
        assert {r.status for r in records} == {"suspended"}
        assert all(r.back is None and r.lay is None for r in records)
        assert all(r.back_size is None and r.lay_size is None for r in records)

    def test_srl_event_record_counts_and_breakdown(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        assert len(SRL_EVENT["result"]) == 41
        assert len({r.market_id for r in records}) == 41
        assert len(records) == 97
        counts = {}
        for r in records:
            counts[r.market_type] = counts.get(r.market_type, 0) + 1
        assert counts == {"over_runs": 54, "other": 41, "match_odds": 2}

    def test_srl_statuses_are_lower_cased_market_states(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        statuses = {r.status for r in records}
        assert statuses == {"open", "closed"}
        assert sum(r.status == "closed" for r in records) == 52
        assert sum(r.status == "open" for r in records) == 45

    def test_zero_price_is_none_and_sizes_kept(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        sri_lanka = next(r for r in records if r.market_type == "match_odds" and r.selection == "Sri Lanka SRL")
        assert sri_lanka.back == 14.5
        assert sri_lanka.back_size == 100.0
        assert sri_lanka.lay is None  # lay price 0.0 means no price
        assert sri_lanka.lay_size is None
        new_zealand = next(r for r in records if r.market_type == "match_odds" and r.selection == "New Zealand SRL")
        assert new_zealand.back is None
        assert new_zealand.lay == 1.07
        assert new_zealand.lay_size == 100.0

    def test_runner_status_and_market_fields(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        assert {r.runner_status for r in records} == {"suspended"}
        assert all(r.market_id and r.market_id.startswith("1.76108412.") for r in records)
        assert all(r.match_id == SRL_MID and r.source == "lotusbook" for r in records)

    def test_source_updated_at_is_utc_from_epoch_ms(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        expected = datetime.fromtimestamp(1793675358094 / 1000.0, tz=timezone.utc)
        winner = [r for r in records if r.market_id == "1.76108412.340._BR"]
        assert len(winner) == 2
        assert all(r.source_updated_at == expected for r in winner)
        assert all(r.source_updated_at is not None and r.source_updated_at.tzinfo is not None for r in records)

    def test_over_under_lines_are_kept_in_selection(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        over_runs = [r for r in records if r.market_type == "over_runs" and r.market_id.endswith("357.overnr=6~total=10.5~inningnr=1_BR")]
        by_sel = {r.selection: r for r in over_runs}
        assert set(by_sel) == {"Under 10.5", "Over 10.5"}
        assert by_sel["Under 10.5"].line_no == 10.5 and by_sel["Under 10.5"].line_yes is None
        assert by_sel["Over 10.5"].line_yes == 10.5 and by_sel["Over 10.5"].line_no is None
        assert by_sel["Under 10.5"].market_name == by_sel["Over 10.5"].market_name

    def test_same_name_different_ids_do_not_collide(self):
        payload = {
            "result": [
                {"id": "1.1.a", "name": "Team total", "status": "OPEN", "runners": [
                    {"name": "over 5.5", "status": "OPEN", "back": [{"price": 1.9, "size": 10}], "lay": []},
                    {"name": "under 5.5", "status": "OPEN", "back": [{"price": 1.9, "size": 10}], "lay": []},
                ]},
                {"id": "1.2.b", "name": "Team total", "status": "OPEN", "runners": [
                    {"name": "over 5.5", "status": "OPEN", "back": [{"price": 2.2, "size": 5}], "lay": []},
                    {"name": "under 5.5", "status": "OPEN", "back": [{"price": 1.6, "size": 5}], "lay": []},
                ]},
            ]
        }
        records = event_records(payload, SRL_MID, NOW)
        assert len(records) == 4
        keys = [(r.market_type, r.market_name, r.selection) for r in records]
        assert len(set(keys)) == 4
        first = [r for r in records if r.market_id == "1.1.a"]
        second = [r for r in records if r.market_id == "1.2.b"]
        assert {r.market_name for r in first} == {"Team total"}
        assert {r.market_name for r in second} == {"Team total#1.2.b"}
        assert {r.back for r in second} == {2.2, 1.6}

    def test_lines_differing_in_one_market_stay_distinct(self):
        records = event_records(SRL_EVENT, SRL_MID, NOW)
        keys = [(r.market_id, r.market_type, r.market_name, r.selection) for r in records]
        assert len(keys) == len(set(keys))

    def test_open_date_with_offset_becomes_utc(self):
        meta = event_meta(SRL_EVENT)
        assert meta == EventMeta(
            event_id="76108412",
            competition_id="36807",
            competition_name="T20 International SRL",
            open_date=datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc),  # 07:30 IST
        )
        real = event_meta(REAL_EVENT)
        assert real.competition_name == "NCL GT20"
        assert real.open_date == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)  # 04:30 IST on 3 Nov

    def test_no_settlement_while_winner_is_open_or_absent(self):
        assert settlement_of(SRL_EVENT) is None  # Winner is OPEN
        assert settlement_of(REAL_EVENT) is None  # no Winner market on this event


class TestSettlementParsing:
    def _closed(self, statuses):
        payload = copy.deepcopy(SRL_EVENT)
        winner = _market(payload, "1.76108412.340._BR")
        winner["status"] = "CLOSED"
        for runner, status in zip(winner["runners"], statuses):
            runner["status"] = status
        return payload

    def test_single_winner_runner_is_the_winner(self):
        payload = self._closed(["LOSER", "WINNER"])
        settled = settlement_of(payload)
        assert settled.winner == "New Zealand SRL"
        assert settled.runner_statuses == (("Sri Lanka SRL", "LOSER"), ("New Zealand SRL", "WINNER"))

    def test_won_wording_is_accepted(self):
        assert settlement_of(self._closed(["WON", "LOSER"])).winner == "Sri Lanka SRL"

    def test_no_or_two_winners_gives_no_winner(self):
        assert settlement_of(self._closed(["LOSER", "LOSER"])).winner is None
        assert settlement_of(self._closed(["WINNER", "WINNER"])).winner is None


# ── 2. cricket menu mapping ───────────────────────────────────────────────────────


class TestMenuMapping:
    def test_menu_maps_every_cricket_event(self):
        mapping = menu_competitions(MENU)
        assert len(mapping) == 66
        assert len({name for _cid, name in mapping.values()}) == 21

    def test_five_fixture_events_map_to_their_competitions(self):
        mapping = menu_competitions(MENU)
        assert mapping["76321652"] == ("53896", "NCL GT20")
        assert mapping["76108412"] == ("36807", "T20 International SRL")
        assert mapping["28045743"] == ("10529093", "WBBL")
        assert mapping["75330120"] == ("53670", "First Class Series New Zealand A vs India A")
        assert mapping["36266944"] == ("10529093", "WBBL")

    def test_non_cricket_types_are_ignored(self):
        payload = {"result": [{"id": "1", "name": "Football", "children": [{"type": "GROUP", "id": "9",
                   "name": "EPL", "children": [{"type": "EVENT", "id": "555"}]}]}]}
        assert menu_competitions(payload) == {}

    def test_url_matchers(self):
        assert event_id_from_url(SRL_URL) == "76108412"
        assert event_id_from_url(f"{SRL_URL}?x=1") == "76108412"
        assert event_id_from_url(f"{SITE_API}/sma-menu/4") is None
        assert event_id_from_url("https://example.com/sma-event/events/4/76108412") is None
        assert is_menu_url(f"{SITE_API}/sma-menu/4")
        assert not is_menu_url(f"{SITE_API}/sma-menu/44")
        assert not is_menu_url(SRL_URL)


class TestManagerMenuAndCompetition:
    def _mgr(self):
        mgr = manager_module.ScraperManager.__new__(manager_module.ScraperManager)
        mgr._competition_by_event = {}
        mgr._menu_tasks = set()
        return mgr

    @pytest.mark.asyncio
    async def test_menu_response_fills_the_competition_map(self):
        mgr = self._mgr()
        await mgr._read_cricket_menu(FakeResponse(f"{SITE_API}/sma-menu/4", MENU))
        assert mgr._competition_by_event["76321652"] == ("53896", "NCL GT20")
        assert mgr._competition_by_event["76108412"] == ("36807", "T20 International SRL")

    @pytest.mark.asyncio
    async def test_listing_listener_reads_only_the_menu(self):
        mgr = self._mgr()
        mgr._on_listing_response(FakeResponse(SRL_URL, SRL_EVENT))
        mgr._on_listing_response(FakeResponse("https://b2b.max247.co/other", {}))
        assert mgr._menu_tasks == set()
        mgr._on_listing_response(FakeResponse(f"{SITE_API}/sma-menu/4", MENU))
        await asyncio.wait(list(mgr._menu_tasks))
        assert len(mgr._competition_by_event) == 66

    @pytest.mark.asyncio
    async def test_unreadable_menu_is_logged_and_skipped(self):
        mgr = self._mgr()
        await mgr._read_cricket_menu(FakeResponse(f"{SITE_API}/sma-menu/4", error=ValueError("bad body")))
        assert mgr._competition_by_event == {}


# ── 3. passive listener and JSON-first worker ────────────────────────────────────


class TestPassiveListener:
    @pytest.mark.asyncio
    async def test_listener_ignores_other_urls_and_never_requests(self):
        worker, store, _clock = _worker(page=ListenPage())
        worker.attach()
        handler = worker.page.handlers["response"][0]
        handler(FakeResponse(f"{SITE_API}/sma-menu/4", MENU))
        handler(FakeResponse(f"{SITE_API}/sma-event/events/4/76000001", SRL_EVENT))  # another event
        handler(FakeResponse("https://b2b.max247.co/api/public/other", SRL_EVENT))
        assert worker._tasks == set()
        await asyncio.sleep(0)
        assert store.calls == []

    @pytest.mark.asyncio
    async def test_own_event_response_is_ingested(self):
        worker, store, _clock = _worker(page=ListenPage())
        worker.attach()
        worker.page.handlers["response"][0](FakeResponse(SRL_URL, SRL_EVENT))
        await _drain(worker)
        assert worker.json_snapshots == 1
        assert len(store.calls) == 1 and len(store.calls[0]) == 97

    @pytest.mark.asyncio
    async def test_unparseable_body_is_logged_once_and_skipped(self):
        worker, store, _clock = _worker(page=ListenPage())
        worker.attach()
        await worker.handle_response(FakeResponse(SRL_URL, error=ValueError("not json")))
        await worker.handle_response(FakeResponse(SRL_URL, error=ValueError("not json again")))
        assert worker._json_error_logged is True
        assert worker.json_snapshots == 0 and store.calls == []

    @pytest.mark.asyncio
    async def test_unexpected_shape_is_skipped_without_raising(self):
        worker, store, _clock = _worker(page=ListenPage())
        worker.attach()
        assert await worker.ingest_event_json({"result": 5}) == 0
        assert store.calls == []

    def test_page_without_on_attaches_quietly(self):
        worker, _store, _clock = _worker(page=SimpleNamespace(content=None))
        worker.attach()  # no `on` on the page: no listener, no error
        assert worker._attached_at is not None
        assert worker._json_is_fresh() is True  # the window still counts from attach


class TestJsonFirstPolling:
    @pytest.mark.asyncio
    async def test_json_suppresses_the_dom_parse(self):
        clock = FloatClock(0.0)
        worker, store, _ = _worker(clock=clock)
        worker.attach()
        await worker.handle_response(FakeResponse(SRL_URL, SRL_EVENT))
        stored_after_json = len(store.calls)
        clock.now = 10.0
        assert await worker.poll_once() == 0  # DOM is not parsed while JSON is fresh
        assert len(store.calls) == stored_after_json

    @pytest.mark.asyncio
    async def test_dom_fallback_after_thirty_seconds_without_json(self):
        clock = FloatClock(0.0)
        worker, store, _ = _worker(clock=clock)
        worker.attach()
        assert JSON_FRESH_S == 30.0
        clock.now = 5.0
        assert await worker.poll_once() == 0
        assert store.calls == []
        clock.now = 31.0
        stored = await worker.poll_once()
        dom_records = parse_market_html(SRL_PAGE, SRL_MID, NOW)
        assert store.calls and stored == len(store.calls[0]) > 0

    @pytest.mark.asyncio
    async def test_dom_resumes_only_after_json_has_been_silent(self):
        clock = FloatClock(0.0)
        worker, store, _ = _worker(clock=clock)
        worker.attach()
        clock.now = 31.0
        await worker.handle_response(FakeResponse(SRL_URL, SRL_EVENT))  # JSON at 31 s
        clock.now = 50.0
        assert await worker.poll_once() == 0  # 19 s since JSON: still JSON's turn
        clock.now = 62.0
        assert await worker.poll_once() > 0  # 31 s since JSON: DOM fallback

    @pytest.mark.asyncio
    async def test_unchanged_json_is_not_stored_twice(self):
        worker, store, _ = _worker()
        worker.attach()
        await worker.ingest_event_json(SRL_EVENT)
        await worker.ingest_event_json(SRL_EVENT)
        assert len(store.calls) == 1

    @pytest.mark.asyncio
    async def test_only_the_changed_record_is_stored(self):
        worker, store, _ = _worker()
        worker.attach()
        await worker.ingest_event_json(SRL_EVENT)
        changed = copy.deepcopy(SRL_EVENT)
        runner = _market(changed, "1.76108412.340._BR")["runners"][0]
        runner["back"][0]["size"] = 250.0
        await worker.ingest_event_json(changed)
        assert len(store.calls) == 2
        assert len(store.calls[1]) == 1
        assert store.calls[1][0].selection == "Sri Lanka SRL"
        assert store.calls[1][0].back_size == 250.0

    @pytest.mark.asyncio
    async def test_market_leaving_the_json_is_written_closed(self):
        worker, store, _ = _worker()
        worker.attach()
        await worker.ingest_event_json(SRL_EVENT)
        trimmed = copy.deepcopy(SRL_EVENT)
        trimmed["result"] = [m for m in trimmed["result"] if m["id"] != "1.76108412.340._BR"]
        await worker.ingest_event_json(trimmed)
        closed = store.calls[-1]
        assert len(closed) == 2
        assert all(r.status == "closed" and r.back is None and r.lay is None for r in closed)

    @pytest.mark.asyncio
    async def test_empty_json_does_not_close_markets(self):
        worker, store, _ = _worker()
        worker.attach()
        await worker.ingest_event_json(SRL_EVENT)
        assert await worker.ingest_event_json({"result": []}) == 0
        assert len(store.calls) == 1


class TestSettlementAndMeta:
    @pytest.mark.asyncio
    async def test_closed_winner_with_one_winner_settles_once(self):
        settled = []
        meta = []

        async def on_settled(mid, winner, observed_at):
            settled.append((mid, winner))

        async def on_meta(mid, m):
            meta.append((mid, m))

        worker, _store, _ = _worker(on_settled=on_settled, on_meta=on_meta)
        worker.attach()
        closed = copy.deepcopy(SRL_EVENT)
        winner = _market(closed, "1.76108412.340._BR")
        winner["status"] = "CLOSED"
        winner["runners"][0]["status"] = "LOSER"
        winner["runners"][1]["status"] = "WINNER"
        await worker.ingest_event_json(closed)
        await worker.ingest_event_json(closed)
        assert settled == [(SRL_MID, "New Zealand SRL")]
        assert len(meta) == 1
        assert meta[0][1].competition_name == "T20 International SRL"

    @pytest.mark.asyncio
    async def test_open_winner_does_not_settle(self):
        settled = []

        async def on_settled(mid, winner, observed_at):
            settled.append(winner)

        worker, _store, _ = _worker(on_settled=on_settled)
        worker.attach()
        await worker.ingest_event_json(SRL_EVENT)
        assert settled == []

    @pytest.mark.asyncio
    async def test_failing_callbacks_never_stop_the_recording(self):
        async def boom(*args):
            raise RuntimeError("callback down")

        worker, store, _ = _worker(on_settled=boom, on_meta=boom)
        worker.attach()
        closed = copy.deepcopy(SRL_EVENT)
        winner = _market(closed, "1.76108412.340._BR")
        winner["status"] = "CLOSED"
        winner["runners"][1]["status"] = "WINNER"
        assert await worker.ingest_event_json(closed) == 97
        assert len(store.calls) == 1


# ── 4. result detector: settled winner ───────────────────────────────────────────


@pytest.fixture
def detector(monkeypatch):
    det = LotusResultDetector()
    det._store_and_publish = AsyncMock()
    det.update_tick(
        match_id=SRL_MID, team_home="Sri Lanka SRL", team_away="New Zealand SRL",
        competition="T20 International SRL", is_live=True, back_home=2.0, back_away=1.9,
    )
    return det


class TestUpdateSettled:
    @pytest.mark.asyncio
    async def test_settled_winner_is_written_once(self, detector):
        result = await detector.update_settled(SRL_MID, "New Zealand SRL")
        assert result["winner"] == "New Zealand SRL"
        assert result["loser"] == "Sri Lanka SRL"
        assert result["result_type"] == "win"
        assert result["source"] == "lotusbook_settled"
        assert await detector.update_settled(SRL_MID, "New Zealand SRL") is None
        detector._store_and_publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_winner_name_matches_the_team_case_and_spacing_insensitively(self, detector):
        result = await detector.update_settled(SRL_MID, "  sri lanka  srl ")
        assert result["winner"] == "Sri Lanka SRL"

    @pytest.mark.asyncio
    async def test_unknown_winner_or_untracked_match_writes_nothing(self, detector):
        assert await detector.update_settled(SRL_MID, "Pakistan") is None
        assert await detector.update_settled("lb_999", "Sri Lanka SRL") is None
        detector._store_and_publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_settled_overrides_a_price_inference(self, detector):
        detector._matches[SRL_MID].written_source = detector_module.SOURCE_ODDS
        result = await detector.update_settled(SRL_MID, "Sri Lanka SRL")
        assert result is not None and result["source"] == "lotusbook_settled"

    @pytest.mark.asyncio
    async def test_scoreboard_result_is_not_overridden_by_a_settled_one(self, detector):
        scoreboard = await detector.update_scoreboard(SRL_MID, "Sri Lanka SRL won by 5 runs", finished=True)
        assert scoreboard is not None
        assert await detector.update_settled(SRL_MID, "New Zealand SRL") is None

    @pytest.mark.asyncio
    async def test_scoreboard_overrides_a_settled_result(self, detector):
        assert await detector.update_settled(SRL_MID, "New Zealand SRL") is not None
        scoreboard = await detector.update_scoreboard(SRL_MID, "Sri Lanka SRL won by 5 runs", finished=True)
        assert scoreboard is not None and scoreboard["winner"] == "Sri Lanka SRL"


class TestManagerSettledHandOff:
    @pytest.mark.asyncio
    async def test_settled_result_marks_the_match_completed(self, detector):
        mgr = manager_module.ScraperManager.__new__(manager_module.ScraperManager)
        mgr._result_detector = detector
        mgr._completed_match_ids = set()
        await mgr._on_market_settled(SRL_MID, "New Zealand SRL", NOW)
        assert SRL_MID in mgr._completed_match_ids


# ── 5. registry refresh from the market JSON ─────────────────────────────────────


class _Result:
    def __init__(self, record=None):
        self._record = record

    def scalar_one_or_none(self):
        return self._record

    def fetchall(self):
        return []


class _Session:
    def __init__(self, record=None):
        self.added = []
        self._record = record

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def execute(self, *args, **kwargs):
        return _Result(self._record)


def _patch_session(monkeypatch, session):
    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr(manager_module, "get_session", fake_get_session)


class TestRegistryRefresh:
    @pytest.mark.asyncio
    async def test_meta_updates_competition_and_start_time_of_a_known_match(self, monkeypatch):
        record = SimpleNamespace(
            competition="", match_start_time=None, scrape_status="discovered", training_status="pending",
            auto_approved=False, is_simulated=False,
        )
        session = _Session(record)
        _patch_session(monkeypatch, session)
        mgr = manager_module.ScraperManager()
        event = OddsEvent(match_id=REAL_MID, timestamp=NOW, team_home="Toronto Kings",
                          team_away="Montreal Warriors", competition="")
        await mgr._apply_market_meta(event, event_meta(REAL_EVENT))
        assert record.competition == "NCL GT20"
        assert record.match_start_time == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)
        assert record.match_start_time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_empty_meta_leaves_the_registry_alone(self, monkeypatch):
        session = _Session(None)
        _patch_session(monkeypatch, session)
        mgr = manager_module.ScraperManager()
        event = OddsEvent(match_id=REAL_MID, timestamp=NOW, team_home="A", team_away="B", competition="X")
        await mgr._apply_market_meta(event, EventMeta("1", "", "", None))
        assert session.added == []

    @pytest.mark.asyncio
    async def test_menu_competition_is_used_for_live_and_registry(self, monkeypatch, tmp_path):
        session = _Session(None)
        _patch_session(monkeypatch, session)
        redis = SimpleNamespace(
            client=SimpleNamespace(get=AsyncMock(side_effect=lambda key: "false" if "international" in key else None)),
            publish_event=AsyncMock(),
            set_json=AsyncMock(),
        )
        monkeypatch.setattr(manager_module, "get_redis", AsyncMock(return_value=redis))
        monkeypatch.setattr(manager_module, "FEED_STATE_PATH", tmp_path / "scraper_feed.json")
        mgr = manager_module.ScraperManager()
        mgr._result_detector = SimpleNamespace(
            update_tick=lambda **kwargs: None,
            check_completed=AsyncMock(return_value=[]),
        )
        mgr._load_scrape_approved = AsyncMock()
        mgr._competition_by_event = {"76321652": ("53896", "NCL GT20")}
        event = OddsEvent(
            match_id=REAL_MID, timestamp=NOW, team_home="Toronto Kings", team_away="Montreal Warriors",
            competition="Premier Cricket (listing)", back_home=1.9, lay_home=1.92, back_away=2.0,
            lay_away=2.04, is_live=True, score_text="",
        )
        await mgr._on_events([event])
        assert mgr._active_matches[REAL_MID]["competition"] == "NCL GT20"
        registry = [o for o in session.added if isinstance(o, MatchTrainingStatus)]
        assert [r.competition for r in registry] == ["NCL GT20"]


    @pytest.mark.asyncio
    async def test_registry_competition_fills_an_empty_one_before_the_menu_arrives(self, monkeypatch, tmp_path):
        """After a restart the first listing poll comes before the menu feed: the registry's competition is used."""
        session = _Session(None)
        _patch_session(monkeypatch, session)
        redis = SimpleNamespace(
            client=SimpleNamespace(get=AsyncMock(side_effect=lambda key: "false" if "international" in key else None)),
            publish_event=AsyncMock(),
            set_json=AsyncMock(),
        )
        monkeypatch.setattr(manager_module, "get_redis", AsyncMock(return_value=redis))
        monkeypatch.setattr(manager_module, "FEED_STATE_PATH", tmp_path / "scraper_feed.json")
        mgr = manager_module.ScraperManager()
        mgr._result_detector = SimpleNamespace(
            update_tick=lambda **kwargs: None,
            check_completed=AsyncMock(return_value=[]),
        )
        mgr._load_scrape_approved = AsyncMock()
        mgr._competition_by_event = {}
        mgr._registry_competition = {REAL_MID: "NCL GT20"}
        event = OddsEvent(
            match_id=REAL_MID, timestamp=NOW, team_home="Toronto Kings", team_away="Montreal Warriors",
            competition="", back_home=1.9, lay_home=1.92, back_away=2.0, lay_away=2.04, is_live=True, score_text="",
        )
        await mgr._on_events([event])
        assert mgr._active_matches[REAL_MID]["competition"] == "NCL GT20"

# ── 6. migration, model and static rules ─────────────────────────────────────────


class TestSchemaAndStaticRules:
    def test_model_has_the_nullable_json_columns(self):
        from backend.models.market_ticks import MarketTick

        cols = MarketTick.__table__.columns
        for name in ("market_id", "back_size", "lay_size", "runner_status", "source_updated_at"):
            assert name in cols and cols[name].nullable is True

    def test_db_init_has_one_entry_adding_the_columns(self):
        from backend.db_init import MIGRATION_STATEMENTS

        entries = [s for s in MIGRATION_STATEMENTS if "market_ticks" in s and "ADD COLUMN" in s]
        assert len(entries) == 1
        for name in ("market_id TEXT", "back_size DOUBLE PRECISION", "lay_size DOUBLE PRECISION",
                     "runner_status TEXT", "source_updated_at TIMESTAMPTZ"):
            assert name in entries[0]

    def test_alembic_005_follows_004(self):
        pytest.importorskip("alembic")
        spec = importlib.util.spec_from_file_location(
            "rev005", ROOT / "alembic" / "versions" / "005_market_ticks_json_fields.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "005_market_ticks_json_fields"
        assert module.down_revision == "004_match_is_simulated"

    def test_market_pages_and_parser_make_no_requests_or_clicks(self):
        forbidden = (".click(", ".fill(", ".type(", ".press(", ".route(", "page.request", ".fetch(",
                     "set_extra_http_headers", "user_agent")
        for relative in ("scraper/market_pages.py", "scraper/parsers/lotusbook_json.py"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, f"{token} in {relative}"
        pages = (ROOT / "scraper" / "market_pages.py").read_text(encoding="utf-8")
        assert len(re.findall(r"\.goto\(", pages)) == 1

    def test_context_is_pinned_to_site_time_and_manager_has_no_requests(self):
        text = (ROOT / "scraper" / "manager.py").read_text(encoding="utf-8")
        assert 'timezone_id="Asia/Kolkata"' in text
        assert "page.request" not in text
        assert ".route(" not in text
