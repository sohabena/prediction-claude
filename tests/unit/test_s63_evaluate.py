"""S63: evaluation harness, pre-registered segments and the gate report (training.evaluate, training.segments)."""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from training import evaluate as ev
from training import segments as seg

UTC = timezone.utc
REAL_DATASET = Path(__file__).resolve().parents[2] / "data" / "training" / "20261103-0522"


# --------------------------------------------------------------------------- synthetic dataset helpers
def _leg(p: float) -> dict:
    """Nested S58 book leg with a 1% margin around probability ``p``."""
    return {"back": 1.0 / (p * 0.995), "lay": 1.0 / (p * 1.005),
            "stale_s_back": 0, "source_back": "listing", "stale_s_lay": 0, "source_lay": "listing"}


def _book(p_home: float, *, back_home=None, lay_home=None, back_away=None, lay_away=None) -> dict:
    home = _leg(p_home)
    away = _leg(1.0 - p_home)
    if back_home is not None:
        home["back"] = back_home
    if lay_home is not None:
        home["lay"] = lay_home
    if back_away is not None:
        away["back"] = back_away
    if lay_away is not None:
        away["lay"] = lay_away
    return {"home": home, "away": away}


def _snapshot(match_id: str, label: str, t: datetime, start: datetime, book: dict,
              book_60s: dict | None = None, book_10m: dict | None = None) -> dict:
    return {
        "snapshot_id": f"{match_id}:{label}",
        "match_id": match_id,
        "label": label,
        "t": t.isoformat(),
        "time_to_start_s": int(round((start - t).total_seconds())),
        "in_play": t >= start,
        "has_prices": True,
        "book": book,
        "book_60s": book_60s or book,
        "book_10m": book_10m or book,
    }


def _match(match_id: str, start: datetime, home_won: int, competition: str = "Ford Trophy",
           family: str = "t20", is_simulated: bool = False) -> dict:
    return {
        "match_id": match_id, "team_home": "A", "team_away": "B", "competition": competition,
        "match_start_time": start.isoformat(), "family": family, "is_simulated": is_simulated,
        "result_type": "win", "winner": "A" if home_won else "B", "winner_matched": True,
        "home_won": home_won,
    }


def make_dataset(n: int, *, seed: int = 1, competition: str = "Ford Trophy", family: str = "t20",
                 is_simulated: bool = False) -> dict:
    """``n`` settled matches, each with a T-60m, a T-0 and an in-play snapshot, priced around a random p."""
    rng = np.random.default_rng(seed)
    matches, snapshots = [], []
    base = datetime(2026, 11, 1, 10, tzinfo=UTC)
    for i in range(n):
        p = float(rng.uniform(0.3, 0.7))
        home_won = int(rng.random() < p)
        match_id = f"m{seed}_{i}"
        start = base + timedelta(hours=i)
        matches.append(_match(match_id, start, home_won, competition, family, is_simulated))
        book = _book(p)
        snapshots.append(_snapshot(match_id, "T-60m", start - timedelta(hours=1), start, book))
        snapshots.append(_snapshot(match_id, "T-0", start - timedelta(microseconds=1), start, book))
        snapshots.append(_snapshot(match_id, "IP+0m", start, start, book))
    return {"dir": "synthetic", "manifest": {"dataset": "synthetic"}, "matches": matches, "snapshots": snapshots}


def _true_outcome(dataset: dict, match_id: str) -> int:
    return next(m["home_won"] for m in dataset["matches"] if m["match_id"] == match_id)


def oracle_predictions(dataset: dict) -> list[dict]:
    """A source that knows the result: p_home is the realised outcome at every snapshot."""
    rows = []
    for snap in dataset["snapshots"]:
        rows.append({"match_id": snap["match_id"], "snapshot": snap["snapshot_id"],
                     "p_home": float(_true_outcome(dataset, snap["match_id"]))})
    return rows


# --------------------------------------------------------------------------- segments
def test_segments_are_frozen_and_versioned():
    assert isinstance(seg.SEGMENTS, tuple)
    assert isinstance(seg.SEGMENTS_VERSION, str) and seg.SEGMENTS_VERSION
    with pytest.raises(dataclasses.FrozenInstanceError):
        seg.SEGMENTS[0].name = "changed"  # type: ignore[misc]
    assert seg.SEGMENT_NAMES[0] == "all"
    assert len(set(seg.SEGMENT_NAMES)) == len(seg.SEGMENT_NAMES)


