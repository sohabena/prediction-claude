"""S47: Bookmaker and session market recording (parser, worker, eligibility, wiring)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scraper import manager as manager_module
from scraper.market_pages import MarketPageWorker, clamp_poll_interval, eligibility
from scraper.parsers.lotusbook_markets import (
    content_fingerprint,
    normalise_market_name,
    parse_market_html,
)
from shared.schemas import OddsEvent

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lotusbook"
SYNTHETIC = (FIX / "bookmaker_session_SYNTHETIC_UNVERIFIED.html").read_text(encoding="utf-8")
REAL_MATCH_ODDS = (FIX / "matchodds_live_20261102_sanitised.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc)
MID = "lb_75747932"


def _by(records, **kw):
    return [r for r in records if all(getattr(r, k) == v for k, v in kw.items())]


class TestParserBookmaker:
    def test_bookmaker_selections_with_back_lay(self):
        recs = parse_market_html(SYNTHETIC, MID, NOW)
        bm = _by(recs, market_type="bookmaker")
        assert {(r.selection, r.back, r.lay) for r in bm} == {
            ("Emirates Blues", 1.95, 2.02),
            ("Ajman", 1.90, 1.98),
        }
        assert all(r.market_name == "bookmaker" and r.status == "open" for r in bm)
        assert all(r.source == "lotusbook" and r.match_id == MID for r in bm)

    def test_other_market_is_recorded_as_other(self):
        recs = parse_market_html(SYNTHETIC, MID, NOW)
        other = _by(recs, market_type="other")
        assert len(other) == 1
        assert other[0].market_name == "Total match runs"
        assert other[0].selection == "Over 300" and other[0].back == 2.10


class TestParserSession:
    def test_session_market_has_no_and_yes_lines_and_rates(self):
        recs = parse_market_html(SYNTHETIC, MID, NOW)
        sess = _by(recs, market_type="session")
        assert len(sess) == 1
        s = sess[0]
        assert s.market_name == "session:6_over:India"
        assert (s.line_no, s.rate_no, s.line_yes, s.rate_yes) == (44.5, 1.90, 46.5, 1.92)
        assert s.status == "open"

    def test_suspended_state_parses_from_status_role(self):
        html = SYNTHETIC.replace(
            '<div role="listitem">\n      <div role="rowheader" aria-label="Runner: Ajman">',
            '<div role="listitem"><div role="status" aria-label="Market suspended">SUSPENDED</div>'
            '<div role="rowheader" aria-label="Runner: Ajman">',
        )
        assert html != SYNTHETIC
        recs = {r.selection: r for r in _by(parse_market_html(html, MID, NOW), market_type="bookmaker")}
        assert recs["Ajman"].status == "suspended"
        assert recs["Emirates Blues"].status == "open"

    def test_ball_running_state_parses_on_session_row(self):
        html = SYNTHETIC.replace(
            '<span data-odd-type="no">', '<span>BALL RUNNING</span><span data-odd-type="no">'
        )
        assert html != SYNTHETIC
        sess = _by(parse_market_html(html, MID, NOW), market_type="session")
        assert sess[0].status == "ball_running"
        assert sess[0].rate_no == 1.90  # prices unaffected by the state marker

    def test_data_status_attribute_sets_block_default(self):
        html = SYNTHETIC.replace(
            '<section data-market-name="6 over runs IND">',
            '<section data-market-name="6 over runs IND" data-status="suspended">',
        )
        sess = _by(parse_market_html(html, MID, NOW), market_type="session")
        assert sess[0].status == "suspended"


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("6 over runs IND", ("session", "session:6_over:India")),
            ("6 over runs", ("session", "session:6_over:match")),
            ("10 overs runs NZ", ("session", "session:10_over:New_Zealand")),
            ("Bookmaker", ("bookmaker", "bookmaker")),
            ("Total match runs", ("other", "Total match runs")),
        ],
    )
    def test_names(self, raw, expected):
        assert normalise_market_name(raw) == expected


class TestRealSnapshot:
    def test_real_match_odds_page_has_no_bookmaker_or_session_block(self):
        # Observed 2026-11-02: only Match Odds (suspended). Nothing to record from it.
        assert parse_market_html(REAL_MATCH_ODDS, MID, NOW) == []
        assert 'role="rowheader"' in REAL_MATCH_ODDS and "Market suspended" in REAL_MATCH_ODDS


class TestRealWinnerMarket:
    """Real Winner block (2026-11-03): the listing showed no lays for this match, the match page did."""

    WINNER = (FIX / "winner_market_live_20261103.html").read_text(encoding="utf-8")

    def test_best_back_and_lay_per_runner(self):
        records = parse_market_html(self.WINNER, MID, NOW)
        got = {(r.market_type, r.selection, r.back, r.lay, r.status) for r in records}
        assert got == {
            ("match_odds", "Toronto Kings", 1.52, 1.77, "open"),
            ("match_odds", "Montreal Warriors", 2.3, 2.92, "open"),
        }

    def test_desktop_ladder_is_used_when_mobile_view_is_missing(self):
        html = re.sub(r'<div aria-label="[^"]+ odds - mobile view">.*?</div><div aria-label', "<div aria-label",
                      self.WINNER)
        assert "mobile view" not in html
        records = parse_market_html(html, MID, NOW)
        assert {(r.selection, r.back, r.lay) for r in records} == {
            ("Toronto Kings", 1.52, 1.77), ("Montreal Warriors", 2.3, 2.92),
        }

    def test_empty_cells_are_none_not_zero(self):
        html = self.WINNER.replace('data-price="1.77"', 'data-price="0"')
        toronto = _by(parse_market_html(html, MID, NOW), selection="Toronto Kings")[0]
        assert toronto.back == 1.52 and toronto.lay is None

    def test_suspended_runner_list_is_recorded_with_status(self):
        html = (
            '<div role="list" aria-label="Match Odds runners and odds"><div role="listitem">'
            '<div role="rowheader" aria-label="Runner: India"></div>'
            '<div role="status" aria-label="Market suspended">SUSPENDED</div></div></div>'
        )
        [rec] = parse_market_html(html, MID, NOW)
        assert (rec.market_type, rec.selection, rec.back, rec.lay, rec.status) == (
            "match_odds", "India", None, None, "suspended")


class FakePage:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self._last = snapshots[-1]

    async def content(self):
        return self._snapshots.pop(0) if self._snapshots else self._last


class TestWorkerReload:
    @pytest.mark.asyncio
    async def test_page_is_reloaded_after_the_interval_only(self):
        winner = (FIX / "winner_market_live_20261103.html").read_text(encoding="utf-8")
        page = FakePage([winner])
        reloads = []

        async def reload(**kwargs):
            reloads.append(kwargs)

        page.reload = reload
        now = [0.0]
        stored = []

        async def store(mid, records):
            stored.append(records)

        worker = MarketPageWorker(MID, page, store, reload_every=300, clock=lambda: now[0])
        await worker.poll_once()
        now[0] = 299
        await worker.poll_once()
        assert reloads == []
        now[0] = 300
        await worker.poll_once()
        assert len(reloads) == 1 and reloads[0]["wait_until"] == "domcontentloaded"
        assert len(stored) == 1  # unchanged content after the reload stores nothing


class TestWorkerDedupe:
    @pytest.mark.asyncio
    async def test_unchanged_snapshots_store_nothing(self):
        calls: list = []

        async def store(mid, records):
            calls.append((mid, len(records)))

        page = FakePage([SYNTHETIC, SYNTHETIC, SYNTHETIC])
        worker = MarketPageWorker(MID, page, store)
        assert await worker.poll_once() > 0
        assert await worker.poll_once() == 0
        assert await worker.poll_once() == 0
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_changed_price_stores_again(self):
        calls: list = []

        async def store(mid, records):
            calls.append(records)

        changed = SYNTHETIC.replace(">1.95<", ">1.97<")
        worker = MarketPageWorker(MID, FakePage([SYNTHETIC, changed]), store)
        await worker.poll_once()
        await worker.poll_once()
        assert len(calls) == 2

    def test_fingerprint_ignores_timestamp(self):
        a = parse_market_html(SYNTHETIC, MID, NOW)
        b = parse_market_html(SYNTHETIC, MID, datetime(2030, 1, 1, tzinfo=timezone.utc))
        assert content_fingerprint(a) == content_fingerprint(b)

    def test_poll_interval_never_below_three_seconds(self):
        assert clamp_poll_interval(1.0) == 3.0
        assert clamp_poll_interval(5.0) == 5.0
        assert MarketPageWorker(MID, FakePage([SYNTHETIC]), None, poll_interval=0.5).poll_interval == 3.0


class TestEligibility:
    def test_live_simulated_match_is_eligible(self):
        # S50: SRL match pages are recorded too (tagged as simulated in the registry)
        assert eligibility(
            is_live=True,
            team_home="Central Districts SRL",
            team_away="Auckland SRL",
            competition="Super Smash SRL",
        ) == (True, "ok")

    def test_live_real_match_is_eligible(self):
        assert eligibility(is_live=True, team_home="Emirates Blues", team_away="Ajman") == (True, "ok")

    def test_not_live_is_not_eligible(self):
        assert eligibility(is_live=False, team_home="Dubai", team_away="Sharjah") == (False, "not_live")


class TestManagerWiring:
    @pytest.mark.asyncio
    async def test_live_simulated_match_opens_a_market_page(self, monkeypatch):
        # S50/S52: SRL matches are recorded like real ones, so a live SRL match gets the read-only page too.
        opened: list = []

        class FakePage:
            async def close(self):
                pass

        class FakeWorker:
            def __init__(self, **kwargs):
                self.stored_snapshots = 0

            def seed(self, records):
                pass

            async def run(self, stop):
                await stop.wait()

        async def fake_open(context, url):
            opened.append(url)
            return FakePage()

        async def fake_href(page, lotus_id):
            return "/cricket/central-districts-srl-vs-auckland-srl/4/76109044"

        monkeypatch.setattr(manager_module, "open_match_page", fake_open)
        monkeypatch.setattr(manager_module, "resolve_match_href", fake_href)
        monkeypatch.setattr(manager_module, "MarketPageWorker", FakeWorker)
        mgr = manager_module.ScraperManager.__new__(manager_module.ScraperManager)
        mgr._market_tasks = {}
        mgr._market_stops = {}
        mgr._market_ended_at = {}
        mgr._market_restarts = {}
        mgr._market_live_seen = {}
        mgr._market_janitor_task = None
        mgr._completed_match_ids = set()
        mgr._listing_page = object()
        mgr._context = object()
        mgr.settings = SimpleNamespace(
            scraper=SimpleNamespace(poll_interval=3.0, betting_site_url="https://lotusbook.cc/cricket")
        )
        event = OddsEvent(
            match_id="lb_76109044",
            timestamp=NOW,
            team_home="Central Districts SRL",
            team_away="Auckland SRL",
            competition="Super Smash SRL",
            is_live=True,
        )
        await mgr._ensure_market_worker(event)
        assert opened == ["https://lotusbook.cc/cricket/central-districts-srl-vs-auckland-srl/4/76109044"]
        assert set(mgr._market_tasks) == {"lb_76109044"}
        mgr._market_stops["lb_76109044"].set()
        await asyncio.wait(list(mgr._market_tasks.values()), timeout=2)
        assert mgr._market_tasks == {}
        assert "lb_76109044" not in mgr._market_ended_at  # an intentional stop is not an error


class TestMigrationAndSchema:
    def test_migration_is_chained_and_creates_table_only(self):
        text = (ROOT / "alembic" / "versions" / "003_market_ticks.py").read_text(encoding="utf-8")
        assert 'down_revision: Union[str, None] = "002_timescale_hardening"' in text
        assert "CREATE TABLE IF NOT EXISTS market_ticks" in text
        assert "create_hypertable" not in text
        assert re.search(r"PRIMARY KEY \(time, match_id, market_type, market_name, selection\)", text)

    def test_hypertable_registered_in_db_init(self):
        text = (ROOT / "backend" / "db_init.py").read_text(encoding="utf-8")
        assert "create_hypertable('market_ticks', 'time'" in text
