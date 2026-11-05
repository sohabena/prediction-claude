"""S57: quarantine rules, the recording guard, the misattribution monitor check and the CLI dry run.

Fakes only: no database, Redis or browser is touched. The CLI is exercised through a fake session that records
every statement; the recorder through the S37 manager fixtures.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import scraper.manager as manager_mod
from backend.models.odds import OddsTick
from backend.models.odds_quarantine import OddsTickQuarantine
from scraper.manager import ScraperManager
from scripts import data_quality_monitor as dqm
from scripts import quarantine_ticks as qt
from tests.unit.test_s37_recording import FakeDetector, live_event, manager_env  # noqa: F401  (fixture)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 15, 50, tzinfo=UTC)
NORMAL = {"back_home": 2.5, "lay_home": 2.6, "back_away": 1.6, "lay_away": 1.62}
DECISIVE = {"back_home": 1.01, "lay_home": 1.01, "back_away": 150.0, "lay_away": 180.0}
RESET = {"back_home": 2.04, "lay_home": 2.14, "back_away": 1.88, "lay_away": 1.96}
DECISIVE_2 = {"back_home": 1.03, "lay_home": 1.04, "back_away": 150.0, "lay_away": 180.0}  # differs, so stored


def tick(match_id: str, when: datetime, prices: dict[str, Any], seq: int = 1,
         home: str = "Afghanistan", away: str = "Zimbabwe") -> dict[str, Any]:
    return {"match_id": match_id, "time": when, "tick_seq": seq, "team_home": home, "team_away": away,
            "competition": "T20I", **prices}


def afghanistan_series(match_id: str = "lb_36255271") -> list[dict[str, Any]]:
    """Shaped like the live series: normal play, decisive 16:00-16:36, a 14 minute gap, then a fresh market."""
    rows = []
    seq = 0

    def add(when, prices):
        nonlocal seq
        seq += 1
        rows.append(tick(match_id, when, prices, seq=seq))

    add(T0, NORMAL)
    for minute in range(0, 37, 2):  # 16:00 .. 16:36
        add(datetime(2026, 11, 1, 16, 0, tzinfo=UTC) + timedelta(minutes=minute), DECISIVE)
    for minute in range(50, 60):  # 16:50 .. 16:59 after the gap
        add(datetime(2026, 11, 1, 16, minute, tzinfo=UTC), RESET)
    return rows


def flagged_keys(ticks: list[dict[str, Any]]) -> set[tuple]:
    return set(qt.classify(ticks))


def key(t: dict[str, Any]) -> tuple:
    return (t["match_id"], t["time"], t["tick_seq"])


# --------------------------------------------------------------------------- decisive predicate
@pytest.mark.parametrize(
    "prices, expected",
    [
        ({"back_home": 1.05, "lay_home": 1.2, "back_away": 15.0, "lay_away": 16.0}, True),  # back at 1.05, other >= 15
        ({"back_home": 1.05, "lay_home": 1.2, "back_away": 14.9, "lay_away": 16.0}, False),  # other side too short
        ({"back_home": 1.5, "lay_home": 1.06, "back_away": None, "lay_away": None}, True),  # lay 1.06, other unquoted
        ({"back_home": 1.06, "lay_home": 1.2, "back_away": 150.0, "lay_away": 180.0}, False),  # back above 1.05
        ({"back_home": 1.9, "lay_home": 1.94, "back_away": 2.05, "lay_away": 2.1}, False),
        ({"back_home": None, "lay_home": None, "back_away": 150.0, "lay_away": None}, False),
        ({"back_home": None, "lay_home": 1.01, "back_away": 150.0, "lay_away": None}, True),
        ({"back_home": 150.0, "lay_home": None, "back_away": 1.02, "lay_away": None}, True),  # away side decisive
    ],
)
def test_is_decisive(prices, expected):
    assert qt.is_decisive(prices) is expected


# --------------------------------------------------------------------------- post_settlement
def test_afghanistan_shape_flags_only_the_reset_rows():
    series = afghanistan_series()
    flagged = qt.post_settlement_flags(series)
    reset_rows = [t for t in series if t["back_home"] == RESET["back_home"]]
    assert len(reset_rows) == 10
    assert [key(t) for t in flagged] == [key(t) for t in reset_rows]


def test_decisive_rows_and_the_rows_before_the_gap_are_kept():
    series = afghanistan_series()
    kept = [t for t in series if key(t) not in flagged_keys(series)]
    assert all(t["back_home"] != RESET["back_home"] for t in kept)
    assert sum(1 for t in kept if t["back_home"] == DECISIVE["back_home"]) == 19


def test_every_tick_after_the_reset_starts_is_flagged_even_a_decisive_one():
    series = afghanistan_series()
    later = series[-1]["time"] + timedelta(minutes=1)
    series.append(tick("lb_36255271", later, DECISIVE, seq=999))
    assert key(series[-1]) in flagged_keys(series)


def test_a_gap_of_four_minutes_is_not_a_reset():
    series = afghanistan_series()
    shifted = []
    for t in series:
        if t["back_home"] == RESET["back_home"]:
            t = {**t, "time": t["time"] - timedelta(minutes=10)}  # first reset row now 4 min after 16:36
        shifted.append(t)
    assert flagged_keys(shifted) == set()


def test_a_gap_of_exactly_five_minutes_is_a_reset():
    last_decisive = datetime(2026, 11, 1, 16, 36, tzinfo=UTC)
    series = [tick("m", last_decisive, DECISIVE, seq=1),
              tick("m", last_decisive + timedelta(seconds=300), RESET, seq=2)]
    assert [t["tick_seq"] for t in qt.post_settlement_flags(series)] == [2]


def test_normal_match_with_a_late_decisive_tick_and_no_gap_is_not_flagged():
    series = [tick("m", T0 + timedelta(minutes=i), NORMAL, seq=i + 1) for i in range(5)]
    series += [tick("m", T0 + timedelta(minutes=5 + i), DECISIVE, seq=10 + i) for i in range(5)]
    series += [tick("m", T0 + timedelta(minutes=10 + i), DECISIVE, seq=20 + i) for i in range(3)]
    assert flagged_keys(series) == set()


def test_matches_are_judged_separately():
    a = afghanistan_series("lb_a")
    other_book = {"back_home": 3.0, "lay_home": 3.1, "back_away": 1.4, "lay_away": 1.45}
    normal = [tick("lb_b", T0 + timedelta(minutes=i), other_book, seq=i + 1, home="Oman", away="Netherlands")
              for i in range(3)]
    flagged = flagged_keys(a + normal)
    assert flagged and all(k[0] == "lb_a" for k in flagged)


# --------------------------------------------------------------------------- cross_match_copy
def _copy_pair(when: datetime) -> list[dict[str, Any]]:
    book = {"back_home": 2.38, "lay_home": 2.82, "back_away": 1.55, "lay_away": 1.72}
    return [tick("lb_36262294", when, book, seq=1, home="Melbourne Stars W", away="Perth Scorchers W"),
            tick("lb_75878940", when, book, seq=1, home="Oman", away="Netherlands")]


def test_identical_book_at_the_same_time_on_two_matches_flags_both_rows():
    pair = _copy_pair(T0)
    assert flagged_keys(pair) == {key(pair[0]), key(pair[1])}
    assert set(qt.classify(pair).values()) == {qt.RULE_CROSS_MATCH}


def test_identical_book_at_different_times_is_not_a_copy():
    a, b = _copy_pair(T0)
    b = {**b, "time": T0 + timedelta(seconds=1)}
    assert flagged_keys([a, b]) == set()


def test_book_with_a_missing_price_is_never_a_copy():
    a, b = _copy_pair(T0)
    a = {**a, "lay_away": None}
    b = {**b, "lay_away": None}
    assert flagged_keys([a, b]) == set()


def test_same_book_twice_on_one_match_is_not_a_cross_match_copy():
    a, b = _copy_pair(T0)
    b = {**b, "match_id": a["match_id"], "tick_seq": 2}
    assert flagged_keys([a, b]) == set()


def test_post_settlement_wins_when_both_rules_flag_a_row():
    series = afghanistan_series("lb_36255271")
    reset = series[-1]
    twin = {**reset, "match_id": "lb_copy", "tick_seq": 1, "team_home": "Oman", "team_away": "Netherlands"}
    reasons = qt.classify(series + [twin])
    assert reasons[key(reset)] == qt.RULE_POST_SETTLEMENT
    assert reasons[key(twin)] == qt.RULE_CROSS_MATCH


def test_nothing_flagged_for_a_normal_match():
    series = [tick("m", T0 + timedelta(minutes=i), NORMAL, seq=i + 1) for i in range(20)]
    assert qt.classify(series) == {}


def test_batch_copy_match_ids_names_both_sides_only():
    def ev(mid, back_home, lay_home=2.82):
        return SimpleNamespace(match_id=mid, back_home=back_home, lay_home=lay_home,
                               back_away=1.55, lay_away=1.72)

    events = [ev("a", 2.38), ev("b", 2.38), ev("c", 5.6), ev("d", None)]
    assert qt.batch_copy_match_ids(events) == {"a", "b"}
    assert qt.batch_copy_match_ids([ev("x", 2.38)]) == set()


# --------------------------------------------------------------------------- recording guard
def _manager(monkeypatch) -> ScraperManager:
    monkeypatch.delenv("SCRAPER_RECORD_ALL", raising=False)
    mgr = ScraperManager()
    mgr._result_detector = FakeDetector()
    mgr._last_approved_refresh = datetime.now(UTC)
    return mgr


def _event(mid: str, when: datetime, prices: dict[str, float | None], home="India", away="Australia"):
    base = live_event(match_id=mid, team_home=home, team_away=away)
    return base.model_copy(update={"timestamp": when, **prices})


def _stored(session) -> list[OddsTick]:
    return [o for o in session.added if isinstance(o, OddsTick)]


async def test_guard_refuses_post_settlement_reset_and_everything_after(monkeypatch, manager_env):
    mgr = _manager(monkeypatch)
    t = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)
    await mgr._on_events([_event("m1", t, DECISIVE)])
    await mgr._on_events([_event("m1", t + timedelta(minutes=2), DECISIVE_2)])
    assert len(_stored(manager_env.session)) == 2

    reset_at = t + timedelta(minutes=16)
    await mgr._on_events([_event("m1", reset_at, RESET)])
    await mgr._on_events([_event("m1", reset_at + timedelta(seconds=10), RESET)])
    await mgr._on_events([_event("m1", reset_at + timedelta(minutes=1), DECISIVE)])

    assert len(_stored(manager_env.session)) == 2
    assert mgr._quality_stats["post_settlement_tick_rejected"] == 3
    assert "m1" in mgr._settled_rejecting


async def test_guard_keeps_a_non_decisive_tick_with_no_gap(monkeypatch, manager_env):
    mgr = _manager(monkeypatch)
    t = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)
    await mgr._on_events([_event("m1", t, DECISIVE)])
    await mgr._on_events([_event("m1", t + timedelta(minutes=2), NORMAL)])
    assert len(_stored(manager_env.session)) == 2
    assert mgr._quality_stats["post_settlement_tick_rejected"] == 0


async def test_guard_does_not_refuse_a_match_that_was_never_decided(monkeypatch, manager_env):
    mgr = _manager(monkeypatch)
    t = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)
    await mgr._on_events([_event("m1", t, RESET)])
    await mgr._on_events([_event("m1", t + timedelta(minutes=30), {**RESET, "lay_home": 2.15})])
    assert len(_stored(manager_env.session)) == 2
    assert mgr._quality_stats["post_settlement_tick_rejected"] == 0


async def test_guard_normalises_naive_timestamps(monkeypatch, manager_env):
    mgr = _manager(monkeypatch)
    t = datetime(2026, 11, 1, 16, 0)  # naive
    await mgr._on_events([_event("m1", t.replace(tzinfo=UTC), DECISIVE)])
    refused = mgr._settlement_guard_refuses(
        SimpleNamespace(match_id="m1", timestamp=datetime(2026, 11, 1, 16, 20), back_home=2.0,
                        lay_home=2.1, back_away=1.9, lay_away=2.0))
    assert refused is True


async def test_copy_pair_in_one_batch_is_neither_stored_nor_published(monkeypatch, manager_env):
    mgr = _manager(monkeypatch)
    mgr._scrape_approved_ids.update({"copy_a", "copy_b"})  # approved: would publish if it got through
    now = datetime.now(UTC)
    book = {"back_home": 2.38, "lay_home": 2.82, "back_away": 1.55, "lay_away": 1.72}
    await mgr._on_events([
        _event("copy_a", now, book, home="India", away="Australia"),
        _event("copy_b", now, book, home="England", away="New Zealand"),
        _event("m3", now, {"back_home": 1.9, "lay_home": 1.94, "back_away": 2.05, "lay_away": 2.1},
               home="Pakistan", away="Sri Lanka"),
    ])
    stored_ids = {o.match_id for o in _stored(manager_env.session)}
    assert stored_ids == {"m3"}
    assert mgr._quality_stats["cross_match_copy_rejected"] == 2
    assert not any("copy_" in str(payload) for _, payload in manager_env.redis.published)


# --------------------------------------------------------------------------- monitor check
def _monitor_rows(series):
    return {"misattribution_ticks": series}


def _run_check(rows, since):
    async def fetch(name, params):
        return [dict(r) for r in rows[name]]

    return asyncio.run(dqm.check_misattribution(fetch, {"since": since, "now": since}))


def test_monitor_misattribution_ok_on_a_clean_window():
    clean = [tick("m", T0 + timedelta(minutes=i), NORMAL, seq=i + 1) for i in range(5)]
    c = _run_check(_monitor_rows(clean), since=T0 - timedelta(hours=1))
    assert c["name"] == "misattribution"
    assert c["status"] == "ok" and c["value"] == 0


def test_monitor_misattribution_fails_and_names_the_match():
    series = afghanistan_series()
    c = _run_check(_monitor_rows(series + _copy_pair(T0)), since=T0 - timedelta(hours=1))
    assert c["status"] == "fail"
    assert c["value"] == 10 + 2
    assert "post_settlement 10" in c["detail"] and "cross_match_copy 2" in c["detail"]
    assert "Afghanistan v Zimbabwe" in c["detail"]


def test_monitor_only_counts_flagged_rows_inside_the_window():
    series = afghanistan_series()
    since = datetime(2026, 11, 1, 16, 55, tzinfo=UTC)
    c = _run_check(_monitor_rows(series), since=since)
    assert c["value"] == 5  # 16:55..16:59 only; earlier flagged rows are outside the window
    assert c["status"] == "fail"


def test_monitor_check_is_part_of_the_full_report():
    from tests.unit.test_s55_data_quality import NOW, healthy_rows, run

    # Move the series inside the 24 h window ending at NOW (the monitor only counts rows in the window).
    shift = (NOW - timedelta(hours=3)) - T0
    series = [{**t, "time": t["time"] + shift} for t in afghanistan_series()]
    rows = healthy_rows()
    rows["misattribution_ticks"] = series
    report = run(rows)
    names = {c["name"]: c for c in report["checks"]}
    assert names["misattribution"]["status"] == "fail"
    assert report["overall"] == "fail"


# --------------------------------------------------------------------------- CLI
class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class RecordingSession:
    """Answers the load query with the given rows and records every other statement."""

    def __init__(self, rows):
        self.rows = rows
        self.statements: list[tuple[str, Any]] = []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append((sql, params))
        if sql.startswith("SELECT time, match_id, tick_seq"):
            return _Result([_Row(dict(r)) for r in self.rows])
        return _Result([])


def _patch_db(monkeypatch, session):
    import shared.db as shared_db

    class Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    class Engine:
        async def dispose(self):
            return None

    monkeypatch.setattr(shared_db, "get_session", lambda: Ctx())
    monkeypatch.setattr(shared_db, "get_engine", lambda: Engine())


def _cli_rows():  # noqa: D401
    series = afghanistan_series()
    return series + _copy_pair(T0)


def test_dry_run_issues_only_selects(monkeypatch, capsys):
    session = RecordingSession(_cli_rows())
    _patch_db(monkeypatch, session)
    summary = asyncio.run(qt.run(apply=False))
    assert summary["per_rule"] == {qt.RULE_POST_SETTLEMENT: 10, qt.RULE_CROSS_MATCH: 2}
    sqls = [sql.upper() for sql, _ in session.statements]
    assert sqls[0] == "SET TRANSACTION READ ONLY"
    assert not any(s.startswith(("INSERT", "DELETE", "UPDATE", "ALTER", "CREATE")) for s in sqls)
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Afghanistan v Zimbabwe" in out


def test_apply_copies_then_deletes_per_rule(monkeypatch):
    session = RecordingSession(_cli_rows())
    _patch_db(monkeypatch, session)
    asyncio.run(qt.run(apply=True))
    kinds = [sql.split(" ")[0].upper() for sql, _ in session.statements]
    assert kinds == ["SELECT", "SELECT", "INSERT", "DELETE", "INSERT", "DELETE"]  # ticks, trusted results, then per rule
    inserts = [p for sql, p in session.statements if sql.startswith("INSERT")]
    assert [p["reason"] for p in inserts] == [qt.RULE_POST_SETTLEMENT, qt.RULE_CROSS_MATCH]
    assert [len(p["match_ids"]) for p in inserts] == [10, 2]
    assert len(inserts[0]["times"]) == len(inserts[0]["seqs"]) == 10
    for sql, _ in session.statements:
        assert "ON CONFLICT" not in sql.upper()


def test_quarantine_model_mirrors_odds_ticks_plus_reason_and_time():
    odds_cols = set(OddsTick.__table__.columns.keys())
    quarantine_cols = set(OddsTickQuarantine.__table__.columns.keys())
    assert quarantine_cols == odds_cols | {"reason", "quarantined_at"}
    assert set(qt.COPY_COLUMNS) == odds_cols
    assert set(OddsTickQuarantine.__table__.primary_key.columns.keys()) == {"time", "match_id", "tick_seq"}
    assert not OddsTickQuarantine.__table__.c.reason.nullable
    assert not OddsTickQuarantine.__table__.c.quarantined_at.nullable


def test_migration_is_chained_after_005():
    from pathlib import Path

    source = (Path(qt.ROOT) / "alembic" / "versions" / "006_odds_ticks_quarantine.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "005_market_ticks_json_fields"' in source
    assert "CREATE TABLE IF NOT EXISTS odds_ticks_quarantine" in source


def test_quarantine_table_is_not_a_hypertable():
    from pathlib import Path

    db_init = (Path(qt.ROOT) / "backend" / "db_init.py").read_text(encoding="utf-8")
    assert "odds_ticks_quarantine" not in "".join(
        line for line in db_init.splitlines() if "create_hypertable" in line)


# --------------------------------------------------------------------------- scraper guard wiring
def test_guard_runs_before_publish_and_store_in_on_events():
    from pathlib import Path

    source = (Path(manager_mod.__file__)).read_text(encoding="utf-8")
    guard = source.index("cross_match_copy_rejected\", match_id")
    context = source.index("Derive MatchContext for every recorded live match")
    store = source.index("await self._store_odds_tick(event)")
    assert guard < context < store


def _t(mid, minute, bh, lh, ba, la, seq=1):
    from datetime import datetime, timedelta, timezone
    return {"match_id": mid, "time": datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
            "tick_seq": seq, "back_home": bh, "lay_home": lh, "back_away": ba, "lay_away": la}


def test_placeholder_book_on_three_matches_is_flagged_but_not_on_two():
    from scripts import quarantine_ticks as q
    ph = (1.92, 1.99, 2.02, 2.1)
    ticks = [_t("a", 0, *ph), _t("b", 5, *ph), _t("c", 9, *ph), _t("d", 1, 1.5, 1.6, 2.6, 2.8),
             _t("x", 2, 1.4, 1.5, 3.0, 3.2), _t("y", 3, 1.4, 1.5, 3.0, 3.2)]
    flagged = {t["match_id"] for t in q.placeholder_flags(ticks)}
    assert flagged == {"a", "b", "c"}


def test_a_real_ladder_price_on_three_matches_hours_apart_is_not_a_placeholder():
    """2026-11-04: 50/100 v 1.01/1.02 and 11.5/12 v 1.09/1.10 are real prices late in a chase; they recur across
    matches on different days. The old rule quarantined 1,958 such real rows."""
    from scripts import quarantine_ticks as q
    late = (50.0, 100.0, 1.01, 1.02)
    ticks = [_t("a", 0, *late), _t("b", 180, *late), _t("c", 720, *late)]
    assert q.placeholder_flags(ticks) == []


def test_the_same_book_on_three_matches_within_the_window_is_a_placeholder():
    from scripts import quarantine_ticks as q
    book = (1.95, 2.0, 1.95, 2.0)
    ticks = [_t("a", 0, *book), _t("b", 4, *book), _t("c", 14, *book), _t("d", 300, *book)]
    assert {t["match_id"] for t in q.placeholder_flags(ticks)} == {"a", "b", "c"}


def test_the_known_default_book_is_flagged_even_when_spread_out():
    from scripts import quarantine_ticks as q
    ph = (1.92, 1.99, 2.02, 2.1)
    ticks = [_t("a", 0, *ph), _t("b", 600, *ph), _t("c", 1200, *ph)]
    assert len(q.placeholder_flags(ticks)) == 3


def test_ticks_after_a_trusted_result_are_flagged():
    from datetime import datetime, timezone
    from scripts import quarantine_ticks as q
    ticks = [_t("m", 0, 1.5, 1.6, 2.6, 2.8), _t("m", 20, 1.9, 2.0, 2.0, 2.1)]
    done = {"m": datetime(2026, 11, 2, 11, 10, tzinfo=timezone.utc)}
    reasons = q.classify(ticks, done)
    assert list(reasons.values()) == [q.RULE_AFTER_RESULT] and len(reasons) == 1


def test_monitor_quarantines_before_checking_when_asked(monkeypatch):
    import argparse
    import asyncio
    from scripts import data_quality_monitor as dqm
    from scripts import quarantine_ticks as qt

    order: list[str] = []

    async def fake_quarantine():
        order.append("quarantine")
        return {"total": 0, "per_rule": {}}

    async def fake_run_once(window_hours):
        order.append("checks")
        return {"exit_code": 0}

    class _Engine:
        async def dispose(self):
            pass

    async def fake_harmonise():
        order.append("harmonise")

    monkeypatch.setattr(qt, "quarantine_now", fake_quarantine)
    monkeypatch.setattr(dqm, "_harmonise", fake_harmonise)
    monkeypatch.setattr(dqm, "run_once", fake_run_once)
    monkeypatch.setattr("shared.db.get_engine", lambda: _Engine())
    code = asyncio.run(dqm._main(argparse.Namespace(interval=0, window_hours=1.0, quarantine=True)))
    assert code == 0 and order == ["quarantine", "harmonise", "checks"]
