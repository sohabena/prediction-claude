"""S37: record every real live match, reject simulated events, exporter and health checks, watchdog.

All database access uses fake sessions; no Redis, DB or browser is touched.
"""

from __future__ import annotations

import argparse
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import scraper.manager as manager_mod
from backend.models.odds import OddsTick
from scraper.manager import ScraperManager
from scraper.match_filter import MatchClassifier, is_simulated_name
from scripts import export_recorded_corpus as exporter
from scripts import recording_health as health
from scripts.run_scraper_forever import Watchdog, WatchdogConfig, another_instance_running
from shared.schemas import OddsEvent

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- fakes
class FakeResult:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return None


class FakeSession:
    """Records every statement; ``handler(sql, params)`` returns the FakeResult for a query."""

    def __init__(self, handler=None):
        self.added: list = []
        self.statements: list[tuple[str, object]] = []
        self._handler = handler

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append((sql, params))
        if self._handler is not None:
            return self._handler(sql, params)
        return FakeResult()


def fake_get_session(session: FakeSession):
    @asynccontextmanager
    async def _get_session():
        yield session

    return _get_session


class FakeRedis:
    def __init__(self):
        self.client = SimpleNamespace(get=AsyncMock(return_value=None))
        self.published: list = []

    async def publish_event(self, channel, data):
        self.published.append((channel, data))

    async def set_json(self, *args, **kwargs):
        return None


class FakeDetector:
    def update_tick(self, **kwargs):
        return None

    async def check_completed(self, ids):
        return []

    def cleanup(self):
        return None

    def load_already_resolved(self, ids):
        return None


def live_event(match_id="m1", team_home="India", team_away="Australia", competition="ICC Men's T20 World Cup"):
    return OddsEvent(
        event_id=f"e_{match_id}",
        match_id=match_id,
        team_home=team_home,
        team_away=team_away,
        competition=competition,
        timestamp=datetime.now(UTC),
        is_live=True,
        back_home=1.90,
        lay_home=1.94,
        back_away=2.05,
        lay_away=2.10,
        volume_back_home=500.0,
        volume_lay_home=500.0,
        volume_back_away=500.0,
        volume_lay_away=500.0,
    )


def _event(team_home="India", team_away="Australia", competition=""):
    return live_event(team_home=team_home, team_away=team_away, competition=competition)


# --------------------------------------------------------------------------- recording gate (manager)
@pytest.fixture
def manager_env(monkeypatch, tmp_path):
    session = FakeSession()
    redis = FakeRedis()
    monkeypatch.setattr(manager_mod, "get_session", fake_get_session(session))
    monkeypatch.setattr(manager_mod, "get_redis", AsyncMock(return_value=redis))
    feed_path = tmp_path / "scraper_feed.json"
    monkeypatch.setattr(manager_mod, "FEED_STATE_PATH", feed_path)
    return SimpleNamespace(session=session, redis=redis, feed_path=feed_path)


def _new_manager(monkeypatch, record_all: str | None) -> ScraperManager:
    if record_all is None:
        monkeypatch.delenv("SCRAPER_RECORD_ALL", raising=False)
    else:
        monkeypatch.setenv("SCRAPER_RECORD_ALL", record_all)
    mgr = ScraperManager()
    mgr._result_detector = FakeDetector()
    mgr._upsert_training_status = AsyncMock()
    # Skip the DB reload of approvals; approval state is set explicitly by each test.
    mgr._last_approved_refresh = datetime.now(UTC)
    return mgr


async def test_live_qualifying_event_is_stored_without_approval(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)  # default: record all
    await mgr._on_events([live_event()])

    ticks = [o for o in manager_env.session.added if isinstance(o, OddsTick)]
    assert len(ticks) == 1
    assert ticks[0].match_id == "m1"
    # Not approved, so nothing is published to live consumers.
    assert manager_env.redis.published == []
    feed = json.loads(manager_env.feed_path.read_text(encoding="utf-8"))
    assert feed["live_matches"] == 1
    assert feed["last_live_event_at"] is not None


async def test_record_all_off_restores_approval_gate(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all="0")
    await mgr._on_events([live_event()])
    assert [o for o in manager_env.session.added if isinstance(o, OddsTick)] == []


