"""End-to-end test: run live trading loop and check if bets are placed."""
import asyncio
import json
from pathlib import Path

async def main():
    from shared.config import get_settings
    from shared.redis_client import get_redis
    from shared.constants import KEY_WATCHED_MATCH_ID, CHANNEL_MATCH_EVENTS
    from rl.trainer import Trainer
    from rl.agent import PhoenixAgent
    from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

    settings = get_settings()
    model_path = Path(settings.rl.model_path)

    # Load agent
    trainer = Trainer()
    env = trainer.create_env()
    agent = PhoenixAgent.load(model_path, env)
    print(f"Agent loaded from {model_path}")

    # Clear watched match to process ALL events
    redis = await get_redis()
    await redis.client.delete(KEY_WATCHED_MATCH_ID)
    print("Cleared watched match filter")

    bets_placed = []

    # Create loop
    loop = LiveVirtualTradingLoop(
        agent=agent,
        initial_balance=float(settings.rl.starting_bankroll),
        agent_version="test-v1",
        get_agent=lambda: agent,
    )
    print("Starting live trading loop...")

    task = asyncio.create_task(loop.start())

    # Also inject a synthetic event to guarantee a prediction
    print("Injecting synthetic test events...")
    for i in range(20):
        test_event = {
            "match_id": "test_match_001",
            "team_home": "Test Team A",
            "team_away": "Test Team B",
            "competition": "Test League",
            "back_home": 1.85 + i * 0.01,
            "lay_home": 1.90 + i * 0.01,
            "back_away": 2.10 - i * 0.005,
            "lay_away": 2.15 - i * 0.005,
            "back_draw": 3.50,
            "lay_draw": 3.60,
            "is_live": True,
            "timestamp": None,
        }
        await redis.client.publish(CHANNEL_MATCH_EVENTS, json.dumps(test_event))
        await asyncio.sleep(0.5)

    print("Injected 20 events. Waiting 20s for delayed bet persistence...")
    await asyncio.sleep(20)

    # Check DB for bets
    from shared.db import get_session
    from sqlalchemy import text
    async with get_session() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM virtual_bets")
        )
        count = result.scalar()
        print(f"\nTotal bets in DB: {count}")

        if count > 0:
            result = await session.execute(
                text("SELECT match_id, action, team, odds, stake, placed_at FROM virtual_bets ORDER BY placed_at DESC LIMIT 5")
            )
            rows = result.fetchall()
            for row in rows:
                print(f"  {row[0]}: {row[1]} {row[2]} @ {row[3]} stake={row[4]} at={row[5]}")

    await loop.stop()
    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(main())
