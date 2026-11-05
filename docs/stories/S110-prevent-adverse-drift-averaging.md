# S110 — Freeze adverse position averaging: do not add to collapsing positions

**Pillar:** 3 · **Priority:** P0 · **Tier:** sonnet · **Depends on:** S70, S75, S109 · **Wave:** 25

## User story

As the operator, I want the strategy engine to stop adding to an existing position (`reason = "add"`) when the market
price has moved significantly against our entry, so that we never "catch a falling knife" on a collapsing team.

## Evidence (2026-11-05 forensic audit)

In the settled paper bets:
- 166 `add` bets on BACK positions where odds drifted to an average of 11.63 lost **`-1,917,918`**.
- In match `lb_36274786` (Kathmandu Gurkhas v Biratnagar Kings), when Biratnagar began collapsing, the model added at
  15.0, then added again at 19.5, 21.0, and 22.0, repeatedly pouring 15,000 to 25,000 into doomed positions because the
  perceived edge appeared to grow as the odds lengthened.
- Professional betting rule: add only when the thesis is improving or stable, never average down into an adverse market
  drift exceeding 30%.

## Acceptance criteria

1. **Model edge strategy (`strategy/model_edge.py`):**
   - In `_move_to`: when `reason == "add"`:
     - Check the position's average entry price or current balance.
     - If the candidate's current decision price represents an adverse drift greater than 30% relative to the position's
       established price (i.e. backing at a price $> 1.30 \times$ previous average entry price, or laying at a price
       $< 0.70 \times$ previous entry price), drop the `add` order (return `None`).
     - Hedges (`reduce`, `green_up`) and initial entries (`enter`) are completely unaffected.
2. **Tests (`tests/unit/test_s110_adverse_drift.py`):**
   - Verify an initial `enter` at 2.0 succeeds.
   - Verify an `add` at 2.2 (10% drift) succeeds.
   - Verify an `add` at 3.5 (>30% adverse drift) is suppressed.
   - Verify `reduce` and `green_up` remain executable regardless of drift.
