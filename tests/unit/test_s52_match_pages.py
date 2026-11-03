"""S52: complete, clean match-page recording (per-record dedupe, closed markets, SRL classification,
coverage and restarts, scoreboard rows, result hand-off). Fakes only: no DB, Redis, browser or network."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scraper import manager as manager_module
from scraper.market_pages import MarketPageWorker
from scraper.parsers.lotusbook_markets import MarketRecord, parse_market_html
from scraper.parsers.lotusbook_scoreboard import parse_scoreboard_html
from shared.schemas import OddsEvent

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lotusbook"
SRL_PAGE = (FIX / "match_page_srl_live_20261103.html").read_text(encoding="utf-8")
SYNTHETIC = (FIX / "bookmaker_session_SYNTHETIC_UNVERIFIED.html").read_text(encoding="utf-8")
SB_LIVE = (FIX / "scoreboard_srl_live_20261103.html").read_text(encoding="utf-8")
SB_PRESTART = (FIX / "scoreboard_real_prestart_20261103.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 3, 2, 50, tzinfo=timezone.utc)
MID = "lb_76110448"
SITE = "https://lotusbook.cc/cricket"


class FakePage:
    """Serves queued HTML snapshots (the last one repeats). Never navigates."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self._last = snapshots[-1]

    async def content(self):
        return self._snapshots.pop(0) if self._snapshots else self._last

    async def reload(self, **kwargs):
        pass

    async def close(self):
        pass


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


class FakeDetector:
    def __init__(self):
        self.scoreboard_calls = []

    async def update_scoreboard(self, match_id, comment, finished):
        self.scoreboard_calls.append((match_id, comment, finished))
        return None


def _collect_store():
    calls = []

    async def store(match_id, records):
        calls.append(list(records))

    return calls, store


def _market_key(r):
    return (r.market_type, r.market_name, r.selection)


# ── task 1: per-record dedupe and closed markets ────────────────────────────────


class TestPerRecordDedupe:
    @pytest.mark.asyncio
    async def test_only_the_changed_record_is_stored(self):
        calls, store = _collect_store()
        changed = SYNTHETIC.replace(">1.95<", ">1.97<")
        worker = MarketPageWorker(MID, FakePage([SYNTHETIC, changed]), store)
        first_total = await worker.poll_once()
        second_total = await worker.poll_once()
        assert first_total == len(calls[0])
        assert len(calls) == 2
        assert [(r.selection, r.back) for r in calls[1]] == [("Emirates Blues", 1.97)]
        assert second_total == 1

    @pytest.mark.asyncio
    async def test_unchanged_markets_are_not_rewritten_when_one_changes(self):
        calls, store = _collect_store()
        changed = SRL_PAGE.replace('data-price="1.4"', 'data-price="1.41"', 1)
        assert changed != SRL_PAGE
        worker = MarketPageWorker(MID, FakePage([SRL_PAGE, changed]), store)
        await worker.poll_once()
        await worker.poll_once()
        assert len(calls[1]) < len(calls[0])
        assert all(r.market_type == "other" for r in calls[1])

    @pytest.mark.asyncio
    async def test_first_poll_stores_every_market_once(self):
        calls, store = _collect_store()
        worker = MarketPageWorker(MID, FakePage([SRL_PAGE]), store)
        await worker.poll_once()
        await worker.poll_once()
        assert len(calls) == 1
        assert len(calls[0]) == len(parse_market_html(SRL_PAGE, MID, NOW))


