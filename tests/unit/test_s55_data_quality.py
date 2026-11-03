"""S55: data quality monitor checks, exit codes, SQL safety and the recording_health SRL change.

All database access goes through injected fakes; no DB, Redis or browser is touched.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import data_quality_monitor as dqm
from scripts import recording_health as health

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PARAM_NAMES = {"now", "since", "min_odds", "max_odds", "tie_seconds", "heartbeat_since",
               "prematch_since", "prematch_lead_s"}  # S77 adds the last two


def _match(match_id="m1", home="India", away="Australia", competition="ICC T20", **extra) -> dict[str, Any]:
    row = {"match_id": match_id, "team_home": home, "team_away": away, "competition": competition}
    row.update(extra)
    return row


def healthy_rows() -> dict[str, list[dict[str, Any]]]:
    """Rows for a clean day: one live real match with pages, context, a result and a registry row."""
    return {
        "freshness": [{"odds_age_s": 5.0, "market_age_s": 30.0, "live_now": 2}],
        "odds_prices": [{"zero_prices": 0, "out_of_range": 0, "crossed": 0, "rows": 100}],
        "market_prices": [{"zero_prices": 0, "out_of_range": 0, "crossed": 0, "rows": 40}],
        "duplicates": [{"rows": 40, "duplicates": 0}],
        "live_matches": [_match(odds_rows=50, has_market=True, has_context=True)],
        "lay_completeness": [{"live_ticks": 100, "null_lay_ticks": 0, "recoverable": 0}],
        "gaps": [{"match_id": "m1", "max_gap_s": 60.0}],
        "registry_coverage": [{"missing_registry": 0, "empty_registry_competition": 0}],
        "odds_totals": [{"rows": 100, "empty_competition": 0}],
        "match_names": [_match(odds_rows=50, name_variants=1)],
        "is_simulated_column": [{"present": False}],
        "score_pipeline_column": [{"present": True}],
        "score_pipelines": [{"match_id": "m1", "score_pipeline": "livescore-v2"}],
        "results": [_match("m0", age_s=7200.0, has_result=True, winner="India", result_type="win")],
        "misattribution_ticks": [],
        "prematch_pages": [],  # S77: no real match started in the window, so nothing to score
        "paper_open_fills": [],
        "paper_stale_settlement": [],
        "paper_sanity_trades": [],  # S75: no paper orders in the last 24 h
        "paper_sanity_edges": [],
        "actual_start_columns": [{"present": True}],  # S86: the columns exist and no real match started in the window
        "start_times": [],
    }


def _write_cycle(path: Path, cycle_at: datetime) -> Path:
    path.write_text(json.dumps({"cycle_at": cycle_at.isoformat(), "intents": 0}) + chr(10), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fresh_paper_log(tmp_path, monkeypatch):
    """S73: the paper trader log is read by the paper_trading check; a healthy day has a cycle a minute old."""
    path = _write_cycle(tmp_path / "paper_trader.log", NOW - timedelta(seconds=60))
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", path)
    return path


def fake_fetch(rows: dict[str, list[dict[str, Any]]]):
    async def fetch(name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert name in rows, f"unexpected query {name}"
        return [dict(r) for r in rows[name]]

    return fetch


NO_REGISTRY = Path("no-registry-here")  # S65: model_drift reads nothing unless a champion exists


def run(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return asyncio.run(dqm.run_checks(fake_fetch(rows), now=NOW, window_hours=24, registry_root=NO_REGISTRY))


def by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in report["checks"]}


def one_check(check_fn, rows) -> dict[str, Any]:
    async def go():
        return await check_fn(fake_fetch(rows), {"now": NOW, "since": NOW - timedelta(hours=24)})

    return asyncio.run(go())


# --------------------------------------------------------------------------- SQL safety
def test_every_query_is_a_select_with_only_known_parameters():
    forbidden = re.compile(r"\b(insert|update|delete|alter|create|drop|truncate|grant|merge|copy|call)\b", re.I)
    for name, sql in dqm.SQL.items():
        stripped = sql.strip()
        assert stripped.upper().startswith(("SELECT", "WITH")), name
        assert not forbidden.search(stripped), name
        assert ";" not in stripped, name
        assert "{" not in stripped and "%(" not in stripped, f"{name} looks string-built"
        params = set(dqm._PARAM_RE.findall(sql))
        assert params <= PARAM_NAMES, (name, params - PARAM_NAMES)


def test_db_fetch_binds_only_the_parameters_the_query_uses():
    captured = {}

    class FakeResult:
        def fetchall(self):
            return []

    class FakeSession:
        async def execute(self, statement, bound):
            captured["bound"] = bound
            captured["sql"] = str(statement)
            return FakeResult()

    asyncio.run(dqm._db_fetch(FakeSession(), "freshness", {"now": NOW, "since": NOW, "min_odds": 1.01}))
    assert set(captured["bound"]) == {"now", "since"}
    assert "CAST(:now AS timestamptz)" in captured["sql"]


def test_report_sets_a_read_only_transaction_in_run_once_source():
    source = (Path(dqm.__file__)).read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in source


# --------------------------------------------------------------------------- healthy baseline
def test_healthy_day_is_ok_with_exit_zero():
    report = run(healthy_rows())
    assert report["overall"] == "ok"
    assert report["exit_code"] == 0
    assert [c["name"] for c in report["checks"]] == [
        "freshness", "price_sanity", "duplicates", "match_page_coverage", "lay_completeness",
        "score_coverage", "registry", "results", "gaps", "srl", "misattribution",
        "model_drift", "paper_trading",
        "prematch_pages",  # S77, appended last
        "results_missing",  # S76, appended last
        "paper_sanity",  # S75, appended last
        "start_time_consistency",  # S86, appended last
    ]


def test_render_lists_every_check_and_the_overall_line():
    text = dqm.render(run(healthy_rows()))
    assert "overall: OK (exit 0)" in text
    assert "freshness" in text and "srl" in text


# --------------------------------------------------------------------------- freshness
def test_freshness_fails_when_a_match_is_live_and_rows_are_old():
    rows = healthy_rows()
    rows["freshness"] = [{"odds_age_s": 1200.0, "market_age_s": 1500.0, "live_now": 1}]
    c = one_check(dqm.check_freshness, rows)
    assert c["status"] == "fail"
    assert c["value"] == 1200.0


def test_freshness_is_ok_with_old_rows_when_nothing_is_live():
    rows = healthy_rows()
    rows["freshness"] = [{"odds_age_s": 5000.0, "market_age_s": 5000.0, "live_now": 0}]
    assert one_check(dqm.check_freshness, rows)["status"] == "ok"


def test_freshness_uses_the_newer_of_the_two_tables():
    rows = healthy_rows()
    rows["freshness"] = [{"odds_age_s": 1800.0, "market_age_s": 60.0, "live_now": 1}]
    assert one_check(dqm.check_freshness, rows)["status"] == "ok"


def test_freshness_fails_when_no_rows_exist_but_a_match_is_live():
    rows = healthy_rows()
    rows["freshness"] = [{"odds_age_s": None, "market_age_s": None, "live_now": 1}]
    assert one_check(dqm.check_freshness, rows)["status"] == "fail"


# --------------------------------------------------------------------------- price sanity
def test_price_sanity_ok_on_clean_prices():
    assert one_check(dqm.check_price_sanity, healthy_rows())["status"] == "ok"


def test_price_sanity_fails_on_zero_prices_stored_as_numbers():
    rows = healthy_rows()
    rows["odds_prices"] = [{"zero_prices": 3, "out_of_range": 0, "crossed": 0, "rows": 100}]
    c = one_check(dqm.check_price_sanity, rows)
    assert c["status"] == "fail" and c["value"] == 3


def test_price_sanity_fails_on_a_crossed_book():
    rows = healthy_rows()
    rows["market_prices"] = [{"zero_prices": 0, "out_of_range": 0, "crossed": 2, "rows": 40}]
    assert one_check(dqm.check_price_sanity, rows)["status"] == "fail"


def test_price_sanity_warns_on_prices_outside_range():
    rows = healthy_rows()
    rows["odds_prices"] = [{"zero_prices": 0, "out_of_range": 4, "crossed": 0, "rows": 100}]
    assert one_check(dqm.check_price_sanity, rows)["status"] == "warn"


# --------------------------------------------------------------------------- duplicates
def test_duplicates_ok_when_no_repeated_rows():
    assert one_check(dqm.check_duplicates, healthy_rows())["status"] == "ok"


def test_duplicates_warn_on_a_small_number_of_repeats():
    rows = healthy_rows()
    rows["duplicates"] = [{"rows": 1000, "duplicates": 5}]
    assert one_check(dqm.check_duplicates, rows)["status"] == "warn"


def test_duplicates_fail_when_dedupe_is_broken():
    rows = healthy_rows()
    rows["duplicates"] = [{"rows": 1000, "duplicates": 200}]
    c = one_check(dqm.check_duplicates, rows)
    assert c["status"] == "fail" and c["value"] == pytest.approx(0.2)


# --------------------------------------------------------------------------- match-page coverage
def test_match_page_coverage_warns_below_ninety_percent_and_counts_srl():
    rows = healthy_rows()
    rows["live_matches"] = [
        _match("a", has_market=True, has_context=True),
        _match("b", has_market=False, has_context=False),
        _match("c", has_market=False, has_context=False),
        _match("srl", "Hobart Hurricanes SRL", "Perth Scorchers", "", has_market=False, has_context=False),
    ]
    c = one_check(dqm.check_match_page_coverage, rows)
    assert c["status"] == "warn"
    assert c["value"] == pytest.approx(1 / 4)  # SRL is recorded from its match page too (S50), so it counts
    assert "1 of all are SRL" in c["detail"]


def test_match_page_coverage_does_not_score_matches_that_ended_before_match_pages():
    rows = healthy_rows()
    rows["live_matches"] = [
        _match("a", has_market=True, has_context=True),
        {**_match("old", has_market=False, has_context=False), "before_match_pages": True},
    ]
    c = one_check(dqm.check_match_page_coverage, rows)
    assert c["status"] == "ok" and c["value"] == 1.0
    assert "1 ended before match-page recording began" in c["detail"]


def test_match_page_coverage_ok_at_full_coverage_and_when_no_live_matches():
    assert one_check(dqm.check_match_page_coverage, healthy_rows())["status"] == "ok"
    rows = healthy_rows()
    rows["live_matches"] = []
    c = one_check(dqm.check_match_page_coverage, rows)
    assert c["status"] == "ok" and c["value"] is None


# --------------------------------------------------------------------------- lay completeness
def test_lay_completeness_ok_when_no_recoverable_lays():
    assert one_check(dqm.check_lay_completeness, healthy_rows())["status"] == "ok"


def test_lay_completeness_warns_on_a_small_uncovered_share():
    rows = healthy_rows()
    rows["lay_completeness"] = [{"live_ticks": 1000, "null_lay_ticks": 30, "recoverable": 10}]  # 20 uncovered
    assert one_check(dqm.check_lay_completeness, rows)["status"] == "warn"


def test_lay_completeness_fails_when_many_lay_less_ticks_have_no_match_page():
    rows = healthy_rows()
    rows["lay_completeness"] = [{"live_ticks": 100, "null_lay_ticks": 60, "recoverable": 20}]  # 40 uncovered
    c = one_check(dqm.check_lay_completeness, rows)
    assert c["status"] == "fail" and c["value"] == pytest.approx(0.4)


def test_lay_completeness_ok_when_every_lay_less_tick_is_covered_by_the_match_page():
    rows = healthy_rows()
    rows["lay_completeness"] = [{"live_ticks": 100, "null_lay_ticks": 89, "recoverable": 89}]
    assert one_check(dqm.check_lay_completeness, rows)["status"] == "ok"


# --------------------------------------------------------------------------- score coverage
def _score_rows(*matches):
    """Live matches with match-page rows; each tuple is (match_id, pipeline or None, has_context)."""
    rows = healthy_rows()
    rows["live_matches"] = [_match(mid, has_market=True, has_context=ctx) for mid, _pipe, ctx in matches]
    rows["score_pipelines"] = [{"match_id": mid, "score_pipeline": pipe} for mid, pipe, _ctx in matches if pipe]
    return rows


def test_score_coverage_fails_when_a_livescore_match_has_no_score_state():
    # S68: a match on the livescore-v2 feed with match-page rows but no score rows is a recording fault
    rows = _score_rows(*[(f"m{i}", "livescore-v2", True) for i in range(19)], ("gap", "livescore-v2", False))
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "fail"
    assert c["value"] == pytest.approx(0.05)
    assert "gap" in c["detail"]


def test_score_coverage_ok_when_every_livescore_match_has_context():
    assert one_check(dqm.check_score_coverage, healthy_rows())["status"] == "ok"


def test_legacy_matches_without_score_state_never_fail_and_are_reported_apart():
    rows = _score_rows(("v2", "livescore-v2", True), ("old", "legacy", False), ("old2", "legacy", False))
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "ok"
    assert c["value"] == pytest.approx(0.0)
    assert "2 on the legacy pipeline" in c["detail"]


def test_matches_without_a_recorded_pipeline_are_not_scored():
    rows = _score_rows(("unread", None, False))
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "ok"
    assert "1 with no pipeline recorded yet" in c["detail"]


def test_score_coverage_is_not_scored_until_the_pipeline_column_exists():
    rows = _score_rows(("m1", "livescore-v2", False))
    rows["score_pipeline_column"] = [{"present": False}]
    rows.pop("score_pipelines")
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "ok"
    assert "migration 007 not applied" in c["detail"]


# --------------------------------------------------------------------------- registry
def test_registry_fails_on_a_recorded_match_without_registry_row():
    rows = healthy_rows()
    rows["registry_coverage"] = [{"missing_registry": 2, "empty_registry_competition": 0}]
    c = one_check(dqm.check_registry, rows)
    assert c["status"] == "fail" and c["value"] == 2


def test_registry_fails_on_empty_competition():
    rows = healthy_rows()
    rows["odds_totals"] = [{"rows": 100, "empty_competition": 100}]
    c = one_check(dqm.check_registry, rows)
    assert c["status"] == "fail"
    assert "empty competition on 100 of 100" in c["detail"]


def test_registry_warns_on_team_name_variants_for_one_match_id():
    rows = healthy_rows()
    rows["match_names"] = [_match(name_variants=2, odds_rows=10)]
    assert one_check(dqm.check_registry, rows)["status"] == "warn"


def test_registry_counts_is_simulated_rows_without_failing_when_column_exists():
    rows = healthy_rows()
    rows["is_simulated_column"] = [{"present": True}]
    rows["is_simulated_rows"] = [{"n": 37}]
    c = one_check(dqm.check_registry, rows)
    assert c["status"] == "ok"
    assert "simulated matches in registry: 37" in c["detail"]


def test_registry_reports_missing_is_simulated_column():
    assert "column not present" in one_check(dqm.check_registry, healthy_rows())["detail"]


# --------------------------------------------------------------------------- results
def test_results_fail_when_last_tick_is_six_hours_old_with_no_result():
    rows = healthy_rows()
    rows["results"] = [_match("old", age_s=7 * 3600.0, has_result=False, winner=None, result_type=None)]
    c = one_check(dqm.check_results, rows)
    assert c["status"] == "fail" and c["value"] == 1


def test_results_ok_for_a_recent_match_without_result_yet():
    rows = healthy_rows()
    rows["results"] = [_match("recent", age_s=3600.0, has_result=False, winner=None, result_type=None)]
    assert one_check(dqm.check_results, rows)["status"] == "ok"


def test_results_fail_on_a_win_with_empty_winner():
    rows = healthy_rows()
    rows["results"] = [_match("w", age_s=20000.0, has_result=True, winner="", result_type="win")]
    c = one_check(dqm.check_results, rows)
    assert c["status"] == "fail" and "empty winner" in c["detail"]


def test_results_ok_for_no_result_and_tie_without_winner():
    rows = healthy_rows()
    rows["results"] = [_match("nr", age_s=20000.0, has_result=True, winner="", result_type="no_result"),
                       _match("tie", age_s=20000.0, has_result=True, winner="", result_type="tie")]
    assert one_check(dqm.check_results, rows)["status"] == "ok"


def test_results_count_simulated_matches_too():
    # SRL results are recorded since S50, so a missing SRL result is a gap like any other
    rows = healthy_rows()
    rows["results"] = [_match("srl", "Hobart Hurricanes SRL", "Perth Scorchers", "",
                              age_s=90000.0, has_result=False, winner=None, result_type=None)]
    assert one_check(dqm.check_results, rows)["status"] == "fail"


# --------------------------------------------------------------------------- gaps
def test_gaps_warn_on_a_gap_over_five_minutes_inside_a_live_match():
    rows = healthy_rows()
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 420.0}]
    c = one_check(dqm.check_gaps, rows)
    assert c["status"] == "warn" and c["value"] == 420.0


def test_gaps_detail_names_the_match_with_the_longest_gap():
    rows = healthy_rows()
    rows["live_matches"] = [_match("m1", "India", "Australia", odds_rows=5, has_market=True, has_context=True)]
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 420.0}]
    c = one_check(dqm.check_gaps, rows)
    assert "India v Australia (m1)" in c["detail"]
    assert "None" not in c["detail"]


def test_gaps_count_srl_matches_too():
    # SRL is recorded from its match page like any match (S50), so its gaps count
    rows = healthy_rows()
    rows["live_matches"] = rows["live_matches"] + [_match("srl", "Hobart Hurricanes SRL", "Perth Scorchers SRL", "")]
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 120.0},
                    {"match_id": "srl", "max_gap_s": 3600.0}]
    assert one_check(dqm.check_gaps, rows)["status"] == "warn"


def test_gaps_before_the_live_heartbeat_are_reported_but_not_scored():
    rows = healthy_rows()
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 120.0, "max_gap_before_heartbeat_s": 9291.0}]
    c = one_check(dqm.check_gaps, rows)
    assert c["status"] == "ok" and "before the live heartbeat (not scored)" in c["detail"]



def test_a_gap_while_the_market_was_closed_is_reported_but_not_scored():
    """2026-11-03, lb_76227860: the site closed match odds at 05:10 and reopened at 05:25 (a stoppage)."""
    rows = healthy_rows()
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 120.0, "max_gap_before_heartbeat_s": None,
                     "max_halted_gap_s": 933.0}]
    c = one_check(dqm.check_gaps, rows)
    assert c["status"] == "ok" and "while the market was closed or suspended (not scored)" in c["detail"]


def test_gaps_sql_excuses_only_a_gap_after_a_closed_or_suspended_match_odds_row():
    sql = dqm.SQL["gaps"]
    assert "lower(m.status) IN ('closed', 'suspended')" in sql and "NOT after_halt" in sql

# --------------------------------------------------------------------------- SRL
def test_srl_is_counted_and_never_a_failure():
    rows = healthy_rows()
    rows["match_names"] = [_match(), _match("srl", "Hobart Hurricanes SRL", "Perth Scorchers", "", odds_rows=500)]
    c = one_check(dqm.check_srl, rows)
    assert c["status"] == "ok" and c["value"] == 1
    assert "500 odds_ticks rows" in c["detail"]


# --------------------------------------------------------------------------- exit codes and records
def test_any_fail_gives_exit_one_and_warn_only_gives_exit_two():
    rows = healthy_rows()
    rows["duplicates"] = [{"rows": 1000, "duplicates": 5}]
    warn_report = run(rows)
    assert warn_report["overall"] == "warn" and warn_report["exit_code"] == 2

    rows["odds_prices"] = [{"zero_prices": 1, "out_of_range": 0, "crossed": 0, "rows": 100}]
    fail_report = run(rows)
    assert fail_report["overall"] == "fail" and fail_report["exit_code"] == 1


def test_run_once_writes_one_json_line_and_returns_the_record(tmp_path, monkeypatch, capsys):
    log = tmp_path / "data_quality.jsonl"
    monkeypatch.setattr(dqm, "LOG_PATH", log)

    healthy = healthy_rows()

    async def fake_db_fetch(session, name, params):
        return [dict(r) for r in healthy[name]]

    monkeypatch.setattr(dqm, "_db_fetch", fake_db_fetch)
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", _write_cycle(tmp_path / "paper_trader.log", datetime.now(UTC) - timedelta(seconds=60)))

    class FakeSession:
        async def execute(self, statement, bound=None):
            return None

    class FakeCtx:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *exc):
            return False

    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "get_session", lambda: FakeCtx())
    record = asyncio.run(dqm.run_once(24))
    assert record["exit_code"] == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["overall"] == "ok"
    assert "overall: OK" in capsys.readouterr().out


def test_database_error_is_reported_as_a_failed_run(tmp_path, monkeypatch, capsys):
    import shared.db as shared_db

    log = tmp_path / "data_quality.jsonl"
    monkeypatch.setattr(dqm, "LOG_PATH", log)

    class BrokenCtx:
        async def __aenter__(self):
            raise ConnectionError("db down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(shared_db, "get_session", lambda: BrokenCtx())
    record = asyncio.run(dqm.run_once(24))
    assert record["overall"] == "fail" and record["exit_code"] == 1
    assert record["checks"][0]["name"] == "database"
    assert "db down" in json.loads(log.read_text(encoding="utf-8").splitlines()[0])["checks"][0]["detail"]


# --------------------------------------------------------------------------- recording_health (S55 SRL change)
def test_recording_health_simulated_rows_are_counted_not_exit_one():
    report = {"contamination": {"synthetic_rows": 0, "simulated_rows": 12, "examples": []}}
    feed = {"stale": False, "reasons": []}
    assert health.exit_code(report, feed) == health.EXIT_OK


def test_recording_health_synthetic_rows_still_exit_one():
    report = {"contamination": {"synthetic_rows": 3, "simulated_rows": 12, "examples": []}}
    feed = {"stale": True, "reasons": ["x"]}
    assert health.exit_code(report, feed) == health.EXIT_CONTAMINATED


def test_lay_completeness_does_not_score_ticks_from_before_match_pages_existed():
    rows = healthy_rows()
    rows["lay_completeness"] = [{"live_ticks": 100, "null_lay_ticks": 90, "recoverable": 40, "before_match_pages": 50}]
    c = one_check(dqm.check_lay_completeness, rows)
    assert c["status"] == "ok" and "50 recorded before match-page recording began" in c["detail"]


# --------------------------------------------------------------------------- S65 model_drift
def _drift_registry(tmp_path: Path, predictions: list[dict[str, Any]], baseline_values: list[float]) -> Path:
    from training.registry import Registry, distribution

    root = tmp_path / "registry"
    registry = Registry(root)
    pred_file = tmp_path / "predictions.jsonl"
    pred_file.write_text("".join(json.dumps(p) + "\n" for p in predictions), encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text("{}", encoding="utf-8")
    baseline = {"p_market": distribution(baseline_values), "p_model": distribution(baseline_values)}
    version = registry.register("blend", {"latest.json": latest, "predictions.jsonl": pred_file},
                                {"baseline": baseline, "gate_source": "blend_x"})
    registry.set_champion("blend", version, reason="test")
    return root


def _settled(n: int, *, home_won: int = 1, team_home: str = "India", team_away: str = "Australia") -> list[dict]:
    winner = team_home if home_won else team_away
    return [{"match_id": f"d{i}", "winner": winner, "team_home": team_home, "team_away": team_away}
            for i in range(n)]


def _preds(n: int, p: float) -> list[dict[str, Any]]:
    return [{"match_id": f"d{i}", "snapshot": f"d{i}:T-10m", "p_home": p, "p_market": p, "p_model": p,
             "date": "2026-11-01"} for i in range(n)]


def _drift(tmp_path: Path, root: Path | None, rows: list[dict]) -> dict[str, Any]:
    async def go():
        params = {"now": NOW, "since": NOW - timedelta(hours=24), "registry_root": root or tmp_path / "none"}
        return await dqm.check_model_drift(fake_fetch({"settled_recent": rows}), params)

    return asyncio.run(go())


def test_model_drift_is_ok_with_no_champion(tmp_path: Path):
    check = _drift(tmp_path, None, _settled(80))
    assert check["status"] == "ok" and "no champion" in check["detail"]


def test_model_drift_ok_when_calibrated_and_on_baseline(tmp_path: Path):
    root = _drift_registry(tmp_path, _preds(60, 0.5), [0.5] * 60)
    rows = [_settled(1, home_won=i % 2)[0] for i in range(60)]  # 30 home wins, 30 losses: mean p 0.5 is calibrated
    check = _drift(tmp_path, root, rows)
    assert check["status"] == "ok"
    assert check["value"] == pytest.approx(0.0)
    assert "60 settled match(es)" in check["detail"]


def test_model_drift_warns_but_never_fails_below_fifty_settled(tmp_path: Path):
    root = _drift_registry(tmp_path, _preds(20, 0.9), [0.9] * 20)
    check = _drift(tmp_path, root, _settled(20, home_won=0))  # 20 losses at p 0.9: a large calibration gap
    assert check["status"] == "warn"
    assert "enforced from 50" in check["detail"]


def test_model_drift_fails_on_a_large_gap_once_enforced(tmp_path: Path):
    root = _drift_registry(tmp_path, _preds(60, 0.9), [0.9] * 60)
    check = _drift(tmp_path, root, _settled(60, home_won=0))
    assert check["status"] == "fail"
    assert check["value"] == pytest.approx(0.9)


def test_model_drift_counts_unmatched_names_and_skips_unpredicted_matches(tmp_path: Path):
    root = _drift_registry(tmp_path, _preds(5, 0.5), [0.5] * 5)
    rows = _settled(5, home_won=1)
    rows.append({"match_id": "d0", "winner": "Somebody Else", "team_home": "India", "team_away": "Australia"})
    rows.append({"match_id": "unpredicted", "winner": "India", "team_home": "India", "team_away": "Australia"})
    check = _drift(tmp_path, root, rows)
    assert "1 result(s) with unmatched team names" in check["detail"]
    assert "5 settled match(es)" in check["detail"]


def test_model_drift_warns_when_the_champion_has_no_predictions_file(tmp_path: Path):
    from training.registry import Registry

    registry = Registry(tmp_path / "registry")
    latest = tmp_path / "latest.json"
    latest.write_text("{}", encoding="utf-8")
    version = registry.register("blend", {"latest.json": latest}, {"baseline": {}})
    registry.set_champion("blend", version, reason="test")
    check = _drift(tmp_path, tmp_path / "registry", _settled(3))
    assert check["status"] == "warn" and "no predictions artefact" in check["detail"]


def test_results_unresolved_row_of_a_match_still_being_recorded_is_provisional():
    rows = healthy_rows()
    rows["results"] = [_match("live", age_s=120.0, has_result=True, winner="", result_type="unresolved")]
    assert one_check(dqm.check_results, rows)["status"] == "ok"
    rows["results"] = [_match("gone", age_s=3 * 3600.0, has_result=True, winner="", result_type="unresolved")]
    assert one_check(dqm.check_results, rows)["status"] == "fail"


# --------------------------------------------------------------------------- paper_trading (S73)
def _fill(variant="w05_e03", match_id="m1", side="BACK", selection="home", price=2.0, stake=1000.0) -> dict[str, Any]:
    return {"variant": variant, "match_id": match_id, "side": side, "selection": selection,
            "price_filled": price, "stake": stake}


def test_paper_trading_ok_when_trader_is_fresh_and_positions_are_within_cap():
    rows = healthy_rows()
    rows["paper_open_fills"] = [
        _fill(selection="home", stake=90_000.0),
        _fill(selection="away", stake=90_000.0),  # both sides: the worst home/away outcome is 0
    ]
    check = by_name(run(rows))["paper_trading"]
    assert check["status"] == "ok"
    assert check["value"] == pytest.approx(0.0)


def test_paper_trading_fails_when_the_trader_log_is_stale_while_matches_are_live(tmp_path, monkeypatch):
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", _write_cycle(tmp_path / "t.log", NOW - timedelta(minutes=10)))
    check = by_name(run(healthy_rows()))["paper_trading"]
    assert check["status"] == "fail"
    assert "trader stale" in check["detail"]


def test_paper_trading_fails_without_any_trader_log_while_matches_are_live(tmp_path, monkeypatch):
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", tmp_path / "absent.log")
    check = by_name(run(healthy_rows()))["paper_trading"]
    assert check["status"] == "fail"
    assert "no trader log" in check["detail"]


def test_paper_trading_ignores_a_stale_trader_when_nothing_is_live(tmp_path, monkeypatch):
    monkeypatch.setattr(dqm, "PAPER_LOG_PATH", _write_cycle(tmp_path / "t.log", NOW - timedelta(hours=3)))
    rows = healthy_rows()
    rows["freshness"] = [{"odds_age_s": 5.0, "market_age_s": 30.0, "live_now": 0}]
    assert by_name(run(rows))["paper_trading"]["status"] == "ok"


def test_paper_trading_fails_when_one_match_is_beyond_the_hard_limit():
    """Budget 100,000 with 20% tolerance (operator, 2026-11-03): beyond 120,000 fails, 110,000 does not."""
    rows = healthy_rows()
    rows["paper_open_fills"] = [_fill(price=2.0, stake=150_000.0)]  # BACK home: away side loses 150,000
    check = by_name(run(rows))["paper_trading"]
    assert check["status"] == "fail"
    assert "worst-case beyond 120,000" in check["detail"]
    assert check["value"] == pytest.approx(150_000.0)
    rows["paper_open_fills"] = [_fill(price=2.0, stake=110_000.0)]
    assert by_name(run(rows))["paper_trading"]["status"] == "ok"


def test_paper_trading_cap_is_per_variant_and_per_match():
    rows = healthy_rows()
    rows["paper_open_fills"] = [
        _fill(variant="w03_e03", stake=80_000.0),
        _fill(variant="w05_e03", stake=80_000.0),  # same match, different variant: each is within the cap
        _fill(variant="w05_e03", match_id="m2", stake=80_000.0),
    ]
    assert by_name(run(rows))["paper_trading"]["status"] == "ok"


def test_paper_trading_fails_on_filled_bets_still_open_after_twelve_hours():
    rows = healthy_rows()
    rows["paper_stale_settlement"] = [
        {"variant": "w05_e03", "match_id": "m9", "bets": 2, "result_at": NOW - timedelta(hours=13)}
    ]
    check = by_name(run(rows))["paper_trading"]
    assert check["status"] == "fail"
    assert "still open over 12 h after the result" in check["detail"]


def test_paper_trading_queries_are_selects_over_the_ledger_and_results():
    for name in ("paper_open_fills", "paper_stale_settlement"):
        sql = dqm.SQL[name].strip().upper()
        assert sql.startswith("SELECT")
        assert "PAPER_BETS" in sql
