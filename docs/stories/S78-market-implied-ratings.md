# S78 — Market-implied ratings for teams Cricsheet lacks

**Pillars:** 1 and 2 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S58, S59, S60 · **Wave:** 19B
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the paper trader, I want a strength view for every team we record, including competitions Cricsheet does not cover
(NCL GT20, Emirates D20, Ford Trophy, PCB and MCA events, Afghanistan, new franchises), so the model can form its own
opinion instead of falling back to the market.

## Acceptance criteria

- Ratings fitted from our recordings: for each settled or closed recorded match, the de-vigged price at the off and the
  result. Bradley–Terry (logistic on rating difference) with a prior of 0 for unknown teams, time decay, and
  shrinkage; one rating per team × family × gender.
- Used only when Cricsheet ratings do not know a team; when both exist, Cricsheet wins (the market-implied one is
  logged for comparison).
- Leakage guard: a match's own closing price never feeds the rating used for that match (fit as of the day before).
- The probability service (S69) gains a `source` value `prematch_market_rated` and reports coverage.
- Report: coverage of real recorded matches before and after (target > 70%), and calibration on a walk-forward over
  our recordings.
- Tests with synthetic leagues.
