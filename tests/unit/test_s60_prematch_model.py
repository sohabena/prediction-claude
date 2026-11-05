"""S60: pre-match strength model v3 on small synthetic match lists."""
from __future__ import annotations

import copy
import dataclasses
import random
from datetime import date, timedelta
from typing import Any, Dict, List

import numpy as np
import pytest

from scripts.train_prematch_model import walk_forward
from strategy.prematch_model import (
    APP_WINDOW,
    CANDIDATES,
    FEATURE_NAMES,
    PROB_CLIP,
    AppearanceLedger,
    MatchFeatures,
    PrematchModel,
    _cross_fit_log_loss,
    _fit_candidate,
    _Platt,
    _predict_candidate,
    build_feature_rows,
    elo_probability,
    feature_matrix,
    feature_vector,
    rest_feature,
)
from strategy.ratings import CricketRatings

TEAMS = ("Alpha", "Bravo", "Charlie", "Delta")
VENUES = ("Ground One", "Ground Two")


def _raw(mid: str, when: date, teams: tuple, winner: str | None, *, gender: str = "male",
         match_type: str = "T20", result: str | None = None, runs: int = 40) -> Dict[str, Any]:
    """A minimal Cricsheet-shaped match: squads, one over per innings, and an outcome."""
    outcome: Dict[str, Any] = {}
    if winner:
        outcome["winner"] = winner
        outcome["by"] = {"runs": 10}
    if result:
        outcome["result"] = result
    t0, t1 = teams
    squads = {t: [f"{t} P{i}" for i in range(11)] for t in teams}
    innings = [
        {"team": t0, "overs": [{"over": 0, "deliveries": [
            {"batter": f"{t0} P0", "bowler": f"{t1} P0", "runs": {"batter": runs, "total": runs}}]}]},
        {"team": t1, "overs": [{"over": 0, "deliveries": [
            {"batter": f"{t1} P0", "bowler": f"{t0} P0", "runs": {"batter": runs // 2, "total": runs // 2}}]}]},
    ]
    return {
        "match_id": mid,
        "info": {
            "match_type": match_type, "gender": gender, "dates": [when.isoformat()], "teams": list(teams),
            "venue": VENUES[sum(map(ord, mid)) % 2], "city": "", "team_type": "international",
            "outcome": outcome, "toss": {"winner": t0, "decision": "bat"}, "players": squads, "overs": 20,
        },
        "innings": innings,
    }


def _season(year: int, n: int, rng: random.Random, start_id: int) -> List[Dict[str, Any]]:
    strength = {"Alpha": 0.8, "Bravo": 0.3, "Charlie": -0.3, "Delta": -0.8}
    out = []
    for i in range(n):
        a, b = rng.sample(TEAMS, 2)
        when = date(year, 1, 1) + timedelta(days=rng.randrange(0, 360))
        p_a = 1 / (1 + np.exp(-(strength[a] - strength[b])))
        winner = a if rng.random() < p_a else b
        out.append(_raw(f"m{start_id + i:05d}", when, (a, b), winner))
    return out


def _dataset(seed: int = 3) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    raws: List[Dict[str, Any]] = []
    for year in (2016, 2017, 2018, 2019):
        raws += _season(year, 60, rng, len(raws))
    return raws


def _rows(raws: List[Dict[str, Any]], **kw: Any) -> List[MatchFeatures]:
    return build_feature_rows(CricketRatings(raws), **kw)


def _synthetic_rows(n: int, seed: int) -> List[MatchFeatures]:
    """Feature rows where the true probability is logistic in ``elo_diff`` alone (other features are noise)."""
    rng = np.random.default_rng(seed)
    rows: List[MatchFeatures] = []
    for i in range(n):
        feats = {name: float(rng.normal()) for name in FEATURE_NAMES}
        feats["tier"] = float(rng.integers(0, 3))
        feats["male"] = float(rng.integers(0, 2))
        p = 1 / (1 + np.exp(-4.0 * feats["elo_diff"]))
        label = int(rng.random() < p)
        season = 2000 + i // 400
        rows.append(MatchFeatures(
            match_id=f"s{i}", date=f"{season}-06-01", season=season, gender="male", tier=int(feats["tier"]),
            home="H", away="A", label=label, features=feats,
        ))
    return rows


# ---------------------------------------------------------------- strictly as-of features
def test_features_use_only_matches_before_the_match_date():
    base = _dataset()
    cut = date(2018, 6, 1)
    # Same matches, but every match dated on or after the cut gets a different winner and different scores.
    altered = copy.deepcopy(base)
    for raw in altered:
        when = date.fromisoformat(raw["info"]["dates"][0])
        if when >= cut:
            t0, t1 = raw["info"]["teams"]
            flipped = t1 if raw["info"]["outcome"].get("winner") == t0 else t0
            raw["info"]["outcome"] = {"winner": flipped, "by": {"wickets": 9}}
            raw["innings"][0]["overs"][0]["deliveries"][0]["runs"] = {"batter": 150, "total": 150}
            raw["innings"][1]["overs"][0]["deliveries"][0]["wickets"] = [{"kind": "bowled"}]

    before = {r.match_id: r.features for r in _rows(base)}
    after = {r.match_id: r.features for r in _rows(altered)}
    checked = 0
    for mid, feats in before.items():
        d = date.fromisoformat(_date_of(base, mid))
        if d >= cut:
            continue
        checked += 1
        assert after[mid] == feats, f"features of {mid} ({d}) changed when later matches changed"
    assert checked > 100


def test_same_day_match_is_not_visible():
    raws = _dataset()
    day = date(2019, 3, 3)
    raws.append(_raw("zz_same_day", day, ("Alpha", "Bravo"), "Alpha"))
    raws.append(_raw("zz_target", day, ("Charlie", "Delta"), "Charlie"))
    before = {r.match_id: r.features for r in _rows(raws)}
    raws_changed = copy.deepcopy(raws)
    for raw in raws_changed:
        if raw["match_id"] == "zz_same_day":
            raw["info"]["outcome"] = {"winner": "Bravo", "by": {"runs": 90}}
    after = {r.match_id: r.features for r in _rows(raws_changed)}
    assert after["zz_target"] == before["zz_target"]


def _date_of(raws: List[Dict[str, Any]], mid: str) -> str:
    return next(r["info"]["dates"][0] for r in raws if r["match_id"] == mid)


def test_appearance_ledger_is_strict_and_windowed():
    led = AppearanceLedger()
    key = ("t20", "male", "P")
    for i in range(APP_WINDOW + 5):
        led.add(key, f"2020-01-{i + 1:02d}", 1.0 if i < 5 else 3.0)
    led.freeze()
    # Strictly before the query date: the appearance on the date itself does not count.
    assert led.strength(key, "2020-01-01") == 0.0
    full = led.strength(key, "2020-02-01")
    window = [3.0] * APP_WINDOW  # the last APP_WINDOW appearances are all 3.0
    assert full == pytest.approx(sum(window) / (APP_WINDOW + 2.0))
    assert led.strength(("t20", "male", "unknown"), "2020-02-01") == 0.0


def test_rest_feature_is_capped_and_monotone():
    assert rest_feature(None) == pytest.approx(rest_feature(10_000))
    assert rest_feature(2) < rest_feature(10) < rest_feature(60)


# ---------------------------------------------------------------- labels
def test_ties_and_no_results_are_excluded():
    raws = _dataset()
    raws.append(_raw("zz_tie", date(2019, 5, 1), ("Alpha", "Bravo"), None, result="tie"))
    raws.append(_raw("zz_nr", date(2019, 5, 2), ("Charlie", "Delta"), None, result="no result"))
    labelled = {r.match_id for r in _rows(raws)}
    assert "zz_tie" not in labelled
    assert "zz_nr" not in labelled
    assert len(labelled) == len(_dataset())


def test_gender_filter_keeps_only_that_gender():
    raws = _dataset()
    raws.append(_raw("zz_w", date(2019, 5, 1), ("Alpha", "Bravo"), "Alpha", gender="female"))
    rows = _rows(raws, gender="female")
    assert [r.match_id for r in rows] == ["zz_w"]
    assert len(_rows(raws)) == len(_dataset()) + 1


# ---------------------------------------------------------------- features and baseline
def test_feature_vector_neutral_defaults_and_order():
    vec = feature_vector({"elo_diff": 0.5})
    assert vec[FEATURE_NAMES.index("elo_diff")] == 0.5
    assert vec[FEATURE_NAMES.index("tier")] == 2.0
    assert len(vec) == len(FEATURE_NAMES)


def test_pure_elo_baseline_matches_formula():
    feats = {"elo_diff": 0.25}
    assert elo_probability(feats) == pytest.approx(1 / (1 + 10 ** (-100 / 400)))
    assert elo_probability({"elo_diff": 0.0}) == pytest.approx(0.5)


# ---------------------------------------------------------------- model
def test_save_load_round_trip(tmp_path):
    rows = _synthetic_rows(1200, seed=1)
    model = PrematchModel("t20").fit(rows[:400], rows[400:])
    path = tmp_path / "t20_v3.joblib"
    model.save(path)
    loaded = PrematchModel.load(path)
    X = feature_matrix(rows[800:])
    np.testing.assert_allclose(model.predict_matrix(X), loaded.predict_matrix(X))
    assert loaded.predict_home(rows[900].features) == pytest.approx(model.predict_home(rows[900].features))
    p = loaded.predict_home(rows[0].features)
    assert 0.01 <= p <= 0.99


def test_stacker_is_at_least_as_good_as_its_parts_on_a_constructed_case():
    rows = _synthetic_rows(4000, seed=7)
    train, calib, test = rows[:2000], rows[2000:2800], rows[2800:]
    model = PrematchModel("t20", seed=0).fit(train, calib)
    X_test = feature_matrix(test)
    y = np.asarray([r.label for r in test], dtype=int)
    parts = model.predict_parts(X_test)
    stacked = model.predict_matrix(X_test)

    def ll(p: np.ndarray) -> float:
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    assert ll(stacked) <= min(ll(parts["logistic"]), ll(parts["gbm"])) + 0.01


def _relabel(rows, fn):
    return [dataclasses.replace(r, label=fn(r)) for r in rows]


def test_fit_needs_both_outcomes_in_train_and_each_calibration_season():
    rows = _synthetic_rows(1200, seed=2)
    train = _relabel([r for r in rows if r.season == 2000], lambda r: 0)
    calib = [r for r in rows if r.season in (2001, 2002)]
    with pytest.raises(ValueError, match="both outcomes"):
        PrematchModel("t20").fit(train, calib)
    train_ok = [r for r in rows if r.season == 2000]
    calib_bad = _relabel(calib, lambda r: 1 if r.season == 2001 else r.label)
    with pytest.raises(ValueError, match="calibration season 2001"):
        PrematchModel("t20").fit(train_ok, calib_bad)


def test_calibration_window_must_span_two_seasons_and_follow_training():
    rows = _synthetic_rows(1200, seed=4)
    with pytest.raises(ValueError, match="two seasons"):
        PrematchModel("t20").fit([r for r in rows if r.season == 2000], [r for r in rows if r.season == 2001])
    with pytest.raises(ValueError, match="precede"):
        PrematchModel("t20").fit([r for r in rows if r.season >= 2001], [r for r in rows if r.season in (2001, 2002)])


# ---------------------------------------------------------------- selection and Platt
def test_selection_is_the_lowest_calibration_score_and_is_recorded():
    rows = _synthetic_rows(1200, seed=5)
    model = PrematchModel("t20").fit([r for r in rows if r.season == 2000], [r for r in rows if r.season in (2001, 2002)])
    assert set(model.selection_scores) == set(CANDIDATES)
    best = min(model.selection_scores.values())
    assert model.selection_scores[model.selected] == pytest.approx(best)
    assert model.selected == min(CANDIDATES, key=lambda n: (model.selection_scores[n], CANDIDATES.index(n)))
    assert model.predict_matrix(feature_matrix(rows[:50])).shape == (50,)


def test_selection_never_looks_at_the_test_season():
    rows = _synthetic_rows(2400, seed=6)
    base = walk_forward(rows, min_train=200, min_test=30)
    # Scramble only the labels of season 2004. Season 2004 is the test season of fold 2004 and must not be
    # used to choose that fold's candidate or to score its calibration.
    scrambled = _relabel(rows, lambda r: (1 - r.label) if r.season == 2004 else r.label)
    other = walk_forward(scrambled, min_train=200, min_test=30)
    fold = {f["season"]: f for f in base["folds"]}[2004]
    fold_other = {f["season"]: f for f in other["folds"]}[2004]
    assert fold_other["selected"] == fold["selected"]
    assert fold_other["selection_scores"] == fold["selection_scores"]
    assert fold_other["calibration_seasons"] == [2002, 2003]
    # Test metrics do change, which shows the scramble did reach the test season itself.
    assert fold_other["metrics"]["v3"]["log_loss"] != pytest.approx(fold["metrics"]["v3"]["log_loss"])


def test_platt_recovers_a_known_slope_and_intercept():
    rng = np.random.default_rng(11)
    z = rng.normal(size=20000)
    y = (rng.random(20000) < 1 / (1 + np.exp(-(2.0 * z - 0.5)))).astype(int)
    platt = _Platt().fit(z, y)
    assert platt._lr.coef_[0][0] == pytest.approx(2.0, abs=0.1)
    assert platt._lr.intercept_[0] == pytest.approx(-0.5, abs=0.1)
    p = platt.predict(np.array([-5.0, 0.0, 5.0]))
    assert np.all((p > 0) & (p < 1))
    assert p[0] < p[1] < p[2]


def test_candidate_probabilities_are_clipped():
    rng = np.random.default_rng(3)
    Z = rng.normal(size=(500, 3)) * 5
    y = (rng.random(500) < 0.5).astype(int)
    seasons = np.repeat([2001, 2002], 250)
    cal = _fit_candidate("elo", Z, y)
    p = _predict_candidate("elo", cal, Z * 100)
    assert p.min() >= PROB_CLIP[0] and p.max() <= PROB_CLIP[1]
    assert _cross_fit_log_loss("stack", Z, y, seasons) > 0