class TestClosedMarkets:
    ODD_EVEN_19 = 'aria-label="1st innings over 19 - Trinbago Knight Riders SRL odd/even runners and odds"'

    @pytest.mark.asyncio
    async def test_market_leaving_the_page_gets_one_closed_record(self):
        calls, store = _collect_store()
        gone = SRL_PAGE.replace(self.ODD_EVEN_19, 'aria-label="Something else runners and odds"')
        worker = MarketPageWorker(MID, FakePage([SRL_PAGE, gone, gone]), store)
        await worker.poll_once()
        await worker.poll_once()
        closed = [r for r in calls[1] if r.status == "closed"]
        assert {r.selection for r in closed} == {"Odd", "Even"}
        assert all(r.market_name == "odd_even:inn1:over19:Trinbago_Knight_Riders_SRL" for r in closed)
        assert all(r.back is None and r.lay is None and r.line_no is None and r.line_yes is None for r in closed)
        # the third poll sees the same page: the closed market is not written again
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_market_that_returns_is_recorded_again(self):
        calls, store = _collect_store()
        gone = SRL_PAGE.replace(self.ODD_EVEN_19, 'aria-label="Something else runners and odds"')
        worker = MarketPageWorker(MID, FakePage([SRL_PAGE, gone, SRL_PAGE]), store)
        await worker.poll_once()
        await worker.poll_once()
        await worker.poll_once()
        back = [r for r in calls[2] if r.market_name == "odd_even:inn1:over19:Trinbago_Knight_Riders_SRL"]
        assert len(back) == 2 and all(r.status == "open" and r.back is not None for r in back)

    @pytest.mark.asyncio
    async def test_closed_market_flickering_in_and_out_is_not_rewritten(self):
        # Seen live: a closed market drops out of some feed responses and comes back, still closed
        calls, store = _collect_store()
        closed_page = SRL_PAGE.replace(self.ODD_EVEN_19, 'aria-label="Something else runners and odds"')
        worker = MarketPageWorker(MID, FakePage([SRL_PAGE, closed_page, closed_page, closed_page]), store)
        for _ in range(4):
            await worker.poll_once()
        closed = [r for batch in calls for r in batch if r.status == "closed"]
        assert len(closed) == 2  # Odd and Even, once each

    @pytest.mark.asyncio
    async def test_page_without_any_market_block_closes_nothing(self):
        calls, store = _collect_store()
        empty = "<html><body><p>loading</p></body></html>"
        worker = MarketPageWorker(MID, FakePage([SYNTHETIC, empty]), store)
        await worker.poll_once()
        assert await worker.poll_once() == 0
        assert len(calls) == 1
        assert all(r.status != "closed" for r in calls[0])

    def test_closed_is_a_known_status(self):
        from scraper.parsers.lotusbook_markets import STATUSES

        assert "closed" in STATUSES


# ── task 2: classification of every block in the SRL fixture ───────────────────


