# S82 — Paper-trading dashboard

**Pillar:** 3 · **Priority:** P2 · **Tier:** haiku · **Depends on:** S71–S73 · **Wave:** 19C
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the operator, I want one page that shows, at a glance, what the paper trader holds right now and how each variant
is doing, so I can see the loop working without reading logs.

## Acceptance criteria

- Live section: each match being traded with its current price, model probability, and each variant's position
  (P&L if home wins / if away wins), worst case against the 100,000 cap, last trades.
- Evidence section: per variant CLV and P&L with CIs, settled bets, R3 progress bars, suspended and retired variants.
- Data health section: the latest quality-monitor result.
- Served locally (a small read-only HTTP endpoint in the existing API, or a static page regenerated every minute);
  no write actions on the page.
