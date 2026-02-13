"""Check match_results table and training data status."""
import asyncio
from sqlalchemy import text
from shared.db import get_session


async def main():
    async with get_session() as s:
        # Check match_results table
        r = await s.execute(text("SELECT COUNT(*) FROM match_results"))
        print(f"match_results rows: {r.scalar()}")

        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='match_results' ORDER BY ordinal_position"
        ))
        cols = [row[0] for row in r.fetchall()]
        print(f"Columns: {cols}")

        # Show any results
        r = await s.execute(text("SELECT * FROM match_results LIMIT 5"))
        rows = r.fetchall()
        if rows:
            print(f"\nSample results:")
            for row in rows:
                print(f"  {row}")
        else:
            print("\n** NO MATCH RESULTS IN DB **")

        # Check match_training_status
        r = await s.execute(text("SELECT * FROM match_training_status LIMIT 5"))
        rows = r.fetchall()
        print(f"\nmatch_training_status ({len(rows)} rows shown):")
        for row in rows:
            print(f"  {row}")

        # Check unique matches in odds_ticks
        r = await s.execute(text(
            "SELECT match_id, team_home, team_away, COUNT(*) as ticks "
            "FROM odds_ticks GROUP BY match_id, team_home, team_away "
            "ORDER BY ticks DESC LIMIT 15"
        ))
        rows = r.fetchall()
        print(f"\nMatches in odds_ticks:")
        for row in rows:
            print(f"  {row[0]}: {row[1]} vs {row[2]} ({row[3]} ticks)")


if __name__ == "__main__":
    asyncio.run(main())
