# S56 — Record the pages' own market JSON (passive capture)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S50, S52, S53 · **Wave:** 16C

## Story

As the strategy owner I want the recorded prices, market states, competitions and start times to come from the
structured data the LotusBook pages already receive, so the recording is exact, complete and needs no DOM guessing.

## Background (verified 2026-11-03, scraper container, headless, not logged in)

- A match page receives `GET https://b2b.max247.co/api/public/exchange/odds/sma-event/events/4/<event_id>` about every
  3 s. It holds **every** market of the event, including ones the page does not show (Toronto v Montreal: 92 markets in
  JSON, only Winner rendered). Fixtures: `tests/fixtures/lotusbook/sma_event_real_76321652_20261103.json`,
  `sma_event_srl_76108412_20261103.json`. Shape (`result[]` = markets):
  `id` ("1.76108412.340._BR"), `name`, `mtype` ("MATCH_ODDS_SB"), `btype`, `status` (OPEN/SUSPENDED/CLOSED),
  `inPlay`, `lastUpdateTime` (epoch ms), `event{id,name,openDate "2026-11-03T07:30:00+05:30"}`,
  `competition{id,name}`, `runners[]{id,name,sort,status,back[{price,size,line}],lay[...]}`. Price `0.0` = no price.
- The listing page receives `GET .../sma-menu/4` once: `result[]` event types; Cricket (`id` "4") → `children` GROUPs
  (competition `name`, `id`) → `children` EVENTs (`id` = LotusBook event id, `name`). Fixture:
  `tests/fixtures/lotusbook/sma_menu_cricket_20261103.json` (22 cricket groups).
- Today the recorder parses the DOM (`scraper/parsers/lotusbook_markets.py`) and the listing extractor sets no
  competition (S53 added a hand-made team → competition table as a stopgap).

## Rules

Passive only: read responses with Playwright's `page.on("response", ...)` for requests **the page itself makes**.
Never issue a request yourself, never change headers or the user agent, never click or type.

## Owned files

- New `scraper/parsers/lotusbook_json.py` (pure: JSON → `MarketRecord`s + event meta; menu JSON → `{event_id:
  (competition_id, competition_name)}`)
- `scraper/market_pages.py` (worker: subscribe to its page's `sma-event` responses; JSON is the primary source, the DOM
  parse runs only when no JSON arrived for 30 s)
- `scraper/parsers/lotusbook_markets.py` (`MarketRecord` new optional fields only)
- `scraper/manager.py`: listing page `sma-menu` listener → competition map; set `event.competition` before gating when
  it is empty; registry update of `competition` and `match_start_time` (from `openDate`, converted to UTC) through the
  existing `_upsert_training_status`; pin the browser context `timezone_id="Asia/Kolkata"` (site renders IST)
- `backend/models/market_ticks.py`, `backend/db_init.py` (one `MIGRATION_STATEMENTS` entry adding nullable columns
  `market_id TEXT`, `back_size DOUBLE PRECISION`, `lay_size DOUBLE PRECISION`, `runner_status TEXT`,
  `source_updated_at TIMESTAMPTZ` to `market_ticks`), new `alembic/versions/005_market_ticks_json_fields.py` (not run)
- New `tests/unit/test_s56_json_capture.py`

## Tasks

1. Parser: one record per runner per market: `market_type`/`market_name` from the existing classifier on `name`
   (`_classify_list_market`), `selection` = runner name via the existing `_list_selection` (keeps over/under lines),
   `back`/`lay` best price (0 → None), sizes, `status` = market status lower-cased (`open`/`suspended`/`closed`),
   `runner_status`, `market_id`, `source_updated_at` from `lastUpdateTime`. Two markets with the same name but
   different ids must not collide: include the market id in the dedupe key and keep `selection` distinct (lines differ).
2. Worker: JSON first, per-record dedupe as S52 (content key now includes sizes and runner_status). Handle a response
   that fails to parse by logging once and continuing.
3. Results: when the Winner market (`match_odds`) is `CLOSED` and exactly one runner has status `WINNER`
   (UNVERIFIED wording; also accept `WON`), call `self._result_detector.update_scoreboard`-equivalent path with an
   explicit winner — add `LotusResultDetector.update_settled(match_id, winner, source="lotusbook_settled")` that
   behaves like a scoreboard result (overrides price inference). Log the runner statuses seen at close so the wording
   can be verified.
4. Listing: from `sma-menu`, map every cricket event id to its competition; `match_id = "lb_" + event_id`. Use it for
   every event (pre-match and live) and in the registry. Keep S53's team table only as a fallback.
5. Tests from the three fixtures: record counts and classification for both events; price 0 → None; sizes; statuses;
   menu mapping (`lb_76321652` → "NCL GT20", `lb_76108412` → "T20 International SRL"); openDate +05:30 → UTC;
   settled-winner path with a synthetic CLOSED market; DOM fallback after 30 s without JSON; listener ignores other
   URLs and never calls `page.request`/`fetch`.

## Guardrails

No DB, browser or network in tests. Do not apply migrations. Do not commit.