def test_unknown_segment_names_are_rejected():
    with pytest.raises(ValueError):
        seg.get_segment("tier_0")
    with pytest.raises(ValueError):
        seg.validate_segment_names(["all", "not_a_segment"])
    assert seg.validate_segment_names(["all", "tier_1"]) == ("all", "tier_1")


def test_segment_membership_by_family_gender_tier_and_price():
    row = {"family": "t20", "competition": "Women's Big Bash League", "is_simulated": False, "p_market": 0.7}
    names = seg.segments_for(row)
    assert {"all", "family_t20", "gender_women", "tier_1", "market_favourite", "real"} <= set(names)
    assert "gender_men" not in names and "family_odi" not in names and "market_longshot" not in names
    longshot = {"family": "odi", "competition": "Some Cup", "is_simulated": True, "p_market": 0.2}
    names = seg.segments_for(longshot)
    assert {"family_odi", "gender_men", "tier_2", "market_longshot", "simulated"} <= set(names)


def test_gender_and_tier_heuristics():
    assert seg.gender_of("WBBL") == "women"
    assert seg.gender_of("Indian Premier League") == "men"
    assert seg.tier_of("Indian Premier League") == "tier_1"
    assert seg.tier_of("Emirates D20") == "tier_2"


def test_policy_min_edges_cover_every_segment():
    edges = seg.policy_min_edges()
    assert set(edges) == set(seg.SEGMENT_NAMES)
    assert edges["all"] == seg.POLICY_MIN_EDGE


# --------------------------------------------------------------------------- metrics
def test_log_loss_and_brier_hand_values():
    assert ev.log_loss(np.array([0.5]), np.array([1.0]))[0] == pytest.approx(math.log(2))
    assert ev.brier(np.array([0.25]), np.array([1.0]))[0] == pytest.approx(0.5625)


def test_calibration_of_a_calibrated_source_has_small_ece():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, size=20000)
    y = (rng.random(20000) < p).astype(float)
    result = ev.calibration(p, y)
    assert result["ece"] < 0.02
    assert sum(b["n"] for b in result["bins"]) == 20000


# --------------------------------------------------------------------------- prediction contract
def test_predictions_are_validated():
    ds = make_dataset(2)
    snaps = {s["snapshot_id"]: s for s in ds["snapshots"]}
    good = {"match_id": "m1_0", "snapshot": "m1_0:T-60m", "p_home": 0.5}
    ev.validate_predictions([good], snaps)
    with pytest.raises(ValueError):
        ev.validate_predictions([{**good, "snapshot": "nope:T-0"}], snaps)
    with pytest.raises(ValueError):
        ev.validate_predictions([{**good, "match_id": "m1_1"}], snaps)
    with pytest.raises(ValueError):
        ev.validate_predictions([{**good, "p_home": 1.4}], snaps)
    with pytest.raises(ValueError):
        ev.validate_predictions([good, dict(good)], snaps)


# --------------------------------------------------------------------------- the referee
def test_market_as_source_has_exactly_zero_difference():
    ds = make_dataset(30)
    report = ev.evaluate(ds, ev.market_predictions(ds))
    for res in report["results"]:
        diff = res["r1"]["logloss_diff"]
        if res["matches"]:
            assert diff["point"] == 0.0
            # S84: an interval needs at least two matches to resample; a one-match segment has none (not [0, 0]).
            if res["matches"] >= 2:
                assert (diff["lo"], diff["hi"]) == (0.0, 0.0)
            else:
                assert diff["lo"] is None and diff["hi"] is None
            assert res["source"]["logloss"] == res["market"]["logloss"]


def test_market_as_source_is_not_a_pass_even_with_enough_matches():
    ds = make_dataset(ev.R1_MIN_MATCHES + 20, seed=3)
    report = ev.evaluate(ds, ev.market_predictions(ds))
    all_arena = next(r for r in report["results"] if r["arena"] == "prematch" and r["segment"] == "all")
    assert all_arena["matches"] == ev.R1_MIN_MATCHES + 20
    assert all_arena["r1"]["status"] == "fail"


def test_oracle_passes_r1_on_a_constructed_set():
    ds = make_dataset(ev.R1_MIN_MATCHES + 20, seed=5)
    report = ev.evaluate(ds, oracle_predictions(ds))
    all_arena = next(r for r in report["results"] if r["arena"] == "prematch" and r["segment"] == "all")
    assert all_arena["r1"]["status"] == "pass"
    assert all_arena["r1"]["logloss_diff"]["hi"] < 0.0
    assert all_arena["source"]["logloss"] < all_arena["market"]["logloss"]


