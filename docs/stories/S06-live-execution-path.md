# S06 — Live execution path correctness

**Priority:** P1 · **Depends on:** S05 merged · **Wave:** 2

## Story

As the operator, I need the live loop to decide on in-sync odds and match context, fill
virtual bets at the price that exists when the bet is actually placed, and never block the
event loop, so that virtual results are a faithful preview of live trading.

## Background

- **Context/odds ordering race:** `scraper/manager.py:358-367` publishes the odds event
  *before* `live_match_tracker.update()` produces and publishes the context. The live
  loop's `get_match_context()` therefore reads the previous ball's context.
- **Stale fills:** `virtual_trading/live_trading_loop.py:236-245` fixes the bet price at
  decision time, then persists it after a 5–15 s random delay at the old price.
- **Risk check on the wrong stake** (`live_trading_loop.py:233-238`) — fixed by S05's API.
- **Blocking model load:** `rl/model_zoo.py:329` calls `PhoenixAgent.load` lazily inside the
  event loop on first use of a format; failures are not cached, so they retry every tick.
- Per-tick Redis GET for `KEY_WATCHED_MATCH_ID`; single sequential consumer.
- Weekly retraining overwrites `.zip` files in place; the loop never reloads and the
  persisted `agent_version` does not identify which weights placed the bet.
- Observation mismatch: the live portfolio is cumulative while training reset bankroll per
  match (portfolio features out of distribution).

## Context pack

- `virtual_trading/live_trading_loop.py` (605 lines): `class LiveVirtualTradingLoop` L41; `__init__(agent=None,
  get_agent=None, agent_version, on_match_settled, initial_balance=100_000)` L52-78 builds `PortfolioManager`,
  `RiskManager`, `VirtualBetEngine(portfolio, slippage_pct=0.005, rejection_rate=0.05)`, `SettlementEngine`,
  `FeaturePipeline`, `FeatureStore`, `_open_bets: dict[match_id, list[(VirtualBet, datetime)]]`,
  `_pending_bet_tasks`, `_completed_match_ids`; `start()` L80-131 (subscribes `CHANNEL_MATCH_EVENTS` +
  `CHANNEL_MATCH_RESULTS`, dispatch L119-124); `stop()` L133; `_delayed_persist_bet(bet, match_id, action_name)` L144;
  `_handle_match_event(data)` L165-258; `_handle_match_result(data)` L260; `_fetch_closing_odds` L350;
  `_build_position_state(event)` L380; `_build_portfolio_state()` L435; `_load_completed_matches` L451;
  `_rebuild_open_bets` L472; `_settle_pending_from_db` L517.
- S05 API (merged first): `VirtualBetEngine.propose(action, event, confidence) -> BetProposal|None`,
  `RiskManager.check(proposal) -> RiskDecision(allowed, violations)`, `engine.commit(proposal, fill_odds=None) ->
  VirtualBet`, `engine.release(proposal)`. Get exact signatures with
  `grep -n "def propose\|def commit\|def release\|class BetProposal\|class RiskDecision" virtual_trading/*.py`
  — don't read the whole engine.
- Context lookup: `features/context_resolver.py:20 get_match_context(match_id) -> MatchContext|None` reads Redis key
  `KEY_MATCH_CONTEXT.format(match_id=...)` via `redis.get_json`. Scraper: `scraper/manager.py` quality gates L309-354,
  `publish_event(CHANNEL_MATCH_EVENTS, event_data)` L358, `_store_odds_tick` L362, `self._live_match_tracker.update(event)`
  L365 → `_publish_match_context(redis, context)` L440-479 (publishes `CHANNEL_MATCH_CONTEXT`, caches the key).
  Move the tracker update **before** L358 and embed the context.
- `OddsEvent.from_redis_data(data)` (`shared/schemas.py:102`): pop `context` from the dict before calling it.
- `rl/model_zoo.py` (469 lines): `ModelZoo(models_dir)`; `_load_metadata` L99; `save_metadata` L112;
  `register_specialist` L121; `resolve_specialist_key(...)` L146-292; `get_agent(key)` L294-346 (lazy
  `PhoenixAgent.load(path, env=None)` L329, default model L341, cache `self._models`); `get_agent_for_match(...) ->
  (agent, key)` L348-373; `predict(...)` L375; `get_action_distribution(...)` L401; `list_specialists()` L426;
  `get_model_zoo()` singleton L464. Other callers: `backend/routers/advisor.py:238` (`list_specialists`),
  `rl/lifecycle/advisor_handler.py`. Keep return types (add version info via a new method, e.g.
  `get_agent_version(key)`, rather than changing tuple arity).
