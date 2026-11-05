# S30 — Specialist Zoo retraining and comparative holdout evaluation on expanded features

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S26, S29 · **Wave:** 11
**Conflicts with:** none (strictly touches retraining scripts and benchmark report generator)

## Story

As the ML lead, I need an automated retraining pipeline to train the 12 Format Specialist models on the expanded observation space (incorporating Team Elo, Venue Priors, and Crease Resources) and benchmark them against the baseline 50-dimensional models on honest chronological holdout sets, to prove statistically that the new features improve out-of-sample Sharpe and ROI.

## Background

- After adding Elo, venue priors, and player resource features, the observation space is upgraded.
- We must not overwrite existing models in `models/specialists/` until an honest, leak-free holdout benchmark proves superiority.
- The retraining script [`scripts/train_format_specialists.py`](file:///c:/Users/sohai/Downloads/prediction-claude/scripts/train_format_specialists.py) must support training new candidate models into a staging directory `models/staging_v3/` using the chronological 80/20 train/test split.
- Success is measured by:
  1. Higher holdout win rate or Sharpe ratio on unseen matches.
  2. Lower Brier score on implied probability calibration.
  3. Zero regression on baseline risk metrics (max drawdown < 15%).

## Context Pack

- Model Zoo: `rl/model_zoo.py`
- Training script: `scripts/train_format_specialists.py`
- Holdout evaluation: `evaluate_specialist_holdout` in `train_format_specialists.py`
- Benchmark comparison script: `scripts/benchmark_offline_improvements.py`
- Output artifact: `docs/reports/model_zoo_v3_evaluation_report.md`

## Owned Files

- `scripts/train_format_specialists.py` (cli arguments and staging directories only)
- `scripts/benchmark_offline_improvements.py`
- New: `tests/unit/test_s30_retraining_pipeline.py`

## Tasks

- [ ] In `scripts/train_format_specialists.py`:
  - Add `--output-dir` argument defaulting to `models/staging_v3/`.
  - Ensure training loop reads expanded observation features without dimension mismatch errors.
  - Automatically write evaluation metrics (`holdout_sharpe`, `holdout_roi`, `holdout_win_rate`, `brier_score`) into `models/staging_v3/metadata.json`.
- [ ] In `scripts/benchmark_offline_improvements.py`:
  - Compare `models/specialists/metadata.json` (baseline) against `models/staging_v3/metadata.json` (candidate).
  - Compute paired t-tests or bootstrap confidence intervals for win rate and return improvements.
  - Export structured Markdown report: `docs/reports/model_zoo_v3_evaluation_report.md`.
- [ ] Implement `tests/unit/test_s30_retraining_pipeline.py`:
  - Verify that staging directory isolation works and production models are never overwritten prematurely.
  - Assert that evaluation metrics are properly parsed and recorded.

## Verification

```bash
# Run unit tests
python -m pytest tests/unit/test_s30_retraining_pipeline.py -v

# Run 100-step dry-run training across 1 specialist into staging
python -m scripts.train_format_specialists --specialist t20i_men --timesteps 500 --output-dir models/test_staging/
```

