# S83 — No pre-match opinions in play

**Pillars:** 2 and 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S64, S70, S72, S75 · **Wave:** 20A
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the strategy owner, I want in-play trades to be priced only by a model that knows the score, because a pre-match
opinion set against an in-play price is not an edge: the price already knows what happened. The first replay placed
204 in-play bets this way, with recorded edges up to 0.86.

## Acceptance criteria

- `ProbabilityService.estimate` reports the source as today, and the trader allows a model edge in play only when the
  source is `inplay` (fresh live score, `STATE_FRESH`). In play with source `prematch` or none, the variant is
  treated as market-only: no enter, add or flip; reduce and green-up still allowed (they lower risk).
- The pre-match model still prices pre-match decisions, up to the actual off (S86 when available, else the start time).
- Each refusal counted in the cycle report as `inplay_without_state`.
- Replay (S74) before and after over the same pinned window: per variant intents by source and in-play flag, largest
  edge, CLV and P&L. Required after: zero in-play intents with source `prematch`.
- Tests: in-play with stale score refuses new risk but allows reduce; pre-match unchanged; the switch at the off.