class TestSrlClassification:
    @pytest.fixture(scope="class")
    def records(self):
        return parse_market_html(SRL_PAGE, MID, NOW)

    def _names(self, records):
        return {(r.market_type, r.market_name) for r in records}

    def test_total_records_and_no_key_collisions(self, records):
        assert len(records) == 32  # 30 priced rows + 2 suspended Winner runners
        keys = [_market_key(r) for r in records]
        assert len(keys) == len(set(keys))

    def test_over_runs_blocks(self, records):
        over = [r for r in records if r.market_type == "over_runs"]
        assert len(over) == 8
        assert {r.market_name for r in over} == {
            "over_runs:inn1:over18:Trinbago_Knight_Riders_SRL",
            "over_runs:inn1:over19:Trinbago_Knight_Riders_SRL",
        }
        assert {(r.selection, r.line_no, r.line_yes) for r in over if r.market_name.endswith("over18:Trinbago_Knight_Riders_SRL")} == {
            ("Under 6.5", 6.5, None), ("Over 6.5", None, 6.5),
            ("Under 7.5", 7.5, None), ("Over 7.5", None, 7.5),
        }
        assert {(r.selection, r.line_no, r.line_yes) for r in over if r.market_name.endswith("over19:Trinbago_Knight_Riders_SRL")} == {
            ("Under 7.5", 7.5, None), ("Over 7.5", None, 7.5),
            ("Under 8.5", 8.5, None), ("Over 8.5", None, 8.5),
        }

    def test_same_name_different_lines_do_not_collide(self, records):
        under_65 = [r for r in records if r.selection == "Under 6.5"]
        under_75 = [r for r in records if r.selection == "Under 7.5" and r.market_name.endswith("over18:Trinbago_Knight_Riders_SRL")]
        assert len(under_65) == 1 and len(under_75) == 1
        assert _market_key(under_65[0]) != _market_key(under_75[0])
        assert under_65[0].market_name == under_75[0].market_name

    def test_over_runs_prices(self, records):
        under_65 = next(r for r in records if r.selection == "Under 6.5")
        assert (under_65.back, under_65.lay) == (2.0, 2.33)

    def test_odd_even_blocks(self, records):
        oe = [r for r in records if r.market_name.startswith("odd_even:")]
        assert len(oe) == 4
        assert {(r.market_name, r.selection) for r in oe} == {
            ("odd_even:inn1:over18:Trinbago_Knight_Riders_SRL", "Odd"),
            ("odd_even:inn1:over18:Trinbago_Knight_Riders_SRL", "Even"),
            ("odd_even:inn1:over19:Trinbago_Knight_Riders_SRL", "Odd"),
            ("odd_even:inn1:over19:Trinbago_Knight_Riders_SRL", "Even"),
        }
        assert all(r.market_type == "other" for r in oe)

    def test_dismissal_blocks(self, records):
        dis = [r for r in records if r.market_name.startswith("dismissal:")]
        assert len(dis) == 18
        assert {r.market_name for r in dis} == {
            "dismissal:inn1:Narine_Sunil",
            "dismissal:inn1:Hosein_Akeal_Jerome",
            "dismissal:inn1:Trinbago_Knight_Riders_SRL:wicket8",
        }
        narine = {r.selection for r in dis if r.market_name == "dismissal:inn1:Narine_Sunil"}
        assert narine == {"fielder catch", "bowled", "keeper catch", "lbw", "run out", "stumped"}
        assert all(r.market_type == "other" for r in dis)

    def test_unpriced_winner_block_is_recorded_as_suspended(self):
        # Every price on the SRL Winner block is data-price="0" (not offered): a suspended market, kept with no prices.
        from scraper.parsers.lotusbook_markets import _classify_list_market

        assert _classify_list_market("Winner (incl. super over)") == ("match_odds", "match_odds")
        winner = [r for r in parse_market_html(SRL_PAGE, MID, NOW) if r.market_type == "match_odds"]
        assert {(r.selection, r.back, r.lay, r.status) for r in winner} == {
            ("St Lucia Kings SRL", None, None, "suspended"),
            ("Trinbago Knight Riders SRL", None, None, "suspended"),
        }

    def test_no_block_is_left_as_other_with_a_raw_name(self, records):
        other_names = {r.market_name for r in records if r.market_type == "other"}
        assert not [n for n in other_names if n.startswith(("1st innings", "Winner"))]

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Tie", ("other", "Tie")),
            ("Toss", ("other", "Toss")),
            ("Winner (incl. super over)", ("match_odds", "match_odds")),
            ("Match Odds", ("match_odds", "match_odds")),
            ("1st innings over 18 - Trinbago Knight Riders SRL total", ("over_runs", "over_runs:inn1:over18:Trinbago_Knight_Riders_SRL")),
            ("2nd innings over 6 - Sri Lanka SRL total", ("over_runs", "over_runs:inn2:over6:Sri_Lanka_SRL")),
            ("1st innings - Narine, Sunil - SRL dismissal method", ("other", "dismissal:inn1:Narine_Sunil")),
        ],
    )
    def test_classification_table(self, label, expected):
        from scraper.parsers.lotusbook_markets import _classify_list_market

        assert _classify_list_market(label) == expected


# ── task 3: coverage (cap, restart, absence stop) ───────────────────────────────


class FakeListWorker:
    """Replaces MarketPageWorker in the manager. mode 'hold' runs until stopped; 'error' ends at once."""

    def seed(self, records):
        pass

    mode = "hold"
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stored_snapshots = 0
        FakeListWorker.instances.append(self)

    async def run(self, stop):
        if FakeListWorker.mode == "hold":
            await stop.wait()


