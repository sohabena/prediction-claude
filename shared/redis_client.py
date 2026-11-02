"""
PHOENIX Redis Client
Reliable Redis connection with retry logic, dead letter support, and pub/sub helpers.

Usage:
    from shared.redis_client import get_redis
    redis = await get_redis()
    await redis.publish_event("match_events", event.model_dump())
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence

import redis.asyncio as aioredis

from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("redis_client")

DEAD_LETTER_FILE = Path("dead_letters.jsonl")

# Pub/Sub channel -> durable stream mirrored by publish_event (TICK_BUS=streams)
STREAM_FOR_CHANNEL: dict[str, str] = {
    "match_events": "stream:match_events",
    "match_results": "stream:match_results",
}
STREAM_MAXLEN = 100_000
STREAM_CLAIM_IDLE_MS = 30_000
STREAM_MAX_DELIVERIES = 5

try:  # Prometheus is optional (same pattern as backend.observability.metrics)
    from prometheus_client import Gauge

    STREAM_LENGTH = Gauge("phoenix_stream_length", "Entries in a Redis stream", ["stream"])
    STREAM_PENDING = Gauge(
        "phoenix_stream_pending",
        "Delivered-but-unacked messages per consumer group",
        ["stream", "group"],
    )
except Exception:  # pragma: no cover - missing or duplicate registration
    STREAM_LENGTH = None
    STREAM_PENDING = None


def tick_bus() -> str:
    """Active tick transport: ``streams`` (default) or ``pubsub``.

    Read from the environment because shared/config.py has no TICK_BUS setting yet.
    """
    value = os.getenv("TICK_BUS", "streams").strip().lower()
    return "pubsub" if value == "pubsub" else "streams"


def default_consumer_name(prefix: str = "") -> str:
    """Stable-per-process consumer name for consumer groups."""
    return f"{prefix}{socket.gethostname()}-{os.getpid()}"


def _text(value: Any) -> Any:
    return value.decode("utf-8") if isinstance(value, bytes) else value


class BoundedDedupe:
    """Bounded LRU set for idempotent handlers (keys such as (match_id, timestamp))."""

    def __init__(self, maxsize: int = 50_000) -> None:
        self._maxsize = maxsize
        self._seen: OrderedDict[Any, None] = OrderedDict()

    def __contains__(self, key: Any) -> bool:
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        return False

    def add(self, key: Any) -> None:
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self._maxsize:
            self._seen.popitem(last=False)

    def __len__(self) -> int:
        return len(self._seen)


@dataclass
class BusMessage:
    """One delivered tick-bus message. ``msg_id`` is None in Pub/Sub mode (nothing to ack)."""

    channel: str
    data: dict[str, Any]
    msg_id: Optional[str] = None
    stream: Optional[str] = None
    group: Optional[str] = None


class ReliableRedis:
    """Redis client with retry logic and dead letter support."""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0) -> None:
        self.host = host
        self.port = port
        self.db = db
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        """Establish Redis connection."""
        self._client = aioredis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        await self._client.ping()
        logger.info("redis_connected", host=self.host, port=self.port)

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    async def publish_event(
        self, channel: str, data: dict[str, Any], max_retries: int = 3
    ) -> bool:
        """
        Publish event to Redis channel with retry logic.
        Falls back to dead letter file if all retries fail.
        """
        message = json.dumps(data, default=str)

        for attempt in range(max_retries):
            try:
                await self.client.publish(channel, message)
                await self._mirror_to_stream(channel, message)
                return True
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                logger.warning(
                    "redis_publish_retry",
                    channel=channel,
                    attempt=attempt + 1,
                    error=str(e),
                )
                await asyncio.sleep(2**attempt)

        # All retries failed -> dead letter
        self._write_dead_letter(channel, data)
        logger.error("redis_publish_failed", channel=channel, sent_to="dead_letter")
        return False

    async def _mirror_to_stream(self, channel: str, message: str) -> None:
        """Dual-publish: also XADD to the durable stream (never raises)."""
        stream = STREAM_FOR_CHANNEL.get(channel)
        if stream is None or tick_bus() != "streams":
            return
        try:
            await self.client.xadd(
                stream, {"data": message}, maxlen=STREAM_MAXLEN, approximate=True
            )
        except Exception as e:
            logger.warning("stream_publish_failed", stream=stream, error=str(e))

    # ------------------------------------------------------------------
    # Redis Streams (durable, at-least-once)
    # ------------------------------------------------------------------

    async def xadd_event(
        self, stream: str, data: dict[str, Any], maxlen: int = STREAM_MAXLEN
    ) -> str:
        """Append ``data`` (JSON in one ``data`` field) with approximate MAXLEN trim."""
        msg_id = await self.client.xadd(
            stream, {"data": json.dumps(data, default=str)}, maxlen=maxlen, approximate=True
        )
        return _text(msg_id)

    async def ensure_group(self, stream: str, group: str, start_id: str = "$") -> None:
        """XGROUP CREATE ... MKSTREAM, ignoring BUSYGROUP."""
        try:
            await self.client.xgroup_create(stream, group, id=start_id, mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def ack(self, stream: str, group: str, msg_id: str) -> None:
        """Acknowledge a processed message."""
        await self.client.xack(stream, group, msg_id)

    async def consume(
        self, stream: str, group: str, consumer: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(msg_id, data)`` from one stream; caller acks via :meth:`ack`."""
        async for _s, msg_id, data in self.consume_multi([stream], group, consumer, **kwargs):
            yield msg_id, data

    async def consume_multi(
        self,
        streams: Sequence[str],
        group: str,
        consumer: str,
        *,
        block_ms: int = 1000,
        count: int = 100,
        claim_idle_ms: int = STREAM_CLAIM_IDLE_MS,
        claim_interval_s: float = 10.0,
        max_deliveries: int = STREAM_MAX_DELIVERIES,
        start_id: str = "$",
    ) -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
        """Yield ``(stream, msg_id, data)`` via XREADGROUP.

        On start and every ``claim_interval_s`` stale pending messages (idle >
        ``claim_idle_ms``) are taken over with XAUTOCLAIM; messages already delivered
        ``max_deliveries`` times are moved to ``<stream>:dlq`` and acked.
        """
        streams = list(streams)
        for s in streams:
            await self.ensure_group(s, group, start_id)
        last_claim = float("-inf")
        while True:
            if time.monotonic() - last_claim >= claim_interval_s:
                last_claim = time.monotonic()
                for s in streams:
                    for msg_id, data in await self._claim_stale(
                        s, group, consumer, claim_idle_ms, max_deliveries, count
                    ):
                        yield s, msg_id, data
                    await self._update_stream_metrics(s, group)
            try:
                resp = await self.client.xreadgroup(
                    group, consumer, {s: ">" for s in streams}, count=count, block=block_ms
                )
            except aioredis.ResponseError as e:
                if "NOGROUP" not in str(e):
                    raise
                for s in streams:
                    await self.ensure_group(s, group, "0")
                continue
            for stream, entries in resp or []:
                stream = _text(stream)
                for msg_id, fields in entries:
                    data = self._decode_fields(fields)
                    if data is None:
                        logger.warning("stream_bad_payload", stream=stream, msg_id=_text(msg_id))
                        await self.ack(stream, group, msg_id)
                        continue
                    yield stream, _text(msg_id), data

    @staticmethod
    def _decode_fields(fields: Any) -> Optional[dict[str, Any]]:
        if not fields:
            return None
        raw = fields.get("data", fields.get(b"data")) if isinstance(fields, dict) else None
        try:
            parsed = json.loads(_text(raw))
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _claim_stale(
        self,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        max_deliveries: int,
        count: int = 100,
    ) -> list[tuple[str, dict[str, Any]]]:
        """XAUTOCLAIM stale pending entries; dead-letter the over-delivered ones."""
        out: list[tuple[str, dict[str, Any]]] = []
        cursor = "0-0"
        for _ in range(100):  # bounded: never spin forever
            res = await self.client.xautoclaim(
                stream, group, consumer, min_idle_time=min_idle_ms, start_id=cursor, count=count
            )
            cursor = _text(res[0])
            for msg_id, fields in res[1]:
                msg_id = _text(msg_id)
                data = self._decode_fields(fields)
                if data is None:  # deleted/trimmed or corrupt entry
                    await self.ack(stream, group, msg_id)
                    continue
                info = await self.client.xpending_range(
                    stream, group, min=msg_id, max=msg_id, count=1
                )
                delivered = int(info[0]["times_delivered"]) if info else 1
                if delivered > max_deliveries:
                    await self.client.xadd(
                        f"{stream}:dlq",
                        {
                            "data": json.dumps(data, default=str),
                            "original_id": msg_id,
                            "deliveries": str(delivered - 1),
                        },
                    )
                    await self.ack(stream, group, msg_id)
                    logger.error(
                        "stream_message_dead_lettered",
                        stream=stream,
                        msg_id=msg_id,
                        deliveries=delivered - 1,
                    )
                    continue
                out.append((msg_id, data))
            if cursor in ("0-0", "0"):
                break
        return out

    async def _update_stream_metrics(self, stream: str, group: str) -> None:
        """Refresh the stream length / pending gauges (best effort)."""
        if STREAM_LENGTH is None or STREAM_PENDING is None:
            return
        try:
            STREAM_LENGTH.labels(stream=stream).set(await self.client.xlen(stream))
            pending = await self.client.xpending(stream, group)
            n = pending.get("pending", 0) if isinstance(pending, dict) else pending[0]
            STREAM_PENDING.labels(stream=stream, group=group).set(n)
        except Exception as e:
            logger.debug("stream_metrics_failed", stream=stream, error=str(e))

    async def consume_bus(
        self,
        group: str,
        consumer: str,
        channels: Sequence[str],
        **kwargs: Any,
    ) -> AsyncIterator[BusMessage]:
        """Transport-agnostic reader honouring TICK_BUS.

        ``streams``: consumer-group reads (ack with :meth:`ack_bus` after handling).
        ``pubsub``: legacy fire-and-forget subscription (ack is a no-op).
        """
        if tick_bus() == "pubsub":
            pubsub = await self.subscribe(*channels)
            try:
                async for raw in pubsub.listen():
                    if raw.get("type") != "message" or not raw.get("data"):
                        continue
                    data = raw["data"]
                    if isinstance(data, (str, bytes)):
                        try:
                            data = json.loads(data)
                        except ValueError:
                            continue
                    yield BusMessage(channel=_text(raw.get("channel")), data=data)
            finally:
                await pubsub.close()
            return

        by_stream = {STREAM_FOR_CHANNEL[c]: c for c in channels if c in STREAM_FOR_CHANNEL}
        async for stream, msg_id, data in self.consume_multi(
            list(by_stream), group, consumer, **kwargs
        ):
            yield BusMessage(
                channel=by_stream[stream], data=data, msg_id=msg_id, stream=stream, group=group
            )

    async def ack_bus(self, message: BusMessage) -> None:
        """Ack a message from :meth:`consume_bus` (no-op for Pub/Sub)."""
        if message.msg_id is not None and message.stream and message.group:
            await self.ack(message.stream, message.group, message.msg_id)

    async def subscribe(self, *channels: str) -> aioredis.client.PubSub:
        """Create pubsub subscription to channels."""
        pubsub = self.client.pubsub()
        await pubsub.subscribe(*channels)
        logger.info("redis_subscribed", channels=list(channels))
        return pubsub

    async def set_json(self, key: str, data: dict[str, Any], ttl: Optional[int] = None) -> None:
        """Set a JSON value with optional TTL."""
        await self.client.set(key, json.dumps(data, default=str), ex=ttl)

    async def get_json(self, key: str) -> Optional[dict[str, Any]]:
        """Get a JSON value."""
        raw = await self.client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def health_check(self) -> bool:
        """Check if Redis is responsive."""
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close Redis connection gracefully."""
        if self._client:
            await self._client.close()
            logger.info("redis_disconnected")

    def _write_dead_letter(self, channel: str, data: dict[str, Any]) -> None:
        """Write failed message to dead letter file for later replay."""
        entry = {
            "channel": channel,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(DEAD_LETTER_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


# Singleton factory
_instance: Optional[ReliableRedis] = None


async def get_redis() -> ReliableRedis:
    """Get or create the Redis client singleton."""
    global _instance
    if _instance is None:
        settings = get_settings()
        _instance = ReliableRedis(
            host=settings.redis.host,
            port=settings.redis.port,
        )
        await _instance.connect()
    return _instance
