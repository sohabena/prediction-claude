"""S100: the OddsPapi client and importer. httpx.MockTransport and fake payloads only: no network, no database, no real
key. Files are written under tmp_path only.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from scraper.importers import oddspapi as op
from training import historical_odds as ho

KEY = "SECRET-KEY-4242"
START = datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_oddspapi.py"


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def at(minutes: float) -> str:
    return iso(START + timedelta(minutes=minutes))


class FakeClock:
    """A clock that only moves when the code sleeps, so cooldown and backoff tests run instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(tmp_path: Path, handler, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    client = op.OddsPapiClient(KEY, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep, root=tmp_path)
    return client, clock


def _fixture_item(**overrides) -> dict:
    item = {
        "fixtureId": "id1000001",
        "tournamentId": 7,
        "tournamentName": "T20 Blast Men",
        "sportId": 9,
        "sportName": "Cricket",
        "startTime": iso(START),
        "participant1Name": "Surrey",
        "participant2Name": "Essex",
        "statusId": 2,
        "statusName": "Ended",
    }
    item.update(overrides)
    return item


def _fixture(**overrides) -> op.Fixture:
    fixture = op.fixture_from_item(_fixture_item(**overrides))
    assert fixture is not None
    return fixture


def _entries(*points: tuple[float, float]) -> list[dict]:
    return [
        {"id": index, "createdAt": at(minutes), "price": price, "limit": 1000, "active": True, "exchangeMeta": None}
        for index, (minutes, price) in enumerate(points)
    ]


def _book(outcomes: Mapping[str, list], market: str = "271") -> dict:
    return {"markets": {market: {"outcomes": {oid: {"players": {"0": entries}} for oid, entries in outcomes.items()}}}}


def _history(**books: dict) -> dict:
    return {"fixtureId": "id1000001", "bookmakers": books}


def _three_way() -> dict:
    """Home, draw, away. Pre-start prices move; one post-start price per side must be ignored."""
    return _book({
        "271": _entries((-120, 1.90), (-30, 1.80), (5, 1.20)),
        "272": _entries((-120, 3.60), (-10, 3.70), (2, 9.00)),
        "273": _entries((-120, 2.00), (-10, 2.10), (1, 1.05)),
    })


def _two_way() -> dict:
    return _book({
        "271": _entries((-60, 1.70), (-5, 1.75)),
        "272": _entries((-60, 2.20), (-5, 2.10)),
    })


def _routes(history: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v4/sports":
            return httpx.Response(200, json=[
                {"sportId": 10, "slug": "soccer", "sportName": "Soccer"},
                {"sportId": 9, "slug": "cricket", "sportName": "Cricket"},
            ])
        if path == "/v4/fixtures":
            return httpx.Response(200, json=[_fixture_item()])
        if path == "/v4/historical-odds":
            assert request.url.params["fixtureId"] == "id1000001"
            return httpx.Response(200, json=history)
        raise AssertionError(f"unexpected request to {path}")

    return handler


def _no_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a request was made: {request.url.path}")


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("import_oddspapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- cooldown, cache, retries ---------------------------------------------------------------------------------------

def test_the_cooldown_is_honoured_between_calls(tmp_path):
    clock = FakeClock()
    times: list[float] = []

    def handler(request):
        times.append(clock.now)
        return httpx.Response(200, json={"n": len(times)})

    client, _ = _client(tmp_path, handler, clock)
    client.fetch_json("sports", "a", "/v4/sports", {})
    client.fetch_json("sports", "b", "/v4/sports", {})
    client.fetch_json("sports", "c", "/v4/sports", {})
    assert len(times) == 3
    assert times[1] - times[0] >= op.COOLDOWN_S
    assert times[2] - times[1] >= op.COOLDOWN_S


def test_a_cached_response_is_never_requested_again(tmp_path):
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"hello": "world"})

    client, _ = _client(tmp_path, handler)
    assert client.fetch_json("sports", "all", "/v4/sports", {}) == {"hello": "world"}
    assert client.fetch_json("sports", "all", "/v4/sports", {}) == {"hello": "world"}
    assert calls == ["/v4/sports"]
    saved = tmp_path / "raw" / "oddspapi" / "sports" / "all.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == {"hello": "world"}


def test_the_cache_layout_is_the_documented_one(tmp_path):
    client, _ = _client(tmp_path, _no_request)
    path = client.cache_path("historical-odds", op.historical_key("id1000001", ("pinnacle", "bet365")))
    assert path == tmp_path / "raw" / "oddspapi" / "historical-odds" / "id1000001__pinnacle-bet365.json"


def test_429_is_retried_after_the_documented_wait(tmp_path):
    replies = [
        httpx.Response(429, json={"code": "RATE_LIMITED", "retryMs": 2500}),
        httpx.Response(200, json={"ok": True}),
    ]

    def handler(request):
        return replies.pop(0)

    client, clock = _client(tmp_path, handler)
    assert client.fetch_json("sports", "a", "/v4/sports", {}) == {"ok": True}
    assert 2.5 in clock.sleeps
    assert replies == []


def test_5xx_is_retried_with_backoff(tmp_path):
    replies = [httpx.Response(500, json={"code": "INTERNAL_ERROR"}), httpx.Response(200, json={"ok": 1})]

    def handler(request):
        return replies.pop(0)

    client, clock = _client(tmp_path, handler)
    assert client.fetch_json("sports", "a", "/v4/sports", {}) == {"ok": 1}
    assert op.BACKOFF_BASE_S in clock.sleeps


def test_at_most_three_retries(tmp_path):
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"code": "MAINTENANCE"})

    client, _ = _client(tmp_path, handler)
    with pytest.raises(op.OddsPapiError) as info:
        client.fetch_json("sports", "a", "/v4/sports", {})
    assert len(calls) == op.MAX_RETRIES + 1
    assert KEY not in str(info.value)


