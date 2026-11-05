"""Quick test to check if match events are flowing on Redis."""
import asyncio
import json

async def test():
    from shared.redis_client import get_redis
    from shared.constants import KEY_WATCHED_MATCH_ID, CHANNEL_MATCH_EVENTS

    redis = await get_redis()

    # Clear watched match so loop processes ALL matches
    await redis.client.delete(KEY_WATCHED_MATCH_ID)
    print("Cleared watched match")

    # Subscribe to match_events to see if events are flowing
    pubsub = await redis.subscribe(CHANNEL_MATCH_EVENTS)
    count = 0
    matches_seen = set()

    print("Listening for match events (max 30s)...")
    try:
        async def listen():
            nonlocal count
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, str):
                    try:
                        d = json.loads(data)
                        mid = d.get("match_id", "?")
                        matches_seen.add(mid)
                        count += 1
                        bh = d.get("back_home")
                        lh = d.get("lay_home")
                        print(f"  Event {count}: match={mid}, back_home={bh}, lay_home={lh}")
                        if count >= 10:
                            break
                    except json.JSONDecodeError:
                        pass

        await asyncio.wait_for(listen(), timeout=30)
    except asyncio.TimeoutError:
        pass

    print(f"\nReceived {count} events from {len(matches_seen)} unique matches")
    if count == 0:
        print("WARNING: No events flowing! Check if scraper is running and publishing.")
    await pubsub.close()

if __name__ == "__main__":
    asyncio.run(test())