def test_tiny_sample_is_insufficient_with_matches_needed():
    ds = make_dataset(5, seed=7)
    report = ev.evaluate(ds, ev.market_predictions(ds))
    all_arena = next(r for r in report["results"] if r["arena"] == "prematch" and r["segment"] == "all")
    assert all_arena["r1"]["status"] == "insufficient"
    assert all_arena["r1"]["matches_needed"] == ev.R1_MIN_MATCHES - 5
    assert all_arena["r2"]["status"] == "insufficient"
    assert all_arena["r2"]["matches_needed"] == ev.R2_MIN_MATCHES - 5
    assert ev.summarise_gates(report["results"])["signals_allowed"] is False


def test_unsettled_matches_are_not_evaluated():
    ds = make_dataset(4, seed=9)
    for m in ds["matches"]:
        m["home_won"] = None
    assert ev.build_eval_rows(ds, ev.market_predictions(ds)) == []


def test_policy_bets_back_at_the_back_price_and_closes_at_t0():
    start = datetime(2026, 11, 1, 10, tzinfo=UTC)
    ds = {"dir": "x", "manifest": {}, "matches": [_match("x1", start, 1)], "snapshots": []}
    # Decision book: fair 50/50. Model says home 0.70.
    decision = _book(0.5)
    # 60 s later a backer of home gets 2.20 (lay 2.30); away is priced for 0.30.
    fill = _book(0.5, back_home=2.20, lay_home=2.30, back_away=3.00, lay_away=3.10)
    # Closing book (T-0): home back 2.00, so the bet beat the close.
    close = _book(0.5, back_home=2.00, lay_home=2.10)
    ds["snapshots"] = [
        _snapshot("x1", "T-60m", start - timedelta(hours=1), start, decision, book_60s=fill),
        _snapshot("x1", "T-0", start - timedelta(microseconds=1), start, close),
    ]
    rows = ev.build_eval_rows(ds, [{"match_id": "x1", "snapshot": "x1:T-60m", "p_home": 0.70, "p_sd": 0.0}])
    snaps = {s["snapshot_id"]: s for s in ds["snapshots"]}
    bets = ev.simulate_policy(rows, snaps)
    assert len(bets) == 1
    bet = bets[0]
    assert bet["side"] == "BACK" and bet["selection"] == "home"
    assert bet["price"] == pytest.approx(2.20)
    assert bet["stake"] > 0.0
    assert bet["clv"] == pytest.approx(1 / 2.00 - 1 / 2.20)
    assert bet["pnl"] == pytest.approx(bet["stake"] * 1.20)  # home won


def test_settlement_for_back_and_lay():
    assert ev._settle("BACK", "home", 2.0, 10.0, 1) == pytest.approx(10.0)
    assert ev._settle("BACK", "home", 2.0, 10.0, 0) == pytest.approx(-10.0)
    assert ev._settle("BACK", "away", 2.0, 10.0, 0) == pytest.approx(10.0)
    assert ev._settle("LAY", "home", 2.0, 10.0, 1) == pytest.approx(-10.0)
    assert ev._settle("LAY", "home", 2.0, 10.0, 0) == pytest.approx(10.0)


def test_report_refuses_to_overwrite_another_source(tmp_path: Path):
    ds = make_dataset(3, seed=11)
    evaluation = ev.evaluate(ds, ev.market_predictions(ds))
    report = ev.build_report(ds, evaluation, source_name="market", source_kind="market",
                             predictions_file=None, predictions_rows=0)
    ev.write_report(report, tmp_path, "20261103")
    other = {**report, "source": {**report["source"], "name": "other_model"}}
    with pytest.raises(FileExistsError):
        ev.write_report(other, tmp_path, "20261103")
    ev.write_report(other, tmp_path, "20261103", overwrite=True)
    assert (tmp_path / "20261103.md").read_text(encoding="utf-8").startswith("# S63 gate report")


def test_evaluation_is_deterministic():
    ds = make_dataset(25, seed=13)
    preds = ev.market_predictions(ds)
    first = ev.evaluate(ds, preds)["results"]
    second = ev.evaluate(ds, preds)["results"]
    assert first == second


@pytest.mark.skipif(not REAL_DATASET.is_dir(), reason="S58 dataset not built in this checkout")
def test_real_dataset_market_source_is_zero_and_insufficient():
    ds = ev.load_dataset(REAL_DATASET)
    report = ev.evaluate(ds, ev.market_predictions(ds))
    for res in report["results"]:
        if res["matches"]:
            assert res["r1"]["logloss_diff"]["point"] == 0.0
        assert res["r1"]["status"] == "insufficient"
        assert res["r2"]["status"] == "insufficient"