def test_the_monthly_quota_is_not_retried(tmp_path):
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"code": "REQUEST_LIMIT_EXCEEDED", "message": "Request limit exceeded"})

    client, _ = _client(tmp_path, handler)
    with pytest.raises(op.QuotaExhausted):
        client.fetch_json("sports", "a", "/v4/sports", {})
    assert len(calls) == 1


def test_a_client_error_is_not_retried_and_hides_the_key(tmp_path):
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"code": "INVALID_API_KEY", "message": "bad key"})

    client, _ = _client(tmp_path, handler)
    with pytest.raises(op.OddsPapiError) as info:
        client.fetch_json("sports", "a", "/v4/sports", {})
    assert len(calls) == 1
    assert "INVALID_API_KEY" in str(info.value)
    assert KEY not in str(info.value)


def test_offline_never_requests_and_counts_the_miss(tmp_path):
    client = op.OddsPapiClient(KEY, transport=httpx.MockTransport(_no_request), root=tmp_path, offline=True)
    assert client.fetch_json("sports", "all", "/v4/sports", {}) is None
    assert client.stats["not_cached"] == 1
    assert client.stats["requests"] == 0


# --- fixtures and parsing -------------------------------------------------------------------------------------------

def test_discovery_keeps_finished_cricket_fixtures_once_each(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v4/sports":
            return httpx.Response(200, json=[
                {"sportId": 10, "slug": "soccer", "sportName": "Soccer"},
                {"sportId": 9, "slug": "cricket", "sportName": "Cricket"},
            ])
        if request.url.path == "/v4/fixtures":
            if request.url.params["from"].startswith("2026-01-10"):
                return httpx.Response(200, json=[
                    _fixture_item(),
                    _fixture_item(fixtureId="id2", statusId=1, statusName="In-Play"),
                ])
            return httpx.Response(200, json=[
                _fixture_item(),
                _fixture_item(fixtureId="id3", startTime=at(24 * 60 + 30),
                              participant1Name="Sussex", participant2Name="Kent"),
            ])
        raise AssertionError(request.url.path)

    client, _ = _client(tmp_path, handler)
    fixtures = op.discover_fixtures(client, date(2026, 1, 10), date(2026, 1, 11))
    assert [f.fixture_id for f in fixtures] == ["id1000001", "id3"]
    assert fixtures[0].tournament_name == "T20 Blast Men"
    assert (fixtures[0].home, fixtures[0].away) == ("Surrey", "Essex")
    assert fixtures[0].start_time_utc == START
    fixture_calls = [r for r in seen if r.url.path == "/v4/fixtures"]
    assert len(fixture_calls) == 2
    for call in fixture_calls:
        assert call.url.params["sportId"] == "9"
        assert call.url.params["statusId"] == "2"
        start, end = datetime.fromisoformat(call.url.params["from"].replace("Z", "+00:00")), datetime.fromisoformat(
            call.url.params["to"].replace("Z", "+00:00"))
        assert end - start == timedelta(days=1)
    assert sum(1 for r in seen if r.url.path == "/v4/sports") == 1


def test_offline_discovery_reads_the_cache_and_counts_gaps(tmp_path):
    online, _ = _client(tmp_path, _routes(_history()))
    op.discover_fixtures(online, date(2026, 1, 10), date(2026, 1, 10))
    offline = op.OddsPapiClient(KEY, transport=httpx.MockTransport(_no_request), root=tmp_path, offline=True)
    fixtures = op.discover_fixtures(offline, date(2026, 1, 10), date(2026, 1, 11))
    assert [f.fixture_id for f in fixtures] == ["id1000001"]
    assert offline.stats["uncached_windows"] == 1
    assert offline.stats["requests"] == 0


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Indian Premier League T20", "t20"),
        ("T20I Series", "t20"),
        ("One Day Internationals ODI", "odi"),
        ("Ashes Test", "test"),
        ("Sheffield Shield", "other"),
        ("Odisha Cup", "other"),
    ],
)
def test_format_comes_from_the_tournament_name(name, expected):
    assert op.format_from_tournament(name) == expected


