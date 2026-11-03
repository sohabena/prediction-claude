"""S77: read-only match pages from PREMATCH_PAGE_LEAD_MIN before the start, slot priority, pre-emption, SRL excluded
pre-match, cap, and the prematch_pages monitor check. Fakes only: no DB, Redis, browser or network."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scraper import manager as manager_module
from scraper.market_pages import (
    LIVE_KIND,
    PREMATCH_KIND,
    PREMATCH_PAGE_LEAD_MIN_DEFAULT,
    PREMATCH_STAY_GRACE_S,
    MarketCandidate,
    in_prematch_window,
    plan_market_workers,
)
from scripts import data_quality_monitor as dqm
from shared.schemas import OddsEvent

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
LEAD = timedelta(minutes=60)
CAP = 12


def pre(mid: str, start_min: float, **kw) -> MarketCandidate:
    """A listed, not-live real match starting ``start_min`` minutes after T0."""
    kw.setdefault("listing_seen", T0)
    return MarketCandidate(match_id=mid, start=T0 + timedelta(minutes=start_min), **kw)


def live(mid: str, **kw) -> MarketCandidate:
    kw.setdefault("listing_seen", T0)
    kw.setdefault("live_seen", T0)
    return MarketCandidate(match_id=mid, is_live=True, **kw)


def plan(matches, running=None, now=T0, cap=CAP, lead=LEAD, absent_s=2700.0):
    return plan_market_workers(matches, running or {}, now, cap, lead, absent_s=absent_s)


# ── lead window boundaries ─────────────────────────────────────────────────────────


class TestLeadWindow:
    @pytest.mark.parametrize(
        "now_offset_s,expected",
        [
            (-1, False),     # one second before the window opens
            (0, True),       # exactly lead before the start: opens
            (30 * 60, True),
            (3600, True),    # exactly the start: still inside (inclusive)
            (3601, False),   # one second after the start and not live: not opened
        ],
    )
    def test_window_edges_for_a_real_match_at_start_60(self, now_offset_s, expected):
        start = T0 + timedelta(minutes=60)
        now = T0 + timedelta(seconds=now_offset_s)
        got = plan([pre("lb_1", 60)], now=now)
        assert ("lb_1" in got.starts) is expected
        assert in_prematch_window(start, now, LEAD) is expected

    def test_lead_is_a_parameter(self):
        match = pre("lb_1", 30)  # starts in 30 min
        assert plan([match], lead=timedelta(minutes=20)).starts == ()
        assert plan([match], lead=timedelta(minutes=30)).starts == ("lb_1",)
        assert plan([match], lead=timedelta(minutes=90)).starts == ("lb_1",)

    def test_pre_match_without_a_start_time_never_opens(self):
        assert plan([MarketCandidate(match_id="lb_1", listing_seen=T0)]).starts == ()

    def test_earliest_start_fills_first(self):
        got = plan([pre("lb_late", 50), pre("lb_early", 10), pre("lb_mid", 30)], cap=2)
        assert got.starts == ("lb_early", "lb_mid")


# ── SRL and the live path ──────────────────────────────────────────────────────────


class TestSimulatedAndLive:
    def test_srl_is_excluded_before_it_is_live(self):
        got = plan([pre("lb_srl", 10, simulated=True)])
        assert got.starts == ()

    def test_srl_opens_once_live(self):
        got = plan([live("lb_srl", simulated=True)])
        assert got.starts == ("lb_srl",)

    def test_live_real_match_opens_with_no_window_needed(self):
        got = plan([live("lb_1", start=T0 - timedelta(hours=2))])
        assert got.starts == ("lb_1",)

    def test_completed_match_never_opens(self):
        assert plan([pre("lb_1", 10, completed=True), live("lb_2", completed=True)]).starts == ()

    def test_not_startable_never_opens(self):
        assert plan([pre("lb_1", 10, startable=False), live("lb_2", startable=False)]).starts == ()


# ── slot priority and pre-emption ──────────────────────────────────────────────────


class TestPriority:
    def test_live_first_when_one_slot_is_free(self):
        got = plan([pre("lb_p", 30), live("lb_l")], cap=1)
        assert got.starts == ("lb_l",)
        assert got.stops == {}

    def test_pre_emption_closes_the_latest_start_first(self):
        running = {"lb_a": PREMATCH_KIND, "lb_b": PREMATCH_KIND, "lb_c": PREMATCH_KIND}
        matches = [pre("lb_a", 10), pre("lb_b", 20), pre("lb_c", 40), live("lb_l1"), live("lb_l2")]
        got = plan(matches, running, cap=3)
        assert got.starts == ("lb_l1", "lb_l2")
        assert got.stops == {"lb_c": "preempted", "lb_b": "preempted"}
        # the page with the earliest start survives, and the preempted ones are not restarted in the same plan
        assert "lb_a" not in got.stops and "lb_c" not in got.starts and "lb_b" not in got.starts

    def test_live_page_is_never_preempted(self):
        running = {"lb_L": LIVE_KIND}
        got = plan([live("lb_L"), live("lb_new")], running, cap=1)
        assert got.starts == () and got.stops == {}

    def test_live_waits_when_only_live_pages_hold_the_slots(self):
        running = {"lb_L1": LIVE_KIND, "lb_L2": LIVE_KIND}
        got = plan([live("lb_L1"), live("lb_L2"), live("lb_new"), pre("lb_p", 10)], running, cap=2)
        assert got.starts == () and got.stops == {}

    def test_running_pre_match_that_goes_live_is_a_live_page(self):
        # lb_a was opened pre-match and is now live: it must not be the one preempted for a new live match
        running = {"lb_a": PREMATCH_KIND, "lb_b": PREMATCH_KIND}
        matches = [live("lb_a"), pre("lb_b", 30), live("lb_new")]
        got = plan(matches, running, cap=2)
        assert got.stops == {"lb_b": "preempted"}
        assert got.starts == ("lb_new",)


# ── closing rules for running pages ────────────────────────────────────────────────


class TestClosing:
    def test_pre_match_page_survives_while_not_live_inside_the_window(self):
        got = plan([pre("lb_a", 10, listing_seen=T0 - timedelta(seconds=2600))], {"lb_a": PREMATCH_KIND})
        assert got.stops == {}

    def test_pre_match_page_closes_after_absence_from_the_listing(self):
        got = plan([pre("lb_a", 10, listing_seen=T0 - timedelta(seconds=2701))], {"lb_a": PREMATCH_KIND})
        assert got.stops == {"lb_a": "absent"}

    def test_pre_match_page_closes_when_start_moved_later_out_of_the_window(self):
        # Lead 60 min, grace 30 min: a page for a start at +91 min is more than 30 min outside its lead window at T0
        got = plan([pre("lb_a", 91)], {"lb_a": PREMATCH_KIND})
        assert got.stops == {"lb_a": "window"}

    def test_pre_match_page_stays_inside_the_grace_of_a_moved_start(self):
        # Start at +90 min: the lead window opens at +30 min and the grace reaches back to T0, so the page stays
        assert plan([pre("lb_a", 90)], {"lb_a": PREMATCH_KIND}).stops == {}
        assert plan([pre("lb_a", 80)], {"lb_a": PREMATCH_KIND}).stops == {}

    def test_pre_match_page_closes_when_not_live_long_after_the_start(self):
        grace = timedelta(seconds=PREMATCH_STAY_GRACE_S)
        start_min = -(grace.total_seconds() / 60) + 1  # started 29 min ago: still held
        assert plan([pre("lb_a", start_min)], {"lb_a": PREMATCH_KIND}).stops == {}
        start_min = -(grace.total_seconds() / 60) - 1  # started 31 min ago: out of the window
        assert plan([pre("lb_a", start_min)], {"lb_a": PREMATCH_KIND}).stops == {"lb_a": "window"}

    def test_pre_match_page_closes_when_completed(self):
        got = plan([pre("lb_a", 10, completed=True)], {"lb_a": PREMATCH_KIND})
        assert got.stops == {"lb_a": "completed"}

    def test_live_page_closes_after_absence_as_before(self):
        got = plan([live("lb_L", live_seen=T0 - timedelta(seconds=2701))], {"lb_L": LIVE_KIND})
        assert got.stops == {"lb_L": "absent"}
        got = plan([live("lb_L", live_seen=T0 - timedelta(seconds=2600))], {"lb_L": LIVE_KIND})
        assert got.stops == {}

    def test_live_page_closes_when_completed(self):
        got = plan([live("lb_L", completed=True)], {"lb_L": LIVE_KIND})
        assert got.stops == {"lb_L": "completed"}


# ── cap ────────────────────────────────────────────────────────────────────────────


class TestCap:
    def test_cap_is_never_exceeded_by_the_plan(self):
        matches = [pre(f"lb_{i}", 10 + i) for i in range(20)] + [live(f"lb_L{i}") for i in range(3)]
        got = plan(matches, cap=CAP)
        assert len(got.starts) == CAP
        assert got.starts[:3] == ("lb_L0", "lb_L1", "lb_L2")

    def test_full_cap_of_live_pages_starts_nothing(self):
        running = {f"lb_L{i}": LIVE_KIND for i in range(CAP)}
        matches = [live(mid) for mid in running] + [live("lb_new"), pre("lb_p", 10)]
        got = plan(matches, running, cap=CAP)
        assert got.starts == () and got.stops == {}

    def test_open_pages_after_the_plan_stay_within_cap(self):
        running = {f"lb_p{i}": PREMATCH_KIND for i in range(CAP)}
        matches = [pre(mid, 10 + i) for i, mid in enumerate(running)] + [live("lb_L1"), live("lb_L2")]
        got = plan(matches, running, cap=CAP)
        open_after = (set(running) - set(got.stops)) | set(got.starts)
        assert len(open_after) <= CAP
        assert got.starts == ("lb_L1", "lb_L2")
        assert set(got.stops) == {"lb_p11", "lb_p10"}  # the two latest starts make room


# ── manager wiring (fake pages) ────────────────────────────────────────────────────


class FakePage:
    async def close(self):
        pass


class FakeWorker:
    """Waits until its stop event is set. Never reads a page."""

    def __init__(self, match_id, page, store, **kwargs):
        self.match_id = match_id
        self.stored_snapshots = 0

    def seed(self, records):
        pass

    async def run(self, stop):
        await stop.wait()


async def _settle():
    for _ in range(6):
        await asyncio.sleep(0)


def _manager(monkeypatch, clock):
    opened: list[str] = []

    async def fake_open(context, url):
        opened.append(url)
        return FakePage()

    async def fake_href(page, lotus_id):
        return f"/cricket/team-{lotus_id}/4/{lotus_id}"

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
    mgr._market_now = clock
    mgr._last_market_state = _no_state  # no DB read
    mgr.settings = SimpleNamespace(scraper=SimpleNamespace(poll_interval=3.0, betting_site_url="https://x.test/cricket"))
    mgr._opened = opened
    return mgr


async def _no_state(match_id):
    return []


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


def _event(mid, *, start_min=None, is_live=False, competition="Test Cup", home="Alpha", away="Beta"):
    return OddsEvent(
        match_id=mid,
        timestamp=T0,
        team_home=home,
        team_away=away,
        competition=competition,
        is_live=is_live,
        scheduled_time=None if start_min is None else T0 + timedelta(minutes=start_min),
    )


async def _listed(mgr, *events):
    for ev in events:
        mgr._note_market_candidate(ev, mgr._market_now())
    await mgr._reconcile_market_workers(mgr._market_now())
    await _settle()


async def _shutdown(mgr):
    for stop in list(mgr._market_stops.values()):
        stop.set()
    if mgr._market_tasks:
        await asyncio.wait(list(mgr._market_tasks.values()), timeout=2)
    if mgr._market_janitor_task is not None:
        mgr._market_janitor_task.cancel()
        await asyncio.gather(mgr._market_janitor_task, return_exceptions=True)


class TestManagerWiring:
    async def test_real_pre_match_opens_inside_the_window_only(self, monkeypatch):
        monkeypatch.delenv("PREMATCH_PAGE_LEAD_MIN", raising=False)
        mgr = _manager(monkeypatch, Clock(T0))
        try:
            await _listed(mgr, _event("lb_in", start_min=50), _event("lb_out", start_min=90))
            assert set(mgr._market_tasks) == {"lb_in"}
            assert mgr._market_store()["kinds"]["lb_in"] == PREMATCH_KIND
        finally:
            await _shutdown(mgr)

    async def test_market_json_start_beats_the_listing_start(self, monkeypatch):
        """Live 2026-11-03: the listing said 11:30, the page's openDate 17:00; the page opened and closed every poll."""
        monkeypatch.delenv("PREMATCH_PAGE_LEAD_MIN", raising=False)
        mgr = _manager(monkeypatch, Clock(T0))
        mgr._market_store()["json_start"]["lb_wi"] = T0 + timedelta(hours=6)
        try:
            await _listed(mgr, _event("lb_wi", start_min=30))
            assert mgr._market_tasks == {}
            assert mgr._market_store()["candidates"]["lb_wi"].start == T0 + timedelta(hours=6)
        finally:
            await _shutdown(mgr)

    async def test_srl_pre_match_never_opens_but_srl_live_does(self, monkeypatch):
        monkeypatch.delenv("PREMATCH_PAGE_LEAD_MIN", raising=False)
        mgr = _manager(monkeypatch, Clock(T0))
        srl = dict(competition="Super Smash SRL", home="Sydney Thunder SRL", away="Perth Scorchers SRL")
        try:
            await _listed(mgr, _event("lb_srl", start_min=20, **srl))
            assert mgr._market_tasks == {}
            await _listed(mgr, _event("lb_srl", start_min=20, is_live=True, **srl))
            assert set(mgr._market_tasks) == {"lb_srl"}
        finally:
            await _shutdown(mgr)

    async def test_live_match_takes_the_slot_of_the_latest_pre_match_page(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_MARKET_WORKER_CAP", "2")
        mgr = _manager(monkeypatch, Clock(T0))
        try:
            await _listed(mgr, _event("lb_p1", start_min=10), _event("lb_p2", start_min=40))
            assert set(mgr._market_tasks) == {"lb_p1", "lb_p2"}
            stop_p1, stop_p2 = mgr._market_stops["lb_p1"], mgr._market_stops["lb_p2"]
            await _listed(mgr, _event("lb_p1", start_min=10), _event("lb_p2", start_min=40),
                          _event("lb_live", is_live=True))
            assert stop_p2.is_set()  # latest start closed first
            assert not stop_p1.is_set()
            assert "lb_live" in mgr._market_tasks
            assert mgr._market_store()["kinds"]["lb_live"] == LIVE_KIND
        finally:
            await _shutdown(mgr)

    async def test_cap_holds_with_pre_match_pages(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_MARKET_WORKER_CAP", "3")
        mgr = _manager(monkeypatch, Clock(T0))
        try:
            await _listed(mgr, *[_event(f"lb_{i}", start_min=5 + i) for i in range(6)])
            assert len(mgr._market_tasks) == 3
            assert set(mgr._market_tasks) == {"lb_0", "lb_1", "lb_2"}  # earliest starts first
        finally:
            await _shutdown(mgr)

    async def test_pre_match_page_is_not_stopped_for_not_being_live(self, monkeypatch):
        clock = Clock(T0)
        mgr = _manager(monkeypatch, clock)
        try:
            await _listed(mgr, _event("lb_p", start_min=30))
            clock.advance(20 * 60)
            mgr._sweep_market_workers(clock())
            assert not mgr._market_stops["lb_p"].is_set()
        finally:
            await _shutdown(mgr)

    async def test_open_page_lingers_after_the_result_then_closes(self, monkeypatch):
        """S76 follow-up: the page stays 30 min after the result so the settled Winner market is captured."""
        clock = Clock(T0)
        mgr = _manager(monkeypatch, clock)
        try:
            await _listed(mgr, _event("lb_live", is_live=True))
            mgr._completed_match_ids.add("lb_live")
            mgr._sweep_market_workers(clock())
            assert not mgr._market_stops["lb_live"].is_set()
            clock.advance(manager_module.MARKET_COMPLETED_LINGER_S - 60)
            mgr._sweep_market_workers(clock())
            assert not mgr._market_stops["lb_live"].is_set()
            clock.advance(120)
            mgr._sweep_market_workers(clock())
            assert mgr._market_stops["lb_live"].is_set()
        finally:
            await _shutdown(mgr)

    async def test_completed_match_gets_no_new_page(self, monkeypatch):
        mgr = _manager(monkeypatch, Clock(T0))
        mgr._completed_match_ids.add("lb_done")
        try:
            await _listed(mgr, _event("lb_done", is_live=True))
            assert mgr._market_tasks == {}
        finally:
            await _shutdown(mgr)

    async def test_pre_match_page_closes_when_absent_from_the_listing(self, monkeypatch):
        clock = Clock(T0)
        mgr = _manager(monkeypatch, clock)
        try:
            await _listed(mgr, _event("lb_p", start_min=30))
            clock.advance(2701)
            mgr._sweep_market_workers(clock())
            assert mgr._market_stops["lb_p"].is_set()
        finally:
            await _shutdown(mgr)

    async def test_lead_env_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_PAGE_LEAD_MIN", "30")
        mgr = _manager(monkeypatch, Clock(T0))
        try:
            await _listed(mgr, _event("lb_early", start_min=45))
            assert mgr._market_tasks == {}
            await _listed(mgr, _event("lb_soon", start_min=25))
            assert set(mgr._market_tasks) == {"lb_soon"}
        finally:
            await _shutdown(mgr)

    def test_invalid_lead_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_PAGE_LEAD_MIN", "soon")
        assert manager_module.ScraperManager._prematch_lead() == timedelta(minutes=PREMATCH_PAGE_LEAD_MIN_DEFAULT)
        monkeypatch.setenv("PREMATCH_PAGE_LEAD_MIN", "45")
        assert manager_module.ScraperManager._prematch_lead() == timedelta(minutes=45)


# ── monitor check ──────────────────────────────────────────────────────────────────


def _row(mid, *, page, listed=True, competition="Test Cup", home="Alpha", away="Beta"):
    return {
        "match_id": mid, "team_home": home, "team_away": away, "competition": competition,
        "match_start_time": T0, "listed_in_time": listed, "has_prematch_page": page,
    }


def _run_check(rows):
    async def fetch(name, params):
        assert name == "prematch_pages"
        return rows

    return asyncio.run(dqm.check_prematch_pages(fetch, {}))


class TestMonitor:
    def test_warns_below_ninety_percent(self):
        rows = [_row("a", page=True), _row("b", page=False)]
        check = _run_check(rows)
        assert check["name"] == "prematch_pages"
        assert check["status"] == dqm.WARN
        assert check["value"] == pytest.approx(0.5)

    def test_ok_at_ninety_percent(self):
        rows = [_row(f"m{i}", page=i != 0) for i in range(10)]
        assert _run_check(rows)["status"] == dqm.OK

    def test_srl_and_late_listed_matches_are_not_scored(self):
        rows = [
            _row("srl", page=False, competition="Super Smash SRL", home="Sydney Thunder SRL",
                 away="Perth Scorchers SRL"),
            _row("late", page=False, listed=False),
            _row("ok", page=True),
        ]
        check = _run_check(rows)
        assert check["status"] == dqm.OK
        assert check["value"] == pytest.approx(1.0)

    def test_nothing_to_score_is_ok(self):
        assert _run_check([])["status"] == dqm.OK

    def test_check_is_registered(self):
        assert dqm.check_prematch_pages in dqm.CHECKS
        assert dqm.PREMATCH_PAGE_WARN == 0.90
