"""S24: in-house chronological Cricket Elo engine (shared/elo.py + scripts/compute_cricsheet_elo.py)."""

from __future__ import annotations

import json
import math
import random
from typing import Any, Optional

import pytest

from scripts.compute_cricsheet_elo import (
    classify_info,
    discover_files,
    load_records,
    run_elo,
    top_teams,
)
from shared.elo import (
    MOV_MAX,
    MOV_MIN,
    CricketEloTracker,
    family_for_match_type,
    margin_multiplier,
)


def _match(
    mid: str,
    date: str,
    a: str,
    b: str,
    *,
    mt: str = "ODI",
    gender: str = "male",
    winner: Optional[str] = None,
    by: Optional[dict[str, Any]] = None,
    result: Optional[str] = None,
) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    if winner:
        outcome["winner"] = winner
    if by:
        outcome["by"] = by
    if result:
        outcome["result"] = result
    return {"match_id": mid, "dates": [date], "teams": [a, b], "match_type": mt, "gender": gender, "outcome": outcome}


# ---------------------------------------------------------------- mapping and MOV


class TestFamilyAndMargin:
    def test_match_type_mapping(self):
        assert family_for_match_type("T20") == "t20"
        assert family_for_match_type("IT20") == "t20"
        assert family_for_match_type("ODI") == "odi"
        assert family_for_match_type("ODM") == "odi"  # not multiday: strategy.family_of has this bug
        assert family_for_match_type("Test") == "multiday"
        assert family_for_match_type("MDM") == "multiday"
        assert family_for_match_type("Other") is None
        assert family_for_match_type(None) is None

    def test_margin_multiplier_values_and_bounds(self):
        assert margin_multiplier({"by": {"runs": 1}}) == MOV_MIN
        assert margin_multiplier({"by": {"runs": 92}}) == pytest.approx(math.log(1 + 92 / 25))
        assert margin_multiplier({"by": {"runs": 500}}) == MOV_MAX
        assert margin_multiplier({"by": {"wickets": 7}}) == pytest.approx(math.log(1 + 7 / 2))
        assert margin_multiplier({"by": {"innings": 1, "runs": 40}}) == MOV_MAX
        assert margin_multiplier({"method": "Awarded"}) == 1.0


# ---------------------------------------------------------------- rating updates


class TestRatingUpdates:
    def test_known_outcome_values(self):
        tr = CricketEloTracker()
        # T20, equal teams, 1-run win: MOV clamps to 0.8, E = 0.5, K = 32 -> delta 12.8.
        assert tr.update(_match("m1", "2024-01-01", "A", "B", mt="T20", winner="A", by={"runs": 1})) is True
        assert tr.rating("A", "t20", "male", "2024-01-02") == pytest.approx(1512.8)
        assert tr.rating("B", "t20", "male", "2024-01-02") == pytest.approx(1487.2)

    def test_expected_matches_logistic_formula(self):
        tr = CricketEloTracker()
        tr.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 1}))
        ra, rb = 1500 + 24 * 0.8 * 0.5, 1500 - 24 * 0.8 * 0.5
        expected_p = 1 / (1 + 10 ** ((rb - ra) / 400))
        assert tr.expected("A", "B", "odi", "male", "2024-06-01") == pytest.approx(expected_p)
        assert tr.expected("B", "A", "odi", "male", "2024-06-01") == pytest.approx(1 - expected_p)

    def test_dominant_win_moves_more_than_one_run_win(self):
        narrow = CricketEloTracker()
        narrow.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 1}))
        dominant = CricketEloTracker()
        dominant.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 92}))
        d_narrow = narrow.rating("A", "odi", "male", "2024-02-01") - 1500
        d_dominant = dominant.rating("A", "odi", "male", "2024-02-01") - 1500
        assert d_dominant > d_narrow > 0

    def test_draw_and_tie_score_half(self):
        tr = CricketEloTracker()
        tr.update(_match("d1", "2024-01-01", "A", "B", mt="Test", result="draw"))
        assert tr.rating("A", "multiday", "male", "2024-02-01") == pytest.approx(1500.0)
        assert tr.rating("B", "multiday", "male", "2024-02-01") == pytest.approx(1500.0)

        tr.update(_match("t1", "2024-01-02", "A", "C", mt="T20", result="tie"))
        # Equal ratings + tie = no change to the expectation; both move by 0 here.
        assert tr.rating("A", "t20", "male", "2024-02-01") == pytest.approx(1500.0)

    def test_no_result_leaves_state_untouched(self):
        tr = CricketEloTracker()
        assert tr.update(_match("n1", "2024-01-01", "A", "B", result="no result")) is False
        assert tr.history() == []
        assert tr.current_ratings() == []
        assert tr.rating("A", "odi", "male", "2024-02-01") == 1500.0