@pytest.mark.parametrize("name, expected", [("Women's T20 Challenge", "female"), ("T20 Blast Men", "male")])
def test_gender_comes_from_the_tournament_name(name, expected):
    assert op.gender_from_tournament(name) == expected


def test_a_fixture_without_participants_or_start_is_skipped():
    assert op.fixture_from_item(_fixture_item(participant2Name="")) is None
    assert op.fixture_from_item(_fixture_item(startTime="not a time")) is None
    assert op.fixture_from_item(_fixture_item(participant2Name="Surrey")) is None


def test_bookmakers_are_requested_in_groups_of_three():
    assert op.bookmaker_groups(["a", "b", "c", "d"]) == [("a", "b", "c"), ("d",)]


# --- Betfair guard --------------------------------------------------------------------------------------------------

def test_betfair_is_removed_from_the_request_list():
    slugs, removed = op.requested_bookmakers(["pinnacle", "Betfair_Ex_UK", "bet365", "betfair", "pinnacle"])
    assert slugs == ["pinnacle", "bet365"]
    assert removed == 2


def test_betfair_is_never_requested_and_never_stored(tmp_path):
    urls: list[str] = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=_history(pinnacle=_three_way()))

    slugs, _ = op.requested_bookmakers(["pinnacle", "betfair_ex_uk"])
    client, _ = _client(tmp_path, handler)
    records, stats = op.import_fixtures(client, [_fixture()], slugs)
    assert urls and all("betfair" not in url.lower() for url in urls)
    assert stats["dropped_betfair"] == 0


def test_betfair_in_a_response_is_dropped_and_counted():
    stats = op.Counter()
    payload = _history(betfair_ex_uk=_three_way(), pinnacle=_three_way(), **{"Betfair Exchange": _three_way()})
    records = op.records_from_history(_fixture(), payload, stats=stats)
    assert {r.bookmaker for r in records} == {"pinnacle"}
    assert stats["dropped_betfair"] == 2
    assert all(not ho.is_betfair(r.bookmaker) for r in records)


