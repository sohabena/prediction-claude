# S60 — Pre-match strength model v3

**Priority:** P0 · **Tier:** haiku · **Depends on:** S38 code (ratings, fair_value) · **Wave:** 17A · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the edge model (S61), I need the best pre-match win probability we can build from 20 years of Cricsheet data,
honestly walk-forward evaluated, because pre-match is where manual bets can act.

## Background

- `strategy/ratings.py` `CricketRatings`: team Elo with a home term, decayed player batting and bowling strength
  shrunk to the league average, venue par and toss priors (`shared/venue_priors.py`), `competition_tier`;
  `pre_match(...)` gives the `pm_*` fields. `strategy/fair_value.py` v2 mixes state and pre-match features in one GBM.
- `scripts/train_fair_value.py` has the walk-forward machinery (season cutoff rule, `brier`, `log_loss`, `ece`).
  v2 results: `models/fair_value/t20_v2_all_walkforward.json` (2026 fold Brier 0.157, per-ball rows).

## Owned files

- New `strategy/prematch_model.py`, `scripts/train_prematch_model.py`, `tests/unit/test_s60_prematch_model.py`
- Read, do not modify: `strategy/ratings.py`, `strategy/fair_value.py`, `scripts/train_fair_value.py` (import helpers)

## Tasks

1. One row per match (not per ball). Features as of the match date from `CricketRatings` (`pm_*`) plus: Elo
   difference, expected-XI strength difference (mean of each team's players over their last 10 appearances in that
   format and gender), days since each team's last match, venue chase bias, tier, gender. Pre-toss (toss unknown).
   Label: home won; ties and no-results excluded.
2. `PrematchModel`: (a) L2 logistic regression on 4–6 standardised core features; (b) `HistGradientBoostingClassifier`
   on all features; (c) a logistic stacker on the two logits fitted on the calibration season, then isotonic on the
   same season. `predict_home(features) -> float`; joblib save/load.
3. `scripts/train_prematch_model.py --family t20|odi [--gender male|female|all]`: season walk-forward (fit < Y-1,
   calibrate Y-1, test Y). Report per fold and pooled: log loss, Brier, ECE for v3, a pure-Elo baseline
   (`1/(1+10^(-Δ/400))` with the home term), and logistic and GBM alone; segment by gender and tier. Write
   `models/prematch/<family>_v3.joblib` (fitted through the latest season) and `<family>_v3_walkforward.json`.
4. Run it for t20 and odi and put the pooled numbers in the final report.
5. Tests on a small synthetic match list: no feature uses information after the match date; save/load round trip;
   ties excluded; the stacker is at least as good as its parts on a constructed case.

## Guardrails

No DB writes. Do not touch Docker, do not commit. Type hints and docstrings in the repo's style; numpy, scipy,
scikit-learn, pandas only. Cricsheet reading may take minutes; keep it to the existing loaders.
