# S72 — Paper trader service

**Priority:** P0 · **Tier:** haiku · **Depends on:** S69, S70, S71 · **Wave:** 18B · **Plan:** docs/architecture/paper-trading.md

## Story

As the operator, I want virtual bets placed automatically on every live and upcoming real match, around the clock.

## Owned files

- New `virtual_trading/paper_trader.py`, `scripts/run_paper_trader.py`, `tests/unit/test_s72_paper_trader.py`
- `docker-compose.yml`: service `paper-trader` (profile `recording`, scraper image, `restart: unless-stopped`,
  `python scripts/run_paper_trader.py --loop`, ./logs ./models ./data mounted, POSTGRES_* env); `Dockerfile.scraper`
  COPY lines as needed

## Tasks

1. Every 15 s (read-only on market tables, writes only to `paper_bets`):
   - matches to trade: real (not simulated) matches with a price in the last 5 min, pre-match within 6 h of the start
     or in play; SRL excluded by default (`--include-simulated` flag);
   - snapshot: as-of book from `v_match_odds` (merge sources like S58: each field from its latest row within 15 min),
     latest `match_context` row if any (in play), registry facts;
   - `ProbabilityService.estimate` (S69), then for each variant `model_edge.decide` (S70) with that variant's position
     from the ledger; record intents (S71); `fill_pending` with a `book_at` reading the same as-of rule at t+60 s;
     `settle` against `match_results`.
2. Variants: the grid in the plan (w ∈ {0.3, 0.5, 0.7} × min_edge ∈ {0.03, 0.06}) + `market_only`; names like
   `w05_e03`. Config in one place.
3. One JSON log line per decision cycle (matches considered, intents, fills, settlements) to `logs/paper_trader.log`.
4. `--once` runs one cycle and exits (for tests and manual checks).
5. Tests with fakes: a cycle places, fills and settles; the 100,000 cap holds across cycles; SRL skipped; a stale book
   gives no trade; restart-safe (positions come from the ledger, not memory).

## Guardrails

No real bets, no site interaction. No DB writes in tests. Do not start containers. Do not commit.
