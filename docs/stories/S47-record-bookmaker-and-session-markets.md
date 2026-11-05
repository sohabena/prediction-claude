# S47 — Record LotusBook's Bookmaker and session ("fancy") markets

**Priority:** P0 · **Tier:** sonnet (DOM discovery needs judgement; haiku only if the operator insists) · **Depends on:** S37 · **Wave:** 15
**Operator prerequisite:** a live cricket match on LotusBook at run time, for discovery. If none is live, build against a
saved page and report that discovery is pending.

## Story

As the strategy owner, I need the in-house Bookmaker prices and session-runs lines recorded alongside the
exchange-mirror match odds, with timestamps. Those in-house markets are where a soft price can beat the sharp one
(S49) and where a runs model can beat a trader's line (S48).

## Background

- The scraper reads only the **listing page** match-odds grid (`scraper/worker.py` JS extractor L30-260: back/lay/draw
  buttons; `[data-odd-type]` / `backBtnBg` / `layBtnBg` classes). It never opens a match page, where these sites show
  the "Bookmaker" market (in-house back/lay, often 0% commission, stake limits) and the "Fancy" or session markets
  (lines like "6 over runs": a NO/YES pair of run values with rates, plus "suspended" / "ball running" states).
- DB tables: `odds_ticks` holds match odds only. There is no table for other markets.

## Context pack

- `scraper/worker.py` (456 lines): the Playwright page worker, `poll_interval=3`, poll loop ~L417-451; CDP session L383.
  `scraper/manager.py` (`ScraperManager`, `_on_events`, `_store_odds_tick` ~L484-552, recording gate from S37, simulated
  gate). `scraper/match_filter.py is_simulated_name`. Watchdog: `scripts/run_scraper_forever.py`.
- ORM base and models: `backend/models/odds.py` (pattern for a TimescaleDB hypertable); migrations in `alembic/versions/`
  (`002_timescale_hardening.py` is the pattern); `backend/db_init.py` hypertable list. **Do not run migrations**; the
  operator applies them (`docs/runbooks/recording.md`).
- Politeness: at most one match page open per live match, polled no faster than every 3 s, and only for matches that
  pass the classifier. No login, no clicking bet buttons, no form input.

## Owned files

- New: `scraper/market_pages.py` (match-page worker and extractor)
- New: `scraper/parsers/lotusbook_markets.py` (DOM → records)
- `scraper/manager.py` (start/stop one market-page worker per live recorded match; storage call only)
- New: `backend/models/market_ticks.py`, `alembic/versions/003_market_ticks.py`, and the `backend/db_init.py`
  hypertable entry only
- New: `tests/fixtures/lotusbook/` (saved, sanitised match-page HTML), `tests/unit/test_s47_market_pages.py`
- `docs/runbooks/recording.md` (a section for the new table)

## Tasks

1. **Discovery** (read-only):
   - with the existing Playwright setup, open one live match page and save its sanitised HTML (strip any
     account/session data) as a fixture;
   - write down, in the story report, the selectors for: the Bookmaker market (selections, back/lay, suspended
     state), the session markets (name, NO value / rate, YES value / rate, suspended / ball-running), and the market
     min/max stake if shown.
2. `market_ticks` hypertable: `time, match_id, market_type ('bookmaker'|'session'|'other'), market_name, selection,
   back, lay, line_no, rate_no, line_yes, rate_yes, status ('open'|'suspended'|'ball_running'), source='lotusbook'`.
   PK `(time, match_id, market_type, market_name, selection)`. Write the migration; do not apply it.
3. The parser turns the DOM into those records. Market names are normalised (e.g. `"6 over runs IND"` →
   `session:6_over:India`) when they match a known pattern; otherwise `other`.
4. Manager wiring: for each live, recorded, non-simulated match, run one market-page worker; store records only when
   they change (dedupe on content); stop the worker when the match completes.
5. Health: `scripts/recording_health.py` is S37's. Do not edit it; report the query the operator can add.

## Success criteria

- [ ] The parser on the saved fixture yields the bookmaker selections with back/lay, and at least one session market
      with NO/YES lines and rates (test).
- [ ] Suspended and ball-running states parse to `status` (test with fixture variants).
- [ ] Unchanged consecutive snapshots store nothing (test).
- [ ] Simulated matches never start a market-page worker (test).
- [ ] The migration exists and is not applied. Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s47_market_pages.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Placing bets, logging in, or any interaction beyond reading the page.