- Constants: `KEY_WATCHED_MATCH_ID = "demo:watched_match_id"` (`shared/constants.py:21`),
  `BET_DELAY_MIN_SECONDS=5`, `BET_DELAY_MAX_SECONDS=15` (L115-116). Betfair tick ladder: import
  `betfair_tick_size` from `rl/environment.py` if S03 merged it, else define a local copy.

## Owned files

- `virtual_trading/live_trading_loop.py`
- `scraper/manager.py` (publish ordering only)
- `rl/model_zoo.py`
- New: `tests/unit/test_s06_live_path.py`

## Tasks

1. **Ordering:** in `scraper/manager.py`, compute the context first and include it in the
   same Redis message as the odds (`event_data["context"] = context.model_dump(mode="json")`)
   *and* keep publishing `CHANNEL_MATCH_CONTEXT` for other consumers. The live loop uses
   the embedded context when present, falls back to `get_match_context()` otherwise, and
   logs a `context_stale` metric when the fallback context's timestamp is older than the
   odds timestamp by > 10 s.
2. **Risk wiring:** replace the inline stake/`check_all` block with
   `proposal = engine.propose(...)` → `risk.check(proposal)` → schedule delayed placement.
3. **Delayed fill:** the delayed task re-reads the latest odds for the match (keep a
   per-match `latest_event` dict updated on every tick), and:
   - fills at the latest same-side price if it is no worse than the decision price by more
     than `MAX_FILL_SLIPPAGE_TICKS = 2` Betfair ticks (define locally, or import if S05
     added it), → `engine.commit(proposal, fill_odds=latest)`;
   - otherwise `engine.release(proposal)` and log `bet_missed_price_moved`;
   - releases the reservation on cancellation or if the match is no longer live.
   Record `decision_odds`, `fill_odds`, and delay seconds on the bet (use existing fields
   or `metadata` dict; report if the schema lacks a place to store them).
4. **Model zoo:**
   - add `preload(keys: Iterable[str] | None = None)` to load all specialists at startup,
     and call it via `await asyncio.to_thread(zoo.preload)` in the loop's `start()`;
   - cache load failures with a 10-minute negative TTL;
   - record `(key, path, mtime, sha256[:12])` as the model version; `get_agent_for_match`
     returns it; the live loop stores it as `agent_version` on every bet;
   - `reload_if_changed()` checks mtimes, loads new weights off-loop via
     `asyncio.to_thread`, and swaps atomically; call it every 5 minutes from the loop.
5. Run `agent.predict` / `get_action_distribution` via `asyncio.to_thread` only if a
   timing test shows > 5 ms per call; otherwise leave inline and note the measurement.
6. Cache `KEY_WATCHED_MATCH_ID` for 2 s instead of a GET per tick.
7. **Portfolio features:** build the portfolio state passed to the feature pipeline as a
   per-match view normalised to the per-match budget (the way training sees it), and
   document the mapping in a docstring.
8. Emit structured latency logs per tick: `t_scraped → t_received → t_obs → t_action`
   (ms), using the event timestamp and `time.perf_counter()`.

## Success criteria

- [ ] Unit test: an odds message with embedded context uses that context, not the cache.
- [ ] Unit test: price moves 3 ticks against during the delay → no bet, reservation released.
- [ ] Unit test: price moves 1 tick against → bet committed at the new price.
- [ ] Unit test: `get_agent_for_match` on a corrupt model path does not retry within TTL
      (count load attempts).
- [ ] Unit test: replacing a model file changes `agent_version` after `reload_if_changed()`.
- [ ] No `PhoenixAgent.load`/`PPO.load` call is reachable from the event loop without
      `asyncio.to_thread` (grep + review in report).
- [ ] Latency log line present with the four timestamps.

## Verification

```bash
python -m pytest tests/unit/test_s06_live_path.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Moving from Pub/Sub to Streams (S10).
- Engine/risk internals (S05).
