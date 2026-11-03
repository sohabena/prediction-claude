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
PARAM_NAMES = {"now", "since", "min_odds", "max_odds", "tie_seconds"}


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
        "results": [_match("m0", age_s=7200.0, has_result=True, winner="India", result_type="win")],
        "misattribution_ticks": [],
    }


def fake_fetch(rows: dict[str, list[dict[str, Any]]]):
    async def fetch(name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert name in rows, f"unexpected query {name}"
        return [dict(r) for r in rows[name]]

    return fetch


def run(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return asyncio.run(dqm.run_checks(fake_fetch(rows), now=NOW, window_hours=24))


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
def test_score_coverage_warns_when_a_small_share_has_no_score_state():
    rows = healthy_rows()
    rows["live_matches"] = [_match(f"m{i}", has_market=True, has_context=True) for i in range(19)]
    rows["live_matches"].append(_match("gap", has_market=True, has_context=False))
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "warn"
    assert c["value"] == pytest.approx(0.05)


def test_score_coverage_only_warns_when_half_the_match_pages_have_no_score_state():
    # Known source limit (S51): the scoreboard does not render headless, so this never fails
    rows = healthy_rows()
    rows["live_matches"] = [_match("a", has_market=True, has_context=True),
                            _match("b", has_market=True, has_context=False)]
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "warn" and c["value"] == pytest.approx(0.5)


def test_score_coverage_only_warns_when_most_match_pages_have_no_score_state():
    rows = healthy_rows()
    rows["live_matches"] = [_match(f"m{i}", has_market=True, has_context=False) for i in range(5)]
    rows["live_matches"].append(_match("ok", has_market=True, has_context=True))
    c = one_check(dqm.check_score_coverage, rows)
    assert c["status"] == "warn"


def test_score_coverage_ok_when_every_match_page_has_context():
    assert one_check(dqm.check_score_coverage, healthy_rows())["status"] == "ok"


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


def test_results_fail_on_empty_winner():
    rows = healthy_rows()
    rows["results"] = [_match("nr", age_s=20000.0, has_result=True, winner="", result_type="no_result")]
    c = one_check(dqm.check_results, rows)
    assert c["status"] == "fail" and "empty winner" in c["detail"]


def test_results_ignore_simulated_matches():
    rows = healthy_rows()
    rows["results"] = [_match("srl", "Hobart Hurricanes SRL", "Perth Scorchers", "",
                              age_s=90000.0, has_result=False, winner=None, result_type=None)]
    assert one_check(dqm.check_results, rows)["status"] == "ok"


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


def test_gaps_ok_with_short_gaps_and_ignore_simulated_matches():
    rows = healthy_rows()
    rows["gaps"] = [{"match_id": "m1", "max_gap_s": 120.0},
                    {"match_id": "srl", "max_gap_s": 3600.0}]
    assert one_check(dqm.check_gaps, rows)["status"] == "ok"


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
