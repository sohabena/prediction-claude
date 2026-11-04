"""S103: the R1 benchmark. Closing-price selection and orientation, the verdict rule, the blend and ROI checks, the
``--export-predictions`` flag (test matches only) and the script's report. Synthetic data and tmp_path only: no
database, no network, no real training.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_historical_odds as script
from scripts import train_prematch_model as tpm
from strategy.prematch_model import FEATURE_NAMES, MatchFeatures
from training import historical_benchmark as hb
from training import historical_odds as ho
from training.dataset import CricsheetIndex

START = datetime(2025, 12, 14, 8, 15, tzinfo=timezone.utc)
HOME, AWAY = "Perth Scorchers", "Sydney Sixers"


def _record(**overrides) -> ho.OddsRecord:
    base = dict(
        source="oddspapi", source_event_id="ev-1", competition="Big Bash League", format="t20", gender="male",
        start_time_utc=START, home=HOME, away=AWAY, bookmaker="pinnacle", kind="close",
        price_home=1.80, price_away=2.10,
    )
    base.update(overrides)
    return ho.OddsRecord(**base)


def _index(teams, match_id="1410001", day="2025-12-14") -> CricsheetIndex:
    return CricsheetIndex([{"match_id": match_id, "teams": list(teams), "dates": [day], "match_type": "T20"}])


# --------------------------------------------------------------------------- closing prices
def test_closing_is_oriented_to_cricsheet_order_when_the_away_side_is_listed_first():
    record = _record()
    entry = hb.closing_by_match([record], _index([AWAY, HOME]))["1410001"]
    assert entry["reversed"] is True
    assert entry["home_team"] == AWAY and entry["away_team"] == HOME
    assert entry["p_home"] == pytest.approx(1.0 - ho.p_home_fair(record))
    assert entry["price_home"] == 2.10 and entry["price_away"] == 1.80


def test_closing_keeps_the_record_order_when_cricsheet_agrees():
    record = _record()
    entry = hb.closing_by_match([record], _index([HOME, AWAY]))["1410001"]
    assert entry["reversed"] is False
    assert entry["home_team"] == HOME
    assert entry["p_home"] == pytest.approx(ho.p_home_fair(record))


def test_a_record_that_does_not_link_gives_no_closing_price():
    assert hb.closing_by_match([_record()], _index(["Hobart Hurricanes", "Brisbane Heat"])) == {}


def test_pinnacle_beats_average_which_beats_other_bookmakers():
    index = _index([HOME, AWAY])
    other = _record(bookmaker="bet365", price_home=1.50, price_away=2.60)
    average = _record(bookmaker="average", kind="average", price_home=1.70, price_away=2.20)
    pinnacle = _record(bookmaker="pinnacle", price_home=1.85, price_away=2.05)
    cases = (([other, average, pinnacle], "pinnacle", 0), ([other, average], "average", 1), ([other], "bet365", 2))
    for records, expected, rank in cases:
        entry = hb.closing_by_match(records, index)["1410001"]
        assert entry["bookmaker"] == expected
        assert entry["bookmaker_rank"] == rank


def test_a_close_is_used_before_a_snapshot_and_the_last_snapshot_before_the_start_wins():
    index = _index([HOME, AWAY])
    early = _record(bookmaker="bet365", kind="snapshot", snapshot_time_utc=START - timedelta(hours=2),
                    price_home=1.60, price_away=2.40)
    late = _record(bookmaker="bet365", kind="snapshot", snapshot_time_utc=START - timedelta(minutes=5),
                   price_home=1.70, price_away=2.30)
    after = _record(bookmaker="bet365", kind="snapshot", snapshot_time_utc=START + timedelta(minutes=5),
                    price_home=1.90, price_away=2.00)
    entry = hb.closing_by_match([early, late, after], index)["1410001"]
    assert entry["kind"] == "snapshot"
    assert entry["price_home"] == 1.70
    close = _record(bookmaker="bet365", price_home=1.65, price_away=2.35)
    assert hb.closing_by_match([early, late, close], index)["1410001"]["kind"] == "close"


def test_a_snapshot_after_the_start_is_not_a_closing_price():
    index = _index([HOME, AWAY])
    after = _record(bookmaker="pinnacle", kind="snapshot", snapshot_time_utc=START + timedelta(minutes=1))
    other = _record(bookmaker="bet365", price_home=1.50, price_away=2.60)
    entry = hb.closing_by_match([after, other], index)["1410001"]
    assert entry["bookmaker"] == "bet365"


# --------------------------------------------------------------------------- verdict rule
def test_the_verdict_needs_200_matches_and_a_negative_upper_bound():
    assert hb.verdict(199, -0.5) == "too few matches"
    assert hb.verdict(200, -0.01) == "beats market"
    assert hb.verdict(200, 0.0) == "does not beat market"
    assert hb.verdict(500, float("nan")) == "does not beat market"


def _synthetic(n: int, seed: int, shrink: float, *, season: int = 2024, prefix: str = "c"):
    """True probabilities; the market is shrunk towards 0.5 (weaker); the model knows the truth."""
    rng = np.random.default_rng(seed)
    p_true = rng.uniform(0.15, 0.85, n)
    labels = (rng.random(n) < p_true).astype(int)
    preds, closing = [], {}
    for i in range(n):
        mid = f"{prefix}{i:05d}"
        p_market = 0.5 + shrink * (p_true[i] - 0.5)
        preds.append({
            "match_id": mid, "date": f"{season}-06-01", "season": season, "family": "t20", "gender": "male",
            "tier": 2, "home": HOME, "away": AWAY, "label": int(labels[i]), "p_v3": float(p_true[i]),
            "p_elo": 0.5, "fold_season": season,
        })
        closing[mid] = _closing(mid, float(p_market))
    return preds, closing


def _closing(mid: str, p_home: float, *, competition: str = "Big Bash League") -> dict:
    return {
        "cricsheet_match_id": mid, "p_home": p_home, "home_team": HOME, "away_team": AWAY,
        "price_home": 1.0 / p_home, "price_away": 1.0 / (1.0 - p_home), "bookmaker": "pinnacle",
        "bookmaker_rank": 0, "kind": "close", "competition": competition, "format": "t20", "gender": "male",
        "source": "oddspapi", "source_event_id": mid, "start_time_utc": START.isoformat(), "reversed": False,
        "day_delta": 0,
    }


def test_a_model_equal_to_the_market_has_a_ci_around_zero_and_no_verdict_of_beating_it():
    preds, closing = _synthetic(300, seed=3, shrink=1.0)
    report = hb.benchmark(preds, closing)
    diff = report["overall"]["log_loss_diff"]
    assert diff["ci_lower"] <= 0.0 <= diff["ci_upper"]
    assert abs(diff["estimate"]) < 1e-9
    assert report["overall"]["verdict"] == "does not beat market"


def test_a_model_that_beats_the_market_is_detected_with_enough_matches():
    preds, closing = _synthetic(600, seed=1, shrink=0.5)
    block = hb.benchmark(preds, closing)["overall"]
    assert block["n_matches"] == 600
    assert block["log_loss_diff"]["ci_upper"] < 0
    assert block["verdict"] == "beats market"


def test_under_200_matches_says_too_few_matches_even_when_the_interval_is_below_zero():
    preds, closing = _synthetic(150, seed=1, shrink=0.3)
    block = hb.benchmark(preds, closing)["overall"]
    assert block["log_loss_diff"]["ci_upper"] < 0
    assert block["n_matches"] == 150
    assert block["verdict"] == "too few matches"


def test_segments_are_reported_with_their_sizes_and_their_own_verdicts():
    preds, closing = _synthetic(600, seed=1, shrink=0.5)
    for i, pred in enumerate(preds):
        if i % 2:
            pred["gender"] = "female"
            closing[pred["match_id"]] = _closing(pred["match_id"], closing[pred["match_id"]]["p_home"],
                                                 competition="Women's Premier League")
    report = hb.benchmark(preds, closing)
    by_gender = report["segments"]["family_gender"]
    assert by_gender["t20 male"]["n_matches"] == 300 and by_gender["t20 female"]["n_matches"] == 300
    male = by_gender["t20 male"]
    assert male["verdict"] == hb.verdict(300, male["log_loss_diff"]["ci_upper"])
    assert set(report["segments"]["competition"]) == {"Big Bash League", "Women's Premier League"}


def test_a_prediction_with_the_teams_swapped_gets_the_flipped_market_probability():
    pred = {"match_id": "c1", "date": "2024-06-01", "season": 2024, "family": "t20", "gender": "male",
            "home": AWAY, "away": HOME, "label": 1, "p_v3": 0.6, "p_elo": 0.5}
    rows, counts = hb.join_matches([pred], {"c1": _closing("c1", 0.7)})
    assert counts["joined"] == 1
    assert rows[0]["p_market"] == pytest.approx(0.3)
    assert rows[0]["price_home"] == pytest.approx(1 / 0.3)


def test_teams_that_do_not_match_are_counted_not_joined():
    pred = {"match_id": "c1", "date": "2024-06-01", "season": 2024, "home": "Hobart Hurricanes",
            "away": AWAY, "label": 1, "p_v3": 0.6, "p_elo": 0.5}
    rows, counts = hb.join_matches([pred], {"c1": _closing("c1", 0.7)})
    assert rows == [] and counts["orientation_mismatch"] == 1


def test_predictions_without_a_closing_price_are_counted():
    pred = {"match_id": "c9", "date": "2024-06-01", "season": 2024, "home": HOME, "away": AWAY, "label": 1,
            "p_v3": 0.6, "p_elo": 0.5}
    rows, counts = hb.join_matches([pred, dict(pred)], {})
    assert rows == [] and counts["without_closing"] == 1 and counts["duplicate_predictions"] == 1


def test_a_predictions_file_missing_a_field_is_refused(tmp_path: Path):
    path = tmp_path / "preds.jsonl"
    path.write_text(json.dumps({"match_id": "c1", "season": 2024}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        hb.load_predictions(path)


# --------------------------------------------------------------------------- blend and ROI
def test_the_blend_is_predicted_season_by_season_and_needs_two_training_seasons():
    preds: list = []
    closing: dict = {}
    for season in (2021, 2022, 2023, 2024):
        p, c = _synthetic(80, seed=season, shrink=0.5, season=season, prefix=f"s{season}_")
        preds += p
        closing.update(c)
    blend = hb.benchmark(preds, closing)["blend"]
    assert blend["seasons_predicted"] == [2021, 2022, 2023, 2024]
    assert blend["n_matches"] == 320
    assert blend["log_loss_diff"]["estimate"] is not None

    two_p, two_c = [], {}
    for season in (2023, 2024):
        p, c = _synthetic(80, seed=season, shrink=0.5, season=season, prefix=f"t{season}_")
        two_p += p
        two_c.update(c)
    short = hb.benchmark(two_p, two_c)["blend"]
    assert short["seasons_predicted"] == [] and short["n_matches"] == 0
    assert short["verdict"] == "too few matches"


def _row(mid: str, p_model: float, p_market: float, label: int, price_home: float = 2.0,
         price_away: float = 2.0) -> dict:
    return {"match_id": mid, "p_model": p_model, "p_market": p_market, "label": label,
            "price_home": price_home, "price_away": price_away}


def test_roi_bets_only_when_the_probability_gap_passes_the_threshold():
    rows = [
        _row("a", 0.90, 0.50, 1),   # gap +0.40: back home, wins
        _row("b", 0.54, 0.50, 0),   # gap +0.04: back home, loses (only at the 3-point threshold)
        _row("c", 0.30, 0.50, 0),   # gap -0.20: back away at 2.0, wins
    ]
    at_three = hb.closing_roi(rows, 0.03)
    assert at_three["n_bets"] == 3 and at_three["back_home"] == 2 and at_three["back_away"] == 1
    assert at_three["roi_per_unit"] == pytest.approx(1 / 3)
    at_six = hb.closing_roi(rows, 0.06)
    assert at_six["n_bets"] == 2
    assert at_six["roi_per_unit"] == pytest.approx(1.0)
    assert "direction only" in at_six["note"]


# --------------------------------------------------------------------------- export flag
class _FakeRatings:
    rejected: dict = {}


def _synthetic_rows(n: int, seed: int) -> list:
    """Feature rows where the true probability is logistic in ``elo_diff`` alone (other features are noise)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        feats = {name: float(rng.normal()) for name in FEATURE_NAMES}
        feats["tier"] = float(rng.integers(0, 3))
        feats["male"] = 1.0
        p = 1 / (1 + np.exp(-4.0 * feats["elo_diff"]))
        season = 2000 + i // 400
        rows.append(MatchFeatures(
            match_id=f"s{i}", date=f"{season}-06-01", season=season, gender="male", tier=int(feats["tier"]),
            home="H", away="A", label=int(rng.random() < p), features=feats,
        ))
    return rows


