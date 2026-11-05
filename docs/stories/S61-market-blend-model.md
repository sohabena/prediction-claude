# S61 — Market-blend edge model (Bayesian, shrinks to the market)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S58, S59, S60, S63 (segments) · **Wave:** 17B · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the signal engine, I need `p_final`: the market's probability, corrected only where recorded evidence shows the
market is wrong or our strength model adds information, with an honest uncertainty.

## Owned files

- New `training/blend.py`, `scripts/train_blend_model.py`, `tests/unit/test_s61_blend.py`

## Tasks

1. `BlendModel` (numpy/scipy only): Bayesian logistic regression
   `logit p = a + b·L_mkt + c·(L_model − L_mkt) + Σ_k d_k·segment_k` with `L = logit`. Gaussian priors centred on
   a=0, b=1, c=0, d=0, configurable sd (defaults a 0.1, b 0.1, c 0.3, d 0.1). Fit by MAP plus a Laplace approximation
   (Hessian) for the posterior mean and covariance. `predict(x) -> (p_mean, p_sd)` from 200 seeded posterior draws.
   With no data it returns the market probability exactly.
2. Training rows: one per match per chosen snapshot (default `T-10m`; configurable) from the S58 dataset, with `p_mkt`
   from `training.market.market_prob_home` (power) and `p_model` from S60 `PrematchModel` when the match can be rated
   (Cricsheet link or registry teams known to the ratings); otherwise the `c` term is 0 for that row (`has_model`
   flag). Exclude simulated matches unless `--include-simulated`; exclude rows with no `home_won`.
3. Segments come only from `training/segments.py` (S63's pre-registered definitions).
4. `scripts/train_blend_model.py`: walk-forward by match date (expanding window, refit before each test day); write
   per-match predictions with sd to `models/blend/predictions.jsonl` and the latest posterior to
   `models/blend/latest.json`. Print usable matches and the posterior of c with its 95% interval.
5. Tests: no data gives the market; seeded data with known a, b, c is recovered within tolerance; the prior shrinks a
   tiny sample towards the market; no test-day row appears in its training set.

## Guardrails

Never write to the DB in tests; read-only queries only. Do not touch Docker, do not commit. Outputs are
probabilities, never bets.
