"""VecNormalize stats travel with each model file through training, promotion and rollback."""

from __future__ import annotations

import json
from pathlib import Path

from rl.agent import PhoenixAgent
from scripts import weekly_cricsheet_sync_and_retrain as w
from scripts.rollback_specialist import rollback_specialist
from tests.unit.test_s07_retrain_pipeline import _metrics, _setup_pipeline
from tests.unit.test_s09_training_regime import _corpus


def test_sidecar_is_per_model_file() -> None:
    assert PhoenixAgent.default_normalize_path("m/ipl_v2.zip") == Path("m/ipl_v2_vecnorm.pkl")
    assert PhoenixAgent.default_normalize_path("m/ipl_v1.zip") != PhoenixAgent.default_normalize_path("m/ipl_v2.zip")


def test_promotion_copies_vecnorm_and_rollback_drops_it(tmp_path: Path) -> None:
    corpus, models, _ = _setup_pipeline(tmp_path)

    def train_fn(key, mix, steps, champion, cand_path, bets):
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_bytes(b"cand")
        w.vecnorm_path_for(cand_path).write_bytes(b"stats")
        return cand_path, {"aborted": False}

    def eval_fn(path, *a):
        return (_metrics(mean=9.0) if "candidates" in str(path) else _metrics(mean=5.0)), []

    res = w.retrain_specialist_model("ipl", 100, corpus_dir=corpus, models_dir=models,
                                     train_fn=train_fn, evaluate_fn=eval_fn)
    assert res["promoted"]
    assert (models / "ipl_v2_vecnorm.pkl").read_bytes() == b"stats"

    rollback_specialist("ipl", models_dir=models)
    v1 = Path(json.loads((models / "metadata.json").read_text())["ipl"]["path"])
    assert not w.vecnorm_path_for(v1).exists()  # legacy v1 keeps loading on raw observations


def _mix(n: int = 12) -> list:
    return _corpus(n, shuffled=False)


def test_fresh_candidate_saves_stats_and_finetune_continues_them(tmp_path: Path) -> None:
    first = tmp_path / "candidates" / "t20i_men_a.zip"
    saved, health = w.train_candidate("t20i_men", _mix(), 256, None, first, 5)
    assert saved == first and not health.get("aborted")
    stats_a = w.load_vecnorm(first)
    assert stats_a is not None

    second = tmp_path / "candidates" / "t20i_men_b.zip"
    w.train_candidate("t20i_men", _mix(), 256, first, second, 5)
    stats_b = w.load_vecnorm(second)
    assert stats_b is not None
    assert stats_b.obs_rms.count > stats_a.obs_rms.count  # continued, not restarted


def test_legacy_champion_finetune_stays_unnormalised(tmp_path: Path) -> None:
    legacy = tmp_path / "t20i_men_v1.zip"
    fresh = tmp_path / "candidates" / "t20i_men_a.zip"
    w.train_candidate("t20i_men", _mix(), 256, None, fresh, 5)
    fresh.replace(legacy)
    w.vecnorm_path_for(fresh).unlink()

    cand = tmp_path / "candidates" / "t20i_men_b.zip"
    w.train_candidate("t20i_men", _mix(), 256, legacy, cand, 5)
    assert cand.exists() and w.load_vecnorm(cand) is None