def test_walk_forward_without_the_flag_has_no_test_predictions_and_with_it_has_only_test_matches():
    rows = _synthetic_rows(1600, seed=5)
    plain = tpm.walk_forward(rows, min_train=50, min_test=20)
    assert "test_predictions" not in plain
    exported = tpm.walk_forward(rows, min_train=50, min_test=20, export_predictions=True)
    assert json.dumps(plain["folds"], sort_keys=True) == json.dumps(exported["folds"], sort_keys=True)
    test = exported["test_predictions"]
    assert {p["season"] for p in test} == {2003} and {p["fold_season"] for p in test} == {2003}
    assert {p["match_id"] for p in test} == {r.match_id for r in rows if r.season == 2003}
    assert all(0.0 < p["p_v3"] < 1.0 and 0.0 < p["p_elo"] < 1.0 for p in test)


def test_the_export_flag_writes_test_matches_only_and_the_report_is_unchanged_without_it(tmp_path, monkeypatch):
    rows = _synthetic_rows(1600, seed=5)
    monkeypatch.setattr(tpm, "CricketRatings", lambda *a, **k: _FakeRatings())
    monkeypatch.setattr(tpm, "build_feature_rows", lambda ratings, gender=None: rows)
    export = tmp_path / "preds" / "t20.jsonl"

    def run(export_path, out_dir):
        args = argparse.Namespace(
            family="t20", gender="all", data_root=str(tmp_path / "data"), out_dir=str(out_dir),
            min_train=50, min_test=20, seed=0, export_predictions=export_path,
        )
        return tpm.run(args)

    report = run(str(export), tmp_path / "models")
    lines = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    assert lines and all(line["family"] == "t20" and line["season"] == 2003 for line in lines)
    assert not any(line["season"] < 2003 for line in lines)
    assert report["predictions_export"] == str(export)
    assert "test_predictions" not in report

    plain_dir = tmp_path / "plain"
    plain = run(None, plain_dir)
    assert "predictions_export" not in plain
    assert list(plain_dir.glob("*.jsonl")) == []
    assert (plain_dir / "t20_v3_walkforward.json").exists()


