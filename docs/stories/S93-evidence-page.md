# S93 — Evidence page

**Area:** UI · **Priority:** P1 · **Tier:** haiku · **Depends on:** S91, S95 · **Wave:** 21B
**Plan:** docs/architecture/ui-plan.md

## User story

As the operator, I want to see at a glance whether any strategy is actually beating the market, using only the
measures that decide money, so I am not misled by activity or by luck.

## Acceptance criteria

- `frontend/app/evidence/page.tsx`, from `/api/paper/evidence` (refresh every 5 min).
- Top: the validation ladder R1, R2, R3 as progress bars counted in matches (and bets for R3), with what each needs.
- Per variant table: CLV in excess of placebo with CI and "n matches / n bets", pre-match and in-play apart; P&L
  with CI; "too few matches" shown plainly. Placebos and the market-only control listed separately as controls.
- The plain sentence from the review when no variant beats the placebo.
- In-play model gate per format (closed/open, matches).
- Fill realism (fill rate; note that the size cap is off until the size unit is verified).
- No per-bet confidence intervals, no row counts.
