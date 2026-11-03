# S87 — Liquidity-aware fills

**Pillars:** 3 and 1 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S71, S74, S77 · **Wave:** 20B
**Plan:** docs/architecture/value-focused-plan.md

## User story

As the operator, I want paper fills to take only what the market showed, because a 25,000 stake filled at a price
with 3,000 available is P&L the real market would never pay.

## Acceptance criteria

- At fill time the ledger reads the size shown on the match page for that side and price (`market_ticks` sizes, as
  of the fill time). It fills up to that size; the rest lapses (a reduced stake with the asked stake kept, documented).
- No size known (listing-only book): fill at most a configured default (10,000) and mark it `size_unknown`.
- Reports: fill rate (filled stake / asked stake), share size-capped, slippage vs the decision price, per variant.
- Replay options `--fill-delay 30|60|120` and `--no-size-cap`; the replay summary shows capped vs uncapped P&L.
- Tests with fake books: full, partial, unknown size, each delay.
