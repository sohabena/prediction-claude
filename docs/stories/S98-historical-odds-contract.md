# S98 — Historical odds: one record contract and store

**Pillars:** 1 and 2 · **Priority:** P1 · **Tier:** lead · **Depends on:** none · **Wave:** 23 · **Status:** done

## User story

As every historical-odds importer, verifier and benchmark, I want one record format and one store, so sources can be
added, checked and compared the same way.

## Acceptance criteria (implemented in `training/historical_odds.py`, tests `tests/unit/test_s98_historical_odds_contract.py`)

- `OddsRecord`: source, source_event_id, competition, format (`t20|odi|test|other`), gender (`male|female`),
  start_time_utc (aware), home, away, bookmaker, kind (`open|close|min|max|average|snapshot`), price_home,
  price_away, optional snapshot_time_utc, price_draw, winner (`home|away|tie|no_result`). Decimal odds.
- `validate(record)` returns stable problem codes; `is_betfair(name)` recognises Betfair in any spelling.
- `write_store(source, records, provenance=..., inputs=...)` writes `data/historical_odds/<source>/records.jsonl.gz`
  and `manifest.json` (sha256 and size of every input file, counts, problem counts, provenance). It refuses Betfair
  records or a Betfair provenance (`ForbiddenSourceError`) and records of another source.
- `read_store`, `overround`, `p_home_fair` (power de-vig), `link_detail` (Cricsheet link via `CricsheetIndex`).
- No database, no network.
