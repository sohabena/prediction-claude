"""S48 session-runs model: trace and row rules, leak tests, monotonicity, pricing and match-constant noise."""
from __future__ import annotations

import copy
import random
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pytest
from scipy import stats

from shared.venue_priors import VenuePriors
from strategy.ratings import CricketRatings
from strategy.session_model import (
    FEATURE_NAMES,
    SessionRunsModel,
    SessionState,
    build_rows,
    concat_rows,
    crps_discrete,
    design,
    design_rows,
    edge,
    fair_rates,
    line_threshold,
    pinball,
    trace_innings,
)


# ---------------------------------------------------------------- helpers
def _d(runs: int = 0, *, wide: bool = False, wicket: Optional[str] = None, bat: str = "A1") -> Dict[str, Any]:
    d: Dict[str, Any] = {"batter": bat, "bowler": "B1", "runs": {"batter": runs, "extras": 0, "total": runs}}
    if wide:
        d["extras"] = {"wides": 1}
        d["runs"] = {"batter": 0, "extras": 1, "total": 1}
    if wicket:
        d["wickets"] = [{"player_out": bat, "kind": wicket}]
    return {"overs": None, "d": d}


def _innings(items: List[Dict[str, Any]], team: str = "A") -> Dict[str, Any]:
    dels = [i["d"] for i in items]
    return {"team": team, "overs": [{"over": 1, "deliveries": dels}]}


def _legal(runs: List[int]) -> List[Dict[str, Any]]:
    return [_d(r) for r in runs]


