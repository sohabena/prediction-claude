# S85 — Placebo controls

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S74, S84 · **Wave:** 20B
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the strategy owner, I want to know how much CLV the fill rule, the delay and price drift produce on their own, so a
variant's CLV counts only for what it adds beyond that. The market-only control almost never trades, so it cannot
answer this.

## Acceptance criteria

- Two placebo variants in replay and live paper trading, seeded and deterministic:
  - `placebo_random`: at the same decision times and stake sizes as a reference variant (default: the primary), takes
    a random side and selection;
  - `placebo_favourite`: backs the market favourite at each decision time the reference variant trades.
- They obey every guard and the 100k cap, are never shown as signals and are never ranked as primary.
- Reports show each variant's match-clustered (S84) CLV as excess over `placebo_random`, with its CI.
- The daily review states plainly when no variant's excess-CLV CI lies above zero.
- Tests: reproducible placebo decisions; same timing and stakes as the reference; excluded from primary selection.
