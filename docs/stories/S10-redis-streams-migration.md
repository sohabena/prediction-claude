# S10 — Durable tick bus: Redis Streams with consumer groups

**Priority:** P2 · **Depends on:** S06 merged · **Wave:** 4

## Story

As the operator, I need ticks and results delivered at-least-once with back-pressure
visibility, so that a slow or restarting consumer (live loop, feature cache) does not
silently lose odds events or match results.

## Background

- All live dispatch uses Redis **Pub/Sub** (`shared/redis_client.py:57-88`): fire-and-forget,
  no persistence, messages dropped when a subscriber is slow or reconnecting. The system
  docs say "Redis Streams", but no stream commands exist in the code.
- Consumers: `virtual_trading/live_trading_loop.py` (match_events, match_results),
  `features/feature_cache_consumer.py` (match_events), `scraper/manager.py` (match_results),
  `backend/routers/websocket_router.py` (UI fan-out — Pub/Sub is fine here).
- Losing a `match_results` message means bets are never settled until the DB fallback runs.

## Context pack

- `shared/redis_client.py` (141 lines): `class ReliableRedis(host, port, db)` L29; `connect()` L38; `client` property
  (`redis.asyncio.Redis`) L52; `publish_event(channel, data, max_retries=3) -> bool` L57-82 (dead-letter file via
  `_write_dead_letter` L116); `subscribe(*channels) -> PubSub` L84; `set_json`/`get_json` L91-100; `health_check` L102;
  `close` L110; `get_redis()` singleton L131.
- Pub/Sub subscribers: `virtual_trading/live_trading_loop.py:98` (events + results; read loop L100-128),
  `features/feature_cache_consumer.py:62` (events; `start()` L56-89, `_handle_event(data)` L97),
  `scraper/manager.py:178` (results), `backend/routers/websocket_router.py:82` (UI — keep Pub/Sub),
  `scripts/test_events.py:16` (dev). Publishers: `scraper/manager.py:358,452`, `virtual_trading/engine.py:332,342`,
  `rl/orchestrator.py` `_publish_state` L439, `rl/lifecycle/advisor_handler.py:62`.
- Channels (`shared/constants.py`): `CHANNEL_MATCH_EVENTS="match_events"` L10, `CHANNEL_MATCH_CONTEXT` L11,
  `CHANNEL_MATCH_RESULTS="match_results"` L89. Streams: `stream:match_events`, `stream:match_results`.
- `shared/config.py` is not owned: read `os.getenv("TICK_BUS", "streams")` in `shared/redis_client.py`; report the
  config follow-up.
- redis-py asyncio: `xgroup_create(name, groupname, id="$", mkstream=True)`, `xreadgroup(groupname, consumername,
  {stream: ">"}, count=, block=)`, `xack`, `xautoclaim(name, groupname, consumername, min_idle_time, start_id="0-0")`,
  `xpending_range(name, groupname, min="-", max="+", count=)`. Payload: one field `data` = JSON string.
- `backend/observability/metrics.py` holds Prometheus metrics (not owned).

## Owned files

- `shared/redis_client.py`
- `features/feature_cache_consumer.py`
- `virtual_trading/live_trading_loop.py` (consumer loop only — the `start()` read loop)
- `scraper/manager.py` (publish calls only)
- New: `tests/integration/test_s10_streams.py`, `tests/unit/test_s10_stream_client.py`

## Tasks

1. `RedisClient.xadd_event(stream, data, maxlen=100_000)` using `XADD ... MAXLEN ~`.
2. `RedisClient.consume(stream, group, consumer, *, block_ms=1000, count=100)`: async
   generator using `XGROUP CREATE ... MKSTREAM` (ignore BUSYGROUP), `XREADGROUP`, yields
   `(msg_id, data)`; caller acks with `RedisClient.ack(stream, group, msg_id)`.
3. On start, claim stale pending messages older than 30 s with `XAUTOCLAIM`.
4. Dead-letter: after 5 deliveries (`XPENDING` delivery count) move the message to
   `<stream>:dlq` and ack it.
5. Dual-publish during migration: `scraper/manager.py` writes both Pub/Sub (for the UI
   websocket) and the stream. Add a settings flag `TICK_BUS=streams|pubsub` (default
   `streams`) read by the consumers.
6. Live loop and feature-cache consumer read from the stream with groups
   `live_trading` and `feature_cache`; ack only after the handler finishes successfully.
   Handlers must be idempotent: dedupe by `(match_id, event timestamp)` with a bounded LRU.
7. Expose Prometheus gauges for stream length and pending count per group
   (`backend/observability/metrics.py` is not owned — add the gauge definitions inside
   `shared/redis_client.py` if the metrics module pattern allows; otherwise report).

## Success criteria

- [ ] Integration test (skips if Redis unavailable): 1 000 events published while the
      consumer is stopped are all processed after it starts (count == 1 000, no dupes).
- [ ] A handler that raises leaves the message pending; after 5 attempts it lands in the DLQ.
- [ ] Killing a consumer mid-batch and restarting a different consumer name processes the
      un-acked messages via `XAUTOCLAIM`.
- [ ] `TICK_BUS=pubsub` restores the old behaviour (unit test with fake client).
- [ ] UI websocket still receives events (Pub/Sub kept).

## Verification

```bash
python -m pytest tests/unit/test_s10_stream_client.py -q
python -m pytest tests/integration/test_s10_streams.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Replacing the websocket fan-out.
