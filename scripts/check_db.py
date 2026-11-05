"""Quick DB check script for training readiness."""
import asyncio
from sqlalchemy import text
from shared.db import get_session


async def main():
    async with get_session() as session:
        # List all tables with row counts
        r = await session.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ))
        tables = [row[0] for row in r.fetchall()]
        print(f"=== DB TABLES ({len(tables)}) ===")
        for t in tables:
            try:
                r2 = await session.execute(text(f"SELECT COUNT(*) FROM {t}"))
                count = r2.scalar()
                print(f"  {t}: {count} rows")
            except Exception as e:
                print(f"  {t}: ERROR ({e})")

        # Show columns per table
        for t in tables:
            try:
                r = await session.execute(text(
                    f"SELECT column_name, data_type FROM information_schema.columns "
                    f"WHERE table_name = '{t}' ORDER BY ordinal_position"
                ))
                cols = r.fetchall()
                print(f"\n  {t} columns: {[c[0] for c in cols]}")
            except Exception:
                pass

        # Check odds_ticks detail
        print("\n=== ODDS DATA ===")
        if "odds_ticks" in tables:
            r = await session.execute(text("SELECT COUNT(*) FROM odds_ticks"))
            total = r.scalar()
            print(f"Total odds ticks: {total}")

            if total > 0:
                r = await session.execute(text("SELECT COUNT(DISTINCT match_id) FROM odds_ticks"))
                print(f"Unique matches with ticks: {r.scalar()}")

                r = await session.execute(text(
                    "SELECT match_id, COUNT(*) as ticks FROM odds_ticks "
                    "GROUP BY match_id ORDER BY ticks DESC LIMIT 10"
                ))
                rows = r.fetchall()
                if rows:
                    print("\nTop 10 matches by tick count:")
                    for row in rows:
                        print(f"  {row[0]}: {row[1]} ticks")

        # Check match_results
        print("\n=== MATCH RESULTS ===")
        r = await session.execute(text("SELECT COUNT(*) FROM match_results"))
        res_count = r.scalar()
        print(f"Match results: {res_count}")
        if res_count > 0:
            r = await session.execute(text("SELECT * FROM match_results LIMIT 3"))
            for row in r.fetchall():
                print(f"  {dict(row._mapping)}")

        # Trainable = have both ticks AND results
        if "odds_ticks" in tables and res_count > 0:
            r = await session.execute(text(
                "SELECT COUNT(DISTINCT match_id) FROM odds_ticks "
                "WHERE match_id IN (SELECT match_id FROM match_results)"
            ))
            print(f"\nTrainable matches (ticks + results): {r.scalar()}")

        # Check match_training_status
        print("\n=== MATCH TRAINING STATUS ===")
        r = await session.execute(text(
            "SELECT training_status, COUNT(*) FROM match_training_status GROUP BY training_status"
        ))
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]} matches")

        # Approved matches with tick counts
        r = await session.execute(text(
            "SELECT ot.match_id, COUNT(*) as ticks "
            "FROM odds_ticks ot "
            "INNER JOIN match_training_status mts ON ot.match_id = mts.match_id "
            "WHERE mts.training_status = 'approved' "
            "GROUP BY ot.match_id ORDER BY ticks DESC"
        ))
        rows = r.fetchall()
        total_approved_ticks = sum(r[1] for r in rows)
        print(f"\nApproved matches with ticks: {len(rows)} ({total_approved_ticks} total ticks)")
        for row in rows:
            print(f"  {row[0]}: {row[1]} ticks")

        # Check Redis state
        print("\n=== REDIS STATE ===")
        from shared.redis_client import get_redis
        redis = await get_redis()
        state = await redis.client.get("orchestrator:state")
        version = await redis.client.get("orchestrator:agent_version")
        print(f"Orchestrator state: {state}")
        print(f"Agent version: {version}")


asyncio.run(main())
