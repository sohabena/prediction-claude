"""S10: Redis Streams integration (skips when Redis is unavailable).

Uses db 15 and uniquely named streams that are deleted afterwards.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from shared.redis_client import ReliableRedis


@pytest.fixture
async def rc():
    r = ReliableRedis(db=15)
    try:
        await r.connect()
    except Exception:
        pytest.skip("Redis not available")
    stream = f"test:s10:{uuid.uuid4().hex[:8]}"
    r.test_stream = stream  # type: ignore[attr-defined]
    yield r
    await r.client.delete(stream, f"{stream}:dlq")
    await r.close()


async def test_backlog_processed_exactly_once(rc) -> None:
    stream, group = rc.test_stream, "g"
    await rc.ensure_group(stream, group, "$")  # group exists, consumer "stopped"
    for i in range(1000):
        await rc.xadd_event(stream, {"i": i})
    seen: list[int] = []

    async def run() -> None:
        async for mid, data in rc.consume(stream, group, "c1", block_ms=100):
            seen.append(data["i"])
            await rc.ack(stream, group, mid)
            if len(seen) == 1000:
                return

    await asyncio.wait_for(run(), timeout=30)
    assert len(seen) == 1000 and len(set(seen)) == 1000
    assert (await rc.client.xpending(stream, group))["pending"] == 0


async def test_failing_handler_ends_in_dlq(rc) -> None:
    stream, group = rc.test_stream, "g"
    await rc.ensure_group(stream, group, "$")
    await rc.xadd_event(stream, {"bad": True})
    attempts = 0

    async def run() -> None:
        nonlocal attempts
        async for _mid, _data in rc.consume(
            stream, group, "c1", block_ms=50, claim_idle_ms=0, claim_interval_s=0.05
        ):
            attempts += 1  # never acked

    task = asyncio.create_task(run())
    for _ in range(100):
        await asyncio.sleep(0.1)
        if await rc.client.xlen(f"{stream}:dlq"):
            break
    task.cancel()
    assert attempts == 5
    assert await rc.client.xlen(f"{stream}:dlq") == 1
    assert (await rc.client.xpending(stream, group))["pending"] == 0


async def test_other_consumer_takes_over_unacked(rc) -> None:
    stream, group = rc.test_stream, "g"
    await rc.ensure_group(stream, group, "$")
    for i in range(10):
        await rc.xadd_event(stream, {"i": i})
    await rc.client.xreadgroup(group, "dead", {stream: ">"}, count=10)  # reads, never acks
    seen: list[int] = []

    async def run() -> None:
        async for mid, data in rc.consume(stream, group, "alive", block_ms=50, claim_idle_ms=0):
            seen.append(data["i"])
            await rc.ack(stream, group, mid)
            if len(seen) == 10:
                return

    await asyncio.wait_for(run(), timeout=15)
    assert sorted(seen) == list(range(10))
