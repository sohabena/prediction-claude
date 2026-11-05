# S92 — Today page

**Area:** UI · **Priority:** P1 · **Tier:** haiku · **Depends on:** S91, S95 · **Wave:** 21B
**Plan:** docs/architecture/ui-plan.md

## User story

As the operator, I want one page that shows what the paper trader holds right now, match by match, so I can watch
the strategy work as matches progress.

## Acceptance criteria

- `frontend/app/page.tsx` is the Today page, refreshing `/api/paper/live` every 15 s.
- A banner: "No real-money signals: no variant has passed R3 yet" (text from the evidence endpoint's R3 status).
- One card per match being traded: teams, in play or minutes to the start, score when known, back/lay for both
  teams, model and market probability.
- In each card, a compact table per variant: P&L if home wins / if away wins, worst case as a bar against 100,000,
  tickets used of 30, last trade (time, side, team, price, stake, reason). Placebos greyed and labelled.
- Empty state: "No match is being traded right now" with the next match start from `/api/data/matches`.
- Works at phone width; no actions on the page.
