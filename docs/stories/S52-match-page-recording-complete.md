# S52 — Complete, clean match-page recording for every live match

**Priority:** P0 · **Tier:** haiku · **Depends on:** S50, S51, S54 · **Wave:** 16B

## Story

As the strategy owner I want one read-only match page open for every live match (SRL included), recording every
market on it, the score, and the result, each stored only when it changes.

## Background (verified 2026-11-03)

- `scraper/market_pages.py::MarketPageWorker` polls `page.content()` every 3 s, reloads every 300 s, and stores the
  whole record list when a single fingerprint of all records changes (so one price change rewrites every market).
- `scraper/manager.py`: `MARKET_WORKER_CAP = 4`; a worker is started only from the live branch of `_on_events`; it is
  stopped only when the match is in `_completed_match_ids`. `_market_attempted` blocks a restart for the process
  lifetime, so a crashed worker is never restarted.
- `scraper/parsers/lotusbook_markets.py::_parse_runner_lists` reads any `role="list"` block labelled
  "<market> runners and odds". Real SRL page (fixture `tests/fixtures/lotusbook/match_page_srl_live_20261103.html`,
  full page via the scraper's own headless Playwright) has: "Winner (incl. super over)", "1st innings over 18 -
  Trinbago Knight Riders SRL total" (two blocks, lines 6.5 and 7.5), "... odd/even", "1st innings - Narine, Sunil -
  SRL dismissal method", "... 8th dismissal method (extended)". These all become `market_type='other'` today.
  Real non-SRL page: `tests/fixtures/lotusbook/match_page_real_prestart_20261103.html`.
- S51 adds `scraper/parsers/lotusbook_scoreboard.py`; S54 adds `LotusResultDetector.update_scoreboard`.

## Owned files

- `scraper/market_pages.py`, `scraper/parsers/lotusbook_markets.py`
- `scraper/manager.py` (market-worker section `_ensure_market_worker` .. `_store_market_records`, and new
  scoreboard storage; do not touch the event gates S50 changed)
- `docker-compose.yml` (`SCRAPER_MARKET_WORKER_CAP` env only)
- `tests/unit/test_s47_market_pages.py`, new `tests/unit/test_s52_match_pages.py`

## Tasks

1. **Per-record dedupe**: store a record only when its own content (`content_key`) differs from the last stored record
   with the same `(market_type, market_name, selection)`. A market that disappears from the page gets one record
   with `status='closed'` and null prices. Add `closed` to `STATUSES`.
2. **Market classification** (`normalise_market_name` / `_classify_list_market`), lower-snake names, team names kept:
   - Winner / Match Odds → `match_odds`
   - "<n>st|nd|rd|th innings over <k> - <team> total" → `market_type='over_runs'`,
     `market_name='over_runs:inn<n>:over<k>:<Team_Name>'`, `selection` = the runner with its line, title-cased
     ("under 6.5" → "Under 6.5"), and the line as a float in `line_no` (Under) or `line_yes` (Over).
   - "... odd/even" → `odd_even:inn<n>:over<k>:<Team_Name>`
   - "... dismissal method" → `dismissal:inn<n>:<player or wicket ordinal>`
   - Tie / toss / other → `other` with the raw name.
   Two blocks with the same name but different lines must not collide on the PK: selection already differs
   ("Under 6.5" vs "Under 7.5"); add a test.
3. **Coverage**: cap from env `SCRAPER_MARKET_WORKER_CAP` (default 12). A worker that ends (error or page closed)
   while its match is still live is restarted after 60 s (at most 5 restarts per match). A worker stops when its
   match has been absent from the listing for 10 min, as well as on completion. Pre-match matches do not get pages.
4. **Scoreboard**: each poll also runs S51's parser on the same HTML; when `started` and the state changed, store a
   `match_context` row (via the existing `MatchContextRecord` model) with `source`-equivalent provenance logged. When
   S51's report says the widget does not render headless, this still runs and simply finds nothing; log once per
   match `scoreboard_not_rendered`.
5. **Results**: when the scoreboard comment parses as a finished result, call
   `self._result_detector.update_scoreboard(...)`.
6. Never click, never type, never log in, never request URLs other than the match page itself.
7. Tests with fakes (FakePage serving fixtures): per-record dedupe; closed market; classification of every block in
   the SRL fixture; worker restart after failure while live; stop after 10 min absent; cap respected; scoreboard
   row stored only on change; result handed to the detector.

## Guardrails

No DB, browser or network in tests. Do not commit.