# ---------------------------------------------------------------- zero lookahead


class TestZeroLookahead:
    def test_updating_match_n_does_not_change_its_pre_match_elo(self):
        tr = CricketEloTracker()
        tr.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 30}))
        pre_n = tr.rating("A", "odi", "male", "2024-01-10")  # pre-match for match N on 2024-01-10
        pre_n_b = tr.rating("B", "odi", "male", "2024-01-10")
        tr.update(_match("m2", "2024-01-10", "A", "B", winner="B", by={"wickets": 3}))
        assert tr.rating("A", "odi", "male", "2024-01-10") == pytest.approx(pre_n)
        assert tr.rating("B", "odi", "male", "2024-01-10") == pytest.approx(pre_n_b)
        # Later matches do not leak back into the earlier as-of query either.
        tr.update(_match("m3", "2024-02-01", "A", "B", winner="A", by={"runs": 90}))
        assert tr.rating("A", "odi", "male", "2024-01-10") == pytest.approx(pre_n)

    def test_match_never_sees_its_own_result(self):
        tr = CricketEloTracker()
        tr.update(_match("m1", "2024-03-05", "A", "B", winner="A", by={"runs": 200}))
        # As of the match date itself, the result is excluded.
        assert tr.rating("A", "odi", "male", "2024-03-05") == 1500.0
        assert tr.expected("A", "B", "odi", "male", "2024-03-05") == pytest.approx(0.5)
        assert tr.rating("A", "odi", "male", "2024-03-06") > 1500.0

    def test_unseen_team_and_pre_history_return_base(self):
        tr = CricketEloTracker()
        assert tr.rating("Nobody", "t20", "female", "2030-01-01") == 1500.0
        tr.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 5}))
        assert tr.rating("A", "odi", "male", "2023-12-31") == 1500.0


# ---------------------------------------------------------------- isolation and validation


class TestIsolationAndValidation:
    def test_format_isolation_t20_does_not_touch_test_elo(self):
        tr = CricketEloTracker()
        tr.update(_match("t1", "2024-01-01", "A", "B", mt="T20", winner="A", by={"runs": 40}))
        assert tr.rating("A", "multiday", "male", "2025-01-01") == 1500.0
        assert tr.rating("A", "odi", "male", "2025-01-01") == 1500.0
        assert tr.rating("A", "t20", "male", "2025-01-01") > 1500.0

    def test_gender_isolation(self):
        tr = CricketEloTracker()
        tr.update(_match("w1", "2024-01-01", "A", "B", mt="T20", gender="female", winner="A", by={"runs": 40}))
        assert tr.rating("A", "t20", "male", "2025-01-01") == 1500.0
        assert tr.rating("A", "t20", "female", "2025-01-01") > 1500.0

    def test_rejects_out_of_order_duplicate_and_invalid_matches(self):
        tr = CricketEloTracker()
        tr.update(_match("m2", "2024-02-01", "A", "B", winner="A", by={"runs": 10}))
        before = tr.history()
        with pytest.raises(ValueError, match="date order"):
            tr.update(_match("m1", "2024-01-01", "A", "B", winner="A", by={"runs": 10}))
        with pytest.raises(ValueError, match="duplicate"):
            tr.update(_match("m2", "2024-03-01", "A", "B", winner="A", by={"runs": 10}))
        with pytest.raises(ValueError, match="unsupported match_type"):
            tr.update(_match("m3", "2024-03-01", "A", "B", mt="Other", winner="A"))
        with pytest.raises(ValueError, match="not one of the teams"):
            tr.update(_match("m4", "2024-03-01", "A", "B", winner="C"))
        with pytest.raises(ValueError, match="two distinct teams"):
            tr.update(_match("m5", "2024-03-01", "A", "A", winner="A"))
        assert tr.history() == before

    def test_team_name_normalizer_is_applied(self):
        tr = CricketEloTracker(normalize_team=lambda n: {"Ind": "India"}.get(n.strip(), n.strip()))
        tr.update(_match("m1", "2024-01-01", "Ind", "Aus", winner="Ind", by={"runs": 60}))
        assert tr.rating("India", "odi", "male", "2024-02-01") == tr.rating("Ind", "odi", "male", "2024-02-01")
        assert tr.rating("India", "odi", "male", "2024-02-01") > 1500.0

    def test_save_and_load_round_trip(self, tmp_path):
        tr = CricketEloTracker()
        rng = random.Random(7)
        teams = ["A", "B", "C", "D"]
        for i in range(30):
            a, b = rng.sample(teams, 2)
            tr.update(_match(f"m{i}", f"2024-01-{i + 1:02d}", a, b, winner=a, by={"runs": rng.randint(1, 150)}))
        path = tmp_path / "elo.json.gz"
        tr.save(path)
        loaded = CricketEloTracker.load(path)
        assert loaded.current_ratings() == tr.current_ratings()
        assert loaded.history() == tr.history()
        assert loaded.rating("A", "odi", "male", "2024-01-15") == tr.rating("A", "odi", "male", "2024-01-15")


