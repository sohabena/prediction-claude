# S69 — Probability service for paper trading (pre-match + in-play state)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S60, S61, S68 · **Wave:** 18A · **Plan:** docs/architecture/paper-trading.md

## Story

As the paper trader, I need one call that gives P(home wins) for a match at a moment: the strength model before the
start, and a state-aware model once a live score exists, each with the market probability alongside.

## Background

- Pre-match: `strategy/prematch_model.py` `PrematchModel` (models/prematch/{t20,odi}_v3.joblib), fed by
  `training.blend.prematch_probability` (ratings via `strategy/live_context.load_ratings`, women's names handled).
- In-play: `strategy/fair_value.py` `FairValueModel` v2 (state features + `pm_*` pre-match block; `tick_features`,
  `state_features`, `prematch_features`) trained by `scripts/train_fair_value.py --family t20|odi --version v2`.
  Current artefacts `models/fair_value/{t20,odi}_v2_all.joblib` were trained before the full Cricsheet download.
- Live score rows: `match_context` (score, wickets, overs, innings, balls_remaining, batting_team, bowling_team,
  run_rate, req_run_rate, match_format) from S68 (`livescore-v2` matches only). Target is not a column: derive it as
  first-innings total + 1 from the last innings-1 row when innings = 2.
- Market: `training.market.market_prob_home` (power de-vig) on a flat book.

## Owned files

- New `strategy/probability.py`, new `tests/unit/test_s69_probability.py`
- Retrain artefacts `models/fair_value/{t20,odi}_v2_all.joblib` (+ walk-forward JSON) by running the existing script on
  the full archive (`data/raw/all` is already read via `FAMILY_DIRS`); do not modify `strategy/fair_value.py` or the
  training script

## Tasks

1. `ProbabilityService(ratings, prematch_models, inplay_models)` with
   `estimate(match, book, state=None, *, as_of) -> Estimate(p_model, p_market, source, has_model, features)`:
   - `p_market` from the book (None when unusable);
   - before the start or without a state: `p_model` from the pre-match model (None when the teams cannot be rated);
   - with a state (score row): build the v2 tick fields the `FairValueModel` expects (map `match_context` columns,
     orient home/away by batting team, derive target), attach `pm_*` from `live_context.prematch_fields`, predict;
     `source` = `prematch` | `inplay` | `market_only`.
   - Never raises on missing data: returns `has_model=False`.
2. Cache per match the pre-match fields (ratings are slow).
3. Retrain v2 for t20 and odi on the full archive and report the walk-forward Brier/log loss next to the old file.
4. Tests with stub ratings/models: home/away orientation when the away side bats; target derivation; pre-match path;
   market-only path; women's names.

## Guardrails

No DB writes. Do not touch Docker. Do not commit. No bets placed anywhere.
