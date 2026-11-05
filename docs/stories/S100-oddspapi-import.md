# S100 — OddsPapi historical odds client and importer

**Pillar:** 1 · **Priority:** P2 · **Tier:** haiku · **Depends on:** S98 · **Wave:** 23

## User story

As the operator, I want cricket match-winner price histories from OddsPapi's free tier (Pinnacle and other
bookmakers, January 2026 onward) as S98 records, so recent matches gain a sharp-bookmaker closing price.

## Facts (from https://oddspapi.io/en/docs, 2026-11-04)

- Host `https://api.oddspapi.io`; every endpoint takes the key as query parameter `apiKey`.
- `GET /v4/historical-odds?fixtureId=..&bookmakers=a,b,c` (at most 3 bookmaker slugs per call; 5000 ms cooldown per
  call, a 304 still counts). Response: `fixtureId`, `bookmakers.<slug>.markets.<marketId>.outcomes.<outcomeId>.
  players."0"` = list of `{createdAt, price, limit, active, exchangeMeta}`. Finished fixtures return an ETag.
- Fixtures, sports, tournaments and markets come from other `/v4/...` endpoints. Read their pages under
  `https://oddspapi.io/en/docs/` (WebFetch is fine for the docs) and implement what they say; where a detail cannot
  be confirmed, make it a named constant with a comment saying it is unverified.
- The operator creates the account; the key is read only from the environment (`ODDSPAPI_API_KEY`). Never ask for
  it, never print it, never write it to a file or a log.

## Acceptance criteria

1. `scraper/importers/oddspapi.py`:
   - `OddsPapiClient(api_key, *, transport=None, clock=None, sleep=None)` over `httpx` with the 5 s cooldown enforced
     (injectable clock and sleep so tests run instantly); retries on 429/5xx with backoff, at most 3.
   - Raw cache: every successful response is saved to `data/historical_odds/raw/oddspapi/<endpoint>/<key>.json`
     before parsing; a cached fixture is never requested again (finished fixtures do not change).
   - `discover_fixtures(...)`: cricket fixtures in a date range (finished only), with tournament name, start time,
     participants.
   - **Betfair guard:** the requested bookmaker list never contains a Betfair slug (`is_betfair`); any Betfair
     bookmaker found in a response is dropped and counted (`dropped_betfair`), never stored.
   - `records_from_history(fixture, payload, market_id)`: for the match-winner market, per bookmaker, an `open`
     record (first price per outcome), a `close` record (last price at or before the start), and nothing after the
     start. Outcomes map to home/away by the fixture's participant order; a draw outcome goes to `price_draw`.
   - Format and gender from the tournament name (`T20`, `ODI`, `Test`, `Women`); unknown → `other`.
2. `scripts/import_oddspapi.py --from YYYY-MM-DD --to YYYY-MM-DD [--bookmakers pinnacle,bet365,1xbet] [--run]`.
   Default is a dry run: it lists how many fixtures and calls it would make and the time that takes at 5 s a call,
   and makes **no** request except fixture discovery from the cache. `--run` makes the calls. Without
   `ODDSPAPI_API_KEY` it exits 2 with a message telling the operator to create a free key and put it in `.env`.
   Writes the store with provenance naming OddsPapi, the bookmakers and the date range.
3. Tests `tests/unit/test_s100_oddspapi.py` with `httpx.MockTransport` and fake payloads only (no network): cooldown
   honoured, cache hit makes no request, retry on 429, Betfair dropped and counted and never requested, open and close
   chosen correctly (prices after the start ignored), draw outcome, missing key exits 2, the key never appears in
   logs or cache file names.
