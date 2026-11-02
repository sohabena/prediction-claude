"""S41: Betfair Exchange adapter (read-only): stream ticks, resume, closing price, mapping, no order placement.

All network access is replaced by fakes. The stream fixtures below are hand-written in the
Exchange Stream message shape (op/mc/rc/batb/batl/clk/initialClk). They were not captured
from the live API.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from scraper import betfair_stream
from scraper.betfair_store import (
    CLOSING_UPDATE_SQL,
    ClosingPriceRecorder,
    TickPublisher,
    run_feed,
)
from scraper.betfair_stream import (
    BetfairAuthError,
    BetfairStreamClient,
    BetfairStreamError,
    BetfairTick,
)
from scraper.match_mapper import link_market
from scraper.parsers.betfair_parser import BetfairParser, betfair_match_id, odds_event_from_book
from shared.schemas import OddsEvent

MARKET = "1.234567"
APP_KEY = "SENTINEL-APPKEY-7f3a"
SESSION = "SENTINEL-SESSION-9c1e"
SCRAPER_DIR = Path(__file__).resolve().parents[2] / "scraper"
PACKAGE_FILES = [
    SCRAPER_DIR / "betfair_stream.py",
    SCRAPER_DIR / "betfair_store.py",
    SCRAPER_DIR / "match_mapper.py",
    SCRAPER_DIR / "parsers" / "betfair_parser.py",
]
ORDER_CALLS = ("placeOrders", "replaceOrders", "cancelOrders", "place_orders", "replace_orders", "cancel_orders")


# ----------------------------------------------------------------------
# Fixtures and fakes
# ----------------------------------------------------------------------

def _runner_def(r_id: int, name: str, status: str = "ACTIVE") -> dict[str, Any]:
    return {"id": r_id, "name": name, "status": status, "sortPriority": r_id}


def _market_def(status: str = "OPEN", in_play: bool = False, winner: int | None = None) -> dict[str, Any]:
    runners = [_runner_def(111, "India"), _runner_def(222, "Australia"), _runner_def(333, "The Draw")]
    if winner is not None:
        runners = [dict(r, status="WINNER" if r["id"] == winner else "LOSER") for r in runners]
    return {
        "eventName": "India v Australia",
        "competitionName": "T20 International",
        "marketType": "MATCH_ODDS",
        "marketTime": "2026-11-02T14:00:00.000Z",
        "status": status,
        "inPlay": in_play,
        "runners": runners,
    }


def _image_mc() -> dict[str, Any]:
    return {
        "id": MARKET,
        "marketDefinition": _market_def(),
        "rc": [
            {"id": 111, "batb": [[0, 2.10, 500], [1, 2.08, 300], [2, 2.06, 200], [3, 2.04, 100]],
             "batl": [[0, 2.12, 400], [1, 2.14, 250]], "ltp": 2.10, "tv": 12000},
            {"id": 222, "batb": [[0, 1.95, 600], [1, 1.94, 300]], "batl": [[0, 1.96, 450]], "ltp": 1.96, "tv": 9000},
            {"id": 333, "batb": [[0, 3.60, 50]], "batl": [[0, 3.70, 50]], "ltp": 3.65, "tv": 800},
        ],
    }


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj)


def _last_sub_id(sent: list[dict[str, Any]]) -> int:
    return max(m["id"] for m in sent if m.get("op") == "marketSubscription")


def _sub_success(sent: list[dict[str, Any]]) -> str:
    return _line({"op": "status", "id": _last_sub_id(sent), "statusCode": "SUCCESS", "connectionClosed": False})


AUTH_SUCCESS = _line({"op": "status", "id": 1, "statusCode": "SUCCESS", "connectionClosed": False})


class FakeConnection:
    """Scripted connection. Script items: str lines, exceptions, or callables taking the sent messages."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_line(self, line: str) -> None:
        self.sent.append(json.loads(line))

    async def read_line(self) -> str:
        if not self._script:
            raise ConnectionError("scripted end of stream")
        item = self._script.pop(0)
        if callable(item):
            item = item(self.sent)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self._connections = list(connections)
        self.opened: list[FakeConnection] = []

    async def open(self) -> FakeConnection:
        if not self._connections:
            raise ConnectionError("no more scripted connections")
        conn = self._connections.pop(0)
        self.opened.append(conn)
        return conn


