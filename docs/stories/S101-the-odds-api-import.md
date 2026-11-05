# S101 — The Odds API historical client with a cost guard

**Pillar:** 1 · **Priority:** P2 · **Tier:** haiku · **Depends on:** S98 · **Wave:** 23

## User story

As the operator, I want a ready importer for The Odds API's paid historical cricket odds (snapshots since mid-2020),
which shows the credit cost before anything is spent, so I can decide whether to buy a plan.

## Facts (The Odds API v4 documentation)

- `GET https://api.the-odds-api.com/v4/historical/sports/{sport}/events?apiKey=&date=<ISO>` (cost 1).
- `GET https://api.the-odds-api.com/v4/historical/sports/{sport}/odds?apiKey=&regions=&markets=h2h&oddsFormat=decimal&date=<ISO>`
  (cost 10 per region per market). Returns the snapshot at or before `date`: `{timestamp, previous_timestamp,
  next_timestamp, data: [{id, sport_key, sport_title, commence_time, home_team, away_team, bookmakers: [{key, title,
  last_update, markets: [{key: "h2h", outcomes: [{name, price}]}]}]}]}`.
- Response headers `x-requests-remaining`, `x-requests-used`, `x-requests-last`.
- Cricket sport keys include `cricket_ipl`, `cricket_big_bash`, `cricket_psl`, `cricket_caribbean_premier_league`,
  `cricket_t20_blast`, `cricket_international_t20`, `cricket_odi`, `cricket_test_match`; keep them in one mapping to
  format and gender (unknown keys → `other`).
- Betfair bookmaker keys (`betfair_ex_uk`, `betfair_ex_eu`, `betfair_ex_au`, `betfair_sb_uk`) are excluded.
- The key comes only from `THE_ODDS_API_KEY`. Never ask for it, print it, log it or write it to a file.

## Acceptance criteria

1. `scraper/importers/the_odds_api.py`:
   - `TheOddsApiClient(api_key, *, transport=None)` over `httpx`; logs the usage headers after every call (never the
     key); raw responses cached in `data/historical_odds/raw/the_odds_api/<sport>/<kind>_<timestamp>.json`, never
     requested twice.
   - `plan_closing_snapshots(events)`: one odds snapshot per distinct `commence_time` (5 minutes before it), merged
     when several events share a start; returns the list of snapshot times.
   - `estimate_cost(n_event_calls, n_odds_calls, regions, markets) -> int` credits.
   - `records_from_snapshot(payload, kind="close")`: per event and bookmaker, one record (`snapshot_time_utc` =
     the snapshot's `timestamp`; only events whose `commence_time` is after the snapshot, so nothing in-play); outcome
     names matched to `home_team` / `away_team`, `Draw` → `price_draw`; Betfair keys dropped and counted.
2. `scripts/import_the_odds_api.py --sport cricket_big_bash --from --to [--regions uk,eu,au] [--max-credits N]`.
   Default `--max-credits 0` = estimate only: prints the planned calls and credits, makes no paid request (event
   lists may come from the cache). With a budget it stops before any call that would exceed it. No key → exit 2 with
   a message that this source needs a paid plan, which is the operator's decision.
3. Tests `tests/unit/test_s101_the_odds_api.py` with `httpx.MockTransport` only: cost estimate, budget stop before
   overspending, snapshot planning merges shared starts, in-play events excluded, Betfair dropped, draw handling,
   cache prevents a second request, missing key exits 2, key never logged.
