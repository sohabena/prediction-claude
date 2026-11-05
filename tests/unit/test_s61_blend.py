"""S61: market-blend model (training.blend): exact market with no data, parameter recovery, shrinkage, walk-forward."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from training import blend as bl
from training.blend import BLEND_SEGMENTS, BlendModel, BlendRow, build_blend_rows, walk_forward
from training.market import market_prob_home

REAL_DATASET = Path(__file__).resolve().parents[2] / "data" / "training" / "20261103-0522"


def _row(i: int, p_mkt: float, p_model, y, *, day: str = "2026-11-01", segments=()) -> BlendRow:
    return BlendRow(match_id=f"m{i}", snapshot_id=f"m{i}:T-10m", date=day, p_market=p_mkt,
                    p_model=p_model, home_won=y, segments=tuple(segments))


def _synthetic(n: int, a: float, b: float, c: float, *, seed: int, d_women: float = 0.0) -> list[BlendRow]:
    """Rows whose outcomes follow ``logit p = a + b L_mkt + c delta + d_women * women`` exactly in expectation."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        p_mkt = float(rng.uniform(0.15, 0.85))
        delta = float(rng.normal(0.0, 0.5))
        p_model = float(1.0 / (1.0 + np.exp(-(np.log(p_mkt / (1 - p_mkt)) + delta))))
        women = bool(rng.integers(0, 2))
        eta = a + b * float(np.log(p_mkt / (1 - p_mkt))) + c * delta + (d_women if women else 0.0)
        y = int(rng.uniform() < 1.0 / (1.0 + np.exp(-eta)))
        rows.append(_row(i, p_mkt, p_model, y, day=f"2026-{1 + i // 400:02d}-{1 + (i // 13) % 28:02d}",
                         segments=("gender_women",) if women else ()))
    return rows


# --------------------------------------------------------------------------- no data is the market
def test_no_data_returns_market_exactly():
    rows = [_row(0, 0.37, 0.61, None), _row(1, 0.80, None, None)]
    model = BlendModel()
    assert model.fit([]).fitted is False
    p, sd = model.predict(rows)
    np.testing.assert_array_equal(p, np.array([0.37, 0.80]))
    np.testing.assert_array_equal(sd, np.zeros(2))


def test_no_data_predict_one_is_market():
    model = BlendModel().fit([])
    assert model.predict_one(_row(0, 0.123, 0.9, None)) == (0.123, 0.0)


def test_empty_input_predicts_empty():
    p, sd = BlendModel().fit([]).predict([])
    assert p.size == 0 and sd.size == 0


# --------------------------------------------------------------------------- recovery of known parameters
def test_recovers_known_a_b_c_and_segment_shift():
    rows = _synthetic(4000, a=0.2, b=0.9, c=0.6, seed=7, d_women=-0.4)
    model = BlendModel().fit(rows)
    post = model.posterior()
    assert abs(post["a"]["mean"] - 0.2) < 0.15
    assert abs(post["b"]["mean"] - 0.9) < 0.1
    assert abs(post["c"]["mean"] - 0.6) < 0.15
    assert abs(post["segments"]["gender_women"]["mean"] + 0.4) < 0.2
    assert post["c"]["lo95"] < 0.6 < post["c"]["hi95"]


def test_predict_is_seeded_and_reports_uncertainty():
    rows = _synthetic(300, a=0.0, b=1.0, c=0.5, seed=3)
    model = BlendModel().fit(rows)
    p1, sd1 = model.predict(rows[:10])
    p2, sd2 = model.predict(rows[:10])
    np.testing.assert_array_equal(p1, p2)
    np.testing.assert_array_equal(sd1, sd2)
    assert np.all(sd1 > 0.0)
    assert np.all((p1 > 0.0) & (p1 < 1.0))


# --------------------------------------------------------------------------- shrinkage towards the market
def test_tiny_sample_shrinks_towards_market():
    # Six matches where the model is 0.9 and the market 0.5; four of six won. A flat prior on c follows the
    # outcomes; the default prior on c (sd 0.3) keeps the blend nearer the market. The intercept keeps its default.
    outcomes = [1, 1, 0, 1, 1, 0]
    rows = [_row(i, 0.5, 0.9, y, day=f"2026-11-0{i + 1}") for i, y in enumerate(outcomes)]
    shrunk = BlendModel().fit(rows)
    flat = BlendModel(prior_sd={"c": 10.0}).fit(rows)
    probe = _row(99, 0.5, 0.9, None)
    p_shrunk, _ = shrunk.predict_one(probe)
    p_flat, _ = flat.predict_one(probe)
    assert abs(p_shrunk - 0.5) < abs(p_flat - 0.5)
    assert 0.5 < p_shrunk < 0.9
    assert abs(shrunk.posterior()["c"]["mean"]) < abs(flat.posterior()["c"]["mean"])
    assert shrunk.posterior()["c"]["sd"] < 0.3  # the prior sd bounds the posterior sd from above