def _manager(monkeypatch, clock):
    opened: list = []

    async def fake_open(context, url):
        opened.append(url)
        return FakePage([SYNTHETIC])

    async def fake_href(page, lotus_id):
        return f"/cricket/team-{lotus_id}/4/{lotus_id}"

    monkeypatch.setattr(manager_module, "open_match_page", fake_open)
    monkeypatch.setattr(manager_module, "resolve_match_href", fake_href)
    monkeypatch.setattr(manager_module, "MarketPageWorker", FakeListWorker)
    FakeListWorker.instances = []
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
    mgr._market_now = clock
    mgr._scoreboard_last = {}
    mgr._scoreboard_missing_logged = set()
    mgr._scoreboard_result_sent = set()
    mgr._result_detector = FakeDetector()
    mgr.settings = SimpleNamespace(scraper=SimpleNamespace(poll_interval=3.0, betting_site_url=SITE))
    mgr._opened = opened
    return mgr


def _live_event(mid, is_live=True):
    return OddsEvent(
        match_id=mid,
        timestamp=NOW,
        team_home="St Lucia Kings SRL",
        team_away="Trinbago Knight Riders SRL",
        competition="T10 SRL League",
        is_live=is_live,
    )


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def _shutdown(mgr):
    for stop in list(mgr._market_stops.values()):
        stop.set()
    if mgr._market_tasks:
        await asyncio.wait(list(mgr._market_tasks.values()), timeout=2)
    if mgr._market_janitor_task is not None:
        mgr._market_janitor_task.cancel()
        await asyncio.gather(mgr._market_janitor_task, return_exceptions=True)


