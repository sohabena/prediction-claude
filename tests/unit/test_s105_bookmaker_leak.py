"""S105: the bookmaker ("_BR") match-odds leg never takes the plain match_odds name on a real match.

Covers the parser's owner rule (event_records), the worker's exchange_seen memory (from a body and from seeded rows),
the monitor check bookmaker_leak (FAIL on a leak, WARN on collisions, SRL left out) and the harmonise relabel (an UPDATE
that skips collisions and simulated matches). Fakes only: no database, browser or network is touched.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scraper import market_pages
from scraper.market_pages import MarketPageWorker
from scraper.parsers.lotusbook_json import event_records, has_exchange_match_odds, is_bookmaker_leg
from scraper.parsers.lotusbook_markets import MarketRecord
from scripts import data_quality_monitor as dqm
from tests.unit import test_s55_data_quality as s55

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
MID = "lb_36266944"  # Hobart Hurricanes W v Melbourne Renegades W (2026-11-04 evidence)
EXCH = "1.264885668"  # the exchange market: plain id
BR = "1.75326606.340._BR"  # the bookmaker leg: its runners are always suspended
TEAMS = ["Hobart Hurricanes W", "Melbourne Renegades W"]
SRL_EVENT = json.loads((ROOT / "tests" / "fixtures" / "lotusbook" / "sma_event_srl_76108412_20261103.json")
                       .read_text(encoding="utf-8"))


# ── fixtures and helpers ──────────────────────────────────────────────────────────────


def _runner(name: str, price: float = 1.9) -> dict[str, Any]:
    return {"name": name, "status": "OPEN", "back": [{"price": price, "size": 100.0}],
            "lay": [{"price": price + 0.02, "size": 50.0}]}


def _market(market_id: str, name: str, runners: list[str], price: float = 1.9, status: str = "OPEN") -> dict[str, Any]:
    return {"id": market_id, "name": name, "status": status,
            "runners": [_runner(r, price) for r in runners]}


def _exchange(price: float = 1.9) -> dict[str, Any]:
    return _market(EXCH, "Match Odds", TEAMS, price)


def _bookmaker(price: float = 1.9) -> dict[str, Any]:
    return _market(BR, "Winner", TEAMS, price)


def _names(records: list[MarketRecord], market_id: str) -> set[str]:
    return {r.market_name for r in records if r.market_id == market_id}


class Store:
    def __init__(self) -> None:
        self.calls: list[list[MarketRecord]] = []

    async def __call__(self, match_id: str, records: list[MarketRecord]) -> None:
        self.calls.append(list(records))

    def names_of(self, market_id: str) -> set[str]:
        return {r.market_name for call in self.calls for r in call if r.market_id == market_id}


def _worker(store: Store) -> MarketPageWorker:
    return MarketPageWorker(MID, SimpleNamespace(), store, clock=lambda: 0.0, reload_every=10 ** 9)


# ── 1. parser: which market keeps the plain name ───────────────────────────────────────


class TestOwnerRule:
    def test_bookmaker_listed_first_is_suffixed_and_the_exchange_keeps_the_name(self):
        records = event_records({"result": [_bookmaker(), _exchange()]}, MID, NOW)
        assert _names(records, EXCH) == {"match_odds"}
        assert _names(records, BR) == {f"match_odds#{BR}"}

    def test_bookmaker_listed_last_gives_the_same_names(self):
        records = event_records({"result": [_exchange(), _bookmaker()]}, MID, NOW)
        assert _names(records, EXCH) == {"match_odds"}
        assert _names(records, BR) == {f"match_odds#{BR}"}

    def test_bookmaker_alone_keeps_the_plain_name_as_on_srl(self):
        # SRL matches carry only the bookmaker leg: it is recorded under the plain name, and never traded
        records = event_records({"result": [_bookmaker()]}, MID, NOW)
        assert _names(records, BR) == {"match_odds"}

    def test_bookmaker_alone_after_the_exchange_was_seen_is_suffixed(self):
        records = event_records({"result": [_bookmaker()]}, MID, NOW, exchange_seen=True)
        assert _names(records, BR) == {f"match_odds#{BR}"}

    def test_exchange_alone_keeps_the_plain_name_with_or_without_the_flag(self):
        assert _names(event_records({"result": [_exchange()]}, MID, NOW), EXCH) == {"match_odds"}
        assert _names(event_records({"result": [_exchange()]}, MID, NOW, exchange_seen=True), EXCH) == {"match_odds"}

    def test_names_do_not_flip_between_payloads_with_a_different_order(self):
        first = event_records({"result": [_bookmaker(), _exchange()]}, MID, NOW)
        second = event_records({"result": [_exchange(), _bookmaker()]}, MID, NOW)
        key = lambda r: (r.market_id, r.selection, r.market_name)  # noqa: E731
        assert {key(r) for r in first} == {key(r) for r in second}

    def test_a_non_match_odds_slot_is_owned_by_the_lowest_id_whatever_the_order(self):
        def team_total(market_id: str, price: float) -> dict[str, Any]:
            return _market(market_id, "Team total", ["over 5.5", "under 5.5"], price)

        first = event_records({"result": [team_total("1.2.b", 2.2), team_total("1.1.a", 1.9)]}, MID, NOW)
        second = event_records({"result": [team_total("1.1.a", 1.9), team_total("1.2.b", 2.2)]}, MID, NOW)
        for records in (first, second):
            assert _names(records, "1.1.a") == {"Team total"}
            assert _names(records, "1.2.b") == {"Team total#1.2.b"}

    def test_the_srl_fixture_still_stores_its_bookmaker_leg_under_the_plain_name(self):
        records = event_records(SRL_EVENT, "lb_76108412", NOW)
        match_odds = [r for r in records if r.market_type == "match_odds"]
        assert len(match_odds) == 2
        assert {r.market_name for r in match_odds} == {"match_odds"}

    def test_helpers_tell_the_exchange_from_the_bookmaker_leg(self):
        assert is_bookmaker_leg(BR) and not is_bookmaker_leg(EXCH) and not is_bookmaker_leg(None)
        assert has_exchange_match_odds({"result": [_exchange()]})
        assert not has_exchange_match_odds({"result": [_bookmaker()]})
        assert not has_exchange_match_odds({"result": []})


# ── 2. worker: remembers the exchange market, from a body or from stored rows ──────────


class TestWorkerExchangeMemory:
    @pytest.mark.asyncio
    async def test_the_worker_suffixes_the_leg_once_the_exchange_is_in_the_body(self):
        store = Store()
        worker = _worker(store)
        await worker.ingest_event_json({"result": [_exchange(), _bookmaker()]})
        assert store.names_of(EXCH) == {"match_odds"}
        assert store.names_of(BR) == {f"match_odds#{BR}"}

    @pytest.mark.asyncio
    async def test_the_exchange_is_remembered_after_it_leaves_the_page(self):
        store = Store()
        worker = _worker(store)
        await worker.ingest_event_json({"result": [_exchange(), _bookmaker()]})
        await worker.ingest_event_json({"result": [_bookmaker(price=2.5)]})  # the exchange market has left
        assert store.names_of(BR) == {f"match_odds#{BR}"}
        assert "match_odds" not in store.names_of(BR)

    @pytest.mark.asyncio
    async def test_a_fresh_worker_keeps_the_plain_name_until_it_has_seen_the_exchange(self):
        store = Store()
        worker = _worker(store)
        await worker.ingest_event_json({"result": [_bookmaker()]})
        assert store.names_of(BR) == {"match_odds"}  # nothing says an exchange market exists yet
        await worker.ingest_event_json({"result": [_exchange(), _bookmaker()]})
        await worker.ingest_event_json({"result": [_bookmaker(price=2.5)]})
        assert f"match_odds#{BR}" in store.names_of(BR)

    @pytest.mark.asyncio
    async def test_the_parser_gets_the_flag_after_the_first_exchange_payload(self, monkeypatch):
        seen_flags: list[bool] = []
        real = market_pages.event_records

        def spy(payload, match_id, observed_at, **kwargs):
            seen_flags.append(kwargs["exchange_seen"])
            return real(payload, match_id, observed_at, **kwargs)

        monkeypatch.setattr(market_pages, "event_records", spy)
        worker = _worker(Store())
        await worker.ingest_event_json({"result": [_exchange()]})
        await worker.ingest_event_json({"result": [_bookmaker(price=2.5)]})
        assert seen_flags == [False, True]

    @pytest.mark.asyncio
    async def test_seeded_exchange_row_counts_as_seen_when_the_page_is_attached(self):
        store = Store()
        worker = _worker(store)
        worker.seed([MarketRecord(time=NOW, match_id=MID, market_type="match_odds", market_name="match_odds",
                                  selection="Hobart Hurricanes W", market_id=EXCH)])
        await worker.ingest_event_json({"result": [_bookmaker()]})
        assert store.names_of(BR) == {f"match_odds#{BR}"}

    @pytest.mark.asyncio
    async def test_a_seeded_bookmaker_row_alone_does_not_count_as_the_exchange(self):
        store = Store()
        worker = _worker(store)
        worker.seed([MarketRecord(time=NOW, match_id=MID, market_type="match_odds", market_name="match_odds",
                                  selection="Hobart Hurricanes W", market_id=BR)])
        await worker.ingest_event_json({"result": [_bookmaker()]})
        assert store.names_of(BR) == {"match_odds"}


# ── 3. monitor: the bookmaker_leak check ───────────────────────────────────────────────


def _leak(match_id=MID, home="Hobart Hurricanes W", away="Melbourne Renegades W", competition="Women's Big Bash",
          leaked=0, colliding=0) -> dict[str, Any]:
    return {"match_id": match_id, "team_home": home, "team_away": away, "competition": competition,
            "leaked": leaked, "colliding": colliding}


def _check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return s55.one_check(dqm.check_bookmaker_leak, {"bookmaker_leak": rows})


class TestBookmakerLeakCheck:
    def test_the_check_is_registered_and_its_query_is_read_only(self):
        assert dqm.check_bookmaker_leak in dqm.CHECKS
        sql = dqm.SQL["bookmaker_leak"].strip()
        assert sql.upper().startswith("WITH") and ";" not in sql
        assert set(dqm._PARAM_RE.findall(dqm.SQL["bookmaker_leak"])) == {"since"}

    def test_a_leak_on_a_real_match_fails_and_names_the_match_and_rows(self):
        check = _check([_leak(leaked=560)])
        assert check["name"] == "bookmaker_leak"
        assert check["status"] == "fail" and check["value"] == 560
        assert MID in check["detail"] and "560 row(s)" in check["detail"]

    def test_healthy_when_nothing_leaks(self):
        check = _check([])
        assert check["status"] == "ok" and check["value"] == 0

    def test_an_srl_match_with_only_the_bookmaker_leg_passes(self):
        srl = _leak(match_id="lb_76108412", home="Sri Lanka SRL", away="New Zealand SRL", competition="SRL T20",
                    leaked=40)
        check = _check([srl])
        assert check["status"] == "ok" and check["value"] == 0

    def test_collisions_only_warn_and_are_counted_apart(self):
        check = _check([_leak(leaked=0, colliding=2)])
        assert check["status"] == "warn" and check["value"] == 2
        assert "collide" in check["detail"] and MID in check["detail"]

    def test_a_leak_fails_even_when_collisions_are_also_present(self):
        check = _check([_leak(leaked=3, colliding=2)])
        assert check["status"] == "fail" and check["value"] == 5
        assert "2 row(s) left as they are" in check["detail"]

    def test_the_check_reads_only_the_bookmaker_query(self):
        rows = s55.healthy_rows()
        rows["bookmaker_leak"] = [_leak(leaked=1)]
        report = s55.run(rows)
        assert s55.by_name(report)["bookmaker_leak"]["status"] == "fail"
        assert report["overall"] == "fail"


# ── 4. harmonise: the relabel is an UPDATE that skips collisions and simulated matches ─


class TestRelabelStep:
    def test_the_relabel_is_an_update_of_the_bookmaker_leak_only(self):
        sql = dqm.BOOKMAKER_LEAK_RELABEL_SQL.strip()
        assert sql.upper().startswith("UPDATE MARKET_TICKS")
        assert "DELETE" not in sql.upper() and "INSERT" not in sql.upper() and ";" not in sql
        assert "SET market_name = 'match_odds#' || m.market_id" in sql
        assert "m.market_name = 'match_odds'" in sql and "RIGHT(m.market_id, 3) = '_BR'" in sql

    def test_the_relabel_skips_simulated_matches_and_collisions(self):
        sql = dqm.BOOKMAKER_LEAK_RELABEL_SQL
        assert "s.is_simulated" in sql  # a simulated match is never touched
        assert sql.count("NOT EXISTS") == 2  # the simulated match, and the row whose new key already exists
        assert "x.market_name = 'match_odds#' || m.market_id" in sql

    def test_the_relabel_needs_an_exchange_market_on_the_same_match(self):
        sql = dqm.BOOKMAKER_LEAK_RELABEL_SQL
        assert "RIGHT(e.market_id, 3) <> '_BR'" in sql  # an SRL match (bookmaker leg only) keeps its plain name

    def test_relabel_runs_the_update_then_counts_the_collisions_left(self, monkeypatch):
        executed: list[tuple[str, dict]] = []

        class FakeResult:
            rowcount = 7

            def fetchall(self):
                return [SimpleNamespace(_mapping={"match_id": MID, "leaked": 0, "colliding": 2})]

        class FakeSession:
            async def execute(self, statement, params=None):
                executed.append((str(statement), params or {}))
                return FakeResult()

        @asynccontextmanager
        async def fake_get_session():
            yield FakeSession()

        import shared.db
        monkeypatch.setattr(shared.db, "get_session", fake_get_session)
        asyncio.run(dqm._relabel_bookmaker_leak())
        assert len(executed) == 2
        assert executed[0][0].lstrip().upper().startswith("UPDATE MARKET_TICKS")
        assert "WITH leaks" in executed[1][0]  # the monitor's own read-only query, after the update
        assert "since" in executed[1][1]

    def test_a_failing_relabel_is_logged_and_does_not_stop_the_harmonise_step(self, monkeypatch):
        @asynccontextmanager
        async def broken_get_session():
            raise RuntimeError("database down")
            yield  # pragma: no cover

        import shared.db
        monkeypatch.setattr(shared.db, "get_session", broken_get_session)
        asyncio.run(dqm._relabel_bookmaker_leak())  # must not raise

    def test_harmonise_relabels_before_it_links_twins(self, monkeypatch):
        order: list[str] = []

        async def relabel():
            order.append("relabel")

        async def link():
            order.append("link")

        @asynccontextmanager
        async def quiet_session():
            class Quiet:
                async def execute(self, statement, params=None):
                    return SimpleNamespace(rowcount=0)
            yield Quiet()

        import shared.db
        monkeypatch.setattr(shared.db, "get_session", quiet_session)
        monkeypatch.setattr(dqm, "_relabel_bookmaker_leak", relabel)
        monkeypatch.setattr(dqm, "_link_twins", link)
        asyncio.run(dqm._harmonise())
        assert order == ["relabel", "link"]