# ---------------------------------------------------------------- script layer


class TestScriptLayer:
    def test_classify_skips_with_reasons_and_maps_names(self):
        base = {"dates": ["2024-01-01"], "teams": ["India", "Foo XI"], "match_type": "ODI", "gender": "male"}
        assert classify_info("x1", {**base, "outcome": {"result": "no result"}}, "s").skip_reason == "no_result"
        assert classify_info("x2", {**base, "match_type": "Weird", "outcome": {}}, "s").skip_reason == "unsupported_match_type"
        assert classify_info("x3", {**base, "gender": "", "outcome": {}}, "s").skip_reason == "unknown_gender"
        assert classify_info("x4", {**base, "dates": [], "outcome": {}}, "s").skip_reason == "no_date"
        assert classify_info("x5", {**base, "outcome": {"winner": "Pakistan"}}, "s").skip_reason == "winner_not_in_teams"
        ok = classify_info("x6", {**base, "outcome": {"winner": "India", "by": {"runs": 9}}}, "s")
        assert ok.skip_reason is None and ok.match_info is not None
        assert ok.match_info["match_id"] == "x6"

    def test_run_elo_orders_by_date_and_applies_limit(self):
        raw = [
            classify_info("m2", {"dates": ["2024-02-01"], "teams": ["A", "B"], "match_type": "T20", "gender": "male",
                                 "outcome": {"winner": "B", "by": {"wickets": 4}}}, "s"),
            classify_info("m1", {"dates": ["2024-01-01"], "teams": ["A", "B"], "match_type": "T20", "gender": "male",
                                 "outcome": {"winner": "A", "by": {"runs": 20}}}, "s"),
            classify_info("m3", {"dates": ["2024-03-01"], "teams": ["A", "B"], "match_type": "T20", "gender": "male",
                                 "outcome": {"result": "no result"}}, "s"),
        ]
        shuffled = list(raw)
        random.Random(1).shuffle(shuffled)
        res = run_elo(shuffled)
        assert res.processed == 2
        assert res.skipped["no_result"] == 1
        assert res.processed_by_group[("t20", "male")] == 2
        ordered = run_elo(raw)
        assert res.tracker.history() == ordered.tracker.history()
        limited = run_elo(raw, limit=1)
        assert limited.processed == 1 and limited.selected == 1

    def test_load_records_dedupes_and_flags_unreadable(self, tmp_path):
        raw = tmp_path / "raw"
        (raw / "odis").mkdir(parents=True)
        (raw / "tests").mkdir(parents=True)
        info = {"dates": ["2024-01-01"], "teams": ["A", "B"], "match_type": "ODI", "gender": "male",
                "outcome": {"winner": "A", "by": {"runs": 10}}}
        (raw / "odis" / "1.json").write_text(json.dumps({"info": info}), encoding="utf-8")
        (raw / "odis" / "bad.json").write_text("{not json", encoding="utf-8")
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        (incoming / "1.json").write_text(json.dumps({"info": info}), encoding="utf-8")  # duplicate id
        paths = discover_files(raw, incoming)
        records, skipped = load_records(paths)
        assert [r.match_id for r in records] == ["1"]
        assert skipped["unreadable"] == 1
        assert skipped["duplicate_match_id"] == 1

    def test_top_teams_limits_and_groups(self):
        tr = CricketEloTracker()
        for i in range(6):
            tr.update(_match(f"m{i}", f"2024-01-{i + 1:02d}", f"T{i}", "Base", winner=f"T{i}", by={"runs": 10 + i}))
        groups = top_teams(tr, top_n=5)
        assert list(groups) == ["odi|male"]
        rows = groups["odi|male"]
        assert len(rows) == 5
        assert all(rows[i]["elo"] >= rows[i + 1]["elo"] for i in range(len(rows) - 1))
