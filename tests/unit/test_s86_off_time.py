"""S86: true off time. Source precedence, set once, missing-column tolerance, the dataset's start fallback, the
``start_time_consistency`` monitor check, the backfill selection and the replay's look-ahead guard.

All database access goes through fakes; no DB, Redis or browser is touched. Fixtures are the stored LotusBook JSON.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

import scraper.manager as manager_mod
from backend.db_init import MIGRATION_STATEMENTS
from backend.models.match_status import MatchTrainingStatus
from scraper.actual_start import (
    BACKFILL_PRECEDENCE,
    LIVE_PRECEDENCE,
    SOURCE_JSON_IN_PLAY,
    SOURCE_LISTING_BACKFILL,
    SOURCE_LISTING_LIVE,
    SOURCE_SCORE_BACKFILL,
    SOURCE_SCORE_FIRST_BALL,
    is_first_ball,
    pick_actual_start,
)
from scraper.market_pages import MarketPageWorker
from scraper.parsers.lotusbook_json import in_play_of
from scraper.parsers.lotusbook_score import ScoreState
from scripts import backfill_actual_start as backfill
from scripts import data_quality_monitor as dqm
from tests.unit.test_s37_recording import (  # noqa: F401  (manager_env is a fixture)
    FakeSession,
    _new_manager,
    fake_get_session,
    live_event,
    manager_env,
)
from training import dataset as ds
from virtual_trading import replay_feed

UTC = timezone.utc
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lotusbook"
MISSING_COLUMN = 'column "actual_start_time" of relation "match_training_status" does not exist'


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


REAL_JSON = _load("sma_event_real_76321652_20261103.json")
SRL_JSON = _load("sma_event_srl_76108412_20261103.json")


class Rowcount:
    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount


def _writes(session: FakeSession) -> list[tuple[str, Any]]:
    return [(sql, params) for sql, params in session.statements if "SET actual_start_time" in sql]


# --------------------------------------------------------------------------- precedence
def test_live_precedence_is_json_then_listing_then_score():
    t = START
    assert LIVE_PRECEDENCE == (SOURCE_JSON_IN_PLAY, SOURCE_LISTING_LIVE, SOURCE_SCORE_FIRST_BALL)
    all_three = [(SOURCE_SCORE_FIRST_BALL, t), (SOURCE_LISTING_LIVE, t), (SOURCE_JSON_IN_PLAY, t)]
    assert pick_actual_start(all_three) == (SOURCE_JSON_IN_PLAY, t)
    assert pick_actual_start(all_three[:2]) == (SOURCE_LISTING_LIVE, t)
    assert pick_actual_start([(SOURCE_SCORE_FIRST_BALL, t + timedelta(minutes=3)),
                              (SOURCE_LISTING_LIVE, t)]) == (SOURCE_LISTING_LIVE, t)


def test_precedence_ignores_signals_without_a_time_and_returns_none_when_none_known():
    assert pick_actual_start([(SOURCE_JSON_IN_PLAY, None), (SOURCE_LISTING_LIVE, START)]) == (SOURCE_LISTING_LIVE, START)
    assert pick_actual_start([(SOURCE_JSON_IN_PLAY, None)]) is None
    assert pick_actual_start([]) is None


def test_backfill_prefers_the_listing_tick_over_the_score_row():
    assert BACKFILL_PRECEDENCE == (SOURCE_LISTING_BACKFILL, SOURCE_SCORE_BACKFILL)
    pair = [(SOURCE_SCORE_BACKFILL, START), (SOURCE_LISTING_BACKFILL, START + timedelta(minutes=1))]
    assert pick_actual_start(pair, BACKFILL_PRECEDENCE) == (SOURCE_LISTING_BACKFILL, START + timedelta(minutes=1))


def test_first_ball_needs_the_first_innings_and_a_ball_bowled():
    assert is_first_ball(1, 1) is True
    assert is_first_ball(1, 0) is False
    assert is_first_ball(2, 37) is False  # a later innings is not the start of the match
    assert is_first_ball(None, 5) is False


# --------------------------------------------------------------------------- JSON in-play flag
def test_in_play_flag_is_read_from_the_market_json_fixtures():
    assert in_play_of(REAL_JSON) is True  # every market inPlay true on the captured live match
    assert in_play_of(SRL_JSON) is True
    assert in_play_of({"result": [{"name": "x", "inPlay": False}]}) is False
    assert in_play_of({"result": [{"name": "x"}]}) is False  # a missing flag is not in play
    assert in_play_of(None) is False


def test_worker_hands_the_in_play_flag_over_once(monkeypatch):
    calls: list[tuple[str, datetime]] = []

    async def on_in_play(match_id, observed_at):
        calls.append((match_id, observed_at))

    async def store(match_id, records):
        return None

    worker = MarketPageWorker(match_id="lb_76321652", page=None, store=store, on_in_play=on_in_play)
    asyncio.run(worker.ingest_event_json(REAL_JSON))
    asyncio.run(worker.ingest_event_json(REAL_JSON))
    assert [m for m, _ in calls] == ["lb_76321652"]
    assert calls[0][1].tzinfo is not None


def test_worker_does_not_hand_over_before_the_off():
    pre_start = {**REAL_JSON, "result": [{**m, "inPlay": False} for m in REAL_JSON["result"]]}
    calls: list = []

    async def on_in_play(match_id, observed_at):
        calls.append(match_id)

    async def store(match_id, records):
        return None

    worker = MarketPageWorker(match_id="lb_76321652", page=None, store=store, on_in_play=on_in_play)
    asyncio.run(worker.ingest_event_json(pre_start))
    assert calls == []


# --------------------------------------------------------------------------- set once (scraper)
def test_the_update_only_writes_null_columns(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    wrote = asyncio.run(mgr._set_actual_start_once("lb_1", SOURCE_JSON_IN_PLAY, START))
    assert wrote is False  # the fake result reports no rowcount, so the fake reports no row written
    sql, params = _writes(manager_env.session)[0]
    assert "actual_start_time IS NULL" in sql
    assert "SET actual_start_time = :when, actual_start_source = :source" in sql
    assert params == {"when": START, "source": SOURCE_JSON_IN_PLAY, "mid": "lb_1"}


def test_a_match_is_attempted_once_per_process(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    asyncio.run(mgr._set_actual_start_once("lb_1", SOURCE_JSON_IN_PLAY, START))
    asyncio.run(mgr._set_actual_start_once("lb_1", SOURCE_LISTING_LIVE, START + timedelta(seconds=9)))
    sources = [params["source"] for _, params in _writes(manager_env.session)]
    assert sources == [SOURCE_JSON_IN_PLAY]


def test_a_later_listing_signal_does_not_overwrite_the_json_start(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    asyncio.run(mgr._set_actual_start_once("lb_m1", SOURCE_JSON_IN_PLAY, START))
    asyncio.run(mgr._on_events([live_event(match_id="lb_m1")]))  # live badge after the JSON flag
    sources = [params["source"] for _, params in _writes(manager_env.session)]
    assert SOURCE_LISTING_LIVE not in sources
    assert sources == [SOURCE_JSON_IN_PLAY]


def test_the_listing_live_badge_is_a_start_signal(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    asyncio.run(mgr._on_events([live_event(match_id="lb_m2")]))
    sources = [params["source"] for _, params in _writes(manager_env.session)]
    assert sources == [SOURCE_LISTING_LIVE]


def test_an_upcoming_match_does_not_set_the_start(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    upcoming = live_event(match_id="lb_m3").model_copy(update={"is_live": False})
    asyncio.run(mgr._on_events([upcoming]))
    assert _writes(manager_env.session) == []


def test_the_score_feed_first_ball_is_a_start_signal(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    event = live_event(match_id="lb_s1")
    no_ball = ScoreState(pipeline="livescore-v2", batting_team="India", runs=0, wickets=0, overs=0.0, balls=0,
                         innings=1)
    first = ScoreState(pipeline="livescore-v2", batting_team="India", runs=1, wickets=0, overs=0.1, balls=1,
                       innings=1)
    asyncio.run(mgr._on_market_score(event, no_ball, "livescore-v2", START))
    assert _writes(manager_env.session) == []
    asyncio.run(mgr._on_market_score(event, first, None, START + timedelta(seconds=30)))
    sources = [params["source"] for _, params in _writes(manager_env.session)]
    assert sources == [SOURCE_SCORE_FIRST_BALL]


# --------------------------------------------------------------------------- missing column (old database)
def test_a_database_without_the_columns_is_logged_once_and_not_retried(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)
    def missing_column(sql, params):
        raise Exception(MISSING_COLUMN)

    failing = FakeSession(handler=missing_column)
    monkeypatch.setattr(manager_mod, "get_session", fake_get_session(failing))

    assert asyncio.run(mgr._set_actual_start_once("lb_1", SOURCE_JSON_IN_PLAY, START)) is False
    assert mgr.__dict__.get("_actual_start_unavailable") is True
    assert asyncio.run(mgr._set_actual_start_once("lb_2", SOURCE_LISTING_LIVE, START)) is False
    assert len(failing.statements) == 1  # the second match did not even try


def test_other_database_errors_are_not_mistaken_for_a_missing_column(monkeypatch, manager_env):
    mgr = _new_manager(monkeypatch, record_all=None)

    def boom(sql, params):
        raise Exception("connection reset")

    monkeypatch.setattr(manager_mod, "get_session", fake_get_session(FakeSession(handler=boom)))
    assert asyncio.run(mgr._set_actual_start_once("lb_1", SOURCE_LISTING_LIVE, START)) is False
    assert mgr.__dict__.get("_actual_start_unavailable", False) is False
    assert "lb_1" not in mgr.__dict__.get("_actual_start_done", set())  # retried on the next signal


def test_orm_plain_select_does_not_read_the_new_columns():
    sql = str(select(MatchTrainingStatus).compile())
    assert "actual_start_time" not in sql and "actual_start_source" not in sql
    assert "match_training_status" in sql


def test_migration_adds_both_nullable_columns_and_the_model_has_them():
    statement = next(s for s in MIGRATION_STATEMENTS if "actual_start_time" in s)
    assert "ADD COLUMN IF NOT EXISTS actual_start_time TIMESTAMPTZ" in statement
    assert "ADD COLUMN IF NOT EXISTS actual_start_source TEXT" in statement
    columns = MatchTrainingStatus.__table__.c
    assert columns.actual_start_time.nullable is True
    assert columns.actual_start_source.nullable is True


# --------------------------------------------------------------------------- dataset fallback
MATCH = {
    "match_id": "lb_1",
    "team_home": "Oman",
    "team_away": "Netherlands",
    "competition": "ICC Cricket World Cup League Two",
    "match_start_time": START,
    "is_simulated": False,
    "training_status": "approved",
}


def _tick(offset_s: float, **prices: float) -> dict:
    row = {"time": START + timedelta(seconds=offset_s), "source": "listing"}
    for field in ("back_home", "lay_home", "back_away", "lay_away"):
        row[field] = prices.get(field)
    return row


def test_effective_start_prefers_the_actual_start_and_falls_back_to_the_scheduled_one():
    actual = START + timedelta(minutes=20)
    assert ds.effective_start({**MATCH, "actual_start_time": actual}) == (actual, "actual_start_time")
    assert ds.effective_start({**MATCH, "actual_start_time": None}) == (START, "match_start_time")
    # an old registry row has no actual_start_time key at all
    assert ds.effective_start(MATCH) == (START, "match_start_time")
    assert ds.effective_start({**MATCH, "match_start_time": None}) == (None, "match_start_time")


def test_snapshots_and_the_in_play_split_use_the_actual_start_when_known():
    actual = START + timedelta(minutes=20)
    rows = [_tick(-700, back_home=2.0), _tick(600, back_home=3.0), _tick(1500, back_home=4.0)]
    snaps = ds.build_snapshots(rows, {**MATCH, "actual_start_time": actual}, snapshot_offsets=[("T-0", 0)],
                               inplay_every_s=0)
    t0 = snaps[0]
    assert t0["t"] == (actual - timedelta(microseconds=1)).isoformat()
    assert t0["in_play"] is False
    # a snapshot at the scheduled start is still pre-match when the actual start is later
    at_scheduled = ds.build_snapshots(rows, {**MATCH, "actual_start_time": actual},
                                      snapshot_offsets=[("AtScheduled", -20 * 60)], inplay_every_s=120)
    assert at_scheduled[0]["t"] == START.isoformat()
    assert at_scheduled[0]["in_play"] is False
    in_play = [s for s in ds.build_snapshots(rows, {**MATCH, "actual_start_time": actual},
                                             snapshot_offsets=[], inplay_every_s=120) if s["in_play"]]
    assert in_play and all(datetime.fromisoformat(s["t"]) >= actual for s in in_play)


def test_snapshots_fall_back_to_match_start_time_when_the_actual_start_is_unknown():
    rows = [_tick(-60, back_home=2.0), _tick(120, back_home=2.5)]
    fallback = ds.build_snapshots(rows, {**MATCH, "actual_start_time": None}, snapshot_offsets=[("T-0", 0)],
                                  inplay_every_s=0)
    legacy = ds.build_snapshots(rows, MATCH, snapshot_offsets=[("T-0", 0)], inplay_every_s=0)
    assert fallback == legacy
    assert fallback[0]["t"] == (START - timedelta(microseconds=1)).isoformat()


def test_match_record_reports_which_start_was_used_and_build_dataset_counts_it():
    actual = START + timedelta(minutes=5)
    record = ds.match_record({**MATCH, "actual_start_time": actual})
    assert record["start_basis"] == "actual_start_time"
    assert record["actual_start_time"] == actual.isoformat()
    assert record["match_start_time"] == START.isoformat()
    assert ds.match_record(MATCH)["start_basis"] == "match_start_time"

    dataset = ds.build_dataset(
        [{**MATCH, "match_id": "lb_2", "match_start_time": None, "actual_start_time": actual}],
        {"lb_2": [_tick(-60, back_home=2.0)]},
        {},
    )
    assert "matches_without_start" not in dataset["stats"]
    assert dataset["snapshots"]  # the actual start alone is enough to build snapshots


# --------------------------------------------------------------------------- monitor check
def _start_rows(*, columns: bool = True, start_times: list[dict] | None = None) -> dict[str, list[dict]]:
    rows = {
        "actual_start_columns": [{"present": columns}],
        "start_times": start_times or [],
    }
    return rows


def _check(rows: dict[str, list[dict]]) -> dict[str, Any]:
    async def fetch(name, params):
        return [dict(r) for r in rows[name]]

    return asyncio.run(dqm.check_start_time_consistency(fetch, {"now": NOW, "since": NOW - timedelta(hours=24)}))


def _row(match_id="lb_1", *, start=START, actual=None, first_live=START, competition="ICC T20", home="India",
         away="Australia", is_simulated=None):
    row = {"match_id": match_id, "team_home": home, "team_away": away, "competition": competition,
           "match_start_time": start, "actual_start_time": actual, "actual_start_source": None,
           "first_live_tick": first_live}
    if is_simulated is not None:
        row["is_simulated"] = is_simulated
    return row


def test_consistency_check_is_registered_last_and_is_a_warning():
    assert dqm.CHECKS[-2] is dqm.check_start_time_consistency  # S96 appended duplicate_fixtures after it
    assert dqm.START_GAP_WARN_S == 15 * 60
    assert dqm.ACTUAL_LATE_WARN_S == 60 * 60


def test_consistency_is_ok_when_listing_and_json_starts_agree():
    c = _check(_start_rows(start_times=[_row(actual=START + timedelta(minutes=2))]))
    assert c["status"] == "ok"
    assert c["value"] == 0


def test_consistency_warns_when_the_listing_goes_live_far_from_the_json_start():
    late_listing = _row(first_live=START + timedelta(hours=5, minutes=30))
    c = _check(_start_rows(start_times=[late_listing]))
    assert c["status"] == "warn"
    assert c["value"] == 1
    assert "listing and JSON starts over 15 min apart" in c["detail"]


def test_consistency_is_ok_for_a_short_delay_once_the_actual_start_is_recorded():
    """Toronto v Montreal, 2026-11-02: rain, live 28 min late; the actual off explains the gap."""
    delayed = _row(actual=START + timedelta(minutes=28), first_live=START + timedelta(minutes=28))
    assert _check(_start_rows(start_times=[delayed]))["status"] == "ok"


def test_consistency_warns_when_the_actual_start_is_over_an_hour_after_the_scheduled_one():
    late = _row(actual=START + timedelta(minutes=75), first_live=START + timedelta(minutes=75))
    c = _check(_start_rows(start_times=[late]))
    assert c["status"] == "warn"
    assert "with actual_start_time over 60 min after match_start_time" in c["detail"]


def test_consistency_counts_matches_without_an_actual_start_but_does_not_warn_for_them():
    c = _check(_start_rows(start_times=[_row(actual=None)]))
    assert c["status"] == "ok"
    assert "1 real match(es) with no actual_start_time yet" in c["detail"]


def test_consistency_leaves_srl_matches_out():
    srl = _row(first_live=START + timedelta(hours=5), competition="Big Bash League SRL", home="Perth SRL",
               away="Hobart SRL")
    assert _check(_start_rows(start_times=[srl]))["status"] == "ok"


def test_consistency_is_not_scored_without_the_columns():
    c = _check(_start_rows(columns=False, start_times=[_row(first_live=START + timedelta(hours=5))]))
    assert c["status"] == "ok"
    assert "not present" in c["detail"]


def test_monitor_queries_are_selects_over_the_registry_and_ticks():
    for name in ("actual_start_columns", "start_times"):
        sql = dqm.SQL[name].strip().upper()
        assert sql.startswith("SELECT")
        assert "MATCH_TRAINING_STATUS" in sql or "INFORMATION_SCHEMA" in sql


# --------------------------------------------------------------------------- backfill selection
def _reg(match_id="m1", *, start=START, competition="ICC T20", home="India", away="Australia", is_simulated=False):
    return {"match_id": match_id, "team_home": home, "team_away": away, "competition": competition,
            "is_simulated": is_simulated, "match_start_time": start, "actual_start_time": None,
            "actual_start_source": None}


def test_backfill_takes_the_first_live_listing_tick_and_falls_back_to_the_score_row():
    listing = {"m1": START + timedelta(minutes=3)}
    score = {"m1": START + timedelta(minutes=1), "m2": START + timedelta(minutes=8)}
    plan = backfill.plan_backfill([_reg("m1"), _reg("m2")], listing, score)
    by_id = {p["match_id"]: p for p in plan}
    assert by_id["m1"]["source"] == SOURCE_LISTING_BACKFILL
    assert by_id["m1"]["when"] == START + timedelta(minutes=3)
    assert by_id["m2"]["source"] == SOURCE_SCORE_BACKFILL
    assert by_id["m2"]["when"] == START + timedelta(minutes=8)


def test_backfill_leaves_a_match_with_no_recorded_signal_unset():
    plan = backfill.plan_backfill([_reg("m3")], {}, {})
    assert plan[0]["when"] is None and plan[0]["source"] is None and plan[0]["delta_min"] is None


def test_backfill_reports_srl_separately_and_counts_matches_more_than_15_minutes_off():
    plan = backfill.plan_backfill(
        [
            _reg("real_off", start=START),
            _reg("real_ok", start=START),
            _reg("srl_off", start=START, competition="Big Bash League SRL", home="Perth SRL", away="Hobart SRL",
                 is_simulated=True),
        ],
        {"real_off": START + timedelta(hours=5, minutes=30), "real_ok": START + timedelta(minutes=10),
         "srl_off": START + timedelta(minutes=40)},
        {},
    )
    summary = backfill.summarise_plan(plan)
    assert summary["real"] == 2 and summary["simulated"] == 1
    assert summary["with_signal"] == 3
    assert summary["differs_over_15_min"] == 2
    assert summary["differs_over_15_min_real"] == 1
    assert summary["differs_over_15_min_simulated"] == 1
    assert summary["by_source"] == {SOURCE_LISTING_BACKFILL: 3}


def test_backfill_table_lists_every_candidate_once():
    plan = backfill.plan_backfill([_reg("m1"), _reg("m2")], {"m1": START}, {})
    lines = backfill.format_table(plan).splitlines()
    assert len(lines) == 2 + 2  # header, rule, two matches
    assert any("m1" in line and "listing_backfill" in line for line in lines)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None


def _backfill_handler(sql: str, params):
    if "information_schema" in sql:
        return SimpleNamespace(scalar=lambda: True, fetchall=lambda: [])
    if "UPDATE match_training_status" in sql:
        return Rowcount(1)
    if "SET TRANSACTION" in sql:
        return _Rows([])
    if "FROM match_training_status s" in sql:
        rows = [{"match_id": "m1", "team_home": "India", "team_away": "Australia", "competition": "ICC T20",
                 "is_simulated": False, "match_start_time": START, "actual_start_time": None,
                 "actual_start_source": None},
                {"match_id": "m2", "team_home": "Perth SRL", "team_away": "Hobart SRL",
                 "competition": "Big Bash League SRL", "is_simulated": True, "match_start_time": START,
                 "actual_start_time": None, "actual_start_source": None}]
        return _Rows([SimpleNamespace(_mapping=r) for r in rows])
    if "first_live_tick" in sql:
        return _Rows([SimpleNamespace(_mapping={"match_id": "m1", "first_live_tick": START + timedelta(minutes=2)})])
    if "first_ball" in sql:
        return _Rows([SimpleNamespace(_mapping={"match_id": "m2", "first_ball": START + timedelta(minutes=20)})])
    return _Rows([])


def _factory(session: FakeSession):
    @asynccontextmanager
    async def cm():
        yield session

    return cm


def test_backfill_default_is_a_dry_run_that_writes_nothing(capsys):
    session = FakeSession(handler=_backfill_handler)
    assert asyncio.run(backfill.run(apply=False, session_factory=_factory(session))) == 0
    assert any("SET TRANSACTION READ ONLY" in sql for sql, _ in session.statements)
    assert not any("UPDATE" in sql for sql, _ in session.statements)
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out
    assert "differs_over_15_min: 1" in out


def test_backfill_apply_writes_only_the_two_columns_where_null(capsys):
    session = FakeSession(handler=_backfill_handler)
    assert asyncio.run(backfill.run(apply=True, session_factory=_factory(session))) == 0
    updates = [(sql, params) for sql, params in session.statements if "UPDATE" in sql]
    assert [params["mid"] for _, params in updates] == ["m1", "m2"]
    for sql, _ in updates:
        assert "actual_start_time IS NULL" in sql
        assert "SET actual_start_time = :when, actual_start_source = :source" in sql
    assert "applied: 2 row(s)" in capsys.readouterr().out


def test_backfill_refuses_when_the_columns_are_missing(capsys):
    def no_columns(sql, params):
        if "information_schema" in sql:
            return SimpleNamespace(scalar=lambda: False, fetchall=lambda: [])
        return _backfill_handler(sql, params)

    session = FakeSession(handler=no_columns)
    assert asyncio.run(backfill.run(apply=True, session_factory=_factory(session))) == 1
    assert not any("UPDATE" in sql for sql, _ in session.statements)
    assert "migration" in capsys.readouterr().out


def test_backfill_main_defaults_to_dry_run(monkeypatch):
    seen = {}

    async def fake_run(apply, session_factory=None):
        seen["apply"] = apply
        return 0

    monkeypatch.setattr(backfill, "run", fake_run)
    assert backfill.main([]) == 0
    assert seen["apply"] is False
    backfill.main(["--apply"])
    assert seen["apply"] is True


# --------------------------------------------------------------------------- replay feed
def _replay_registry(actual):
    return {"match_id": "m1", "team_home": "m1 home", "team_away": "m1 away", "competition": "Indian Premier League",
            "match_start_time": START, "is_simulated": False, "toss_winner": None, "toss_decision": None,
            "actual_start_time": actual}


def test_replay_exposes_the_actual_start_only_once_it_was_observed():
    actual = START + timedelta(minutes=12)
    feed = replay_feed.ReplayFeed(prices=[], registry=[_replay_registry(actual)], context=[], results=[],
                                  end=START + timedelta(hours=2))
    feed.seek(START + timedelta(minutes=5))
    assert asyncio.run(feed.registry(["m1"]))[0]["actual_start_time"] is None
    assert asyncio.run(feed.registry(["m1"]))[0]["match_start_time"] == START
    feed.seek(START + timedelta(minutes=12))
    assert asyncio.run(feed.registry(["m1"]))[0]["actual_start_time"] == actual


def test_replay_load_selects_null_when_the_column_is_missing():
    seen: list[str] = []

    class Result:
        def __init__(self, rows=(), scalar=None):
            self._rows = list(rows)
            self._scalar = scalar

        def mappings(self):
            return self

        def all(self):
            return self._rows

        def scalar(self):
            return self._scalar

    class Session:
        async def execute(self, stmt, params=None):
            sql = str(stmt)
            seen.append(sql)
            if "information_schema" in sql:
                return Result(scalar=False)
            if "FROM market_ticks" in sql:  # S87: the size query also names match_training_status; no sizes here
                return Result()
            if "FROM match_training_status" in sql:
                return Result(rows=[_replay_registry(None)])
            if "FROM v_match_odds" in sql:
                return Result(rows=[{"match_id": "m1", "time": START, "back_home": 2.0, "lay_home": 2.1,
                                     "back_away": 2.0, "lay_away": 2.1, "source": "listing"}])
            return Result()

    feed = asyncio.run(replay_feed.ReplayFeed.load(Session(), end=START + timedelta(hours=1)))
    registry_sql = next(s for s in seen if "FROM match_training_status WHERE" in s)
    assert "NULL::timestamptz AS actual_start_time" in registry_sql
    assert feed._registry["m1"].get("actual_start_time") is None


def test_the_market_json_in_play_flag_is_not_a_start_signal():
    """2026-11-03: "inPlay": true means in-play betting is allowed; it is true as soon as a pre-match page opens."""
    import inspect

    from scraper import manager

    source = inspect.getsource(manager.ScraperManager)
    assert "on_in_play=" not in source
    assert "SOURCE_JSON_IN_PLAY" not in source