class HangingConnection(FakeConnection):
    """Accepts the subscription, then never sends anything (silent connection)."""

    async def read_line(self) -> str:
        await asyncio.Event().wait()
        return ""


class HangingConnector:
    def __init__(self) -> None:
        self.opened: list[HangingConnection] = []

    async def open(self) -> HangingConnection:
        conn = HangingConnection([])
        self.opened.append(conn)
        return conn


class RaisingConnector:
    async def open(self) -> FakeConnection:
        raise OSError(f"connect refused for session {SESSION} key {APP_KEY}")


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _rec(self, level: str, event: str, **kwargs: Any) -> None:
        self.records.append((level, event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        self._rec("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._rec("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._rec("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._rec("error", event, **kwargs)


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(connector: Any, max_attempts: int | None = 1, **kwargs: Any) -> BetfairStreamClient:
    return BetfairStreamClient(
        APP_KEY,
        SESSION,
        [MARKET],
        connector=connector,
        max_attempts=max_attempts,
        sleep=_no_sleep,
        **kwargs,
    )


async def _collect_until_give_up(client: BetfairStreamClient) -> list[BetfairTick]:
    ticks: list[BetfairTick] = []
    with pytest.raises(BetfairStreamError):
        async for tick in client.stream():
            ticks.append(tick)
    return ticks


def _single_connection_script() -> list[Any]:
    """Auth ok, subscription ok, image, in-play update with a removed level, close, heartbeat."""
    delta = {
        "op": "mcm", "pt": 2000, "clk": "c2",
        "mc": [{
            "id": MARKET,
            "marketDefinition": _market_def(in_play=True),
            "rc": [
                {"id": 111, "batb": [[0, 2.10, 0]]},
                {"id": 222, "ltp": 2.00, "tv": 9500},
            ],
        }],
    }
    closed = {
        "op": "mcm", "pt": 3000, "clk": "c3",
        "mc": [{"id": MARKET, "marketDefinition": _market_def(status="CLOSED", winner=111)}],
    }
    return [
        AUTH_SUCCESS,
        _sub_success,
        _line({"op": "mcm", "pt": 1000, "ct": "SUB_IMAGE", "clk": "c1", "initialClk": "i1", "mc": [_image_mc()]}),
        _line(delta),
        _line({"op": "mcm", "pt": 2500, "ct": "HEARTBEAT"}),
        _line({"op": "heartbeat", "id": 0}),
        _line(closed),
        ConnectionError("reset by peer"),
    ]


def _tick(ts: float, *, in_play: bool, closed: bool = False, back_home: float = 2.08,
          lay_home: float = 2.12, back_away: float = 1.94, lay_away: float = 1.96,
          market_id: str = MARKET) -> BetfairTick:
    timestamp = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc) + timedelta(seconds=ts)
    event = OddsEvent(
        match_id=betfair_match_id(market_id),
        timestamp=timestamp,
        team_home="India",
        team_away="Australia",
        back_home=back_home,
        lay_home=lay_home,
        back_away=back_away,
        lay_away=lay_away,
        is_live=in_play,
        source="betfair",
    )
    return BetfairTick(
        market_id=market_id,
        match_id=event.match_id,
        timestamp=timestamp,
        in_play=in_play,
        market_status="CLOSED" if closed else "OPEN",
        closed=closed,
        event=event,
        levels={},
        ltp={"home": back_home, "away": back_away, "draw": None},
        traded_volume={"home": None, "away": None},
    )


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def xadd_event(self, stream: str, data: dict[str, Any], maxlen: int = 0) -> str:
        self.published.append((stream, data))
        return "1-0"


class FakeExecutor:
    def __init__(self, rows: int = 1) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, sql: str, params: dict[str, Any]) -> int:
        self.calls.append((sql, dict(params)))
        return self.rows


class FakeFeedClient:
    def __init__(self, ticks: list[BetfairTick]) -> None:
        self._ticks = ticks

    async def stream(self):
        for tick in self._ticks:
            yield tick


# ----------------------------------------------------------------------
# Stream: fixture ticks, resume, heartbeats, failures
# ----------------------------------------------------------------------

class TestStreamTicks:
    async def test_fixture_messages_produce_expected_tick_sequence(self):
        connector = FakeConnector([FakeConnection(_single_connection_script())])
        ticks = await _collect_until_give_up(_client(connector, max_attempts=0))

        assert [t.in_play for t in ticks] == [False, True, False]
        assert [t.closed for t in ticks] == [False, False, True]

        pre = ticks[0]
        assert pre.match_id == "bf_1_234567"
        assert pre.event.team_home == "India" and pre.event.team_away == "Australia"
        assert pre.event.competition == "T20 International"
        assert pre.event.source == "betfair"
        assert pre.event.back_home == 2.10 and pre.event.lay_home == 2.12
        assert pre.event.back_away == 1.95 and pre.event.lay_away == 1.96
        assert pre.event.back_draw == 3.60 and pre.event.lay_draw == 3.70
        assert pre.event.volume_back_home == 500.0
        assert pre.ltp == {"home": 2.10, "away": 1.96, "draw": 3.65}
        assert pre.traded_volume == {"home": 12000.0, "away": 9000.0}
        assert pre.levels["back_home"] == [(2.10, 500.0), (2.08, 300.0), (2.06, 200.0)]
        assert pre.event.is_live is False

        live = ticks[1]
        assert live.event.back_home == 2.08  # level 0 removed (size 0), next level becomes best
        assert live.ltp["away"] == 2.00
        assert live.event.is_live is True and live.in_play is True

        closed = ticks[2]
        assert closed.market_status == "CLOSED"
        assert closed.event.back_home == 2.08  # ladder state carried forward

    async def test_client_sends_only_authentication_and_subscription(self):
        conn = FakeConnection(_single_connection_script())
        await _collect_until_give_up(_client(FakeConnector([conn]), max_attempts=0))
        ops = [m["op"] for m in conn.sent]
        assert ops == ["authentication", "marketSubscription"]
        sub = conn.sent[1]
        assert sub["marketFilter"] == {"marketIds": [MARKET]}
        assert sub["marketDataFilter"]["fields"] == ["EX_BEST_OFFERS", "EX_LTP", "EX_TRADED_VOL", "EX_MARKET_DEF"]
        assert sub["marketDataFilter"]["ladderLevels"] == 3
        assert "initialClk" not in sub  # first connection has nothing to resume from

    async def test_resume_after_reconnect_sends_clk_and_keeps_state(self):
        conn1 = FakeConnection([
            AUTH_SUCCESS,
            _sub_success,
            _line({"op": "mcm", "pt": 1000, "ct": "SUB_IMAGE", "clk": "c1", "initialClk": "i1", "mc": [_image_mc()]}),
            _line({
                "op": "mcm", "pt": 2000, "clk": "c2",
                "mc": [{"id": MARKET, "marketDefinition": _market_def(in_play=True),
                        "rc": [{"id": 111, "batb": [[0, 2.10, 0]]}]}],
            }),
            ConnectionError("reset by peer"),
        ])
        conn2 = FakeConnection([
            AUTH_SUCCESS,
            _sub_success,
            _line({
                "op": "mcm", "pt": 3000, "clk": "c3",
                "mc": [{"id": MARKET, "marketDefinition": _market_def(status="CLOSED", winner=111)}],
            }),
        ])
        sleeps: list[float] = []

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        client = BetfairStreamClient(APP_KEY, SESSION, [MARKET], connector=FakeConnector([conn1, conn2]),
                                     max_attempts=2, sleep=record_sleep)
        ticks = await _collect_until_give_up(client)

        sub2 = next(m for m in conn2.sent if m["op"] == "marketSubscription")
        assert sub2["initialClk"] == "i1"
        assert sub2["clk"] == "c2"
        assert sleeps and sleeps[0] == 1.0  # first backoff uses the base delay
        assert [t.closed for t in ticks] == [False, False, True]
        assert ticks[-1].event.back_home == 2.08  # removal from before the drop still applies

    async def test_image_message_replaces_ladder(self):
        image_only_level_zero = {
            "op": "mcm", "pt": 4000, "ct": "SUB_IMAGE", "clk": "c4", "initialClk": "i4",
            "mc": [{"id": MARKET, "img": True,
                    "rc": [{"id": 111, "batb": [[0, 2.30, 100]], "batl": [[0, 2.32, 100]]},
                           {"id": 222, "batb": [[0, 1.90, 100]], "batl": [[0, 1.92, 100]]}]}],
        }
        conn = FakeConnection([
            AUTH_SUCCESS, _sub_success, _line(_image_mc()),
            _line(image_only_level_zero), ConnectionError("eof"),
        ])
        ticks = await _collect_until_give_up(_client(FakeConnector([conn]), max_attempts=0))
        last = ticks[-1]
        assert last.event.back_home == 2.30  # old levels 1-3 were dropped by the image
        assert last.levels["back_home"] == [(2.30, 100.0)]

    async def test_heartbeats_and_bad_json_produce_no_ticks(self):
        conn = FakeConnection([
            AUTH_SUCCESS, _sub_success,
            "not json at all",
            _line({"op": "heartbeat", "id": 0}),
            _line({"op": "mcm", "pt": 1000, "ct": "HEARTBEAT"}),
            _line({"op": "connection", "connectionId": "001"}),
            ConnectionError("eof"),
        ])
        ticks = await _collect_until_give_up(_client(FakeConnector([conn]), max_attempts=0))
        assert ticks == []

    async def test_silent_connection_is_dropped_after_heartbeat_timeout(self):
        connector = HangingConnector()
        client = _client(connector, max_attempts=0, heartbeat_timeout_s=0.01)
        await _collect_until_give_up(client)
        assert len(connector.opened) == 1
        assert connector.opened[0].closed is True

    async def test_auth_failure_is_not_retried(self):
        rejected = FakeConnection([
            _line({"op": "status", "id": 1, "statusCode": "FAILURE", "errorCode": "INVALID_SESSION_INFORMATION"}),
        ])
        connector = FakeConnector([rejected, FakeConnection([AUTH_SUCCESS])])
        client = _client(connector, max_attempts=5)
        with pytest.raises(BetfairAuthError):
            async for _ in client.stream():
                pass
        assert len(connector.opened) == 1

    def test_missing_credentials_are_rejected_without_echoing_values(self):
        with pytest.raises(BetfairAuthError) as info:
            BetfairStreamClient("", "", [MARKET], connector=FakeConnector([]))
        assert "required" in str(info.value)


class TestLogRedaction:
    async def test_credentials_never_reach_logs(self, monkeypatch):
        recorder = RecordingLogger()
        monkeypatch.setattr(betfair_stream, "logger", recorder)
        client = _client(RaisingConnector(), max_attempts=0)
        with pytest.raises(BetfairStreamError) as info:
            async for _ in client.stream():
                pass
        dump = repr(recorder.records) + str(info.value)
        assert SESSION not in dump
        assert APP_KEY not in dump
        assert any("[redacted]" in str(kw.get("error", "")) for _, _, kw in recorder.records)

    def test_parser_repr_hides_credentials(self):
        parser = BetfairParser(app_key=APP_KEY, session_token=SESSION, enabled=True)
        text = repr(parser)
        assert APP_KEY not in text and SESSION not in text


# ----------------------------------------------------------------------
# Publish and closing price
# ----------------------------------------------------------------------

class TestPublishAndClosingPrice:
    async def test_publisher_throttles_per_market_but_always_sends_close(self):
        bus = FakeBus()
        publisher = TickPublisher(bus, min_interval_s=3.0)
        decisions = [
            await publisher.publish(_tick(0, in_play=True)),
            await publisher.publish(_tick(1, in_play=True)),
            await publisher.publish(_tick(2, in_play=True)),
            await publisher.publish(_tick(3.5, in_play=True)),
            await publisher.publish(_tick(4, in_play=False, closed=True)),
        ]
        assert decisions == [True, False, False, True, True]
        assert [stream for stream, _ in bus.published] == ["stream:match_events"] * 3
        payload = bus.published[0][1]
        assert payload["source"] == "betfair"
        assert payload["match_id"] == "bf_1_234567"
        assert payload["market_id"] == MARKET
        assert OddsEvent.from_redis_data(payload) is not None  # consumers still read the payload

    async def test_closing_price_is_stored_at_market_close(self):
        executor = FakeExecutor(rows=1)
        recorder = ClosingPriceRecorder(executor, resolve_match_id=lambda market_id: "lb_123")
        assert await recorder.observe(_tick(1, in_play=False)) is False  # pre-match is not the close
        assert await recorder.observe(_tick(2, in_play=True, back_home=2.08)) is False
        assert await recorder.observe(_tick(3, in_play=True, back_home=2.02, lay_home=2.04)) is False
        assert await recorder.observe(_tick(4, in_play=False, closed=True)) is True

        assert len(executor.calls) == 1
        sql, params = executor.calls[0]
        assert sql == CLOSING_UPDATE_SQL
        assert "UPDATE match_results" in sql and "INSERT" not in sql.upper()
        assert params == {
            "match_id": "lb_123",
            "closing_back_home": 2.02,
            "closing_back_away": 1.94,
            "closing_lay_home": 2.04,
            "closing_lay_away": 1.96,
        }
        # match_results.source records where the result came from; price provenance is odds_ticks.source
        assert "source" not in sql.split("WHERE")[0]

    async def test_closing_price_retried_when_result_row_missing(self):
        executor = FakeExecutor(rows=0)
        recorder = ClosingPriceRecorder(executor, resolve_match_id=lambda market_id: "lb_123")
        await recorder.observe(_tick(2, in_play=True))
        assert await recorder.observe(_tick(3, in_play=False, closed=True)) is False
        executor.rows = 1
        assert await recorder.observe(_tick(4, in_play=False, closed=True)) is True
        assert len(executor.calls) == 2

    async def test_no_write_without_in_play_prices_or_link(self):
        executor = FakeExecutor()
        unlinked = ClosingPriceRecorder(executor, resolve_match_id=lambda market_id: None)
        await unlinked.observe(_tick(2, in_play=True))
        assert await unlinked.observe(_tick(3, in_play=False, closed=True)) is False

        no_prices = ClosingPriceRecorder(executor, resolve_match_id=lambda market_id: "lb_1")
        assert await no_prices.observe(_tick(3, in_play=False, closed=True)) is False
        assert executor.calls == []

    async def test_run_feed_publishes_and_records(self):
        bus = FakeBus()
        executor = FakeExecutor()
        ticks = [_tick(1, in_play=True), _tick(2, in_play=True, back_home=2.05), _tick(3, in_play=False, closed=True)]
        await run_feed(
            FakeFeedClient(ticks),  # type: ignore[arg-type]
            TickPublisher(bus, min_interval_s=3.0),
            ClosingPriceRecorder(executor, resolve_match_id=lambda market_id: "lb_9"),
        )
        assert len(bus.published) == 2  # first in-play tick, then the closing tick (2 s is throttled)
        assert executor.calls[0][1]["closing_back_home"] == 2.05


# ----------------------------------------------------------------------
# REST catalogue (read-only) and mapping
# ----------------------------------------------------------------------

CATALOGUE = [{
    "marketId": MARKET,
    "marketStartTime": "2026-11-02T14:00:00.000Z",
    "competition": {"name": "T20 International"},
    "event": {"name": "India v Australia"},
    "runners": [
        {"selectionId": 111, "runnerName": "India", "sortPriority": 1},
        {"selectionId": 222, "runnerName": "Australia", "sortPriority": 2},
        {"selectionId": 333, "runnerName": "The Draw", "sortPriority": 3},
    ],
}]


def _book(in_play: bool) -> dict[str, Any]:
    return {
        "marketId": MARKET,
        "inplay": in_play,
        "runners": [
            {"selectionId": 111, "ex": {"availableToBack": [{"price": 2.1, "size": 500}],
                                        "availableToLay": [{"price": 2.12, "size": 400}]}},
            {"selectionId": 222, "ex": {"availableToBack": [{"price": 1.95, "size": 600}],
                                        "availableToLay": [{"price": 1.96, "size": 450}]}},
            {"selectionId": 333, "ex": {"availableToBack": [{"price": 3.6, "size": 50}],
                                        "availableToLay": [{"price": 3.7, "size": 50}]}},
        ],
    }


class TestRestCatalogue:
    async def test_fetch_live_odds_uses_read_endpoints_only(self):
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            assert request.headers["X-Authentication"] == SESSION  # sent as header, not in the URL
            assert SESSION not in str(request.url)
            if request.url.path.endswith("/listMarketCatalogue/"):
                return httpx.Response(200, json=CATALOGUE)
            if request.url.path.endswith("/listMarketBook/"):
                return httpx.Response(200, json=[_book(in_play=True)])
            return httpx.Response(404)

        parser = BetfairParser(APP_KEY, SESSION, enabled=True,
                               client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        events = await parser.fetch_live_cricket_odds()
        await parser.close()

        assert len(events) == 1
        event = events[0]
        assert event.match_id == "bf_1_234567"
        assert event.source == "betfair"
        assert event.is_live is True
        assert event.back_home == 2.1 and event.lay_away == 1.96 and event.back_draw == 3.6
        assert event.volume_back_home == 500.0
        assert sorted(set(p.rsplit("/", 2)[-2] for p in paths)) == ["listMarketBook", "listMarketCatalogue"]

    def test_prematch_book_is_built_but_filtered_for_live_fetch(self):
        event = odds_event_from_book(CATALOGUE[0], _book(in_play=False))
        assert event is not None and event.is_live is False

    async def test_disabled_or_unconfigured_parser_makes_no_requests(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        parser = BetfairParser("", "", enabled=True, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert await parser.fetch_live_cricket_odds() == []

    def test_from_env_reads_credentials_and_disables_when_missing(self, monkeypatch):
        monkeypatch.setenv("BETFAIR_APP_KEY", APP_KEY)
        monkeypatch.setenv("BETFAIR_SESSION_TOKEN", SESSION)
        assert BetfairParser.from_env().enabled is True
        monkeypatch.delenv("BETFAIR_SESSION_TOKEN")
        assert BetfairParser.from_env().enabled is False


class TestMatchMapper:
    START = "2026-11-02T14:00:00.000Z"

    def _entry(self, home: str = "India", away: str = "Australia") -> dict[str, Any]:
        return {
            "marketId": MARKET,
            "marketStartTime": self.START,
            "event": {"name": f"{home} v {away}"},
            "runners": [{"runnerName": home}, {"runnerName": away}, {"runnerName": "The Draw"}],
        }

    def test_links_one_match_across_three_sources(self):
        lotus = [{"match_id": "lb_1", "team_home": "India", "team_away": "Australia",
                  "start_time": "2026-11-02T13:50:00Z"}]
        cricsheet = [{"match_id": "cs_9", "team_home": "Australia", "team_away": "India",
                      "start_time": "2026-11-02T14:10:00+00:00"}]
        link = link_market(self._entry(), lotus, cricsheet)
        assert link.status == "linked"
        assert link.betfair_match_id == "bf_1_234567"
        assert link.lotusbook_match_id == "lb_1"
        assert link.cricsheet_match_id == "cs_9"

    def test_canonical_team_names_are_used(self):
        lotus = [{"match_id": "lb_2", "team_home": "Royal Challengers Bangalore",
                  "team_away": "Mumbai Indians", "start_time": self.START}]
        link = link_market(self._entry("Royal Challengers Bengaluru", "Mumbai Indians"), lotus, [])
        assert link.lotusbook_match_id == "lb_2"

    def test_ambiguous_candidates_link_to_nothing(self):
        lotus = [
            {"match_id": "lb_1", "team_home": "India", "team_away": "Australia", "start_time": self.START},
            {"match_id": "lb_3", "team_home": "India", "team_away": "Australia", "start_time": self.START},
        ]
        link = link_market(self._entry(), lotus, [])
        assert link.status == "ambiguous"
        assert link.lotusbook_match_id is None

    def test_start_time_outside_window_is_unmatched(self):
        lotus = [{"match_id": "lb_4", "team_home": "India", "team_away": "Australia",
                  "start_time": "2026-11-05T14:00:00Z"}]
        link = link_market(self._entry(), lotus, [])
        assert link.status == "unmatched"
        assert link.lotusbook_match_id is None


# ----------------------------------------------------------------------
# No order placement
# ----------------------------------------------------------------------

class TestNoOrderPlacement:
    def test_package_contains_no_order_placement_call(self):
        for path in PACKAGE_FILES:
            source = path.read_text(encoding="utf-8")
            for name in ORDER_CALLS:
                assert name not in source, f"{name} found in {path.name}"

    async def test_stream_never_sends_order_operations(self):
        conn = FakeConnection(_single_connection_script())
        await _collect_until_give_up(_client(FakeConnector([conn]), max_attempts=0))
        assert {m["op"] for m in conn.sent} <= {"authentication", "marketSubscription"}