def test_rows_without_model_leave_c_at_its_prior():
    rows = [_row(i, 0.3 + 0.05 * i, None, i % 2, day=f"2026-11-0{i + 1}") for i in range(6)]
    post = BlendModel().fit(rows).posterior()
    assert post["c"]["mean"] == pytest.approx(0.0, abs=1e-12)
    assert post["c"]["sd"] == pytest.approx(0.3, rel=1e-9)


def test_prior_sd_validation():
    with pytest.raises(ValueError):
        BlendModel(prior_sd={"x": 0.1})
    with pytest.raises(ValueError):
        BlendModel(prior_sd={"c": 0.0})


def test_unsettled_rows_are_rejected_by_fit():
    with pytest.raises(ValueError):
        BlendModel().fit([_row(0, 0.5, 0.6, None)])


# --------------------------------------------------------------------------- walk-forward
def test_walk_forward_never_trains_on_the_test_day(monkeypatch):
    rows = _synthetic(120, a=0.0, b=1.0, c=0.4, seed=11)
    calls: list[tuple[str, ...]] = []
    original_fit = BlendModel.fit

    def spy(self, train_rows):
        calls.append(tuple(sorted({r.date for r in train_rows})))
        return original_fit(self, train_rows)

    monkeypatch.setattr(BlendModel, "fit", spy)
    records = walk_forward(rows)
    days = sorted({r["date"] for r in records})
    assert len(calls) == len(days)
    for day, train_dates in zip(days, calls):
        assert all(d < day for d in train_dates)
    for rec in records:
        assert rec["train_max_date"] is None or rec["train_max_date"] < rec["date"]


def test_walk_forward_first_day_is_market_and_later_days_use_history():
    rows = _synthetic(120, a=0.0, b=1.0, c=0.4, seed=11)
    records = walk_forward(rows)
    first_day = min(r["date"] for r in records)
    for rec in records:
        if rec["date"] == first_day:
            assert rec["n_train"] == 0
            assert rec["p_home"] == pytest.approx(rec["p_market"], abs=0.0)
            assert rec["p_sd"] == 0.0
    assert any(rec["n_train"] > 0 for rec in records)
    assert len(records) == len(rows)


def test_walk_forward_ignores_unsettled_rows():
    rows = [_row(0, 0.5, 0.6, 1), _row(1, 0.5, 0.6, None, day="2026-11-02")]
    records = walk_forward(rows)
    assert [r["match_id"] for r in records] == ["m0"]


# --------------------------------------------------------------------------- building rows
def _book(p_home: float) -> dict:
    def leg(p):
        return {"back": 1.0 / (p * 0.99), "lay": 1.0 / (p * 1.01)}
    return {"home": leg(p_home), "away": leg(1.0 - p_home)}


def test_build_blend_rows_counts_exclusions_and_segments():
    matches = [
        {"match_id": "a", "home_won": 1, "is_simulated": False, "match_start_time": "2026-11-01T10:00:00+00:00",
         "family": "t20", "competition": "Big Bash League"},
        {"match_id": "b", "home_won": None, "is_simulated": False, "match_start_time": "2026-11-01T10:00:00+00:00",
         "family": "t20", "competition": "Big Bash League"},
        {"match_id": "c", "home_won": 0, "is_simulated": True, "match_start_time": "2026-11-01T10:00:00+00:00",
         "family": "t20", "competition": "Premier League SRL"},
        {"match_id": "d", "home_won": 0, "is_simulated": False, "match_start_time": None,
         "family": "t20", "competition": "WBBL"},
        {"match_id": "e", "home_won": 1, "is_simulated": False, "match_start_time": "2026-11-01T10:00:00+00:00",
         "family": "odi", "competition": "One Day Internationals"},
        {"match_id": "f", "home_won": 0, "is_simulated": False, "match_start_time": "2026-11-01T10:00:00+00:00",
         "family": "t20", "competition": "WBBL"},
    ]
    snaps = [
        {"snapshot_id": "a:T-10m", "match_id": "a", "label": "T-10m", "book": _book(0.7)},
        {"snapshot_id": "b:T-10m", "match_id": "b", "label": "T-10m", "book": _book(0.5)},
        {"snapshot_id": "c:T-10m", "match_id": "c", "label": "T-10m", "book": _book(0.5)},
        {"snapshot_id": "e:T-10m", "match_id": "e", "label": "T-10m", "book": _book(0.4)},
        {"snapshot_id": "f:T-10m", "match_id": "f", "label": "T-10m",
         "book": {"home": {"back": None, "lay": None}, "away": {"back": None, "lay": None}}},
    ]
    rows, reasons = build_blend_rows({"matches": matches, "snapshots": snaps}, {"a": 0.66, "e": None})
    assert [r.match_id for r in rows] == ["a", "e"]
    assert rows[0].p_market == pytest.approx(market_prob_home(
        {"back_home": _book(0.7)["home"]["back"], "lay_home": _book(0.7)["home"]["lay"],
         "back_away": _book(0.7)["away"]["back"], "lay_away": _book(0.7)["away"]["lay"]}))
    assert rows[0].p_model == 0.66 and rows[0].has_model
    assert rows[1].p_model is None and not rows[1].has_model
    assert "gender_women" not in rows[0].segments and "family_t20" in rows[0].segments
    assert "family_odi" in rows[1].segments
    assert reasons == {"not_settled": 1, "simulated": 1, "no_start_date": 1, "no_prices": 1}


