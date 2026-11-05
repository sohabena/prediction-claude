"""S10: Redis Streams client behaviour against an in-memory fake (no Redis needed)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from shared.redis_client import (
    BoundedDedupe,
    ReliableRedis,
    STREAM_FOR_CHANNEL,
    tick_bus,
)


class FakeRedis:
    """Minimal async stand-in for redis.asyncio.Redis stream + pubsub commands."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.groups: dict[tuple[str, str], dict] = {}
        self.published: list[tuple[str, str]] = []
        self._seq = 0

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self._seq += 1
        mid = f"{self._seq}-0"
        self.streams.setdefault(name, []).append((mid, dict(fields)))
        return mid

    async def xgroup_create(self, name, groupname, id="$", mkstream=False):
        import redis.asyncio as aioredis

        if (name, groupname) in self.groups:
            raise aioredis.ResponseError("BUSYGROUP Consumer Group name already exists")
        self.streams.setdefault(name, [])
        pos = len(self.streams[name]) if id == "$" else 0
        self.groups[(name, groupname)] = {"pos": pos, "pending": {}}

    async def xreadgroup(self, groupname, consumername, streams, count=None, block=None):
        await asyncio.sleep(0)  # let the loop breathe like a blocking read would
        out = []
        for name in streams:
            g = self.groups[(name, groupname)]
            entries = self.streams[name][g["pos"] : g["pos"] + (count or 10**9)]
            g["pos"] += len(entries)
            for mid, _ in entries:
                g["pending"][mid] = {"consumer": consumername, "t": time.monotonic(), "n": 1}
            if entries:
                out.append((name, entries))
        return out

    async def xack(self, name, groupname, *ids):
        for i in ids:
            self.groups[(name, groupname)]["pending"].pop(i, None)

    async def xautoclaim(self, name, groupname, consumername, min_idle_time, start_id="0-0", count=100):
        g = self.groups[(name, groupname)]
        lookup = dict(self.streams[name])
        claimed = []
        for mid, p in list(g["pending"].items()):
            if (time.monotonic() - p["t"]) * 1000 >= min_idle_time:
                p.update(consumer=consumername, t=time.monotonic(), n=p["n"] + 1)
                claimed.append((mid, lookup.get(mid)))
        return ("0-0", claimed, [])

    async def xpending_range(self, name, groupname, min="-", max="+", count=None):
        p = self.groups[(name, groupname)]["pending"].get(min)
        return [{"message_id": min, "times_delivered": p["n"]}] if p else []

    async def xpending(self, name, groupname):
        return {"pending": len(self.groups[(name, groupname)]["pending"])}

    async def xlen(self, name):
        return len(self.streams.get(name, []))


def _client() -> tuple[ReliableRedis, FakeRedis]:
    r = ReliableRedis()
    fake = FakeRedis()
    r._client = fake  # type: ignore[assignment]
    return r, fake


async def test_xadd_event_and_consume_ack() -> None:
    r, fake = _client()
    await r.ensure_group("s", "g")
    await r.ensure_group("s", "g")  # BUSYGROUP ignored
    for i in range(3):
        await r.xadd_event("s", {"i": i})
    got = []
    async for mid, data in r.consume("s", "g", "c1", block_ms=1):
        got.append(data["i"])
        await r.ack("s", "g", mid)
        if len(got) == 3:
            break
    assert got == [0, 1, 2]
    assert fake.groups[("s", "g")]["pending"] == {}


async def test_failed_handler_is_redelivered_then_dead_lettered() -> None:
    r, fake = _client()
    await r.ensure_group("s", "g")
    await r.xadd_event("s", {"match_id": "m"})
    attempts = 0

    async def run() -> None:
        nonlocal attempts
        async for _mid, _data in r.consume(
            "s", "g", "c1", block_ms=1, claim_idle_ms=0, claim_interval_s=0, max_deliveries=5
        ):
            attempts += 1  # handler "raises": never acked

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run(), timeout=1)
    assert attempts == 5
    assert len(fake.streams["s:dlq"]) == 1
    assert json.loads(fake.streams["s:dlq"][0][1]["data"]) == {"match_id": "m"}
    assert fake.groups[("s", "g")]["pending"] == {}


async def test_new_consumer_claims_unacked_messages() -> None:
    r, fake = _client()
    await r.ensure_group("s", "g")
    for i in range(4):
        await r.xadd_event("s", {"i": i})
    # consumer A reads everything and "dies" without acking
    await fake.xreadgroup("g", "A", {"s": ">"}, count=10)
    seen = []
    async for mid, data in r.consume("s", "g", "B", block_ms=1, claim_idle_ms=0):
        seen.append(data["i"])
        await r.ack("s", "g", mid)
        if len(seen) == 4:
            break
    assert sorted(seen) == [0, 1, 2, 3]


async def test_publish_event_mirrors_to_stream_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TICK_BUS", raising=False)
    r, fake = _client()
    assert tick_bus() == "streams"
    await r.publish_event("match_events", {"match_id": "m1"})
    await r.publish_event("match_context", {"match_id": "m1"})  # not mirrored
    assert len(fake.published) == 2  # Pub/Sub kept for the UI websocket
    assert len(fake.streams[STREAM_FOR_CHANNEL["match_events"]]) == 1
    assert "stream:match_context" not in fake.streams


async def test_pubsub_mode_restores_old_behaviour(monkeypatch) -> None:
    monkeypatch.setenv("TICK_BUS", "pubsub")
    r, fake = _client()
    await r.publish_event("match_events", {"match_id": "m1"})
    assert len(fake.published) == 1
    assert fake.streams == {}

    class FakePubSub:
        closed = False

        async def listen(self):
            yield {"type": "subscribe", "channel": "match_events", "data": 1}
            yield {"type": "message", "channel": "match_events", "data": '{"match_id": "m1"}'}

        async def close(self):
            FakePubSub.closed = True

    async def fake_subscribe(*channels):
        return FakePubSub()

    r.subscribe = fake_subscribe  # type: ignore[assignment]
    msgs = [m async for m in r.consume_bus("g", "c", ["match_events"])]
    assert len(msgs) == 1 and msgs[0].data == {"match_id": "m1"}
    assert msgs[0].msg_id is None
    await r.ack_bus(msgs[0])  # no-op, must not raise
    assert FakePubSub.closed


def test_bounded_dedupe_evicts_oldest() -> None:
    d = BoundedDedupe(maxsize=2)
    d.add(("m", 1))
    d.add(("m", 2))
    assert ("m", 1) in d  # refreshes recency
    d.add(("m", 3))
    assert ("m", 2) not in d and ("m", 1) in d and ("m", 3) in d
    assert len(d) == 2
