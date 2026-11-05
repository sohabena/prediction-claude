# S59 — Market probability module (de-vig, margin, favourite–longshot)

**Priority:** P0 · **Tier:** haiku · **Depends on:** none · **Wave:** 17A · **Plan:** docs/architecture/training-strategy.md §3

## Story

As every model and evaluator, I need one definition of "the market's probability" and of the margin we pay.

## Owned files

- New `training/market.py` (create `training/__init__.py` only if it does not exist yet; another agent may create it
  at the same time, so if it exists leave it alone)
- New `tests/unit/test_s59_market.py`

## Tasks

1. Pure functions (numpy/scipy only):
   - `implied(back, lay)`: mid implied probability for one runner (a missing side uses the other; both missing gives
     None); `takeable(side, back, lay)`.
   - `overround(p_home_raw, p_away_raw)`.
   - Two-runner de-vig: `devig_multiplicative`, `devig_power` (solve k with Σ p_i^k = 1), `devig_shin` (Shin's z by
     root finding). Each returns (p_home, p_away) summing to 1 within 1e-9.
   - `market_prob_home(book, method="power")` from a snapshot book dict (back_home, lay_home, back_away, lay_away);
     uses whatever prices are present; None when unusable.
   - `margin_paid(side, price, p_fair)`: expected loss per unit staked at a fair probability.
   - `fit_fav_longshot(p_market, outcome, n_bins=10)`: binned calibration with Wilson intervals plus a fitted
     `logit p_true = a + b·logit p_mkt` with standard errors; `apply_fav_longshot`.
2. Tests: the three methods agree on a fair book; a known Shin example; power method on an asymmetric book; missing
   sides; the fitted line recovers a and b from seeded synthetic data.
3. Module docstring: which method is the default and why (power handles the favourite–longshot shape).

## Guardrails

No DB, no network. Do not commit. Type hints and docstrings in the repo's style.
