# S75 — Trading sanity guards and control checks

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S70, S72, S74 · **Wave:** 19A
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the operator, I want the trader to refuse trades whose edge comes from the book itself or from bad data, and to be
told at once if the control strategy starts trading, so a pricing bug can never run for an hour unnoticed again.

## Acceptance criteria

- Decision-time guards in the trader (each refusal logged with a reason and counted per cycle):
  - the market probability must lie inside the band both runners' prices allow (S59 `band`); outside → no trade;
  - the book's fields must all be fresher than 60 s for in-play decisions (15 min pre-match);
  - back < lay for each runner; spread under a configured maximum (default 15% of price) or no new risk;
  - no more than N trades per match per variant per 10 minutes (default 3), and no flip within 5 minutes of the last
    flip.
- Monitor check `paper_sanity` (fails): the `market_only` control placed more than 2 trades in the last 24 h; any
  filled bet whose recorded edge at the decision price is above 20% (implausible); any variant with more than 15 trades
  on one match.
- Replay (S74) over the 2026-11-03 Biratnagar v Janakpur match shows the control placing zero trades and the model
  variants placing fewer than 10 trades each.
- Tests for each guard.
