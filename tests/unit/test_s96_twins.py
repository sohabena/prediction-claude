"""S96: one fixture, one match. Twin rule, canonical choice, result copies, the write path, the consumers that skip
twins, the monitor check and the migration. Fakes only: no database, Redis or file system is written.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import data_quality_monitor as dqm
from scripts import link_twins as lt
from scripts import results_reconciliation as rr
from tests.unit import test_s55_data_quality as s55
from virtual_trading import paper_trader as pt
from virtual_trading import replay_feed


@pytest.fixture(autouse=True)
def fresh_paper_log(tmp_path, monkeypatch):
    """The s55 helpers run the paper_trading check, which reads the trader log: keep it off the real logs folder."""
    path = tmp_path / "paper_trader.log"
    path.write_text(json.dumps({"cycle_at": END.isoformat(), "intents": 0}) + "\n", encoding="utf-8")
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", path)
    return path

UTC = timezone.utc
START = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
END = datetime(2026, 11, 4, 0, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]
OTAGO_V2 = "lb_36266991"
OTAGO_LEGACY = "lb_75330686"

WIN = dict(result_type="win", winner="Canterbury Kings", loser="Otago Volts", margin="12 runs",
           completed_at=END, result_source="public_scorecard")
UNRESOLVED = dict(result_type="unresolved", winner=None, loser=None, margin=None, completed_at=END,
                  result_source="lotus_result_detector")


def reg(mid, *, home="Otago Volts", away="Canterbury Kings", competition="Ford Trophy", start=START,
        actual=START, pipeline=None, pages=0, canonical=None, simulated=False, **result) -> dict[str, Any]:
    row: dict[str, Any] = {
        "match_id": mid, "team_home": home, "team_away": away, "competition": competition,
        "is_simulated": simulated, "match_start_time": start, "actual_start_time": actual,
        "score_pipeline": pipeline, "canonical_match_id": canonical, "match_page_rows": pages,
        "result_type": None, "winner": None, "loser": None, "margin": None, "completed_at": None,
        "result_source": None,
    }
    row.update(result)
    return row


def otago_pair(**overrides) -> list[dict[str, Any]]:
    """The 2026-11-03 evidence: the same fixture, the v2 feed on one id and the legacy feed on the other."""
    return [reg(OTAGO_V2, pipeline="livescore-v2", pages=899, **overrides.get("v2", {})),
            reg(OTAGO_LEGACY, pipeline="legacy", pages=11948, **overrides.get("legacy", {}))]


# --------------------------------------------------------------------------- twin rule (pure)
def test_the_evidence_pair_is_a_twin_and_the_fuller_record_is_canonical():
    v2, legacy = otago_pair()
    assert lt.is_twin(v2, legacy) and lt.is_twin(legacy, v2)
    plan = lt.build_plan([v2, legacy])
    # 2026-11-03: lb_75330686 kept recording 13 times the rows after lb_36266991 left the listing: it is canonical.
    assert plan.links == [lt.TwinLink(OTAGO_LEGACY, OTAGO_V2)]
    assert plan.desired == {OTAGO_LEGACY: None, OTAGO_V2: OTAGO_LEGACY}


def test_home_and_away_may_be_swapped():
    assert lt.is_twin(reg("lb_1"), reg("lb_2", home="Canterbury Kings", away="Otago Volts"))


def test_case_and_whitespace_are_ignored():
    assert lt.is_twin(reg("lb_1"), reg("lb_2", home="  otago   VOLTS ", away="canterbury kings"))


def test_srl_suffix_is_normalised_when_both_sides_are_simulated():
    a = reg("lb_1", home="Hyderabad Kingsmen Srl", away="Lahore Qalandars Srl", competition="Cup",
            simulated=True)
    b = reg("lb_2", home="Hyderabad Kingsmen SRL", away="Lahore Qalandars SRL", competition="Cup",
            simulated=True)
    assert lt.is_twin(a, b)


def test_a_simulated_match_is_never_the_twin_of_a_real_one():
    real = reg("lb_1", home="Perth Scorchers", away="Hobart Hurricanes", competition="Big Bash")
    # Same teams and competition, but the registry flags it simulated: the flag alone separates them.
    flagged = reg("lb_3", home="Perth Scorchers", away="Hobart Hurricanes", competition="Big Bash", simulated=True)
    assert not lt.is_twin(real, flagged)
    # A simulated league by name (SRL on both sides) is simulated on the name rule even without the flag.
    srl_name = reg("lb_2", home="Perth Scorchers SRL", away="Hobart Hurricanes SRL", competition="Big Bash SRL")
    assert not lt.is_twin(real, srl_name)


def test_only_lb_ids_are_twins():
    assert not lt.is_twin(reg("lb_1"), reg("cric_2"))
    assert not lt.is_twin(reg("sim_1"), reg("sim_2"))
    assert not lt.is_twin(reg("lb_1"), reg("lb_1"))


def test_the_start_window_is_thirty_minutes_inclusive():
    assert lt.is_twin(reg("lb_1", actual=START), reg("lb_2", actual=START + timedelta(minutes=30)))
    assert not lt.is_twin(reg("lb_1", actual=START), reg("lb_2", actual=START + timedelta(minutes=31)))


def test_the_actual_start_is_compared_when_known_else_the_scheduled_one():
    # Scheduled starts five hours apart (a stale JSON openDate) but the actual offs agree: twins.
    a = reg("lb_1", start=START, actual=START)
    b = reg("lb_2", start=START + timedelta(hours=5), actual=START + timedelta(minutes=2))
    assert lt.is_twin(a, b)
    # Scheduled starts agree but the actual offs are two hours apart: not twins.
    c = reg("lb_3", start=START, actual=START + timedelta(hours=2))
    assert not lt.is_twin(a, c)
    # No actual start on either side: the scheduled start is used.
    assert lt.is_twin(reg("lb_4", actual=None), reg("lb_5", actual=None))


def test_no_start_at_all_is_not_a_twin():
    assert not lt.is_twin(reg("lb_1", start=None, actual=None), reg("lb_2", start=None, actual=None))


def test_competition_must_agree_only_when_both_are_known():
    assert not lt.is_twin(reg("lb_1", competition="Ford Trophy"), reg("lb_2", competition="Pro20 Cup"))
    assert lt.is_twin(reg("lb_1", competition="Ford Trophy"), reg("lb_2", competition=""))
    assert lt.is_twin(reg("lb_1", competition="Ford  Trophy"), reg("lb_2", competition="ford trophy"))


def test_different_teams_or_an_empty_team_name_are_not_twins():
    assert not lt.is_twin(reg("lb_1"), reg("lb_2", home="Otago Volts", away="Northern Knights"))
    assert not lt.is_twin(reg("lb_1"), reg("lb_2", home="", away="Canterbury Kings"))


# --------------------------------------------------------------------------- canonical choice and groups
def test_more_match_page_rows_beat_the_score_feed():
    legacy = reg("lb_1", pipeline="legacy", pages=5000)
    v2 = reg("lb_2", pipeline="livescore-v2", pages=10)
    assert lt.choose_canonical([legacy, v2])["match_id"] == "lb_1"


def test_the_score_feed_breaks_a_tie_in_rows():
    legacy = reg("lb_1", pipeline="legacy", pages=7)
    v2 = reg("lb_2", pipeline="livescore-v2", pages=7)
    assert lt.choose_canonical([legacy, v2])["match_id"] == "lb_2"  # the lower id would have won without the feed


def test_without_a_score_feed_the_one_with_more_match_page_rows_wins():
    few = reg("lb_1", pages=10)
    many = reg("lb_2", pages=500)
    assert lt.choose_canonical([few, many])["match_id"] == "lb_2"


def test_a_tie_on_feed_and_rows_goes_to_the_lower_id():
    assert lt.choose_canonical([reg("lb_9", pages=3), reg("lb_2", pages=3)])["match_id"] == "lb_2"


def test_a_group_of_three_links_every_twin_to_one_canonical():
    rows = [reg("lb_3", pages=1), reg("lb_1", pages=9), reg("lb_2", pages=4)]
    plan = lt.build_plan(rows)
    assert plan.links == [lt.TwinLink("lb_1", "lb_2"), lt.TwinLink("lb_1", "lb_3")]


def test_ordinary_matches_are_not_linked_and_need_no_update():
    rows = [reg("lb_1", home="India", away="Australia", competition="ICC T20"), reg("lb_2", home="Oman", away="Nepal")]
    plan = lt.build_plan(rows)
    assert plan.links == [] and plan.updates == []


# --------------------------------------------------------------------------- canonical updates
def test_updates_name_only_rows_whose_stored_link_differs():
    rows = otago_pair()
    plan = lt.build_plan(rows)
    assert plan.updates == [lt.CanonicalUpdate(OTAGO_V2, OTAGO_LEGACY)]
    linked = otago_pair(v2={"canonical": OTAGO_LEGACY})
    assert lt.build_plan(linked).updates == []


def test_links_follow_a_change_of_canonical():
    # The rows still name the old canonical (lb_36266991); the rule now names lb_75330686.
    rows = [reg(OTAGO_V2, pipeline="livescore-v2", pages=899, canonical=OTAGO_V2),
            reg(OTAGO_LEGACY, pipeline="legacy", pages=11948, canonical=OTAGO_V2)]
    plan = lt.build_plan(rows)
    assert lt.CanonicalUpdate(OTAGO_V2, OTAGO_LEGACY) in plan.updates
    assert lt.CanonicalUpdate(OTAGO_LEGACY, None) in plan.updates


def test_an_ordinary_row_with_a_stale_link_is_unlinked():
    plan = lt.build_plan([reg("lb_7", home="India", away="Australia", competition="ICC T20", canonical="lb_1")])
    assert plan.updates == [lt.CanonicalUpdate("lb_7", None)]


def test_unlinked_links_reports_pairs_not_yet_linked():
    rows = otago_pair()
    plan = lt.build_plan(rows)
    assert lt.unlinked_links(plan, rows) == plan.links
    linked = otago_pair(v2={"canonical": OTAGO_LEGACY})
    assert lt.unlinked_links(lt.build_plan(linked), linked) == []


# --------------------------------------------------------------------------- result copies (pure)
def test_a_decided_result_is_copied_to_a_twin_that_has_none():
    rows = [reg(OTAGO_V2, pipeline="livescore-v2", pages=899), reg(OTAGO_LEGACY, pipeline="legacy", pages=11948, **WIN)]
    copies, conflicts = lt.plan_result_copies([lt.TwinLink(OTAGO_V2, OTAGO_LEGACY)], rows)
    assert conflicts == []
    assert [(c.target_id, c.source_id) for c in copies] == [(OTAGO_V2, OTAGO_LEGACY)]
    row = copies[0].row
    assert row["result_type"] == "win" and row["winner"] == "Canterbury Kings" and row["loser"] == "Otago Volts"
    assert row["margin"] == "12 runs" and row["completed_at"] == END
    assert row["source"] == f"twin:{OTAGO_LEGACY}"
    assert (row["team_home"], row["team_away"]) == ("Otago Volts", "Canterbury Kings")


def test_the_copy_uses_the_target_spelling_and_side_when_the_twin_is_swapped():
    target = reg("lb_1", home="Canterbury Kings", away="Otago Volts", pipeline="livescore-v2")
    source = reg("lb_2", home="Otago Volts", away="Canterbury Kings", **{**WIN, "winner": "canterbury  KINGS"})
    copies, _ = lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], [target, source])
    row = copies[0].row
    assert row["winner"] == "Canterbury Kings" and row["loser"] == "Otago Volts"
    assert (row["team_home"], row["team_away"]) == ("Canterbury Kings", "Otago Volts")


def test_a_tie_copies_with_empty_winner_and_loser():
    tie = dict(WIN, result_type="tie", winner=None, loser=None, margin=None)
    rows = [reg("lb_1", pipeline="livescore-v2"), reg("lb_2", **tie)]
    copies, _ = lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows)
    assert copies[0].row["result_type"] == "tie" and copies[0].row["winner"] == "" and copies[0].row["loser"] == ""


def test_an_unresolved_canonical_row_is_replaced_by_the_twins_decided_result():
    rows = [reg("lb_1", pipeline="livescore-v2", **UNRESOLVED), reg("lb_2", **WIN)]
    copies, _ = lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows)
    assert [(c.target_id, c.source_id) for c in copies] == [("lb_1", "lb_2")]


def test_a_decided_target_is_never_overwritten():
    rows = [reg("lb_1", pipeline="livescore-v2", result_type="win", winner="Otago Volts", loser="Canterbury Kings",
                completed_at=END, result_source="lotus_result_detector"), reg("lb_2", **WIN)]
    copies, conflicts = lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows)
    assert copies == [] and len(conflicts) == 1  # both decided and they disagree: a conflict, nothing copied


def test_two_agreeing_decided_results_are_left_alone():
    rows = [reg("lb_1", pipeline="livescore-v2", **WIN), reg("lb_2", **dict(WIN, margin="4 wickets"))]
    assert lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows) == ([], [])


def test_a_hand_entered_unresolved_row_is_never_overwritten():
    hand = dict(UNRESOLVED, result_source="manual_fix")
    rows = [reg("lb_1", pipeline="livescore-v2", **hand), reg("lb_2", **WIN)]
    assert lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows) == ([], [])


def test_a_win_with_no_winner_is_not_undecided_and_is_not_replaced():
    empty_win = dict(WIN, winner=None)
    rows = [reg("lb_1", pipeline="livescore-v2", **empty_win), reg("lb_2", **WIN)]
    assert lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows) == ([], [])


def test_a_source_without_a_completion_time_is_not_copied():
    rows = [reg("lb_1", pipeline="livescore-v2"), reg("lb_2", **dict(WIN, completed_at=None))]
    assert lt.plan_result_copies([lt.TwinLink("lb_1", "lb_2")], rows) == ([], [])


def test_a_target_receives_at_most_one_copy_in_a_group():
    rows = [reg("lb_1", pipeline="livescore-v2"), reg("lb_2", **WIN), reg("lb_3", **WIN)]
    plan = lt.build_plan(rows)
    assert len([c for c in plan.copies if c.target_id == "lb_1"]) == 1


# --------------------------------------------------------------------------- database layer (fakes)
class FakeResult:
    def __init__(self, rows=(), scalar=None, rowcount=1):
        self._rows = list(rows)
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchall(self):
        return [SimpleNamespace(_mapping=dict(r)) for r in self._rows]

    def scalar(self):
        return self._scalar


class FakeSession:
    def __init__(self, rows, column=True):
        self.rows = rows
        self.column = column
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append((sql, dict(params or {})))
        if "information_schema" in sql:
            return FakeResult(scalar=self.column)
        if "FROM match_training_status s" in sql:
            return FakeResult(rows=self.rows)
        return FakeResult(rowcount=1)

    def writes(self):
        return [(sql, p) for sql, p in self.statements
                if re.match(r"\s*(UPDATE|INSERT|DELETE|ALTER|CREATE|DROP)", sql, re.I)]


def test_dry_run_reads_the_registry_without_the_new_column_and_writes_nothing():
    session = FakeSession(otago_pair(), column=False)
    result = asyncio.run(lt.link_now(session, apply=False))
    assert result["column_present"] is False
    assert len(result["plan"].links) == 1
    assert session.writes() == []


def test_apply_without_the_migration_is_refused_before_any_write():
    session = FakeSession(otago_pair(), column=False)
    with pytest.raises(lt.MigrationMissing):
        asyncio.run(lt.link_now(session, apply=True))
    assert session.writes() == []


def test_apply_writes_only_the_canonical_column_and_the_copies():
    rows = [reg(OTAGO_V2, pipeline="livescore-v2", pages=899, **UNRESOLVED),
            reg(OTAGO_LEGACY, pipeline="legacy", pages=11948, **WIN)]
    session = FakeSession(rows, column=True)
    summary = asyncio.run(lt.link_now(session, apply=True))
    writes = session.writes()
    updates = [(sql, p) for sql, p in writes if sql.lstrip().upper().startswith("UPDATE")]
    inserts = [(sql, p) for sql, p in writes if sql.lstrip().upper().startswith("INSERT")]
    assert len(writes) == len(updates) + len(inserts)  # nothing but UPDATE and INSERT
    # Only the twin needs a stored link (the canonical already holds NULL): one write, on canonical_match_id only.
    assert len(updates) == 1
    sql, params = updates[0]
    assert "SET canonical_match_id = :canonical" in sql and "match_results" not in sql
    assert params == {"canonical": OTAGO_LEGACY, "match_id": OTAGO_V2}
    assert len(inserts) == 1 and inserts[0][1]["match_id"] == OTAGO_V2
    assert inserts[0][1]["source"] == f"twin:{OTAGO_LEGACY}"
    assert summary["applied"] == {"canonical_updated": 1, "results_copied": 1}


def test_the_copy_statement_refuses_decided_and_hand_entered_targets_in_sql():
    sql = lt.COPY_RESULT_SQL
    assert "WHERE match_results.result_type = 'unresolved'" in sql
    assert "'public_scorecard', 'manual_fix'" in sql
    assert "ON CONFLICT (match_id)" in sql


def test_the_canonical_statement_touches_only_the_canonical_column():
    sql = lt.UPDATE_CANONICAL_SQL
    assert "SET canonical_match_id = :canonical" in sql
    assert "team_" not in sql and "winner" not in sql and "start" not in sql


def test_the_registry_query_is_a_read_that_never_names_the_new_column_directly():
    sql = lt.TWIN_ROWS_SQL.strip()
    assert sql.upper().startswith("SELECT") and ";" not in sql
    assert "s.canonical_match_id" not in sql  # read through to_jsonb: the dry run works without the column
    assert "to_jsonb(s) ->> 'canonical_match_id'" in sql
    assert lt.CANONICAL_COLUMN_SQL.strip().upper().startswith("SELECT") and ";" not in lt.CANONICAL_COLUMN_SQL


def test_the_hand_entered_list_matches_the_detector():
    from scraper.lotus_result_detector import MANUAL_SOURCES

    assert tuple(lt.HAND_ENTERED_SOURCES) == tuple(MANUAL_SOURCES)


def test_the_render_names_the_pair_the_canonical_and_the_dry_run():
    result = asyncio.run(lt.link_now(FakeSession(otago_pair(), column=False), apply=False))
    text = lt.render(result, apply=False)
    assert "DRY RUN (nothing written)" in text
    assert "Otago Volts v Canterbury Kings" in text
    assert f"canonical {OTAGO_LEGACY}" in text and f"twin {OTAGO_V2}" in text
    assert "absent" in text


def test_the_monitor_harmonise_links_twins_with_the_same_functions(monkeypatch):
    session = FakeSession(otago_pair(), column=True)

    @asynccontextmanager
    async def fake_get_session():
        yield session

    import shared.db

    monkeypatch.setattr(shared.db, "get_session", fake_get_session)
    asyncio.run(dqm._link_twins())
    updates = [p for sql, p in session.writes() if sql.lstrip().upper().startswith("UPDATE")]
    assert updates == [{"canonical": OTAGO_LEGACY, "match_id": OTAGO_V2}]


def test_the_monitor_skips_linking_quietly_until_the_migration(monkeypatch):
    session = FakeSession(otago_pair(), column=False)

    @asynccontextmanager
    async def fake_get_session():
        yield session

    import shared.db

    monkeypatch.setattr(shared.db, "get_session", fake_get_session)
    asyncio.run(dqm._link_twins())  # must not raise
    assert session.writes() == []


# --------------------------------------------------------------------------- monitor: duplicate_fixtures
def _dup(rows, *, column=True):
    fetch_rows = {"canonical_column": [{"present": column}], "twin_rows": rows}
    return s55.one_check(dqm.check_duplicate_fixtures, fetch_rows)


def test_duplicate_fixtures_is_registered_last_and_is_a_warning():
    assert dqm.CHECKS[-1] is dqm.check_duplicate_fixtures


def test_duplicate_fixtures_is_not_scored_without_the_column():
    c = _dup(otago_pair(), column=False)
    assert c["status"] == "ok" and "not present" in c["detail"]


def test_duplicate_fixtures_is_ok_when_the_twins_are_linked():
    c = _dup(otago_pair(v2={"canonical": OTAGO_LEGACY}))
    assert c["status"] == "ok" and c["value"] == 0


def test_duplicate_fixtures_warns_on_an_unlinked_twin():
    c = _dup(otago_pair())
    assert c["status"] == "warn" and c["value"] == 1
    assert OTAGO_V2 in c["detail"] and "not linked" in c["detail"]


def test_duplicate_fixtures_warns_on_conflicting_decided_results():
    rows = otago_pair(legacy={**WIN},
                      v2={"canonical": OTAGO_LEGACY, **dict(WIN, winner="Otago Volts", loser="Canterbury Kings")})
    c = _dup(rows)
    assert c["status"] == "warn" and c["value"] == 1 and "conflict" in c["detail"]


def test_duplicate_fixtures_reads_only_select_queries():
    for name in ("canonical_column", "twin_rows"):
        sql = dqm.SQL[name].strip()
        assert sql.upper().startswith(("SELECT", "WITH")) and ";" not in sql


# --------------------------------------------------------------------------- monitor: results skip twins
def test_the_results_check_leaves_out_a_non_canonical_twin():
    stale = 30000.0  # over the 6 h RESULT_STALE_S
    twin = s55._match("lb_t", age_s=stale, has_result=False, canonical_match_id=OTAGO_V2)
    rows = s55.healthy_rows()
    rows["results"] = [twin]
    assert s55.by_name(s55.run(rows))["results"]["status"] == "ok"
    rows["results"] = [s55._match("lb_t", age_s=stale, has_result=False, canonical_match_id=None)]
    assert s55.by_name(s55.run(rows))["results"]["status"] == "fail"


def test_select_missing_skips_a_twin_and_keeps_its_canonical_match():
    def row(mid, canonical):
        return {"match_id": mid, "team_home": "Otago Volts", "team_away": "Canterbury Kings",
                "competition": "Ford Trophy", "is_simulated": False, "match_start_time": START,
                "last_time": END - timedelta(hours=5), "age_s": 5 * 3600, "has_result": False,
                "winner": None, "result_type": None, "went_live": True, "canonical_match_id": canonical}

    missing = rr.select_missing([row("lb_1", None), row("lb_2", OTAGO_V2)], END)
    assert [m["match_id"] for m in missing] == ["lb_1"]


def test_results_sql_carries_the_canonical_flag_and_stays_a_select():
    assert "canonical_match_id" in rr.RESULTS_SQL
    assert rr.RESULTS_SQL.strip().upper().startswith("WITH") and ";" not in rr.RESULTS_SQL


# --------------------------------------------------------------------------- consumers: paper trader and replay
def _cand(mid, canonical=None):
    return {"match_id": mid, "team_home": "Alpha", "team_away": "Beta", "competition": "Indian Premier League",
            "match_start_time": START, "actual_start_time": START, "is_simulated": False, "toss_winner": None,
            "toss_decision": None, "settled": False, "canonical_match_id": canonical}


def test_the_paper_trader_skips_a_non_canonical_twin_as_a_candidate():
    now = START - timedelta(minutes=5)
    last = {"a": now, "b": now}
    candidates, skipped = pt.select_candidates([_cand("a"), _cand("b", canonical="a")], last, now)
    assert [c.match_id for c in candidates] == ["a"]
    assert skipped.get("twin") == 1


def test_the_paper_trader_keeps_ordinary_matches():
    now = START - timedelta(minutes=5)
    candidates, skipped = pt.select_candidates([_cand("a")], {"a": now}, now)
    assert [c.match_id for c in candidates] == ["a"] and "twin" not in skipped


def test_the_paper_trader_registry_query_selects_the_column():
    import inspect

    source = inspect.getsource(pt.DbFeed.registry)
    assert "(to_jsonb(r) ->> 'canonical_match_id') AS canonical_match_id" in source


class _Res:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _ReplaySession:
    def __init__(self, column: bool, registry_rows):
        self.column = column
        self.registry_rows = registry_rows
        self.seen: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append(sql)
        if "information_schema" in sql:
            return _Res(scalar=self.column)
        if "FROM market_ticks" in sql:
            return _Res()
        if "FROM match_training_status" in sql:
            return _Res(rows=self.registry_rows)
        if "FROM v_match_odds" in sql:
            return _Res(rows=[{"match_id": "a", "time": START, "back_home": 2.0, "lay_home": 2.1, "back_away": 2.0,
                               "lay_away": 2.1, "source": "listing"}])
        return _Res()


def test_replay_load_reads_the_canonical_column_when_present():
    session = _ReplaySession(True, [{**_cand("a"), "actual_start_time": None}, _cand("b", canonical="a")])
    feed = asyncio.run(replay_feed.ReplayFeed.load(session, end=START + timedelta(hours=1)))
    registry_sql = next(s for s in session.seen if "FROM match_training_status WHERE" in s)
    assert "canonical_match_id" in registry_sql and "NULL::text" not in registry_sql
    assert feed._registry["b"]["canonical_match_id"] == "a"


def test_replay_load_uses_null_when_the_column_is_missing():
    session = _ReplaySession(False, [_cand("a")])
    asyncio.run(replay_feed.ReplayFeed.load(session, end=START + timedelta(hours=1)))
    registry_sql = next(s for s in session.seen if "FROM match_training_status WHERE" in s)
    assert "NULL::text AS canonical_match_id" in registry_sql


def test_replay_registry_exposes_the_canonical_id_for_the_candidate_filter():
    feed = replay_feed.ReplayFeed(prices=[], registry=[_cand("a"), _cand("b", canonical="a")], context=[],
                                  results=[], end=START + timedelta(hours=2))
    feed.seek(START - timedelta(minutes=5))
    rows = asyncio.run(feed.registry(["a", "b"]))
    assert {r["match_id"]: r["canonical_match_id"] for r in rows} == {"a": None, "b": "a"}
    candidates, skipped = pt.select_candidates(rows, {"a": START, "b": START}, START - timedelta(minutes=5))
    assert [c.match_id for c in candidates] == ["a"] and skipped.get("twin") == 1


# --------------------------------------------------------------------------- consumers: training dataset
def test_the_training_registry_query_adds_the_column_only_when_present():
    from scripts import build_training_dataset as btd
    with_col = btd._registry_sql(True, True)
    without = btd._registry_sql(True, False)
    assert with_col.endswith("actual_start_time, canonical_match_id FROM match_training_status")
    assert "canonical_match_id" not in without and "actual_start_time FROM" in without
    assert "canonical_match_id" not in btd._registry_sql(False, False)


def test_build_dataset_skips_a_twin_but_keeps_the_canonical_match():
    from training.dataset import build_dataset, group_by_match

    def match(mid, canonical=None):
        return {"match_id": mid, "team_home": "Otago Volts", "team_away": "Canterbury Kings",
                "competition": "Ford Trophy", "match_start_time": START, "actual_start_time": START,
                "is_simulated": False, "training_status": "approved", "canonical_match_id": canonical}

    tick = {"time": START - timedelta(minutes=10), "source": "listing", "back_home": 2.0, "lay_home": 2.1,
            "back_away": 2.0, "lay_away": 2.1}
    prices = group_by_match([{**tick, "match_id": OTAGO_V2}, {**tick, "match_id": OTAGO_LEGACY}])
    dataset = build_dataset([match(OTAGO_V2), match(OTAGO_LEGACY, OTAGO_V2)], prices, {})
    assert [m["match_id"] for m in dataset["matches"]] == [OTAGO_V2]
    assert dataset["stats"]["non_canonical_twins"] == 1
    assert dataset["stats"].get("price_matches_without_registry", 0) == 0


# --------------------------------------------------------------------------- migration, ORM and image
def test_the_migration_adds_a_nullable_text_column_if_not_exists():
    from backend import db_init

    statements = [s for s in db_init.MIGRATION_STATEMENTS if "canonical_match_id" in s]
    assert len(statements) == 1
    assert "ALTER TABLE match_training_status" in statements[0]
    assert "ADD COLUMN IF NOT EXISTS canonical_match_id TEXT" in statements[0]
    assert "NOT NULL" not in statements[0]


def test_the_orm_model_has_the_nullable_deferred_column():
    from backend.models.match_status import MatchTrainingStatus

    column = MatchTrainingStatus.__table__.c["canonical_match_id"]
    assert column.nullable is True
    prop = MatchTrainingStatus.__mapper__.column_attrs["canonical_match_id"]
    assert prop.deferred is True


def test_the_scraper_image_copies_the_monitor_import():
    text = (REPO / "Dockerfile.scraper").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY (\S+) ", text, flags=re.M))
    assert "scripts/link_twins.py" in copied


# --------------------------------------------------------------------------- detector: a twin result is kept
def test_a_twin_result_is_never_replaced_by_the_detector(monkeypatch):
    """S96: a result copied from a twin (source 'twin:<id>') stays; a later detector write is skipped and not
    published, as for a hand-entered result (the test_s54 pattern)."""
    from backend.models.results import MatchResultRecord
    from scraper import lotus_result_detector as ld
    from scraper.lotus_result_detector import LotusResultDetector
    from tests.unit.test_s54_result_accuracy import AWAY, HOME, MID, T0, _FakeSession

    merged: list = [MatchResultRecord(match_id=MID, completed_at=T0, winner=HOME, loser=AWAY, result_type="win",
                                      margin="2 runs", team_home=HOME, team_away=AWAY,
                                      source=f"twin:{OTAGO_LEGACY}")]
    published: list = []

    class _CM:
        async def __aenter__(self):
            return _FakeSession(merged)

        async def __aexit__(self, *exc):
            return False

    class _Redis:
        async def publish_event(self, channel, payload):
            published.append((channel, payload))

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr(ld, "get_session", lambda: _CM())
    monkeypatch.setattr(ld, "get_redis", fake_get_redis)

    asyncio.run(LotusResultDetector()._store_and_publish({
        "match_id": MID, "completed_at": T0.isoformat(), "winner": AWAY, "loser": HOME, "margin": "",
        "team_home": HOME, "team_away": AWAY, "source": "lotusbook_odds", "result_type": "win",
        "closing_back_home": 9.0, "closing_back_away": 1.05, "closing_lay_home": None, "closing_lay_away": None,
    }))
    assert len(merged) == 1 and merged[0].winner == HOME and merged[0].source == f"twin:{OTAGO_LEGACY}"
    assert published == []