def test_include_simulated_keeps_srl_rows():
    matches = [{"match_id": "c", "home_won": 0, "is_simulated": True,
                "match_start_time": "2026-11-01T10:00:00+00:00", "family": "t20", "competition": "x"}]
    snaps = [{"snapshot_id": "c:T-10m", "match_id": "c", "label": "T-10m", "book": _book(0.5)}]
    rows, _ = build_blend_rows({"matches": matches, "snapshots": snaps}, {}, include_simulated=True)
    assert len(rows) == 1 and "simulated" in rows[0].segments


def test_real_dataset_rows_account_for_every_match():
    if not REAL_DATASET.exists():
        pytest.skip("S58 dataset not present")
    from training.evaluate import load_dataset

    dataset = load_dataset(REAL_DATASET)
    rows, reasons = build_blend_rows(dataset, {})
    assert len(rows) + sum(reasons.values()) == len(dataset["matches"])


# --------------------------------------------------------------------------- recorded-match pre-match probability
class _StubRatings:
    rows: dict = {}

    def __init__(self, known):
        self.known = set(known)
        self.calls = []

    def _team_known(self, team, family, gender):
        return team in self.known

    def pre_match_live(self, home, away, family, gender, as_of, *, tier, normalize_team):
        self.calls.append({"tier": tier, "gender": gender, "as_of": as_of})
        return {"pm_elo_home": 1550.0, "pm_elo_away": 1500.0, "pm_xi_home": 0.0, "pm_xi_away": 0.0,
                "pm_venue_chase_pct": 50.0, "pm_venue_n": 0.0}


class _StubModel:
    def __init__(self):
        self.seen = None

    def predict_home(self, features):
        self.seen = features
        return 0.62


def test_prematch_probability_needs_both_teams_and_a_rated_family():
    # Women's sides carry a " W" suffix in the registry (it sets the gender); the ratings know them without it.
    ratings = _StubRatings({"Sydney Sixers", "Perth Scorchers"})
    model = _StubModel()
    rest = bl.RestIndex(ratings)
    kwargs = dict(away="Perth Scorchers W", competition="WBBL", as_of="2026-11-01", ratings=ratings,
                  model=model, rest=rest)
    assert bl.prematch_probability(home="Unknown XI W", family="t20", **kwargs) is None
    assert bl.prematch_probability(home="Sydney Sixers W", family="unknown", **kwargs) is None
    assert model.seen is None
    p = bl.prematch_probability(home="Sydney Sixers W", family="t20", **kwargs)
    assert p == 0.62
    assert model.seen["elo_diff"] == pytest.approx(50.0 / 400.0)
    assert model.seen["xi_recent_diff"] == 0.0 and model.seen["male"] == 0.0  # women's side
    assert ratings.calls[-1]["gender"] == "female"


def test_recorded_tier_and_rest_index():
    assert bl.recorded_tier("Afghanistan v Zimbabwe T20I Series 2026-27") == 0
    assert bl.recorded_tier("One Day Internationals") == 0
    assert bl.recorded_tier("Nepal Premier League") == 1

    class _R:
        def __init__(self, date, teams):
            self.date, self.teams, self.family, self.gender = date, teams, "t20", "male"

    ratings = _StubRatings(set())
    ratings.rows = {"1": _R("2026-10-01", ("A", "B")), "2": _R("2026-10-05", ("A", "C"))}
    idx = bl.RestIndex(ratings)
    assert idx.days_since("t20", "male", "A", "2026-10-08") == 3.0
    assert idx.days_since("t20", "male", "A", "2026-10-01") is None  # strictly earlier only
    assert idx.days_since("t20", "male", "Z", "2026-10-08") is None


def test_segment_names_are_pre_registered():
    from training import segments as seg

    for name in BLEND_SEGMENTS:
        seg.get_segment(name)


def test_womens_suffix_is_stripped_for_the_rating_lookup():
    from training import blend as bl

    seen = []

    class _Ratings:
        def _team_known(self, team, family, gender):
            seen.append((team, gender))
            return False

    assert bl.prematch_probability(home="Melbourne Stars W", away="Perth Scorchers W", family="t20",
                                   competition="WBBL", as_of="2026-11-02", ratings=_Ratings(), model=object(),
                                   rest=None) is None
    assert seen[0] == ("Melbourne Stars", "female")