async def test_approved_live_event_is_also_published(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    mgr._scrape_approved_ids.add("m1")
    await mgr._on_events([live_event()])
    assert [o for o in manager_env.session.added if isinstance(o, OddsTick)]
    assert manager_env.redis.published, "approved matches must still reach live consumers"


async def test_simulated_event_is_recorded_but_never_published(monkeypatch, manager_env):
    """S50 reversed S37's rule: SRL is recorded (tagged in the registry) but never reaches live consumers."""
    mgr = _new_manager(monkeypatch, record_all=None)
    mgr._match_filter.enabled = False
    await mgr._on_events([live_event(match_id="srl1", team_home="Hobart Hurricanes SRL",
                                     team_away="New Zealand SRL", competition="Simulated Reality League")])
    assert len([o for o in manager_env.session.added if isinstance(o, OddsTick)]) == 1
    assert manager_env.redis.published == []
    mgr._upsert_training_status.assert_awaited()


# --------------------------------------------------------------------------- simulated rejection
@pytest.mark.parametrize(
    "home, away, competition",
    [
        ("Hobart Hurricanes SRL", "Perth Scorchers SRL", ""),
        ("India", "Australia", "Simulated Reality League"),
        ("Virtual Kings", "Virtual Queens", ""),
        ("E-Cricket Titans", "E-Cricket Rangers", ""),
        ("Team A", "Team B", "e-cricket league"),
    ],
)
def test_simulated_names_are_rejected_and_counted(home, away, competition):
    classifier = MatchClassifier(enabled=True)
    ev = _event(home, away, competition)
    assert not classifier.qualifies(ev)
    assert classifier.stats["simulated_rejected"] >= 1
    assert classifier.stats["rejected"] >= 1


def test_real_names_are_not_flagged_as_simulated():
    assert not is_simulated_name("ICC Men's T20 World Cup", "India", "Australia")
    assert not is_simulated_name("Indian Premier League", "Chennai Super Kings", "Mumbai Indians")
    assert not is_simulated_name("Sri Lanka", "Lanka Lions", None, "")
    assert is_simulated_name(None, "Hobart Hurricanes SRL", "X")


def test_reject_if_simulated_works_when_filter_disabled():
    classifier = MatchClassifier(enabled=False)
    assert classifier.reject_if_simulated(_event("New Zealand SRL", "Australia"))
    assert not classifier.reject_if_simulated(_event("India", "Australia", "ICC"))
    assert classifier.stats["simulated_rejected"] == 1


# --------------------------------------------------------------------------- exporter
def _odds_row(match_id, source, competition="ICC Men's T20 World Cup", home="India", away="Australia"):
    return {
        "time": datetime(2026, 10, 1, 12, 0, tzinfo=UTC), "match_id": match_id,
        "team_home": home, "team_away": away, "competition": competition,
        "back_home": 1.9, "lay_home": 1.94, "back_away": 2.05, "lay_away": 2.1,
        "source": source, "is_live": True,
    }


def _export_handler(sql, params):
    if "to_regclass" in sql:
        return FakeResult([{"t": None}])  # odds_ticks_historical missing
    if "FROM match_results" in sql:
        return FakeResult([
            {"match_id": "m1", "team_home": "India", "team_away": "Australia", "winner": "India", "loser": "Australia"},
            {"match_id": "srl1", "team_home": "Hobart Hurricanes SRL", "team_away": "Perth Scorchers SRL",
             "winner": "Hobart Hurricanes SRL", "loser": "Perth Scorchers SRL"},
        ])
    if "FROM odds_ticks" in sql:
        # The fake returns every row for the match; the exporter must drop the synthetic one itself.
        mid = (params or {}).get("m")
        if mid == "m1":
            return FakeResult([_odds_row("m1", "lotusbook"), _odds_row("m1", "cricsheet_official")])
        return FakeResult([_odds_row(mid, "lotusbook", "Simulated Reality League")])
    return FakeResult([])  # match_context


async def test_exporter_runs_without_historical_table_and_drops_synthetic_and_srl(monkeypatch):
    session = FakeSession(_export_handler)
    monkeypatch.setattr("shared.db.get_session", fake_get_session(session))

    matches = await exporter._fetch_matches()

    assert [m["match_id"] for m in matches] == ["m1"]
    assert all(o["source"] == "lotusbook" for o in matches[0]["odds"])
    assert len(matches[0]["odds"]) == 1
    assert not any("FROM odds_ticks_historical" in sql for sql, _ in session.statements)


async def test_exporter_exits_zero_with_no_recorded_matches(monkeypatch, tmp_path, capsys):
    async def nothing():
        return []

    monkeypatch.setattr(exporter, "_fetch_matches", nothing)
    args = argparse.Namespace(out_dir=str(tmp_path), cadence="decision", min_ticks=50,
                              min_coverage=0.6, force=False)
    assert await exporter.main_async(args) == 0
    assert "No recorded matches" in capsys.readouterr().out


def test_select_real_rows_keeps_only_lotusbook_non_simulated():
    rows = [
        _odds_row("m1", "lotusbook"),
        _odds_row("m1", "cricsheet_official"),
        _odds_row("srl1", "lotusbook", competition="Simulated Reality League"),
    ]
    kept, dropped = exporter.select_real_rows(rows)
    assert len(kept) == 1 and dropped == 2


# --------------------------------------------------------------------------- recording health
def test_classify_groups_counts_synthetic_and_simulated_rows():
    groups = [
        {"source": "lotusbook", "competition": "ICC", "team_home": "India", "team_away": "Australia", "n": 10},
        {"source": "cricsheet_official", "competition": "", "team_home": "A", "team_away": "B", "n": 5},
        {"source": "lotusbook", "competition": "Simulated Reality League",
         "team_home": "Hobart Hurricanes SRL", "team_away": "X", "n": 3},
    ]
    counts = health.classify_groups(groups)
    assert counts["synthetic_rows"] == 5
    assert counts["simulated_rows"] == 3


async def test_recording_health_flags_synthetic_rows_with_exit_one(monkeypatch):
    def handler(sql, params):
        if "GROUP BY" in sql:
            return FakeResult([{"source": "cricsheet_official", "competition": "", "team_home": "A",
                                "team_away": "B", "n": 7}])
        return FakeResult([])

    report = await health.collect(FakeSession(handler), since=datetime(2026, 1, 1, tzinfo=UTC))
    feed = health.feed_status(None, None, datetime.now(UTC), max_feed_age_min=10, stall_minutes=10)
    assert report["contamination"]["synthetic_rows"] == 7
    assert health.exit_code(report, feed) == health.EXIT_CONTAMINATED


def test_summarise_days_pools_gaps_within_each_match_only():
    base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    rows = [
        {"time": base + timedelta(seconds=s), "match_id": "A", "source": "lotusbook"} for s in (0, 60, 120)
    ] + [
        {"time": base + timedelta(seconds=s), "match_id": "B", "source": "lotusbook"} for s in (0, 600)
    ]
    seen = [{"match_id": "A", "created_at": base}, {"match_id": "B", "created_at": base},
            {"match_id": "C", "created_at": base}]
    days = health.summarise_days(rows, seen, context_ids={"A"}, result_ids={"A"})
    assert len(days) == 1
    day = days[0]
    assert day["recorded"] == 2 and day["seen"] == 3 and day["missed"] == 1
    assert day["ticks"] == 5
    # Within-match gaps are [60, 60, 600]; a cross-match gap would have produced a different median.
    assert day["gap_median_s"] == 60
    assert day["gap_p95_s"] == 600
    assert day["with_context"] == 0.5 and day["with_result"] == 0.5


def test_feed_status_flags_stalled_and_missing_recorder():
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    fresh = {"updated_at": now.isoformat(), "last_live_event_at": now.isoformat(), "live_matches": 2}
    stalled = {"updated_at": now.isoformat(),
               "last_live_event_at": (now - timedelta(minutes=30)).isoformat(), "live_matches": 2}
    wd = {"updated_at": now.isoformat()}

    assert not health.feed_status(fresh, wd, now, max_feed_age_min=10, stall_minutes=10)["stale"]
    assert health.feed_status(stalled, wd, now, max_feed_age_min=10, stall_minutes=10)["stale"]
    assert health.feed_status(None, wd, now, max_feed_age_min=10, stall_minutes=10)["stale"]
    assert health.exit_code({"contamination": {"synthetic_rows": 0, "simulated_rows": 0}},
                            health.feed_status(stalled, wd, now, max_feed_age_min=10, stall_minutes=10)) \
        == health.EXIT_STALE


# --------------------------------------------------------------------------- watchdog
class FakeChild:
    _pids = 100

    def __init__(self):
        FakeChild._pids += 1
        self.pid = FakeChild._pids
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def watchdog_env(tmp_path):
    clock = FakeClock()
    children: list[FakeChild] = []
    feed: dict = {"value": None}

    def spawn():
        child = FakeChild()
        children.append(child)
        return child

    def make(stall_minutes=10.0, backoff=5.0):
        return Watchdog(
            spawn=spawn,
            read_feed=lambda: feed["value"],
            config=WatchdogConfig(stall_minutes=stall_minutes, backoff_base_seconds=backoff),
            heartbeat_path=tmp_path / "scraper_heartbeat.json",
            log_path=tmp_path / "scraper_watchdog.log",
            clock=clock,
            sleep=clock.advance,
        )

    return SimpleNamespace(clock=clock, children=children, feed=feed, make=make, tmp=tmp_path)


def test_watchdog_restarts_child_that_exits_after_backoff(watchdog_env):
    dog = watchdog_env.make(backoff=5.0)
    dog.step()
    assert len(watchdog_env.children) == 1

    watchdog_env.children[0].returncode = 1
    dog.step()  # notices the exit, schedules a restart
    assert dog.state.restarts_today == 1
    dog.step()  # still inside the backoff window
    assert len(watchdog_env.children) == 1

    watchdog_env.clock.advance(5)
    dog.step()
    assert len(watchdog_env.children) == 2
    heartbeat = json.loads((watchdog_env.tmp / "scraper_heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["restarts_today"] == 1
    assert heartbeat["child_alive"] is True


def test_watchdog_restarts_child_that_stalls_while_a_match_is_live(watchdog_env):
    dog = watchdog_env.make(stall_minutes=10.0)
    dog.step()  # child 1 starts
    start = watchdog_env.clock.now

    watchdog_env.clock.advance(11 * 60)
    watchdog_env.feed["value"] = {
        "live_matches": 2,
        "last_live_event_at": start.isoformat(),  # no live event since the child started
        "updated_at": start.isoformat(),
    }
    dog.step()
    assert watchdog_env.children[0].killed
    assert dog.state.last_stop_reason == "stalled"

    watchdog_env.clock.advance(5)
    dog.step()
    assert len(watchdog_env.children) == 2


def test_watchdog_does_not_restart_healthy_or_quiet_child(watchdog_env):
    dog = watchdog_env.make(stall_minutes=10.0)
    dog.step()

    watchdog_env.clock.advance(11 * 60)
    now = watchdog_env.clock.now
    # Live and recently active: healthy.
    watchdog_env.feed["value"] = {"live_matches": 1, "last_live_event_at": now.isoformat()}
    dog.step()
    # Old event but no live match: nothing is expected, so no restart.
    watchdog_env.feed["value"] = {"live_matches": 0,
                                  "last_live_event_at": (now - timedelta(hours=2)).isoformat()}
    dog.step()

    assert not watchdog_env.children[0].killed
    assert len(watchdog_env.children) == 1


def test_watchdog_run_stops_child_on_exit(watchdog_env):
    dog = watchdog_env.make()
    dog.run(max_iterations=3)
    assert len(watchdog_env.children) == 1
    assert watchdog_env.children[0].killed  # shutdown() stops the child when the loop ends


def test_another_watchdog_instance_is_detected(tmp_path):
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    path = tmp_path / "scraper_heartbeat.json"
    path.write_text(json.dumps({"watchdog_pid": 4242, "updated_at": now.isoformat()}), encoding="utf-8")
    assert another_instance_running(path, now, own_pid=1)
    assert not another_instance_running(path, now, own_pid=4242)
    assert not another_instance_running(tmp_path / "missing.json", now, own_pid=1)


def test_install_script_is_idempotent_and_has_uninstall_line():
    text = (ROOT / "scripts" / "install_scraper_task.ps1").read_text(encoding="utf-8")
    assert "-Force" in text
    assert "Unregister-ScheduledTask" in text
    assert "PHOENIX scraper" in text
    assert all(ord(ch) < 128 for ch in text), "keep the PowerShell script ASCII for Windows PowerShell 5.1"