class TestCap:
    @pytest.mark.asyncio
    async def test_env_cap_is_respected(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_MARKET_WORKER_CAP", "2")
        FakeListWorker.mode = "hold"
        mgr = _manager(monkeypatch, Clock(NOW))
        try:
            for mid in ("lb_1", "lb_2", "lb_3"):
                await mgr._ensure_market_worker(_live_event(mid))
            assert set(mgr._market_tasks) == {"lb_1", "lb_2"}
            assert "lb_3" not in mgr._market_ended_at  # cap is not a failure: no restart bookkeeping
        finally:
            await _shutdown(mgr)

    def test_default_cap_is_twelve(self, monkeypatch):
        monkeypatch.delenv("SCRAPER_MARKET_WORKER_CAP", raising=False)
        assert manager_module.ScraperManager._market_cap() == 12

    @pytest.mark.parametrize("raw,expected", [("0", 1), ("abc", 12), ("7", 7)])
    def test_cap_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("SCRAPER_MARKET_WORKER_CAP", raw)
        assert manager_module.ScraperManager._market_cap() == expected


class TestRestart:
    @pytest.mark.asyncio
    async def test_error_end_is_restarted_after_60s_only_while_live(self, monkeypatch):
        clock = Clock(NOW)
        FakeListWorker.mode = "error"
        mgr = _manager(monkeypatch, clock)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            assert MID not in mgr._market_tasks and MID in mgr._market_ended_at
            clock.advance(30)
            await mgr._ensure_market_worker(_live_event(MID))
            assert len(mgr._opened) == 1  # still inside the 60 s delay
            clock.advance(31)
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            assert len(mgr._opened) == 2
            assert mgr._market_restarts[MID] == 1
        finally:
            FakeListWorker.mode = "hold"
            await _shutdown(mgr)

    @pytest.mark.asyncio
    async def test_at_most_five_restarts_per_match(self, monkeypatch):
        clock = Clock(NOW)
        FakeListWorker.mode = "error"
        mgr = _manager(monkeypatch, clock)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            for _ in range(10):
                clock.advance(61)
                await mgr._ensure_market_worker(_live_event(MID))
                await _settle()
            assert len(mgr._opened) == 6  # one start plus five restarts
            assert mgr._market_restarts[MID] == 5
        finally:
            FakeListWorker.mode = "hold"
            await _shutdown(mgr)

    @pytest.mark.asyncio
    async def test_failed_page_open_is_retried_like_a_crash(self, monkeypatch):
        clock = Clock(NOW)
        mgr = _manager(monkeypatch, clock)

        async def broken_open(context, url):
            raise RuntimeError("navigation timeout")

        monkeypatch.setattr(manager_module, "open_match_page", broken_open)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            assert mgr._market_tasks == {} and MID in mgr._market_ended_at
            monkeypatch.setattr(manager_module, "open_match_page", lambda c, u: _open_ok(mgr, u))
            clock.advance(61)
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            assert MID in mgr._market_tasks
        finally:
            await _shutdown(mgr)


async def _open_ok(mgr, url):
    mgr._opened.append(url)
    return FakePage([SYNTHETIC])


class TestAbsenceStop:
    @pytest.mark.asyncio
    async def test_worker_stops_after_ten_minutes_without_a_live_event(self, monkeypatch):
        clock = Clock(NOW)
        FakeListWorker.mode = "hold"
        mgr = _manager(monkeypatch, clock)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            clock.advance(599)
            mgr._sweep_market_workers(clock())
            assert not mgr._market_stops[MID].is_set()
            clock.advance(2)
            mgr._sweep_market_workers(clock())
            assert mgr._market_stops[MID].is_set()
            await asyncio.wait(list(mgr._market_tasks.values()), timeout=2)
            assert MID not in mgr._market_tasks
            assert MID not in mgr._market_ended_at  # a stop for absence is not an error, no restart
        finally:
            await _shutdown(mgr)

    @pytest.mark.asyncio
    async def test_completed_match_stops_at_once(self, monkeypatch):
        clock = Clock(NOW)
        FakeListWorker.mode = "hold"
        mgr = _manager(monkeypatch, clock)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            mgr._completed_match_ids.add(MID)
            mgr._sweep_market_workers(clock())
            assert mgr._market_stops[MID].is_set()
        finally:
            await _shutdown(mgr)

    @pytest.mark.asyncio
    async def test_live_again_after_an_absence_stop_starts_a_new_worker(self, monkeypatch):
        clock = Clock(NOW)
        FakeListWorker.mode = "hold"
        mgr = _manager(monkeypatch, clock)
        try:
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            clock.advance(601)
            mgr._sweep_market_workers(clock())
            await asyncio.wait(list(mgr._market_tasks.values()), timeout=2)
            await mgr._ensure_market_worker(_live_event(MID))
            await _settle()
            assert MID in mgr._market_tasks and len(mgr._opened) == 2
        finally:
            await _shutdown(mgr)

    @pytest.mark.asyncio
    async def test_pre_match_events_never_open_a_page(self, monkeypatch):
        clock = Clock(NOW)
        mgr = _manager(monkeypatch, clock)
        # _ensure_market_worker is only called for live events by _on_events; a not-live event must not open one
        await mgr._ensure_market_worker(_live_event(MID, is_live=False))
        assert mgr._opened == [] and mgr._market_tasks == {}


# ── task 4 and 5: scoreboard rows and result hand-off ───────────────────────────


class TestScoreboard:
    @pytest.mark.asyncio
    async def test_context_row_stored_only_on_change(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(NOW))
        stored = []

        async def recorder(redis, context, publish=True, store=True):
            stored.append((context, publish, store))

        mgr._publish_match_context = recorder
        state = parse_scoreboard_html(SB_LIVE)
        await mgr._on_market_scoreboard(MID, state, NOW)
        await mgr._on_market_scoreboard(MID, state, NOW + timedelta(seconds=3))
        assert len(stored) == 1
        context, publish, store = stored[0]
        # the batting side is the one with a CRR label: Trinbago Knight Riders, 74/7 in innings 2
        assert (context.score, context.wickets, context.batting_team) == (74, 7, "Trinbago Knight Riders SRL")
        assert publish is False and store is True  # stored for the recording, never published here

        bumped = replace(state, away=replace(state.away, runs=75))
        await mgr._on_market_scoreboard(MID, bumped, NOW + timedelta(seconds=6))
        assert len(stored) == 2 and stored[1][0].score == 75

    @pytest.mark.asyncio
    async def test_prestart_scoreboard_stores_nothing(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(NOW))
        stored = []

        async def recorder(redis, context, publish=True, store=True):
            stored.append(context)

        mgr._publish_match_context = recorder
        await mgr._on_market_scoreboard(MID, parse_scoreboard_html(SB_PRESTART), NOW)
        assert stored == []

    @pytest.mark.asyncio
    async def test_missing_widget_is_logged_once_per_match(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(NOW))
        await mgr._on_market_scoreboard(MID, None, NOW)
        await mgr._on_market_scoreboard(MID, None, NOW)
        assert mgr._scoreboard_missing_logged == {MID}
        await mgr._on_market_scoreboard("lb_other", None, NOW)
        assert mgr._scoreboard_missing_logged == {MID, "lb_other"}

    @pytest.mark.asyncio
    async def test_worker_passes_scoreboard_on_every_poll_and_still_records_markets(self):
        seen = []
        calls, store = _collect_store()

        async def on_sb(mid, state, observed_at):
            seen.append(state)

        worker = MarketPageWorker(MID, FakePage([SRL_PAGE, SB_LIVE]), store, on_scoreboard=on_sb)
        await worker.poll_once()
        await worker.poll_once()
        assert seen[0] is None  # the market page has no scoreboard widget
        assert seen[1] is not None and seen[1].home.runs == 183
        assert len(calls) == 1  # the scoreboard-only page changed nothing in the markets

    @pytest.mark.asyncio
    async def test_scoreboard_error_does_not_stop_market_recording(self):
        calls, store = _collect_store()

        async def boom(mid, state, observed_at):
            raise RuntimeError("scoreboard down")

        worker = MarketPageWorker(MID, FakePage([SYNTHETIC]), store, on_scoreboard=boom)
        assert await worker.poll_once() > 0
        assert len(calls) == 1


class TestResultHandOff:
    @pytest.mark.asyncio
    async def test_finished_comment_goes_to_the_detector_once(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(NOW))

        async def recorder(redis, context, publish=True, store=True):
            pass

        mgr._publish_match_context = recorder
        state = parse_scoreboard_html(SB_LIVE)
        finished = replace(state, comment="St Lucia Kings SRL won by 109 runs")
        await mgr._on_market_scoreboard(MID, finished, NOW)
        await mgr._on_market_scoreboard(MID, finished, NOW + timedelta(seconds=3))
        assert mgr._result_detector.scoreboard_calls == [
            (MID, "St Lucia Kings SRL won by 109 runs", True)
        ]

    @pytest.mark.asyncio
    async def test_live_comment_is_not_a_result(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(NOW))

        async def recorder(redis, context, publish=True, store=True):
            pass

        mgr._publish_match_context = recorder
        await mgr._on_market_scoreboard(MID, parse_scoreboard_html(SB_LIVE), NOW)
        assert mgr._result_detector.scoreboard_calls == []


class TestNoInteraction:
    def test_market_page_module_has_no_click_or_input_calls(self):
        text = (ROOT / "scraper" / "market_pages.py").read_text(encoding="utf-8")
        for forbidden in (".click(", ".fill(", ".type(", ".press(", ".goto(", ".route("):
            # open_match_page is the single navigation (goto) and only for the match URL
            if forbidden == ".goto(":
                assert text.count(".goto(") == 1
            else:
                assert forbidden not in text


@pytest.mark.asyncio
async def test_seeded_worker_does_not_rewrite_unchanged_markets_after_a_restart():
    first_calls, first_store = _collect_store()
    first = MarketPageWorker(MID, FakePage([SRL_PAGE]), first_store)
    await first.poll_once()
    stored = [r for batch in first_calls for r in batch]

    calls, store = _collect_store()
    restarted = MarketPageWorker(MID, FakePage([SRL_PAGE]), store)
    restarted.seed(stored)  # what the manager reads back from market_ticks
    await restarted.poll_once()
    assert calls == []
