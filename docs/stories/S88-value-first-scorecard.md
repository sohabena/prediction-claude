# S88 — Value-first scorecard and report

**Pillar:** 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S84, S85 · **Wave:** 20C · **Absorbs:** S82
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the operator, I want the daily report and the dashboard to lead with the few measures that decide money, so I am
not reassured by row counts or by per-bet confidence intervals.

## Acceptance criteria

- Report headline, in this order: settled real matches with a tradable book (total and last 7 days); per variant,
  match-clustered CLV excess over placebo, pre-match and in-play apart; result coverage (the S76 missing list); fill
  rate (S87, when available); R1/R2/R3 progress counted in matches.
- Tier-B measures in a second section. Tier-C measures (row counts, Elo comparisons, per-bet CIs) leave the report
  (the monitor keeps its own checks).
- The scorecard in `docs/GOAL-AND-ARCHITECTURE.md` uses the same measures.
- Dashboard (the S82 scope): a static page regenerated every minute with live positions and the headline above;
  read-only.