# --- open, close, draw ----------------------------------------------------------------------------------------------

def test_open_and_close_use_the_right_prices_and_ignore_those_after_the_start():
    records = op.records_from_history(_fixture(), _history(pinnacle=_three_way()))
    by_kind = {r.kind: r for r in records}
    assert set(by_kind) == {"open", "close"}

    opened = by_kind["open"]
    assert (opened.price_home, opened.price_away, opened.price_draw) == (1.90, 2.00, 3.60)
    assert opened.snapshot_time_utc == START - timedelta(minutes=120)

    closed = by_kind["close"]
    # the home price of 1.20 at +5 min and the draw at 9.00 at +2 min are after the start and must not be used
    assert (closed.price_home, closed.price_away, closed.price_draw) == (1.80, 2.10, 3.70)
    assert closed.snapshot_time_utc == START - timedelta(minutes=10)


def test_a_book_with_only_prices_after_the_start_gives_no_records():
    late = _book({"271": _entries((5, 1.2)), "272": _entries((5, 1.1))})
    assert op.records_from_history(_fixture(), _history(pinnacle=late)) == []


def test_a_draw_outcome_goes_to_price_draw_and_a_two_way_market_has_none():
    three = op.records_from_history(_fixture(), _history(pinnacle=_three_way()))
    assert all(r.price_draw is not None for r in three)
    two = op.records_from_history(_fixture(), _history(bet365=_two_way()))
    assert {r.bookmaker for r in two} == {"bet365"}
    assert all(r.price_draw is None for r in two)
    assert [(r.kind, r.price_home, r.price_away) for r in two] == [("open", 1.70, 2.20), ("close", 1.75, 2.10)]


def test_a_three_way_book_missing_its_draw_before_the_start_is_incomplete():
    book = _book({
        "271": _entries((-30, 1.8)),
        "272": _entries((5, 3.5)),
        "273": _entries((-30, 2.1)),
    })
    stats = op.Counter()
    assert op.records_from_history(_fixture(), _history(pinnacle=book), stats=stats) == []
    assert stats["incomplete"] == 1


def test_records_are_valid_under_the_s98_contract():
    records = op.records_from_history(_fixture(), _history(pinnacle=_three_way()))
    for record in records:
        assert ho.validate(record) == []
        assert record.source == "oddspapi" and record.source_event_id == "id1000001"
        assert record.format == "t20" and record.gender == "male"
        assert record.start_time_utc == START


# --- the script -----------------------------------------------------------------------------------------------------

