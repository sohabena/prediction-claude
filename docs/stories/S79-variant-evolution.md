# S79 — Variant evolution

**Pillar:** 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S73, S74 · **Wave:** 19B
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the strategy owner, I want the system to change its own settings from evidence: drop variants that lose on CLV,
try new ones near the best, so it improves over time instead of only ranking a fixed grid.

## Acceptance criteria

- A weekly step (after the daily review on Mondays) reads replay (S74) and live evidence per variant.
- Retire: a variant whose CLV 95% CI upper bound is below 0 on at least 100 settled bets (replay and live combined,
  live weighted 2×). Retired variants stop opening new risk and are listed in the report.
- Spawn: up to 3 new variants per week around the best variant (neighbouring w, min edge, hedge band, cooldown,
  max trade risk), each tested on replay first; a spawn goes live only if its replay CLV CI lower bound is not below
  the parent's.
- Never more than 10 live variants; the market-only control is never retired.
- Every change is recorded in `models/paper/evolution.jsonl` with the evidence; the report shows the family tree.
- Tests with synthetic evidence.
