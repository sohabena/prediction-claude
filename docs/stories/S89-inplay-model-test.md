# S89 — In-play model test on our recordings

**Pillar:** 2 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S64, S83, S84 · **Wave:** 20B
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the strategy owner, I want to know whether the in-play model beats the LotusBook in-play price on our own
recordings before it may trade in play, because it was trained on Cricsheet and has never been compared with this
market.

## Acceptance criteria

- On real `livescore-v2` matches, at every recorded score state: the in-play model's P(home) and the market's (band
  de-vig), each scored against the final result and against the market price 10 min later; log loss and Brier,
  match-clustered (S84).
- Report per format. A per-format flag `inplay_model_allowed` in `models/registry` is true only when the model is not
  worse than the market (CI of the log-loss difference not above zero) on at least 15 matches.
- The trader (S83) uses the in-play model for new risk only where the flag is true; otherwise market-only in play.
- Tests with synthetic states; no look-ahead (state at t, price at t).