def test_missing_key_exits_2_and_asks_for_a_free_key(script, tmp_path, capsys):
    code = script.main(
        ["--from", "2026-01-10", "--to", "2026-01-10", "--run"],
        environ={},
        dotenv_path=tmp_path / "absent.env",
        transport=httpx.MockTransport(_no_request),
        root=tmp_path,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "ODDSPAPI_API_KEY" in err and "free" in err and ".env" in err


def test_the_key_can_come_from_the_dotenv_file(script, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=1\nODDSPAPI_API_KEY='from-file-123'\n", encoding="utf-8")
    assert script.api_key({}, env_file) == "from-file-123"
    assert script.api_key({"ODDSPAPI_API_KEY": "from-env"}, env_file) == "from-env"
    assert script.api_key({}, tmp_path / "missing.env") == ""


def test_dry_run_makes_no_request_and_lists_the_calls(script, tmp_path, capsys):
    code = script.main(
        ["--from", "2026-01-10", "--to", "2026-01-10"],
        environ={"ODDSPAPI_API_KEY": KEY},
        dotenv_path=tmp_path / "absent.env",
        transport=httpx.MockTransport(_no_request),
        root=tmp_path,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "no request made" in out
    assert "discovery windows not cached: 2" in out
    assert KEY not in out


def test_dry_run_after_a_run_counts_zero_uncached_calls(script, tmp_path, capsys):
    run_code = script.main(
        ["--from", "2026-01-10", "--to", "2026-01-10", "--bookmakers", "pinnacle,betfair_ex_uk", "--run"],
        environ={"ODDSPAPI_API_KEY": KEY},
        dotenv_path=tmp_path / "absent.env",
        transport=httpx.MockTransport(_routes(_history(pinnacle=_three_way()))),
        clock=FakeClock(),
        sleep=FakeClock().sleep,
        root=tmp_path / "store",
    )
    assert run_code == 0
    capsys.readouterr()
    code = script.main(
        ["--from", "2026-01-10", "--to", "2026-01-10", "--bookmakers", "pinnacle,betfair_ex_uk"],
        environ={"ODDSPAPI_API_KEY": KEY},
        dotenv_path=tmp_path / "absent.env",
        transport=httpx.MockTransport(_no_request),
        root=tmp_path / "store",
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "odds calls not cached: 0" in out
    assert "records already cached: 2" in out


def test_run_writes_the_store_with_provenance_and_no_key(script, tmp_path, capsys):
    clock = FakeClock()
    history = _history(pinnacle=_three_way(), bet365=_two_way(), betfair_ex_uk=_three_way())
    code = script.main(
        ["--from", "2026-01-10", "--to", "2026-01-10", "--bookmakers", "pinnacle,bet365,betfair_ex_uk", "--run"],
        environ={"ODDSPAPI_API_KEY": KEY},
        dotenv_path=tmp_path / "absent.env",
        transport=httpx.MockTransport(_routes(history)),
        clock=clock,
        sleep=clock.sleep,
        root=tmp_path / "store",
    )
    assert code == 0
    records, manifest = ho.read_store("oddspapi", root=tmp_path / "store")
    assert len(records) == 4  # pinnacle open and close (3-way), bet365 open and close (2-way)
    assert {r.bookmaker for r in records} == {"pinnacle", "bet365"}
    assert manifest["problems"] == {}
    assert manifest["stats"]["dropped_betfair"] == 1
    assert manifest["requested_bookmakers"] == ["pinnacle", "bet365"]
    assert "OddsPapi" in manifest["provenance"] and "pinnacle" in manifest["provenance"]
    assert "betfair" not in manifest["provenance"].lower()
    assert manifest["date_range"] == {"from": "2026-01-10", "to": "2026-01-10"}
    assert KEY not in json.dumps(manifest)
    assert KEY not in capsys.readouterr().out


def test_the_key_never_reaches_logs_or_cache_file_names(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    replies = [httpx.Response(500, json={"code": "INTERNAL_ERROR"}), httpx.Response(200, json=_history(pinnacle=_three_way()))]

    def handler(request):
        return replies.pop(0)

    client, _ = _client(tmp_path, handler)
    records, _ = op.import_fixtures(client, [_fixture()], ["pinnacle"])
    assert records
    replies.append(httpx.Response(401, json={"code": "INVALID_API_KEY"}))
    with pytest.raises(op.OddsPapiError) as info:
        client.fetch_json("sports", "other", "/v4/sports", {})
    assert KEY not in str(info.value)
    assert KEY not in caplog.text
    for path in tmp_path.rglob("*"):
        assert KEY not in str(path)
        if path.is_file():
            assert KEY not in path.read_text(encoding="utf-8")


def test_srl_tournaments_are_skipped_and_windows_cover_several_days():
    """2026-11-04: about 60% of cricket fixtures are SRL; one billable fixtures call covers up to 9 days."""
    assert op.is_srl("Big Bash League SRL") and op.is_srl("T20 International SRL")
    assert not op.is_srl("Big Bash League") and not op.is_srl("Sri Lanka Premier League")
    with pytest.raises(ValueError):
        op.discover_fixtures(None, date(2026, 1, 1), date(2026, 1, 2), sport_id=27, window_days=10)
