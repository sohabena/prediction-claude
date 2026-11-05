"""S101: The Odds API historical client and importer. Cost guard, budget stop, snapshot planning, in-play and Betfair
exclusions, draw handling, cache, key handling and the script's exit codes. httpx.MockTransport only; the store and
the cache live under tmp_path. No network, no database.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import httpx
import pytest

from scraper.importers import the_odds_api as oa
from training import historical_odds as ho

SECRET = "SECRET-KEY-S101-123"
SPORT = "cricket_big_bash"


def _event(eid, commence, home="Perth Scorchers", away="Sydney Sixers", sport=SPORT, bookmakers=None):
    return {
        "id": eid,
        "sport_key": sport,
        "sport_title": "Big Bash League",
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers or [],
    }


def _book(key, outcomes, title=None):
    return {
        "key": key,
        "title": title or key,
        "last_update": "2025-12-14T08:09:00Z",
        "markets": [{"key": "h2h", "outcomes": [{"name": n, "price": p} for n, p in outcomes]}],
    }


def _snapshot(timestamp, events):
    return {"timestamp": timestamp, "previous_timestamp": None, "next_timestamp": None, "data": events}


def _fake_api(events_by_date=None, odds_by_time=None, *, status=200):
    """A MockTransport that answers like the historical endpoints and prices each call as the docs say."""
    requests: list[httpx.Request] = []
    events_by_date = events_by_date or {}
    odds_by_time = odds_by_time or {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": f"bad call {request.url.path}"})
        when = request.url.params.get("date")
        if request.url.path.endswith("/events"):
            body = events_by_date.get(when, _snapshot(when, []))
            cost = 1
        else:
            body = odds_by_time.get(when, _snapshot(when, []))
            regions = len(request.url.params["regions"].split(","))
            markets = len(request.url.params["markets"].split(","))
            cost = 10 * regions * markets
        headers = {"x-requests-last": str(cost), "x-requests-used": "100", "x-requests-remaining": "900"}
        return httpx.Response(200, json=body, headers=headers)

    return httpx.MockTransport(handler), requests


def _no_request_transport():
    def handler(request):
        raise AssertionError(f"no request expected, got {request.url.path}")

    return httpx.MockTransport(handler)


# Estimate and cost

def test_estimate_counts_one_per_events_call_and_ten_per_region_and_market():
    assert oa.estimate_cost(3, 2, ["uk", "eu", "au"], ["h2h"]) == 3 + 2 * 30
    assert oa.estimate_cost(1, 0, "uk", "h2h") == 1
    assert oa.estimate_cost(0, 1, "uk,eu", "h2h,totals") == 40
    assert oa.estimate_cost(0, 0, "uk", "h2h") == 0


def test_client_spends_what_the_usage_header_says(tmp_path):
    transport, _ = _fake_api()
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=100)
    client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    assert client.spent == 1
    client.odds(SPORT, datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc), ["uk", "eu"], "h2h")
    assert client.spent == 21
    assert client.last_usage["remaining"] == 900


def test_odds_request_has_the_documented_parameters(tmp_path):
    transport, requests = _fake_api()
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=100)
    client.odds(SPORT, datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc), ["uk", "eu"], "h2h")
    url = requests[0].url
    assert url.path == f"/v4/historical/sports/{SPORT}/odds"
    assert url.params["regions"] == "eu,uk"  # sorted: the same cache file for any order of the same regions
    assert url.params["markets"] == "h2h"
    assert url.params["oddsFormat"] == "decimal"
    assert url.params["date"] == "2025-12-14T08:10:00Z"


# Budget

def test_zero_budget_sends_nothing(tmp_path):
    client = oa.TheOddsApiClient(SECRET, transport=_no_request_transport(), cache_root=tmp_path)
    with pytest.raises(oa.BudgetExceeded):
        client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    assert client.requests_made == 0 and client.spent == 0


def test_budget_stops_before_a_call_that_would_exceed_it(tmp_path):
    transport, requests = _fake_api()
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=25)
    # 3 regions x 1 market = 30 credits, more than the 25 budget: refused before any request is sent.
    with pytest.raises(oa.BudgetExceeded):
        client.odds(SPORT, datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc), ["uk", "eu", "au"], "h2h")
    assert requests == []


def test_budget_stops_the_run_once_spent(tmp_path):
    transport, requests = _fake_api()
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=5)
    client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))  # 1 credit: fits
    with pytest.raises(oa.BudgetExceeded):
        client.odds(SPORT, datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc), ["uk"], "h2h")  # 10: does not fit
    assert len(requests) == 1 and client.spent == 1


# Snapshot planning

def test_shared_starts_share_one_snapshot():
    events = [
        _event("a", "2025-12-14T08:15:00Z"),
        _event("b", "2025-12-14T08:15:00Z", home="Hobart Hurricanes", away="Melbourne Stars"),
        _event("c", "2025-12-14T08:20:00Z", home="Adelaide Strikers", away="Brisbane Heat"),
    ]
    snapshots = oa.plan_closing_snapshots(events)
    assert snapshots == [
        datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc),
        datetime(2025, 12, 14, 8, 15, tzinfo=timezone.utc),
    ]


def test_two_events_on_one_start_make_one_snapshot():
    events = [_event("a", "2025-12-14T08:15:00Z"), _event("b", "2025-12-14T08:15:00Z")]
    assert len(oa.plan_closing_snapshots(events)) == 1


def test_every_planned_snapshot_is_before_its_start():
    events = [_event("a", "2025-12-14T08:02:00Z"), _event("b", "2025-12-14T14:30:00Z")]
    for snapshot in oa.plan_closing_snapshots(events):
        starts = [oa._parse_time(e["commence_time"]) for e in events]
        assert any(snapshot < start for start in starts)
        assert snapshot.minute % 5 == 0


def test_events_in_window_keeps_distinct_events_on_their_start_date():
    day = _snapshot("2025-12-14T00:00:00Z", [_event("a", "2025-12-14T08:15:00Z")])
    again = _snapshot("2025-12-13T00:00:00Z", [_event("a", "2025-12-14T08:15:00Z"),
                                              _event("z", "2025-12-20T08:15:00Z")])
    kept = oa.events_in_window([day, again], date(2025, 12, 14), date(2025, 12, 14))
    assert [e["id"] for e in kept] == ["a"]


# Records

def test_a_record_follows_the_s98_contract():
    payload = _snapshot("2025-12-14T08:10:00Z", [
        _event("evt1", "2025-12-14T08:15:00Z", bookmakers=[_book("pinnacle", [("Perth Scorchers", 1.80),
                                                                                ("Sydney Sixers", 2.10)])]),
    ])
    records = oa.records_from_snapshot(payload)
    assert len(records) == 1
    record = records[0]
    assert record.source == "the_odds_api" and record.kind == "close"
    assert record.source_event_id == "evt1" and record.bookmaker == "pinnacle"
    assert record.format == "t20" and record.gender == "male" and record.competition == "Big Bash League"
    assert record.start_time_utc == datetime(2025, 12, 14, 8, 15, tzinfo=timezone.utc)
    assert record.snapshot_time_utc == datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc)
    assert (record.price_home, record.price_away, record.price_draw) == (1.80, 2.10, None)
    assert ho.validate(record) == []


def test_in_play_events_are_excluded():
    payload = _snapshot("2025-12-14T08:10:00Z", [
        _event("early", "2025-12-14T08:00:00Z", bookmakers=[_book("pinnacle", [("Perth Scorchers", 1.8),
                                                                              ("Sydney Sixers", 2.1)])]),
        _event("later", "2025-12-14T08:15:00Z", bookmakers=[_book("pinnacle", [("Perth Scorchers", 1.8),
                                                                              ("Sydney Sixers", 2.1)])]),
    ])
    counts: dict[str, int] = {}
    records = oa.records_from_snapshot(payload, counts=counts)
    assert [r.source_event_id for r in records] == ["later"]
    assert counts["in_play_skipped"] == 1


def test_betfair_bookmakers_are_dropped_and_counted():
    book_rows = [
        _book("betfair_ex_uk", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)]),
        _book("exch_1", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)], title="Betfair Exchange"),
        _book("pinnacle", [("Perth Scorchers", 1.9), ("Sydney Sixers", 2.0)]),
    ]
    payload = _snapshot("2025-12-14T08:10:00Z", [_event("evt1", "2025-12-14T08:15:00Z", bookmakers=book_rows)])
    counts: dict[str, int] = {}
    records = oa.records_from_snapshot(payload, counts=counts)
    assert [r.bookmaker for r in records] == ["pinnacle"]
    assert counts["betfair_dropped"] == 2
    assert all(not ho.is_betfair(r.bookmaker) for r in records)


def test_draw_becomes_price_draw_and_two_way_has_none():
    three_way = _book("pinnacle", [("Perth Scorchers", 2.5), ("Draw", 3.6), ("Sydney Sixers", 2.9)])
    two_way = _book("bet365", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)])
    payload = _snapshot("2025-12-14T08:10:00Z", [
        _event("test1", "2025-12-14T08:15:00Z", sport="cricket_test_match", bookmakers=[three_way, two_way]),
    ])
    by_book = {r.bookmaker: r for r in oa.records_from_snapshot(payload)}
    assert by_book["pinnacle"].price_draw == 3.6
    assert by_book["pinnacle"].format == "test"
    assert by_book["bet365"].price_draw is None
    assert ho.validate(by_book["pinnacle"]) == []


def test_an_unknown_outcome_name_skips_the_bookmaker_and_is_counted():
    payload = _snapshot("2025-12-14T08:10:00Z", [
        _event("evt1", "2025-12-14T08:15:00Z", bookmakers=[_book("pinnacle", [("Perth Scorchers", 1.8),
                                                                              ("Someone Else", 2.1)])]),
    ])
    counts: dict[str, int] = {}
    assert oa.records_from_snapshot(payload, counts=counts) == []
    assert counts["unmatched_outcome"] == 1


def test_unknown_sport_key_is_other_format():
    assert oa.sport_format("cricket_something_new") == ("other", "male")
    assert oa.sport_format("cricket_odi") == ("odi", "male")


def test_latest_snapshot_is_kept_per_event_and_bookmaker():
    early = _snapshot("2025-12-14T07:50:00Z", [_event("evt1", "2025-12-14T08:15:00Z", bookmakers=[
        _book("pinnacle", [("Perth Scorchers", 1.7), ("Sydney Sixers", 2.2)])])])
    late = _snapshot("2025-12-14T08:10:00Z", [_event("evt1", "2025-12-14T08:15:00Z", bookmakers=[
        _book("pinnacle", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)])])])
    records = oa.records_from_snapshot(early) + oa.records_from_snapshot(late)
    kept = oa.latest_per_match_book(records)
    assert len(kept) == 1 and kept[0].price_home == 1.8


# Cache

def test_cache_prevents_a_second_request(tmp_path):
    transport, requests = _fake_api()
    when = datetime(2025, 12, 14, 8, 10, tzinfo=timezone.utc)
    first = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=100)
    first.odds(SPORT, when, ["uk"], "h2h")
    first.odds(SPORT, when, ["uk"], "h2h")
    assert len(requests) == 1 and first.spent == 10

    again = oa.TheOddsApiClient(SECRET, transport=_no_request_transport(), cache_root=tmp_path, max_credits=0)
    assert again.odds(SPORT, when, ["uk"], "h2h") is not None
    assert again.requests_made == 0 and again.spent == 0
    assert (tmp_path / SPORT / "odds_20251214T081000Z_uk_h2h.json").exists()


def test_error_responses_are_not_cached(tmp_path):
    transport, _ = _fake_api(status=500)
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=100)
    with pytest.raises(oa.OddsApiError):
        client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    assert not any(tmp_path.rglob("*.json"))


def test_sport_keys_cannot_escape_the_cache_folder(tmp_path):
    client = oa.TheOddsApiClient(SECRET, transport=_no_request_transport(), cache_root=tmp_path)
    with pytest.raises(ValueError):
        client.cache_file("../escape", "events_x")


# Key handling

def test_the_key_never_reaches_logs_errors_or_file_names(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    # the OddsPapi client (S100) raises the httpx loggers to WARNING; show their records here to test the filter
    caplog.set_level(logging.DEBUG, logger="httpx")
    transport, _ = _fake_api()
    client = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path, max_credits=100)
    client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    # httpx logs the request URL, which holds the key; the filter must have redacted it
    assert any("HTTP Request" in r.getMessage() for r in caplog.records)
    assert SECRET not in caplog.text
    assert SECRET not in capsys.readouterr().out
    assert all(SECRET not in str(p) for p in tmp_path.rglob("*"))


def test_error_text_never_contains_the_key(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = oa.TheOddsApiClient(SECRET, transport=httpx.MockTransport(handler), cache_root=tmp_path,
                                 max_credits=100)
    with pytest.raises(oa.OddsApiError) as excinfo:
        client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    assert SECRET not in str(excinfo.value)
    assert SECRET not in caplog.text


def test_an_error_body_that_echoes_the_key_is_scrubbed(tmp_path):
    def handler(request):
        return httpx.Response(401, text=f"invalid key {SECRET}")

    client = oa.TheOddsApiClient(SECRET, transport=httpx.MockTransport(handler), cache_root=tmp_path,
                                 max_credits=100)
    with pytest.raises(oa.OddsApiError) as excinfo:
        client.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))
    assert "401" in str(excinfo.value) and SECRET not in str(excinfo.value)


def test_an_empty_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        oa.TheOddsApiClient("  ", transport=_no_request_transport(), cache_root=tmp_path)


# Script

def _run(argv, monkeypatch, tmp_path, transport, *, key=SECRET):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "import_the_odds_api.py"
    spec = importlib.util.spec_from_file_location("import_the_odds_api_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if key is None:
        monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("THE_ODDS_API_KEY", key)
    return module.main(
        argv,
        transport=transport,
        cache_root=tmp_path / "raw",
        store_root=tmp_path / "store",
    )


def test_missing_key_exits_two_and_sends_nothing(monkeypatch, tmp_path, capsys):
    code = _run(["--sport", SPORT, "--from", "2025-12-14", "--to", "2025-12-14", "--max-credits", "100"],
                monkeypatch, tmp_path, _no_request_transport(), key=None)
    assert code == 2
    assert "paid" in capsys.readouterr().out


def test_default_run_only_estimates_and_writes_nothing(monkeypatch, tmp_path, capsys):
    # Cache one event list so the plan has a snapshot to price.
    transport, _ = _fake_api({"2025-12-14T00:00:00Z": _snapshot("2025-12-14T00:00:00Z", [
        _event("evt1", "2025-12-14T08:15:00Z")])})
    seed = oa.TheOddsApiClient(SECRET, transport=transport, cache_root=tmp_path / "raw", max_credits=10)
    seed.events(SPORT, datetime(2025, 12, 14, tzinfo=timezone.utc))

    code = _run(["--sport", SPORT, "--from", "2025-12-14", "--to", "2025-12-14"], monkeypatch, tmp_path,
                _no_request_transport())
    out = capsys.readouterr().out
    assert code == 0
    assert "Nothing was requested" in out
    assert "Odds calls    : 1 planned, 0 cached, 1 to buy" in out
    assert "Credits to buy this plan: 10" in out
    assert not (tmp_path / "store").exists()
    assert SECRET not in out


def test_budgeted_run_writes_the_store_and_stays_within_budget(monkeypatch, tmp_path, capsys):
    snapshot_time = "2025-12-14T08:10:00Z"
    transport, requests = _fake_api(
        {"2025-12-14T00:00:00Z": _snapshot("2025-12-14T00:00:00Z", [
            _event("evt1", "2025-12-14T08:15:00Z", bookmakers=[
                _book("pinnacle", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)]),
                _book("betfair_ex_uk", [("Perth Scorchers", 1.7), ("Sydney Sixers", 2.2)]),
            ])])},
        {snapshot_time: _snapshot(snapshot_time, [
            _event("evt1", "2025-12-14T08:15:00Z", bookmakers=[
                _book("pinnacle", [("Perth Scorchers", 1.8), ("Sydney Sixers", 2.1)]),
                _book("betfair_ex_uk", [("Perth Scorchers", 1.7), ("Sydney Sixers", 2.2)]),
            ])])},
    )
    code = _run(["--sport", SPORT, "--from", "2025-12-14", "--to", "2025-12-14", "--max-credits", "50"],
                monkeypatch, tmp_path, transport)
    assert code == 0
    assert len(requests) == 2  # one events call (1) and one odds call (10): both inside the 50 budget
    records, manifest = ho.read_store("the_odds_api", root=tmp_path / "store")
    assert [r.bookmaker for r in records] == ["pinnacle"]
    assert manifest["runs"][-1]["credits_spent"] == 11
    assert manifest["runs"][-1]["complete"] is True
    assert manifest["problems"] == {}
    # no key in any file the run wrote
    assert all(SECRET not in p.read_text(encoding="utf-8", errors="ignore")
               for p in tmp_path.rglob("*") if p.is_file())


def test_a_budget_too_small_for_the_run_stops_and_says_so(monkeypatch, tmp_path, capsys):
    transport, requests = _fake_api({"2025-12-14T00:00:00Z": _snapshot("2025-12-14T00:00:00Z", [
        _event("evt1", "2025-12-14T08:15:00Z", bookmakers=[_book("pinnacle", [("Perth Scorchers", 1.8),
                                                                              ("Sydney Sixers", 2.1)])])])})
    code = _run(["--sport", SPORT, "--from", "2025-12-14", "--to", "2025-12-14", "--max-credits", "5"],
                monkeypatch, tmp_path, transport)
    out = capsys.readouterr().out
    assert code == 0
    assert len(requests) == 1  # the events call (1 credit) went; the 10-credit odds call was refused unsent
    assert "Complete               : False" in out
    _, manifest = ho.read_store("the_odds_api", root=tmp_path / "store")
    assert manifest["runs"][-1]["complete"] is False
    assert "needs 10 credits" in manifest["runs"][-1]["stop_reason"]


def test_bad_dates_exit_two(monkeypatch, tmp_path):
    code = _run(["--sport", SPORT, "--from", "2025-12-15", "--to", "2025-12-14"], monkeypatch, tmp_path,
                _no_request_transport())
    assert code == 2
