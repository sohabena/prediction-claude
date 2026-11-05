"""S38: fair value v2 (as-of ratings, walk-forward, market evaluator) on small synthetic fixtures."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from scripts import eval_fair_value_vs_market as evm
from scripts import train_fair_value as tfv
from strategy.fair_value import (
    V1_WIDTH,
    V2_WIDTH,
    FairValueModel,
    family_of,
    model_path,
    tick_features,
)
from strategy.ratings import CricketRatings, competition_tier, home_label, parse_row, state_ticks

TEAMS = ("Alpha", "Bravo", "Charlie", "Delta")
STRENGTH = {"Alpha": 0.8, "Bravo": 0.2, "Charlie": -0.2, "Delta": -0.8}
SEASONS = (2019, 2020, 2021)
PER_SEASON = 40


# ---------------------------------------------------------------- fixtures
def _innings(team: str, opp: str, rng: random.Random, overs: int = 20) -> Dict[str, Any]:
    strength = STRENGTH[team]
    weights = [0.40 - 0.03 * strength, 0.25, 0.08, 0.12 + 0.03 * strength, 0.10, 0.05 + 0.02 * strength]
    deliveries_by_over: List[Dict[str, Any]] = []
    wk = 0
    for o in range(overs):
        dels: List[Dict[str, Any]] = []
        for b in range(6):
            batter = f"{team} P{min(wk, 9)}"
            bowler = f"{opp} P{(o % 5) + 6}"
            runs = rng.choices([0, 1, 2, 4, 6, 3], weights=weights)[0]
            d: Dict[str, Any] = {"batter": batter, "bowler": bowler,
                                 "runs": {"batter": runs, "extras": 0, "total": runs}}
            if b == 2 and o % 4 == 1:
                d["extras"] = {"wides": 1}
                d["runs"] = {"batter": 0, "extras": 1, "total": 1}
            if rng.random() < 0.03 and wk < 9:
                d["wickets"] = [{"player_out": batter, "kind": "bowled"}]
                wk += 1
            dels.append(d)
        deliveries_by_over.append({"over": o + 1, "deliveries": dels})
    return {"team": team, "overs": deliveries_by_over}


def _make_matches() -> List[Dict[str, Any]]:
    rng = random.Random(7)
    raws: List[Dict[str, Any]] = []
    pairs = [(a, b) for i, a in enumerate(TEAMS) for b in TEAMS[i + 1:]]
    for year in SEASONS:
        for idx in range(PER_SEASON):
            a, b = pairs[(idx + year) % len(pairs)]
            if rng.random() < 0.5:
                a, b = b, a
            day = date(year, 1, 10) + timedelta(days=5 * idx)
            venue_owner = a if rng.random() < 0.5 else b
            p_a = 1.0 / (1.0 + np.exp(-2.0 * (STRENGTH[a] - STRENGTH[b])))
            winner = a if rng.random() < p_a else b
            mid = f"m{year}{idx:03d}"
            info = {
                "dates": [day.isoformat()],
                "teams": [a, b],
                "match_type": "T20",
                "gender": "male",
                "venue": f"{venue_owner} Ground",
                "city": "City",
                "team_type": "international",
                "event": {"name": "Fixture Series"},
                "toss": {"winner": a, "decision": "bat" if idx % 2 else "field"},
                "players": {t: [f"{t} P{j}" for j in range(11)] for t in (a, b)},
                "outcome": {"winner": winner, "by": {"runs": 10}},
                "overs": 20,
            }
            innings = [_innings(a, b, rng), _innings(b, a, rng)]
            raws.append({"match_id": mid, "info": info, "innings": innings})
    return raws


def _write_corpus(root: Path, raws: List[Dict[str, Any]]) -> Path:
    folder = root / "raw" / "t20s"
    folder.mkdir(parents=True, exist_ok=True)
    for raw in raws:
        payload = {"info": raw["info"], "innings": raw["innings"]}
        (folder / f"{raw['match_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def raws() -> List[Dict[str, Any]]:
    return _make_matches()


@pytest.fixture(scope="module")
def corpus_root(tmp_path_factory: pytest.TempPathFactory, raws: List[Dict[str, Any]]) -> Path:
    return _write_corpus(tmp_path_factory.mktemp("cricsheet"), raws)


@pytest.fixture(scope="module")
def ratings(raws: List[Dict[str, Any]]) -> CricketRatings:
    return CricketRatings(raws)


def _by_id(raws: List[Dict[str, Any]], mid: str) -> Dict[str, Any]:
    return next(r for r in raws if r["match_id"] == mid)


def _state_episode(mid: str, winner_home: int, rng: random.Random) -> List[Dict[str, Any]]:
    ticks = []
    score = 0.0
    for k in range(0, 120, 6):
        score += rng.choice([4.0, 6.0, 8.0]) + 2.0 * winner_home
        ticks.append({
            "match_id": mid, "team_home": "H", "team_away": "A", "innings": 1,
            "score": score, "wickets": float(k // 30), "overs": k / 6.0,
            "balls_remaining": float(120 - k), "run_rate": score * 6 / (k + 6),
            "req_run_rate": 0.0, "batting_team": "H", "in_play": True,
        })
    ticks[0]["_meta"] = {"winner": "H" if winner_home else "A", "winner_is_home": bool(winner_home)}
    return ticks


# ---------------------------------------------------------------- features and families
def test_family_of_maps_odm_to_odi_and_keeps_other_formats() -> None:
    assert family_of("ODM") == "odi"
    assert family_of("ODI") == "odi"
    assert family_of("T20") == "t20"
    assert family_of("Test") == "multiday"
    assert family_of("MDM") == "multiday"


def test_v2_is_v1_plus_prematch_block_and_ignores_results() -> None:
    tick = {"innings": 2, "balls_remaining": 60, "wickets": 3, "run_rate": 7.5, "req_run_rate": 9.0,
            "score": 80, "overs": 10.0, "batting_team": "H", "team_home": "H",
            "pm_elo_home": 1550.0, "pm_elo_away": 1500.0, "pm_xi_home": 0.1, "pm_xi_away": -0.05,
            "pm_toss_home": 1.0, "pm_toss_bat": 0.0, "pm_venue_chase_pct": 60.0, "pm_venue_n": 40, "pm_tier": 1}
    v1 = tick_features(tick, "t20", "v1")
    v2 = tick_features(tick, "t20", "v2")
    assert len(v1) == V1_WIDTH and len(v2) == V2_WIDTH
    assert v2[:V1_WIDTH] == v1
    assert tick_features(tick, "t20") == v1
    with_result = dict(tick, _meta={"winner": "A", "winner_is_home": False}, winner_is_home=False)
    assert tick_features(with_result, "t20", "v2") == v2
    changed = dict(tick, pm_elo_home=1400.0)
    assert tick_features(changed, "t20", "v1") == v1
    assert tick_features(changed, "t20", "v2") != v2


def test_v1_model_roundtrip_and_pre_s38_pickle_still_loads(tmp_path: Path) -> None:
    rng = random.Random(3)
    eps = [_state_episode(f"x{i}", i % 2, rng) for i in range(30)]
    model = FairValueModel("t20").fit(eps)
    assert model.version == "v1"
    path = tmp_path / "t20.joblib"
    model.save(path)
    loaded = FairValueModel.load(path)
    assert np.allclose(loaded.predict_batch(eps[0]), model.predict_batch(eps[0]))
    # Simulate a model pickled before S38 (no instance attribute "version").
    del loaded.__dict__["version"]
    loaded.save(path)
    old = FairValueModel.load(path)
    assert old.version == "v1"
    assert np.allclose(old.predict_batch(eps[1]), model.predict_batch(eps[1]))


def test_model_path_names() -> None:
    assert model_path("t20") == Path("models/fair_value/t20.joblib")
    assert model_path("t20", "t20i_men", "v1").name == "t20_t20i_men.joblib"
    assert model_path("t20", None, "v2", root=Path("x")) == Path("x/t20_v2_all.joblib")
    assert model_path("odi", "odi_women", "v2", root=Path("x")).name == "odi_v2_odi_women.joblib"


def test_competition_tier_and_home_label() -> None:
    assert competition_tier({"team_type": "international"}) == 0
    assert competition_tier({"team_type": "club"}) == 1
    assert competition_tier({}) == 2
    assert home_label("m1", ("A", "B")) in ("A", "B")
    assert home_label("m1", ("A", "B")) == home_label("m1", ("A", "B"))


# ---------------------------------------------------------------- as-of ratings and leak tests
def test_match_result_never_changes_its_own_prematch_features(raws: List[Dict[str, Any]]) -> None:
    target = raws[70]
    mid, day = target["match_id"], target["info"]["dates"][0]
    flipped = copy.deepcopy(target)
    teams = target["info"]["teams"]
    flipped["info"]["outcome"]["winner"] = teams[1] if target["info"]["outcome"]["winner"] == teams[0] else teams[0]
    other = [flipped if r is target else r for r in raws]
    base = CricketRatings(raws).pre_match(mid, day)
    changed = CricketRatings(other).pre_match(mid, day)
    assert base == changed
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    later_a = CricketRatings(raws).pre_match(mid, next_day)
    later_b = CricketRatings(other).pre_match(mid, next_day)
    assert later_a != later_b  # the result does count once it is in the past


def test_elo_query_is_strictly_before_cutoff(raws: List[Dict[str, Any]], ratings: CricketRatings) -> None:
    target = raws[70]
    team = target["info"]["teams"][0]
    day = target["info"]["dates"][0][:10]
    before = ratings._elo.rating(team, "t20", "male", day)
    same_day_plus = ratings._elo.rating(team, "t20", "male", (date.fromisoformat(day) + timedelta(days=1)).isoformat())
    assert before != same_day_plus


def test_player_strength_excludes_the_match_on_its_own_date(raws: List[Dict[str, Any]]) -> None:
    target = raws[70]
    day = target["info"]["dates"][0][:10]
    without = [r for r in raws if r is not target]
    full = CricketRatings(raws)
    minus = CricketRatings(without)
    ordinal = date.fromisoformat(day).toordinal()
    names = target["info"]["players"][target["info"]["teams"][0]]
    for name in names:
        assert full._player_rating("t20", "male", name, ordinal) == pytest.approx(
            minus._player_rating("t20", "male", name, ordinal))
    moved = [full._player_rating("t20", "male", n, ordinal + 1) != full._player_rating("t20", "male", n, ordinal)
             for n in names]
    assert any(moved)


def test_unknown_players_rate_zero(ratings: CricketRatings) -> None:
    assert ratings.xi_strength("t20", "male", ["Nobody Known"], "2021-01-01") == 0.0
    assert ratings.xi_strength("t20", "male", [], "2021-01-01") == 0.0


def test_state_ticks_follow_the_deliveries(raws: List[Dict[str, Any]], ratings: CricketRatings) -> None:
    raw = raws[3]
    row = ratings.rows[raw["match_id"]]
    pre = ratings.pre_match(row.match_id, row.date)
    ticks = state_ticks(raw, row, pre, max_balls=120)
    deliveries = sum(len(o["deliveries"]) for inn in raw["innings"] for o in inn["overs"])
    assert len(ticks) == deliveries + 1
    assert ticks[0]["in_play"] is False and "_meta" in ticks[0]
    assert all("_meta" not in t for t in ticks[1:])
    first_total = sum(d["runs"]["total"] for o in raw["innings"][0]["overs"] for d in o["deliveries"])
    second_innings_ticks = [t for t in ticks if t.get("innings") == 2]
    assert second_innings_ticks and second_innings_ticks[0]["target"] == first_total + 1
    assert tfv._label([ticks[0]]) == int(row.winner == row.home)


def test_holdout_result_shuffle_changes_no_features_or_predictions(
    tmp_path: Path, raws: List[Dict[str, Any]], corpus_root: Path
) -> None:
    # Shuffle the winners of the holdout season (2021) among themselves in a second copy of the corpus.
    holdout_ids = [r["match_id"] for r in raws if r["info"]["dates"][0].startswith("2021")]
    winners = {r["match_id"]: r["info"]["outcome"]["winner"] for r in raws}
    rotated = dict(zip(holdout_ids, [winners[m] for m in holdout_ids[1:] + holdout_ids[:1]]))
    shuffled = []
    for r in raws:
        if r["match_id"] in rotated:
            r2 = copy.deepcopy(r)
            r2["info"]["outcome"]["winner"] = rotated[r["match_id"]]
            shuffled.append(r2)
        else:
            shuffled.append(r)
    other_root = _write_corpus(tmp_path / "shuffled", shuffled)

    _, a = tfv.build_dataset(corpus_root, "t20", timing="season")
    _, b = tfv.build_dataset(other_root, "t20", timing="season")
    by_a = {m.match_id: m for m in a}
    by_b = {m.match_id: m for m in b}
    for mid in holdout_ids:
        assert np.array_equal(by_a[mid].X, by_b[mid].X)
    train = [m for m in a if m.season < 2020]
    calib = [m for m in a if m.season == 2020]
    test_a = [m for m in a if m.season == 2021]
    test_b = [m for m in b if m.season == 2021]
    X_tr, y_tr = tfv._stack(train, V2_WIDTH)
    X_ca, y_ca = tfv._stack(calib, V2_WIDTH)
    model = FairValueModel("t20", version="v2").fit_arrays(X_tr, y_tr, X_ca, y_ca)
    pa = model.predict_X(tfv._stack(test_a, V2_WIDTH)[0])
    pb = model.predict_X(tfv._stack(test_b, V2_WIDTH)[0])
    assert np.array_equal(pa, pb)
    # Non-vacuous: in-season (match) timing does let earlier holdout results into later features.
    _, c = tfv.build_dataset(corpus_root, "t20", timing="match")
    _, d = tfv.build_dataset(other_root, "t20", timing="match")
    cmap = {m.match_id: m for m in c}
    dmap = {m.match_id: m for m in d}
    assert any(not np.array_equal(cmap[m].X, dmap[m].X) for m in holdout_ids)


def test_v1_walk_forward_matrix_is_the_v2_state_block(corpus_root: Path) -> None:
    _, matches = tfv.build_dataset(corpus_root, "t20", timing="season")
    m = matches[0]
    assert m.X.shape[1] == V2_WIDTH
    assert np.array_equal(tfv._stack([m], V1_WIDTH)[0], m.X[:, :V1_WIDTH])
    # The pre-match block is constant within a match at season timing (features do not move with the ball).
    assert np.allclose(m.X[:, V1_WIDTH:], m.X[0, V1_WIDTH:])


# ---------------------------------------------------------------- walk-forward
def test_walk_forward_reports_per_season_metrics(corpus_root: Path) -> None:
    _, matches = tfv.build_dataset(corpus_root, "t20", timing="season")
    result = tfv.walk_forward(matches, "t20", "v2", min_train_matches=20, min_test_matches=10)
    assert [f["season"] for f in result["folds"]] == [2021]
    fold = result["folds"][0]
    assert fold["trained_through"].startswith("2019")
    assert fold["n_test_matches"] == PER_SEASON
    for key in ("brier", "log_loss", "ece", "n"):
        assert key in fold["model"]
    assert "baseline_v1_state_only" in fold
    assert 0.0 < fold["model"]["brier"] < 0.5


def test_walk_forward_refuses_multiday() -> None:
    with pytest.raises(ValueError):
        tfv.build_dataset(Path("."), "multiday")


# ---------------------------------------------------------------- market evaluator
def test_market_probability_helpers() -> None:
    both = {"back_home": 2.0, "lay_home": 2.1, "back_away": 2.0, "lay_away": 2.1}
    p = evm.market_home_prob(both)
    assert p == pytest.approx(0.5)
    assert evm.market_home_prob({"back_home": 1.5}) is None
    ltp = {"ltp_home": 1.5, "ltp_away": 3.0}
    assert evm.market_home_prob(ltp) == pytest.approx((1 / 1.5) / (1 / 1.5 + 1 / 3.0))
    assert evm.phase_of({"in_play": False}, "t20") == "pre"
    assert evm.phase_of({"in_play": True, "overs": 3.0}, "t20") == "powerplay"
    assert evm.phase_of({"in_play": True, "overs": 10.0}, "t20") == "middle"
    assert evm.phase_of({"in_play": True, "overs": 17.0}, "t20") == "death"


def test_paired_bootstrap_ci_brackets_the_mean() -> None:
    rng = np.random.default_rng(0)
    d = np.concatenate([np.full(50, 0.1), np.full(50, 0.12)])
    ep = np.repeat(np.arange(10), 10)
    lo, hi = evm.paired_bootstrap_ci(d, ep, 500, rng)
    assert lo <= float(np.mean(d)) <= hi
    assert evm.paired_bootstrap_ci(np.array([]), np.array([]), 10, rng) == (None, None)


def _write_real_corpus(real: Path, entries: List[Dict[str, Any]]) -> None:
    real.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "accepted": [], "rejected": []}
    for e in entries:
        payload = gzip.compress(json.dumps(e["ticks"]).encode("utf-8"), mtime=0)
        rel = f"{e['family']}/{e['market_id']}.json.gz"
        (real / e["family"]).mkdir(parents=True, exist_ok=True)
        (real / rel).write_bytes(payload)
        manifest["accepted"].append({
            "match_id": f"bf_{e['market_id'].replace('.', '_')}", "market_id": e["market_id"],
            "family": e["family"], "match_format": "T20", "competition": "Fixture",
            "team_home": e["team_home"], "team_away": e["team_away"], "data_tier": "advanced",
            "start": e["start"], "file": rel, "sha1": hashlib.sha1(payload).hexdigest(),
            **({"cricsheet_match_id": e["cricsheet_match_id"]} if e.get("cricsheet_match_id") else {}),
        })
    (real / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _real_ticks(home: str, away: str, winner: str, start: str) -> List[Dict[str, Any]]:
    prices = {"back_home": 2.0, "lay_home": 2.1, "back_away": 1.9, "lay_away": 2.0}
    base = {"team_home": home, "team_away": away, "competition": "Fixture", "batting_team": home}
    pre = {**base, "time": start, "in_play": False, **prices,
           "_meta": {"winner": winner, "winner_is_home": winner == home}}
    ticks = [pre]
    for ov in (2.0, 8.0, 14.0, 18.0):
        ticks.append({**base, "time": start, "in_play": True, "innings": 1, "score": ov * 8.0, "wickets": 1.0,
                      "overs": ov, "balls_remaining": 120.0 - ov * 6, "run_rate": 8.0, "req_run_rate": 0.0,
                      **prices})
    ticks.append({**base, "time": start, "in_play": True, "innings": 1, "score": 40.0, "wickets": 2.0,
                  "overs": 5.0, "balls_remaining": 90.0, "run_rate": 8.0, "req_run_rate": 0.0})
    return ticks


def test_evaluator_builds_segment_table_with_paired_cis(
    tmp_path: Path, raws: List[Dict[str, Any]], corpus_root: Path
) -> None:
    _, matches = tfv.build_dataset(corpus_root, "t20", timing="season")
    trained = [m for m in matches if m.season < 2021]
    model = tfv.fit_final(trained, "t20", "v2")
    model_file = tmp_path / "t20_v2_all.joblib"
    model.save(model_file)
    assert model.trained_through and model.trained_through < "2021-01-01"

    entries: List[Dict[str, Any]] = []
    holdout = [r for r in raws if r["info"]["dates"][0].startswith("2021")]
    for i, raw in enumerate(holdout[:12]):
        info = raw["info"]
        real_home = info["teams"][0] if i % 2 == 0 else info["teams"][1]
        real_away = info["teams"][1] if real_home == info["teams"][0] else info["teams"][0]
        start = f"{info['dates'][0]}T10:00:00Z"
        entries.append({"market_id": f"1.{i:04d}", "family": "t20", "team_home": real_home, "team_away": real_away,
                        "start": start, "ticks": _real_ticks(real_home, real_away, info["outcome"]["winner"], start),
                        "cricsheet_match_id": raw["match_id"]})
    # Not linked to Cricsheet: counted, not evaluated.
    entries.append({"market_id": "1.9998", "family": "t20", "team_home": "Alpha", "team_away": "Bravo",
                    "start": "2021-06-01T10:00:00Z", "ticks": _real_ticks("Alpha", "Bravo", "Alpha",
                                                                           "2021-06-01T10:00:00Z")})
    # Linked but before the model's training cutoff: counted, not evaluated.
    old = next(r for r in raws if r["info"]["dates"][0].startswith("2020"))
    entries.append({"market_id": "1.9999", "family": "t20", "team_home": old["info"]["teams"][0],
                    "team_away": old["info"]["teams"][1], "start": old["info"]["dates"][0] + "T10:00:00Z",
                    "ticks": _real_ticks(old["info"]["teams"][0], old["info"]["teams"][1],
                                         old["info"]["outcome"]["winner"], old["info"]["dates"][0] + "T10:00:00Z"),
                    "cricsheet_match_id": old["match_id"]})
    real = tmp_path / "real"
    _write_real_corpus(real, entries)

    report = evm.evaluate(model_file, real, corpus_root, n_boot=200, seed=0, min_episodes=3)
    assert report["counts"]["unlinked_no_cricsheet_match_id"] == 1
    assert report["counts"]["before_model_trained_through"] == 1
    assert report["counts"]["episodes_used"] == 12
    seg = {(s["phase"], s["tier"]): s for s in report["segments"]}
    pre = seg[("pre", "0")]
    assert pre["n_episodes"] == 12 and pre["status"] == "ok"
    lo, hi = pre["brier_diff_ci95"]
    assert lo is not None and lo <= hi
    assert pre["model"]["brier"] > 0 and pre["market"]["brier"] > 0
    assert ("powerplay", "0") in seg and ("death", "all") in seg
    assert seg[("all", "all")]["n_ticks"] == sum(s["n_ticks"] for k, s in seg.items() if k[0] in evm.PHASES
                                                  and k[1] == "all")
    md = evm.to_markdown(report)
    assert "| pre | 0 |" in md and "rung R1" in md


def test_evaluator_refuses_models_without_training_cutoff(tmp_path: Path, corpus_root: Path) -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, V2_WIDTH))
    y = (X[:, 0] > 0).astype(int)
    model = FairValueModel("t20", version="v2").fit_arrays(X, y)
    model_file = tmp_path / "no_cutoff.joblib"
    model.save(model_file)
    with pytest.raises(ValueError, match="trained_through"):
        evm.evaluate(model_file, tmp_path, corpus_root)


def test_v2_does_not_memorise_match_constant_noise():
    """Regression (S38 diagnosis): uninformative match-constant pre-match features must not hurt held-out Brier.

    The first v2 fed the 7 pre-match values straight to the trees; being constant per match and nearly unique,
    they let the trees memorise training matches. Averaged over seeds, the two-stage model must stay close to
    state-only v1, and the old direct design must be detectably worse (so the test discriminates)."""
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression

    from strategy.fair_value import V1_WIDTH, V2_WIDTH, FairValueModel

    new_gap, old_gap = [], []
    for seed in (0, 1, 2):
        rng = np.random.default_rng(seed)

        def make(n_matches: int, rows: int = 40):
            X, y = [], []
            for _ in range(n_matches):
                label = int(rng.random() < 0.5)
                pm = rng.normal(size=V2_WIDTH - V1_WIDTH)  # pure noise, constant within the match
                for r in range(rows):
                    progress = r / rows
                    signal = (1 if label else -1) * progress * 2.0 + rng.normal()
                    X.append([1.0, 1 - progress, 10.0, 7.0, 8.0, 50.0 * progress, signal, 0.0, 20.0 * progress]
                             + list(pm))
                    y.append(label)
            return np.asarray(X), np.asarray(y)

        X_tr, y_tr = make(400)
        X_ca, y_ca = make(100)
        X_te, y_te = make(150)

        def brier(p):
            return float(np.mean((p - y_te) ** 2))

        v1 = FairValueModel("t20", version="v1").fit_arrays(X_tr[:, :V1_WIDTH], y_tr, X_ca[:, :V1_WIDTH], y_ca)
        v2 = FairValueModel("t20", version="v2").fit_arrays(X_tr, y_tr, X_ca, y_ca)
        old = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=150, random_state=0)
        old.fit(X_tr, y_tr)
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(old.predict_proba(X_ca)[:, 1], y_ca)
        b1 = brier(v1.predict_X(X_te[:, :V1_WIDTH]))
        new_gap.append(brier(v2.predict_X(X_te)) - b1)
        old_gap.append(brier(np.clip(iso.predict(old.predict_proba(X_te)[:, 1]), 0.01, 0.99)) - b1)

    assert np.mean(old_gap) > 0.015, old_gap        # the failure mode is real and detected
    assert np.mean(new_gap) < 0.01, new_gap         # the two-stage fix stays close to v1
    assert np.mean(new_gap) < np.mean(old_gap) / 2
