"""Reset orchestrator state and clear old training data for fresh start."""
import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import text
from shared.db import get_session
from shared.redis_client import get_redis


async def main():
    print("=== RESETTING FOR FRESH TRAINING ===\n")

    # 1. Clear old training metrics
    async with get_session() as session:
        r = await session.execute(text("DELETE FROM training_metrics"))
        print(f"Cleared training_metrics: {r.rowcount} rows deleted")

        r = await session.execute(text("DELETE FROM virtual_bets"))
        print(f"Cleared virtual_bets: {r.rowcount} rows deleted")

        r = await session.execute(text("DELETE FROM graduation_snapshots"))
        print(f"Cleared graduation_snapshots: {r.rowcount} rows deleted")

        await session.commit()

    # 2. Reset Redis orchestrator state
    redis = await get_redis()

    new_state = json.dumps({
        "state": "accumulating",
        "model_version": 0,
        "curriculum_stage": "PATTERN_RECOGNITION",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await redis.client.set("orchestrator:state", new_state)
    print(f"\nRedis orchestrator:state -> {new_state}")

    # Clear stale keys
    for key in [
        "orchestrator:agent_version",
        "orchestrator:graduation_status",
        "orchestrator:shadow_performance",
        "orchestrator:drift_status",
    ]:
        await redis.client.delete(key)
        print(f"Cleared Redis key: {key}")

    print("\n=== RESET COMPLETE ===")
    print("Ready for fresh training with 88-dim observation space.")


asyncio.run(main())
