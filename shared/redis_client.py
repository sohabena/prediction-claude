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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import redis.asyncio as aioredis

from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("redis_client")

DEAD_LETTER_FILE = Path("dead_letters.jsonl")


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