# --------------------------------------------------------------------------- the script
def test_the_script_writes_the_report_and_says_too_few_matches(tmp_path: Path):
    data = tmp_path / "data"
    (data / "raw" / "all").mkdir(parents=True)
    (data / "raw" / "all" / "1410001.json").write_text(json.dumps({"info": {
        "teams": [AWAY, HOME], "dates": ["2025-12-14"], "match_type": "T20",
    }}), encoding="utf-8")
    root = tmp_path / "historical"
    ho.write_store("oddspapi", [_record()], provenance="test fixture", root=root)
    preds = tmp_path / "preds.jsonl"
    preds.write_text(json.dumps({
        "family": "t20", "match_id": "1410001", "date": "2025-12-14", "season": 2025, "gender": "male",
        "tier": 2, "home": HOME, "away": AWAY, "label": 1, "p_v3": 0.6, "p_elo": 0.55, "fold_season": 2025,
    }) + "\n", encoding="utf-8")
    out = tmp_path / "reports"
    script.main(["--predictions", str(preds), "--root", str(root), "--out", str(out), "--data-root", str(data)])

    dirs = list(out.glob("r1-*"))
    assert len(dirs) == 1
    report = json.loads((dirs[0] / "report.json").read_text(encoding="utf-8"))
    assert report["matches"]["joined"] == 1
    assert report["overall"]["n_matches"] == 1
    assert report["overall"]["verdict"] == "too few matches"
    assert report["sources"]["oddspapi"]["records"] == 1
    text = (dirs[0] / "report.md").read_text(encoding="utf-8")
    assert "too few matches" in text and "Blend" in text and "Caveats" in text
