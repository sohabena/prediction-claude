"""S64: in-play price dynamics research (training.inplay, scripts/research_inplay_dynamics.py).

Synthetic S58-shaped datasets: a mean-reverting price path must be detected, a random walk must not, and no feature may
read a book from the future or a label book.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from training import inplay as ip
from training.evaluate import load_dataset
from training.market import market_prob_home

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REAL_DATASET = ROOT / "data" / "training" / "20261103-0522"


# --------------------------------------------------------------------------- synthetic dataset helpers
def _leg(p: float) -> dict:
    """Nested S58 leg whose mid implied probability is exactly ``p`` (1% margin on each side)."""
    return {"back": 1.0 / (p * 0.995), "lay": 1.0 / (p * 1.005),
            "stale_s_back": 0, "source_back": "listing", "stale_s_lay": 0, "source_lay": "listing"}


def _book(p: float) -> dict:
    return {"home": _leg(p), "away": _leg(1.0 - p)}


EMPTY_BOOK = {"home": {"back": None, "lay": None}, "away": {"back": None, "lay": None}}


def _path(kind: str, rng: np.random.Generator, n: int, phi: float = 0.7, noise: float = 0.03) -> np.ndarray:
    p = np.empty(n)
    p[0] = 0.5
    for k in range(1, n):
        if kind == "mean_reverting":
            p[k] = 0.5 + phi * (p[k - 1] - 0.5) + rng.normal(0.0, noise)
        else:
            p[k] = p[k - 1] + rng.normal(0.0, noise)
        p[k] = min(0.95, max(0.05, p[k]))
    return p


def _dataset(kind: str = "mean_reverting", seed: int = 1, n_matches: int = 12, n_steps: int = 150,
             paths: list | None = None) -> dict:
    """Matches played one after another (6 h apart), in-play snapshots every 2 min, S58 field names."""
    rng = np.random.default_rng(seed)
    base = datetime(2026, 11, 1, tzinfo=UTC)
    matches, snaps = [], []
    for m in range(n_matches):
        mid = f"syn_{m:02d}"
        t0 = base + timedelta(hours=6 * m)
        p = paths[m] if paths is not None else _path(kind, rng, n_steps)
        matches.append({"match_id": mid, "team_home": "A", "team_away": "B", "competition": "Test League",
                        "match_start_time": (t0 - timedelta(minutes=1)).isoformat(), "family": "t20",
                        "is_simulated": False, "home_won": int(p[-1] > 0.5)})
        for k in range(len(p)):
            t = t0 + timedelta(seconds=120 * k)
            book_10m = _book(p[k + 5]) if k + 5 < len(p) else EMPTY_BOOK
            snaps.append({"snapshot_id": f"{mid}:IP+{2 * k}m", "match_id": mid, "label": f"IP+{2 * k}m",
                          "t": t.isoformat(), "time_to_start_s": -60 - 120 * k, "in_play": True,
                          "has_prices": True, "book": _book(p[k]), "book_60s": _book(p[k]), "book_10m": book_10m})
    return {"matches": matches, "snapshots": snaps}


def _write_dataset(directory: Path, ds: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({"generated_at_utc": "synthetic"}), encoding="utf-8")
    (directory / "matches.jsonl").write_text("\n".join(json.dumps(m) for m in ds["matches"]), encoding="utf-8")
    (directory / "snapshots.jsonl").write_text("\n".join(json.dumps(s) for s in ds["snapshots"]), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- rows and labels
def test_row_features_and_label_match_hand_computation():
    path = np.array([0.50, 0.52, 0.49, 0.55, 0.58, 0.54, 0.57, 0.61, 0.60, 0.63, 0.59, 0.62, 0.66, 0.64])
    ds = _dataset(n_matches=1, paths=[path])
    rows = {r["snapshot_id"]: r for r in ip.build_rows(ds)}
    assert rows, "expected rows from a complete synthetic match"
    for k in range(5, len(path) - 5):
        row = rows[f"syn_00:IP+{2 * k}m"]
        assert row["p_now"] == pytest.approx(path[k], abs=1e-9)
        assert row["y"] == pytest.approx(path[k + 5] - path[k], abs=1e-9)
        f = row["features"]
        assert f["d2"] == pytest.approx(path[k] - path[k - 1], abs=1e-9)
        assert f["abs_d2"] == pytest.approx(abs(path[k] - path[k - 1]), abs=1e-9)
        assert f["d6"] == pytest.approx(path[k] - path[k - 3], abs=1e-9)
        assert f["d10"] == pytest.approx(path[k] - path[k - 5], abs=1e-9)
        moves = [path[k - j] - path[k - j - 1] for j in range(5)]
        assert f["vol10"] == pytest.approx(float(np.std(moves, ddof=1)), abs=1e-9)
        assert f["minutes"] == pytest.approx(2.0 * k, abs=1e-9)
        assert set(f) == set(ip.FEATURES)


def test_rows_need_full_history_and_a_label():
    ds = _dataset(seed=4, n_matches=1, n_steps=30)
    rows = ip.build_rows(ds)
    ids = {r["snapshot_id"] for r in rows}
    # first 5 snapshots lack a 10-minute lookback; last 5 lack a 10-minute label book
    assert "syn_00:IP+0m" not in ids and "syn_00:IP+8m" not in ids
    assert "syn_00:IP+10m" in ids
    assert "syn_00:IP+48m" in ids and "syn_00:IP+50m" not in ids


def test_no_look_ahead_in_features():
    """Features ignore the look-ahead books, and a change in a later book cannot move an earlier feature."""
    base = _dataset(seed=5, n_matches=2, n_steps=60)
    relabelled = copy.deepcopy(base)
    for snap in relabelled["snapshots"]:
        snap["book_60s"] = _book(0.2)  # look-ahead books feed labels only
        if snap["book_10m"]["home"]["back"] is not None:
            snap["book_10m"] = _book(0.8)
    rows_a = {r["snapshot_id"]: r for r in ip.build_rows(base)}
    rows_b = {r["snapshot_id"]: r for r in ip.build_rows(relabelled)}
    assert rows_a.keys() == rows_b.keys()
    for sid, row in rows_a.items():
        assert row["features"] == rows_b[sid]["features"]
        assert row["y"] != rows_b[sid]["y"]

    target = "syn_00:IP+40m"
    future = copy.deepcopy(base)
    for snap in future["snapshots"]:
        if snap["snapshot_id"] == target:
            snap["book"] = _book(0.99)
    target_t = next(s["t"] for s in base["snapshots"] if s["snapshot_id"] == target)
    rows_f = {r["snapshot_id"]: r for r in ip.build_rows(future)}
    earlier = [sid for sid, r in rows_a.items() if r["t"].isoformat() < target_t]
    assert len(earlier) > 10
    for sid in earlier:
        assert rows_f[sid]["features"] == rows_a[sid]["features"], sid
    assert rows_f[target]["features"]["d2"] != rows_a[target]["features"]["d2"]


def test_walk_forward_purges_labels_and_uses_past_matches_only(monkeypatch):
    ds = _dataset(seed=6, n_matches=6, n_steps=120)
    rows = ip.build_rows(ds)
    seen = []
    real_fit = ip.fit_predictor

    def spy(name, train):
        seen.append((name, list(train)))
        return real_fit(name, train)

    monkeypatch.setattr(ip, "fit_predictor", spy)
    evaluated = ip.walk_forward(rows, models=("random_walk", "ridge"))
    assert evaluated, "later matches should have enough history"
    first_t = {}
    for r in rows:
        first_t[r["match_id"]] = min(first_t.get(r["match_id"], r["t"]), r["t"])
    fold_cutoffs = {}
    for r in evaluated:
        fold_cutoffs.setdefault(r["match_id"], first_t[r["match_id"]])
    # every training row's label window closed before its test match began, and no test-match row was used
    train_sets = [t for name, t in seen if name == "ridge"]
    assert len(train_sets) == len(fold_cutoffs)
    for train, (match_id, cutoff) in zip(train_sets, sorted(fold_cutoffs.items(), key=lambda kv: kv[1])):
        for row in train:
            assert row["match_id"] != match_id
            assert row["t"] + timedelta(seconds=ip.HORIZON_S) <= cutoff


def test_small_training_sets_make_no_prediction():
    ds = _dataset(seed=7, n_matches=2, n_steps=60)
    assert ip.walk_forward(ip.build_rows(ds)) == []


# --------------------------------------------------------------------------- predictors and metrics
def test_sign_rules_follow_the_move():
    rows = [{"y": 0.02, "features": {"d6": 0.1}}, {"y": -0.02, "features": {"d6": -0.1}}]
    momentum = ip.fit_predictor("momentum", rows)(rows)
    reversion = ip.fit_predictor("reversion", rows)(rows)
    assert list(momentum) == pytest.approx([0.02, -0.02])
    assert list(reversion) == pytest.approx([-0.02, 0.02])
    assert list(ip.fit_predictor("random_walk", rows)(rows)) == [0.0, 0.0]


def test_mean_reverting_path_is_detected():
    ds = _dataset("mean_reverting", seed=1)
    scores = ip.score_models(ip.walk_forward(ip.build_rows(ds)))
    assert scores["fade_hit_large"]["lo"] > 0.5
    assert ip.detected(scores, "ridge") or ip.detected(scores, "gbm")
    assert scores["models"]["ridge"]["mse_gain_vs_rw"]["lo"] > 0


def test_random_walk_is_not_detected():
    ds = _dataset("random_walk", seed=2)
    scores = ip.score_models(ip.walk_forward(ip.build_rows(ds)))
    for name in ip.MODELS:
        assert not ip.detected(scores, name), name
    assert scores["models"]["ridge"]["mse_gain_vs_rw"]["lo"] <= 0 or scores["models"]["ridge"]["mse_gain_vs_rw"]["hi"] < 0.001


def test_score_models_empty_input():
    assert ip.score_models([])["rows"] == 0


def test_fade_predictions_move_only_large_moves_and_stay_in_range():
    rows = [
        {"match_id": "m", "snapshot_id": "a", "p_now": 0.5, "features": {"d6": 0.10}},
        {"match_id": "m", "snapshot_id": "b", "p_now": 0.5, "features": {"d6": 0.01}},
        {"match_id": "m", "snapshot_id": "c", "p_now": 0.98, "features": {"d6": 0.20}},
        {"match_id": "m", "snapshot_id": "d", "p_now": 0.02, "features": {"d6": -0.20}},
    ]
    preds = {r["snapshot"]: r["p_home"] for r in ip.fade_predictions(rows, fraction=0.5)}
    assert preds["a"] == pytest.approx(0.45)
    assert preds["b"] == pytest.approx(0.5)
    assert preds["c"] == pytest.approx(0.88)
    assert preds["d"] == pytest.approx(0.12)


def test_bet_summary_empty_and_non_empty():
    assert ip.bet_summary([])["bets"] == 0
    bets = [{"match_id": "a", "pnl": 10.0, "clv": 0.02}, {"match_id": "b", "pnl": -5.0, "clv": None}]
    summary = ip.bet_summary(bets)
    assert summary["bets"] == 2 and summary["matches"] == 2 and summary["clv_n"] == 1
    assert summary["pnl_total"] == pytest.approx(5.0)


# --------------------------------------------------------------------------- script and real data
def test_research_script_writes_report_once(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("research_inplay", ROOT / "scripts" / "research_inplay_dynamics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dataset_dir = _write_dataset(tmp_path / "ds", _dataset("random_walk", seed=8))
    out_dir = tmp_path / "research"
    assert module.main(["--dataset", str(dataset_dir), "--date", "20990101", "--out-dir", str(out_dir)]) == 0
    text = (out_dir / "inplay_20990101.md").read_text(encoding="utf-8")
    assert "Produces no signals" in text and "random_walk" in text and "Policy simulation" in text
    assert module.main(["--dataset", str(dataset_dir), "--date", "20990101", "--out-dir", str(out_dir)]) == 1


def test_loaded_dataset_round_trip(tmp_path):
    dataset_dir = _write_dataset(tmp_path / "ds", _dataset("mean_reverting", seed=9, n_matches=3, n_steps=40))
    loaded = load_dataset(dataset_dir)
    assert len(loaded["matches"]) == 3 and len(loaded["snapshots"]) == 120
    assert ip.build_rows(loaded)


@pytest.mark.skipif(not (REAL_DATASET / "snapshots.jsonl").exists(), reason="real S58 dataset not present")
def test_real_dataset_rows_are_well_formed():
    dataset = load_dataset(REAL_DATASET)
    rows = ip.build_rows(dataset)
    assert rows
    for row in rows:
        assert all(math.isfinite(row["features"][f]) for f in ip.FEATURES)
        assert math.isfinite(row["y"]) and -1.0 < row["y"] < 1.0
        assert 0.0 <= row["p_now"] <= 1.0
        assert row["features"]["minutes"] >= 0.0
