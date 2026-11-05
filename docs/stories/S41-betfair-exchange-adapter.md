# S41 — Betfair Exchange market-data adapter (read-only): live ladder and closing price

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S40 · **Wave:** 12
**Operator prerequisite to run live:** a Betfair account and app key (the free delayed key is enough for closing prices
and CLV), provided through environment variables. Nothing in this story creates accounts, stores credentials or places
orders.

> **Operator decision (2026-11-02): no automated betting.** This adapter is **read-only market data**. It contains no
> order-placement code.

## Story

As the person judging signals, I need the exchange's own live prices and closing price, so that every signal's CLV is
measured against the sharp market, and so that signals can quote the exchange price I will actually bet at.

## Background

- `scraper/parsers/betfair_parser.py` is a placeholder (its docstring says "structural placeholder with the interface
  defined"). It has `BetfairParser(app_key, session_token, enabled)` and `httpx` REST stubs against
  `https://api.betfair.com/exchange/betting/rest/v1.0`, with event type `4` (cricket).
- CLV today is measured against the LotusBook mirror price. The trust gate (S42) needs the Betfair close.

## Context pack

- Credentials: `BETFAIR_APP_KEY`, `BETFAIR_SESSION_TOKEN` (env). Never log them; never write them to disk.
- APIs, read-only:
  - REST `listMarketCatalogue` (eventTypeIds=["4"], marketTypeCodes=["MATCH_ODDS"]) and `listMarketBook`
    (priceProjection EX_BEST_OFFERS, EX_TRADED);
  - Exchange Stream API `marketSubscription` (`EX_BEST_OFFERS`, `EX_LTP`, `EX_TRADED_VOL`, `EX_MARKET_DEF`).
  Stream messages have the same `mc`/`rc`/`batb`/`batl`/`ltp` shape as the historical files, so reuse
  `scraper/parsers/betfair_historical.py` tick extraction (S35).
- Output: `OddsEvent` (`shared/schemas.py`) with `source="betfair"`, published to the S10 tick bus
  (`shared/redis_client.py xadd_event`, stream `stream:match_events`). Storage goes through a small helper you own;
  if storing requires changing `scraper/manager.py`, report it instead of editing it.
- Match mapping: canonical team names (`scripts/cricsheet_harmonizer.py:89 canonicalize_team`, falling back to the raw
  name) plus start time, linking Betfair markets to LotusBook ids and Cricsheet matches.

## Owned files

- `scraper/parsers/betfair_parser.py` (rewrite)
- New: `scraper/betfair_stream.py`, `scraper/betfair_store.py`, `scraper/match_mapper.py`
- New: `tests/unit/test_s41_betfair_adapter.py` with recorded stream-message fixtures

## Tasks

1. A REST catalogue plus a Stream client that yields `OddsEvent`s with the best three back/lay levels, `ltp`, traded volume
   and the in-play flag. Reconnect with backoff; handle heartbeats and `clk` / `initialClk` resume.
2. Store and publish ticks (`source="betfair"`). At market close, record the last in-play best prices as the closing
   price in `match_results.closing_*` with `source="betfair"`, via `scraper/betfair_store.py`.
3. `scraper/match_mapper.py` links one real match across Betfair, LotusBook and Cricsheet.
4. Assert in a test that the package contains no order-placement call (`placeOrders`, `replaceOrders`, `cancelOrders`).

## Success criteria

- [ ] Fixture stream messages produce the expected `OddsEvent` sequence, including resume after reconnect.
- [ ] The closing price is stored at market close (fake session).
- [ ] No credential value appears in logs (sentinel test).
- [ ] The no-order-placement test passes.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s41_betfair_adapter.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Any order placement. Account creation.
