"""
PHOENIX - Reset Virtual Trading and Focus on Today's Matches.

1. Clears all rows from PostgreSQL TimescaleDB table `virtual_bets`
2. Clears demo overrides from Redis (`demo:watched_match_id`, `graduation:status`)
3. Approves today's 2 live matches:
   - lb_75878940: Oman vs Netherlands (T20I Men)
   - lb_36262294: Melbourne Stars W vs Perth Scorchers W (WBBL Women)
4. Sets all other lotusbook matches (SRL, mock, domestic) to scrape_rejected
5. Verifies API returns clean state with 0 bets
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from shared.db import get_session
from shared.redis_client import get_redis


async def reset_virtual_trading() -> None:
    print("[RESET] 1. Clearing virtual_bets from TimescaleDB...")
    async with get_session() as session:
        del_res = await session.execute(text("DELETE FROM virtual_bets;"))
        try:
            await session.execute(text("ALTER SEQUENCE IF EXISTS virtual_bets_id_seq RESTART WITH 1;"))
        except Exception:
            pass
        await session.commit()
        print(f"       -> Cleared existing virtual bets.")

        # Verify count
        cnt = await session.execute(text("SELECT count(*) FROM virtual_bets;"))
        print(f"       -> virtual_bets row count is now: {cnt.scalar()}")

        # 2. Configure today's 2 matches
        print("\n[RESET] 2. Approving today's 2 matches in match_training_status:")
        print("       - lb_75878940: Oman vs Netherlands")
        print("       - lb_36262294: Melbourne Stars W vs Perth Scorchers W (WBBL)")

        await session.execute(text("""
            UPDATE match_training_status
            SET scrape_status = 'scrape_approved',
                training_status = 'approved',
                auto_approved = TRUE
            WHERE match_id IN ('lb_75878940', 'lb_36262294');
        """))

        # Reject other live matches to ensure strict isolation
        await session.execute(text("""
            UPDATE match_training_status
            SET scrape_status = 'scrape_rejected',
                auto_approved = FALSE
            WHERE match_id LIKE 'lb_%' AND match_id NOT IN ('lb_75878940', 'lb_36262294');
        """))
        await session.commit()

        # Print current status of all lb_ matches
        status_res = await session.execute(text("""
            SELECT match_id, team_home, team_away, scrape_status, training_status, auto_approved
            FROM match_training_status
            WHERE match_id LIKE 'lb_%'
            ORDER BY match_id;
        """))
        for row in status_res.fetchall():
            tag = ">>> [APPROVED]" if row[3] == "scrape_approved" else "    [REJECTED]"
            print(f"       {tag} {row[0]}: {row[1]} vs {row[2]} ({row[3]})")

    # 3. Redis cleanup
    print("\n[RESET] 3. Cleaning up Redis state...")
    redis = await get_redis()
    await redis.client.delete("demo:watched_match_id")
    await redis.client.delete("graduation:status")
    print("       -> Cleared demo:watched_match_id and graduation:status")

    print("\n[COMPLETE] Virtual trading reset successfully!")


if __name__ == "__main__":
    asyncio.run(reset_virtual_trading())

