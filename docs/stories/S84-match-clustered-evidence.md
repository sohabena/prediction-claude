# S84 — Match-clustered evidence

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S62, S73, S74 · **Wave:** 20A
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the operator, I want every confidence interval on CLV and P&L to treat the match as the unit, because bets on one
match move together. Today 60 bets from 3 matches give a CI that looks like proof and is several times too narrow.

## Acceptance criteria

- One shared function (e.g. `shared.metrics.cluster_bootstrap`) that resamples matches, not bets: mean CLV
  (stake-weighted and unweighted) and total P&L per match, with a seeded 95% CI and the match count.
- Used by the daily review (`virtual_trading/paper_review.py`), the replay summary (`scripts/replay_paper.py`), the
  referee's policy simulation and the R2/R3 gate checks. Per-bet CIs leave the headlines (a detail table may keep them).
- Every figure is shown with "n matches / n bets"; a CI from fewer than 10 matches is labelled "too few matches".
- Reported separately for pre-match and in-play bets.
- Gates: R2 and R3 minimums are checked on matches; a gate cannot pass below its match minimum whatever the bet count.
- Tests: perfectly correlated bets within a match give the same CI width as one bet per match; determinism with the seed.
