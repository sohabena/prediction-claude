"""S09: training regime (chronological split, vec envs, vecnorm inference, early stopping)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.train_format_specialists as tfs  # noqa: E402
from rl.agent import PhoenixAgent  # noqa: E402
from scripts.train_format_specialists import (  # noqa: E402
    SpecialistEvaluationMetrics,
    build_vec_env,
    load_training_config,
    split_matches,
)


def _match(i: int, start: datetime, n_ticks: int = 4) -> Dict[str, Any]:
    ticks = []
    for t in range(n_ticks):
        ticks.append({
            "match_id": f"m{i}",
            "timestamp": start + timedelta(seconds=3 * t),
            "team_home": "India", "team_away": "Australia", "competition": "Test Cup",
            "back_home": 2.00, "lay_home": 2.04, "back_away": 2.10, "lay_away": 2.14,
            "back_draw": 15.0, "lay_draw": 18.0, "is_live": True,
            "score": 10 * t, "wickets": t // 2, "overs": float(t),
        })
    ticks[0]["_meta"] = {"winner": "India", "loser": "Australia", "winner_is_home": True}
    return {"match_id": f"m{i}", "start_time": start.isoformat(), "ticks": ticks}


def _corpus(n: int, shuffled: bool = True) -> List[Dict[str, Any]]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ms = [_match(i, base + timedelta(days=i)) for i in range(n)]
    if shuffled:
        order = np.random.default_rng(1).permutation(n)
        ms = [ms[i] for i in order]
    return ms


def _metrics(**kw: Any) -> SpecialistEvaluationMetrics:
    base = dict(key="t", format_name="T", matches_trained=0, matches_tested=0, timesteps=0,
                total_bets=0, avg_bets_per_match=0.0, hedged_bets=0, hedge_rate_pct=0.0,
                win_rate_pct=0.0, total_pnl=0.0, mean_roi_pct=0.0, sharpe_ratio=0.0,
                max_drawdown_dollars=0.0, max_drawdown_pct=0.0, profit_factor=0.0)
    base.update(kw)
    return SpecialistEvaluationMetrics(**base)


class TestSplit:
    def test_chronological(self, tmp_path: Path) -> None:
        tr, va, ho = split_matches(_corpus(100), "none_key", corpus_dir=tmp_path)
        assert (len(tr), len(va), len(ho)) == (70, 15, 15)
        assert max(m["start_time"] for m in tr) <= min(m["start_time"] for m in va)
        assert max(m["start_time"] for m in va) <= min(m["start_time"] for m in ho)

    def test_uses_s07_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "splits_k.json").write_text(json.dumps(
            {"holdout_ids": ["m98", "m99"], "val_ids": ["m97"], "pending_ids": ["m96"]}))
        tr, va, ho = split_matches(_corpus(100), "k", corpus_dir=tmp_path)
        assert {m["match_id"] for m in ho} == {"m98", "m99"}
        assert [m["match_id"] for m in va] == ["m97"]
        assert "m96" not in {m["match_id"] for m in tr} and len(tr) == 96


class TestConfig:
    def test_yaml_loaded_with_defaults(self) -> None:
        cfg = load_training_config("test_men")
        assert cfg["gamma"] == 0.995 and cfg["lr"] == 0.0002
        assert cfg["timesteps_per_match"] == 300 and cfg["n_envs"] == 8


class TestVecEnv:
    def test_random_sampling_visits_many_episodes(self) -> None:
        eps = [m["ticks"] for m in _corpus(100, shuffled=False)]
        venv = build_vec_env(eps, n_envs=8, seed=0, max_bets=5, backend="dummy")
        venv.reset()
        seen = set()
        steps = 0
        while steps < 20_000:
            _, _, _, infos = venv.step(np.zeros(8, dtype=np.int64))
            seen.update(i["data_index"] for i in infos)
            steps += 8
        venv.close()
        assert len(seen) >= 80


class TestAgentVecnorm:
    def test_load_applies_vecnorm_like_wrapper(self, tmp_path: Path) -> None:
        eps = [m["ticks"] for m in _corpus(10, shuffled=False)]
        venv = build_vec_env(eps, n_envs=2, seed=0, max_bets=5, backend="dummy")
        agent = PhoenixAgent(venv, n_steps=64, batch_size=32, n_epochs=1, n_envs=2, seed=0)
        agent.model.learn(total_timesteps=256)
        model_path = tmp_path / "x_v1.zip"
        agent.save(model_path)
        venv.save(str(tmp_path / "x_v1_vecnorm.pkl"))
        venv.training = False  # reset() must not mutate the saved statistics
        raw = np.asarray(venv.reset(), dtype=np.float32)[0]  # normalised obs from wrapper
        raw_obs = venv.unnormalize_obs(venv.reset())[0]
        loaded = PhoenixAgent.load(model_path, env=None)
        assert loaded._obs_norm is not None
        expected_obs = venv.normalize_obs(raw_obs)
        with torch.no_grad():
            t = agent.model.policy.obs_to_tensor(expected_obs)[0]
            expected = agent.model.policy.get_distribution(t).distribution.probs.numpy().flatten()
        got = loaded.get_action_distribution(raw_obs)
        np.testing.assert_allclose(got, expected, atol=1e-5)
        assert raw.shape == raw_obs.shape
        venv.close()

    def test_load_without_vecnorm_is_identity(self, tmp_path: Path) -> None:
        eps = [m["ticks"] for m in _corpus(5, shuffled=False)]
        venv = build_vec_env(eps, n_envs=1, seed=0, max_bets=5, backend="dummy")
        agent = PhoenixAgent(venv, n_steps=32, batch_size=32, n_epochs=1)
        agent.save(tmp_path / "y_v1.zip")
        loaded = PhoenixAgent.load(tmp_path / "y_v1.zip", env=None)
        assert loaded._obs_norm is None
        obs = np.ones(venv.observation_space.shape[0], dtype=np.float32)
        assert np.array_equal(loaded._normalize(obs), obs)
        venv.close()


class TestTrainPipeline:
    def test_manifest_and_holdout_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        matches = _corpus(30)
        monkeypatch.setattr(tfs, "CORPUS_DIR", tmp_path)
        calls: List[int] = []
        holdout_ids = {m["match_id"] for m in split_matches(matches, "t20i_men", tmp_path)[2]}

        def fake_eval(model: Any, specialist_key: str, format_name: str, test_episodes: list, **kw: Any):
            ids = {ep[0]["match_id"] for ep in test_episodes}
            if ids == holdout_ids:
                calls.append(1)
            return _metrics(mean_match_pnl=float(len(calls)), matches_tested=len(test_episodes)), []

        monkeypatch.setattr(tfs, "evaluate_specialist_holdout", fake_eval)
        tfs.train_single_specialist(
            "t20i_men", "T20I Men", timesteps=512, models_dir=tmp_path / "out", n_envs=2,
            backend="dummy", eval_freq=128, matches=matches, seed=3,
            overrides={"n_steps": 64, "batch_size": 32, "n_epochs": 1, "patience": 2},
        )
        assert len(calls) == 1
        out = tmp_path / "out"
        assert (out / "t20i_men_v1.zip").exists() and (out / "t20i_men_v1_vecnorm.pkl").exists()
        man = json.loads((out / "t20i_men_train_manifest.json").read_text())
        for k in ("config", "seed", "data_hash", "split_ids_hash", "git_sha", "timesteps_run",
                  "best_val_mean_match_pnl", "holdout_metrics"):
            assert k in man
        assert man["seed"] == 3 and man["timesteps_run"] > 0