def _synthetic_rows(rng: np.random.Generator, n: int, family: str = "t20") -> Dict[str, np.ndarray]:
    windows = np.array([6, 10, 15, 20]) if family == "t20" else np.array([10, 20, 30, 40, 50])
    k = rng.choice(windows, size=n)
    wb = k * 6
    bd = np.array([rng.integers(0, w) for w in wb])
    wk = rng.integers(0, 10, size=n)
    par = rng.uniform(6.0, 9.0, size=n) if family == "t20" else rng.uniform(4.5, 6.5, size=n)
    xi = rng.normal(0.0, 3.0, size=n)
    fem = rng.integers(0, 2, size=n).astype(float)
    runs = np.array([rng.integers(0, 6 * max(1, b // 6) * 2 + 10) for b in bd]).astype(float)
    bl = (wb - bd) / 6.0
    mu = bl * par * 0.9 * (1.0 - 0.05 * wk)
    r = 4.0
    target = rng.negative_binomial(r, r / (r + mu))
    return {
        "balls_done": bd.astype(np.int64), "runs": runs, "wickets": wk.astype(np.int64),
        "window_balls": wb.astype(np.int64), "window_overs": k, "target": target.astype(float),
        "season": np.full(n, 2020), "par_rr": par, "xi_diff": xi, "female": fem,
    }


# ---------------------------------------------------------------- trace and rows
def test_trace_counts_legal_balls_and_skips_illegal_wickets() -> None:
    items = [_d(1), _d(1, wide=True), _d(4), _d(0, wicket="bowled"), _d(0, wicket="retired hurt")]
    tr = trace_innings(_innings(items))
    assert tr.legal_balls == 4
    assert tr.runs_after.tolist() == [0.0, 1.0, 6.0, 6.0, 6.0]
    assert tr.wkts_after.tolist() == [0, 0, 0, 1, 1]


def test_window_needs_enough_legal_balls_and_match_overs() -> None:
    short = trace_innings(_innings(_legal([1] * 50)))  # all out or cut short before the 10-over window
    rows = build_rows(short, family="t20", match_overs=20, par_rr=7.5, xi_diff=0.0, female=False, stride=1)
    assert set(np.unique(rows["window_overs"]).tolist()) == {6}  # 50 legal balls reach only the 6-over window
    full = trace_innings(_innings(_legal([1] * 120)))
    capped = build_rows(full, family="t20", match_overs=8, par_rr=7.5, xi_diff=0.0, female=False, stride=1)
    assert set(np.unique(capped["window_overs"]).tolist()) == {6}  # windows beyond the match overs are dropped
    rows20 = build_rows(full, family="t20", match_overs=20, par_rr=7.5, xi_diff=0.0, female=False, stride=1)
    assert set(np.unique(rows20["window_overs"]).tolist()) == {6, 10, 15, 20}


def test_target_is_runs_in_window_including_extras() -> None:
    items = _legal([1] * 10) + [_d(1, wide=True)] + _legal([1] * 26)  # 36 legal balls, one wide after ball 10
    tr = trace_innings(_innings(items))
    rows = build_rows(tr, family="t20", match_overs=20, par_rr=7.5, xi_diff=0.0, female=False, stride=1)
    k6 = rows["window_overs"] == 6
    assert rows["target"][k6 & (rows["balls_done"] == 0)][0] == 37.0
    assert rows["target"][k6 & (rows["balls_done"] == 10)][0] == 27.0  # 26 legal balls and the wide


def test_later_deliveries_never_change_features_of_an_earlier_state() -> None:
    rng = random.Random(3)
    base = [_d(rng.choice([0, 1, 2, 4, 6])) for _ in range(60)]
    base[5] = _d(0, wicket="caught")
    changed = copy.deepcopy(base)
    for item in changed[30:]:
        item["d"]["runs"] = {"batter": 6, "extras": 0, "total": 6}
        item["d"].pop("wickets", None)
    t1 = trace_innings(_innings(base))
    t2 = trace_innings(_innings(changed))
    assert np.array_equal(t1.runs_after[:21], t2.runs_after[:21])
    assert np.array_equal(t1.wkts_after[:21], t2.wkts_after[:21])
    r1 = build_rows(t1, family="t20", match_overs=20, par_rr=7.5, xi_diff=1.0, female=False, stride=1)
    r2 = build_rows(t2, family="t20", match_overs=20, par_rr=7.5, xi_diff=1.0, female=False, stride=1)
    early1 = r1["balls_done"] <= 20
    X1, o1 = design_rows({k: v[early1] for k, v in r1.items()}, "t20")
    early2 = r2["balls_done"] <= 20
    X2, o2 = design_rows({k: v[early2] for k, v in r2.items()}, "t20")
    assert np.array_equal(X1, X2) and np.array_equal(o1, o2)
    assert not np.array_equal(r1["target"][early1], r2["target"][early2])  # targets may use later balls


# ---------------------------------------------------------------- ratings-level leak test
def _leak_matches() -> List[Dict[str, Any]]:
    rng = random.Random(11)
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    raws: List[Dict[str, Any]] = []
    for year in (2019, 2020, 2021):
        for idx in range(6):
            a, b = teams[idx % 4], teams[(idx + 1) % 4]
            day = date(year, 1, 5) + timedelta(days=9 * idx)
            innings = []
            for team in (a, b):
                dels = [_d(rng.choice([0, 1, 2, 4, 6]), bat=f"{team} P{rng.randrange(11)}") for _ in range(120)]
                innings.append(_innings(dels, team=team))
            raws.append({
                "match_id": f"m{year}{idx}",
                "info": {
                    "dates": [day.isoformat()], "teams": [a, b], "match_type": "T20", "gender": "male",
                    "venue": f"{a} Ground", "city": "City", "team_type": "international",
                    "toss": {"winner": a, "decision": "bat"},
                    "players": {t: [f"{t} P{j}" for j in range(11)] for t in (a, b)},
                    "outcome": {"winner": a if rng.random() < 0.5 else b}, "overs": 20,
                },
                "innings": innings,
            })
    return raws


def _as_of_features(raws: List[Dict[str, Any]], season: int) -> Dict[str, tuple]:
    ratings = CricketRatings(raws)
    priors = VenuePriors.build(raws)
    cutoff = f"{season}-01-01"
    out: Dict[str, tuple] = {}
    for mid, row in ratings.rows.items():
        if row.date[:4] != str(season):
            continue
        par = priors.get(row.venue, row.family, row.gender, cutoff, city=row.city or None)["par_run_rate"]
        xi = (ratings.xi_strength("t20", row.gender, list(row.players[row.teams[0]]), cutoff)
              - ratings.xi_strength("t20", row.gender, list(row.players[row.teams[1]]), cutoff))
        out[mid] = (par, round(xi, 12))
    return out


def test_holdout_season_results_change_no_pre_season_features() -> None:
    raws = _leak_matches()
    before = _as_of_features(raws, 2021)
    shuffled = copy.deepcopy(raws)
    season_idx = [i for i, r in enumerate(shuffled) if r["info"]["dates"][0].startswith("2021")]
    winners = [shuffled[i]["info"]["outcome"]["winner"] for i in season_idx]
    random.Random(5).shuffle(winners)
    innings_runs = [shuffled[i]["innings"] for i in season_idx]
    random.Random(6).shuffle(innings_runs)
    for i, w, inn in zip(season_idx, winners, innings_runs):
        shuffled[i]["info"]["outcome"]["winner"] = w
        shuffled[i]["innings"] = inn
    after = _as_of_features(shuffled, 2021)
    assert before.keys() == after.keys() and len(before) == 6
    assert before == after


# ---------------------------------------------------------------- model behaviour
@pytest.fixture(scope="module")
def fitted() -> SessionRunsModel:
    rng = np.random.default_rng(0)
    rows = _synthetic_rows(rng, 6000)
    X, off = design_rows(rows, "t20")
    m = SessionRunsModel("t20").fit(X, off, rows["target"])
    m.calibrate(X[:1500], off[:1500], rows["target"][:1500], season="2020")
    m.feature_names = list(FEATURE_NAMES)
    return m


def test_p_at_least_is_monotone_non_increasing_in_line(fitted: SessionRunsModel) -> None:
    for state in (
        SessionState("t20", 0, 0.0, 0, 7.5),
        SessionState("t20", 42, 55.0, 3, 8.0, xi_diff=2.0, female=True),
        SessionState("t20", 90, 80.0, 6, 6.5),
    ):
        for k in (6, 10, 15, 20):
            if state.balls_done >= k * 6:
                continue
            probs = [fitted.p_at_least(state, k, line) for line in np.arange(0.5, 90.0, 0.5)]
            assert all(0.0 <= p <= 1.0 for p in probs)
            assert all(b <= a + 1e-12 for a, b in zip(probs, probs[1:]))
            assert fitted.p_at_least(state, k, 0.0) == 1.0


def test_threshold_rule_and_fair_rates_conventions(fitted: SessionRunsModel) -> None:
    assert line_threshold(45.5) == 46 and line_threshold(46.0) == 46
    state = SessionState("t20", 36, 40.0, 2, 7.5)
    fr = fair_rates(fitted, state, 10, 60.5)
    assert fr["threshold"] == 61.0
    assert fr["p_yes"] + fr["p_no"] == pytest.approx(1.0)
    assert fr["yes_price"] == pytest.approx(100.0 * fr["p_yes"])
    assert edge("YES", 60.0, fr["p_yes"]) == pytest.approx(fr["p_yes"] - 0.6)
    assert edge("NO", 40.0, fr["p_yes"]) == pytest.approx((1.0 - fr["p_yes"]) - 0.4)
    with pytest.raises(ValueError):
        edge("OVER", 50.0, 0.5)


def test_state_past_window_end_is_refused(fitted: SessionRunsModel) -> None:
    with pytest.raises(ValueError):
        fitted.p_at_least(SessionState("t20", 60, 30.0, 1, 7.5), 10, 20.0)


def test_match_constant_noise_does_not_hurt_heldout_loss() -> None:
    """S38 lesson: a feature constant within a match must not change held-out loss when it is uninformative."""
    rng = np.random.default_rng(42)
    train = _synthetic_rows(rng, 6000)
    test = _synthetic_rows(rng, 3000)
    n_match_tr, n_match_te = 600, 300
    z_tr = rng.normal(size=n_match_tr)[rng.integers(0, n_match_tr, size=6000)]
    z_te = rng.normal(size=n_match_te)[rng.integers(0, n_match_te, size=3000)]

    def heldout_nll(noise: bool) -> float:
        X_tr, o_tr = design_rows(train, "t20")
        X_te, o_te = design_rows(test, "t20")
        if noise:
            X_tr = np.column_stack([X_tr, z_tr])
            X_te = np.column_stack([X_te, z_te])
        m = SessionRunsModel("t20").fit(X_tr, o_tr, train["target"])
        n, p = m.nb_params(X_te, o_te)
        return float(-np.mean(stats.nbinom.logpmf(test["target"], n, p)))

    base = heldout_nll(False)
    with_noise = heldout_nll(True)
    assert abs(with_noise - base) < 0.01


def test_crps_prefers_the_correct_distribution() -> None:
    y = np.full(400, 10.0)
    n = np.full(400, 4.0)
    good = crps_discrete(n, n / (n + 10.0), y)
    bad = crps_discrete(n, n / (n + 25.0), y)
    assert good < bad


def test_pinball_is_zero_at_the_realised_value() -> None:
    y = np.array([3.0, 7.0, 0.0])
    assert pinball(y, y, 0.5) == 0.0
    assert pinball(y, y + 1.0, 0.9) > 0.0


def test_design_offsets_scale_with_balls_left_and_rejects_bad_states() -> None:
    X, off = design("t20", 0, 0.0, 0, 120, 7.5, 0.0, 0.0)
    assert X.shape[1] == len(FEATURE_NAMES)
    assert off[0] == pytest.approx(np.log(20.0))
    with pytest.raises(ValueError):
        design("t20", 120, 100.0, 2, 120, 7.5, 0.0, 0.0)
