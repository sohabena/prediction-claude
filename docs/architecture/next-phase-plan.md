# Next phase: where to push each pillar

Status: plan, 2026-11-03. Goal and pillars: `docs/GOAL-AND-ARCHITECTURE.md`. Stories S74–S82.

## Where the bottleneck is

The goal (profit) is gated by **evidence**: settled bets and settled matches with good prices, judged by CLV. Today:

| Fact (2026-11-03) | Number |
|---|---|
| Real matches recorded since 1 Nov | 22 |
| … with pre-match listing prices | 17 |
| … with a settled result | 7 |
| Pre-match rows from match pages (lays, depth, all markets) | 0: match pages open only once a match is live |
| Real recorded matches the strength model can rate | 7 of 19 |
| Settled paper bets | 0 |
| Bugs found by the first live paper match | 2 (false edges from wide-spread books, edge recorded at the wrong price) |

**Verdict.** Weight the next phase to **Pillar 3 (refinement)** and **Pillar 1 (data)**. Pillar 2 is ahead of its data:
its models are good enough to be tested, and what they lack is settled evidence, which Pillars 1 and 3 produce.

- **Pillar 3 first.** The loop learns only as fast as real matches finish (weeks to R3), and it just traded on false
  edges for an hour before anyone noticed. Replaying the paper trader over everything already recorded turns weeks into
  minutes for every change, and catches false edges before they reach live paper trading. Every bet must also settle.
- **Pillar 1 second.** Pre-match is the main arena, yet no pre-match match-page data exists, and 12 of 19 real matches
  have no strength view. Both are fixable.
- **Pillar 2 third.** One cheap, high-value change (the toss, now recorded) and one that waits for data (calibrating
  the in-play model on our own recordings).

## Stories

| Story | Pillar | Priority | What it changes | Why it helps profit |
|---|---|---|---|---|
| S74 Replay the paper trader over recorded history | 3 | P0 | Run the exact live trading code over every recorded match, offline, as if live (60 s fills, settlement, CLV) | Evidence for every change in minutes; variants compared on the same 22+ matches today; regression net |
| S75 Trading sanity guards and control checks | 3 | P0 | Invariants enforced at decision time and in the monitor: no edge from the book alone, control near zero trades, churn limits | Stops false-edge trading from ever reaching the ledger again |
| S76 Every bet settles: results reconciliation | 1/3 | P0 | Daily list of finished matches without a result; settled-market and score-feed wording verified; a small CLI to enter a public scorecard result | Unsettled bets are invisible evidence |
| S77 Pre-match match pages | 1 | P0 | Open the match page from 60 min before the start (within the cap) so pre-match books have lays, sizes and all markets | Pre-match is the main arena; best prices are on the match page |
| S78 Market-implied ratings for teams Cricsheet lacks | 1/2 | P1 | Team strengths fitted from our own recorded closing prices and results (Bradley–Terry on de-vigged probabilities) | Strength view for NCL, Emirates D20, Ford Trophy, Afghanistan… (12 of 19 unrated today) |
| S79 Variant evolution | 3 | P1 | From replay and live evidence, retire variants that lose on CLV and spawn new ones around the best (w, min edge, hedge band, cooldown) | The loop improves its own settings, not just ranks fixed ones |
| S80 Toss-aware pre-match model | 2 | P1 | Toss winner and decision as features (Cricsheet has them; we record them live); estimate updates at the toss | The toss moves T20 win probability; markets can lag it |
| S81 Zero-downtime scraper deploys | 1 | P2 | Start the new scraper before stopping the old; dedupe on overlap | No lost minutes on redeploy (two gaps today) |
| S82 Paper-trading dashboard | 3 | P2 | One page: live positions per variant and match, CLV and P&L to date, R3 progress, last trades | The operator sees the loop working (or not) at a glance |

## Order of work

- **Wave 19A (parallel):** S74 replay, S75 guards, S76 results, S77 pre-match pages.
- **Wave 19B:** S78 market-implied ratings, S80 toss-aware model, S79 variant evolution (needs S74).
- **Wave 19C:** S81 deploys, S82 dashboard.

## How we will know it worked

- A replay over all recorded matches runs in minutes and reproduces live paper decisions for the same inputs.
- The market-only control places near zero trades in replay and live; no trade ever shows an edge from the book alone.
- Every real match older than 12 h has a settled result or is on the reconciliation list.
- Every real match has pre-match match-page rows from 60 min before the start.
- Rated share of real recorded matches rises from 7/19 to more than 70%.
- Variant evolution produces at least one variant with a better CLV CI than the original grid on replay.
