# S09 — Training regime: scale, sampling, normalisation, splits

**Priority:** P1 · **Depends on:** S02, S03, S04 merged · **Wave:** 3
**Conflicts with:** S02 (same file) — run only after S02 is merged

## Story

As an RL researcher, I need each specialist trained on a meaningful fraction of its
corpus with normalised observations/rewards, a chronological split, early stopping on a
validation set, and reproducible configs, so that "trained on N matches" is true and the
models are not under-fitted noise.

## Background

- Specialists were trained for 4 000–15 000 timesteps. With ≈ 106 ticks/match and
  sequential episodes, each saw ≈ 40–140 matches (< 1 % of its corpus).
- `scripts/train_format_specialists.py` splits by list index after a shuffle (not by date).
- No `VecNormalize`; reward scale differs per specialist; single env (no vectorisation).
- `rl/agent.py` fixes `n_steps=2048, batch_size=64` regardless of data size; the weekly
  script uses `n_steps=256, n_epochs=4` — inconsistent configs.
- No validation-based early stopping or checkpoint selection; no seeds recorded.

## Context pack

- `scripts/train_format_specialists.py` training region (S02 will have shifted line numbers — re-grep):
  `load_corpus(key) -> list[list[tick]]` (~L233; drops `start_time` — return match dicts or `(start_time, ticks)` and
  update its callers in the file); `train_single_specialist(specialist_key, format_name, timesteps=8000,
  target_bets_per_match=25, eval_only=False)` (~L489-595: index split ~L509-516, `CricketBettingEnv(...)` ~L526,
  `PPO("MlpPolicy", env, ..., n_steps=256, batch_size=64)` ~L534-548, `model.learn` ~L555, `model.save` ~L557,
  evaluator call ~L563); `main()` ~L666-726 (loops `ALL_SPECIALISTS_CONFIG`, calls `persist_virtual_bets`).
- `rl/agent.py` (117 lines): `PhoenixAgent(env, learning_rate=3e-4, n_steps=2048, batch_size=64, n_epochs=10, gamma=0.99,
  gae_lambda=0.95, clip_range=0.2, ent_coef=0.01, vf_coef=0.5, max_grad_norm=0.5, tensorboard_log, device)`;
  net_arch `{"pi": [256,256,128], "vf": [256,256,128]}`, ReLU; `predict(obs, deterministic)`; `learn(...)`;
  `save(path)`; `@classmethod load(path, env)` (`cls.__new__` + `PPO.load`); `get_action_distribution(obs)`.
  Callers: `rl/trainer.py`, `rl/model_zoo.py:329,341` (`PhoenixAgent.load(path, env=None)`),
  `virtual_trading/live_trading_loop.py`, scripts. Keep `load(path, env)` positional-compatible.
- VecNormalize at inference with `env=None`: load the pickle, take `obs_rms.mean/var`, `clip_obs`, `epsilon`, and apply
  `np.clip((obs - mean) / np.sqrt(var + eps), -clip, clip)` inside `PhoenixAgent.predict` /
  `get_action_distribution`.
- Windows: `SubprocVecEnv` needs the `if __name__ == "__main__":` guard and picklable env factories; fall back to
  `DummyVecEnv` for `n_envs == 1` or on spawn errors.
- Evaluator: S02's `evaluate_specialist_holdout(..., policy="model")` for val (early stopping) and holdout (once).
- PyYAML is installed (`import yaml`).

## Owned files

- `scripts/train_format_specialists.py` — **training** functions only
  (`train_single_specialist`, `load_corpus`, CLI/main, config maps). Do not modify the
  evaluation functions delivered by S02.
- `rl/agent.py`
- New: `configs/specialists.yaml`
- New: `tests/unit/test_s09_training_regime.py`

## Tasks

1. **Config file** `configs/specialists.yaml`: per specialist `gamma, lr, ent_coef, n_steps,
   batch_size, n_epochs, net_arch, timesteps_per_match` with a `defaults` block. Load it in
   `train_format_specialists.py`; CLI flags override.
2. **Budget:** `total_timesteps = timesteps_per_match (default 300) × n_train_matches ×
   epochs_over_data (default 3)`, with `--max-timesteps` cap and `--smoke` mode
   (2 000 steps, 20 matches) for tests. Print matches, ticks and planned timesteps.
3. **Chronological split:** sort by `start_time`; train = oldest 70 %, val = next 15 %,
   holdout = newest 15 %. If S07's split manifest `data/specialists/splits_<key>.json`
   exists, use it instead. Never shuffle across the boundary.
4. **Vectorised envs:** `SubprocVecEnv` (or `DummyVecEnv` on Windows if subprocess fails)
   with `n_envs` (default 8), each with `episode_sampling="random"` and a distinct seed;
   wrap with `VecMonitor` and `VecNormalize(norm_obs=True, norm_reward=True, clip_obs=10)`.
   Save the `VecNormalize` statistics next to the model (`<key>_vecnorm.pkl`) and load
   them in `PhoenixAgent.load` when present (inference with `training=False`,
   `norm_reward=False`). Document that the live loop must apply the same normalisation
   (report to orchestrator if `live_trading_loop.py` needs a change — do not edit it).
5. **Early stopping:** every `eval_freq` steps evaluate on the val set with S02's
   evaluator (`policy="model"`, sequential sampling); keep the best checkpoint by
   val mean per-match P&L; stop after `patience` (default 5) evaluations without
   improvement. Holdout is evaluated **once**, at the end, on the selected checkpoint.
6. **Reproducibility:** set seeds (python, numpy, torch, env); write
   `<key>_train_manifest.json` with config, seed, data hash, split IDs hash, git SHA,
   timesteps actually run, best val metric, and the holdout metrics.
7. `rl/agent.py`: accept `policy_kwargs`/`net_arch` and `n_envs`-aware `n_steps`; add an
   optional `normalize_path` to `load`. Keep backwards-compatible defaults.
8. Output models to `--models-dir` (default `models/specialists/candidates/`); never write
   the champion path directly — promotion is S07's job.

## Success criteria

- [ ] `--smoke` run for one specialist completes in < 3 min on CPU, produces model,
      vecnorm stats and manifest in a temp dir (test or documented manual run).
- [ ] Test: split is chronological — max(train date) ≤ min(val date) ≤ min(holdout date).
- [ ] Test: with 8 random-sampling envs and 20 000 steps on a 100-match fixture, ≥ 80
      distinct episodes are visited (log via env info).
- [ ] Test: `PhoenixAgent.load` with a vecnorm file returns normalised predictions equal to
      the training-time wrapper's on the same observation.
- [ ] Manifest contains all fields listed in task 6.
- [ ] Holdout evaluated exactly once per training run (mock evaluator call count).

## Verification

```bash
python -m pytest tests/unit/test_s09_training_regime.py -q
python scripts/train_format_specialists.py --specialist t20i_men --smoke --models-dir %TEMP%/phoenix_s09
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Full production retraining (orchestrator runs it after S01 data regeneration).
- MaskablePPO / algorithm changes (research backlog).
