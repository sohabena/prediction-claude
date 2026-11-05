# S90 — Positions a person can place: target position and ticket budget

**Pillar:** 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S70, S75, S83 · **Wave:** 20B
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the operator placing bets by hand, I want each variant to change its position only in a few deliberate tickets per
match, because the S75 replay showed 31 to 48 fills per variant on one T20 match (Biratnagar v Janakpur, 2026-11-03),
including 7 reduces in 9 minutes, which no person can place with a 60 s delay.

## Acceptance criteria

- The decision computes a **target position** (fractional-Kelly balance from the blended probability) and trades only
  when the gap between target and current balance exceeds a no-trade band (default: 40% of `max_trade_risk`), in one
  ticket sized to close the gap (within the cap and the per-trade risk limit).
- A per-match ticket budget per variant (default 10), shared by every reason; when 2 are left, only risk-reducing
  tickets are allowed. The budget never blocks a trade the 100k cap requires.
- The S75 rate guard and flip cooldown stay as they are.
- Replay (S74), pinned window: Biratnagar fills per model variant ≤ 10; full-window match-clustered CLV (S84) and P&L
  before vs after, both reported. The change goes live only if CLV does not fall by more than its CI half-width.
- Tests: no ticket inside the band; one ticket closes a large gap; budget exhaustion keeps reducing allowed.

## Evidence that motivated it (w05_e03, Biratnagar replay)

The blend moved by a few points every few minutes (the in-play model went from 0.53 to 0.71 inside 15 minutes), and
each move produced its own Kelly-sized increment. A 0.25 Kelly with a 3% entry edge is sensitive to such moves; the
band turns many small increments into a few larger ones.
