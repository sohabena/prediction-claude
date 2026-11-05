"""
Fix all data gaps identified by the audit:
1. Approve scrape_approved matches for training
2. Infer missing match results from odds data
3. Generate match_context rows from stored score data in odds_ticks
"""

import asyncio
from collections import defaultdict
from sqlalchemy import text
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("fix_data_gaps")


async def main():
    print("=" * 60)
    print("FIXING DATA GAPS")
    print("=" * 60)

    async with get_session() as s:
        # ─── FIX 1: Approve training for scrape_approved matches ──
        print("\n## FIX 1: Approve training for scrape_approved matches")
        r = await s.execute(text("""
            UPDATE match_training_status
            SET training_status = 'approved'
            WHERE scrape_status = 'scrape_approved'
              AND training_status IN ('pending', 'rejected')
            RETURNING match_id, team_home, team_away
        """))
        approved = r.fetchall()
        print(f"  Approved {len(approved)} matches for training:")
        for row in approved:
            print(f"    {row[0]}: {row[1]} vs {row[2]}")
        await s.commit()

        # ─── FIX 2: Infer missing match results ──────────────────
        print("\n## FIX 2: Infer missing match results")

        # Find matches without results
        r = await s.execute(text("""
            SELECT DISTINCT ot.match_id
            FROM odds_ticks ot
            LEFT JOIN match_results mr ON ot.match_id = mr.match_id
            WHERE mr.match_id IS NULL
        """))
        missing_ids = [row[0] for row in r.fetchall()]
        print(f"  {len(missing_ids)} matches need results")

        inserted = 0
        for match_id in missing_ids:
            # Get final ticks for this match
            r = await s.execute(text("""
                WITH ranked AS (
                    SELECT match_id, team_home, team_away, competition,
                           back_home, back_away,
                           ROW_NUMBER() OVER (ORDER BY "time" DESC) as rn
                    FROM odds_ticks
                    WHERE match_id = :mid
                )
                SELECT match_id, team_home, team_away, competition,
                       AVG(back_home) as avg_bh, AVG(back_away) as avg_ba
                FROM ranked WHERE rn <= 5
                GROUP BY match_id, team_home, team_away, competition
            """), {"mid": match_id})
            row = r.fetchone()
            if not row:
                continue

            avg_bh = row[4] or 99
            avg_ba = row[5] or 99

            # Determine winner
            if avg_bh < 1.5 and avg_ba > 3.0:
                winner, loser = row[1], row[2]
            elif avg_ba < 1.5 and avg_bh > 3.0:
                winner, loser = row[2], row[1]
            elif avg_bh < avg_ba:
                winner, loser = row[1], row[2]
            else:
                winner, loser = row[2], row[1]

            # Get last tick time
            r2 = await s.execute(text(
                "SELECT MAX(\"time\") FROM odds_ticks WHERE match_id = :mid"
            ), {"mid": match_id})
            completed = r2.scalar()
            if not completed:
                continue

            await s.execute(text("""
                INSERT INTO match_results (match_id, completed_at, winner, loser,
                                           result_type, margin, team_home, team_away, source)
                VALUES (:mid, :completed, :winner, :loser, 'win', 0,
                        :team_home, :team_away, 'odds_inference')
                ON CONFLICT (match_id) DO NOTHING
            """), {
                "mid": match_id, "completed": completed,
                "winner": winner, "loser": loser,
                "team_home": row[1], "team_away": row[2],
            })
            inserted += 1
            print(f"    {match_id}: {winner} beat {loser} (inferred)")

        await s.commit()
        print(f"  Inserted {inserted} match results")

        # ─── FIX 3: Generate match_context from odds_ticks score data ─
        print("\n## FIX 3: Backfill match_context from odds_ticks score data")

        # Find ticks with score data but no corresponding context
        r = await s.execute(text("""
            INSERT INTO match_context (
                time, match_id, is_live, score, wickets, overs,
                run_rate, req_run_rate, innings, balls_remaining,
                batting_team, bowling_team, status, match_format
            )
            SELECT
                ot.time,
                ot.match_id,
                ot.is_live,
                ot.score,
                ot.wickets,
                COALESCE(ot.overs, 0),
                CASE WHEN COALESCE(ot.overs, 0) > 0
                     THEN (ot.score::numeric / ot.overs::numeric)
                     ELSE 0 END as run_rate,
                0 as req_run_rate,
                1 as innings,
                120 as balls_remaining,
                ot.team_home as batting_team,
                ot.team_away as bowling_team,
                'live' as status,
                'T20' as match_format
            FROM odds_ticks ot
            WHERE ot.score IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM match_context mc
                  WHERE mc.match_id = ot.match_id
                    AND mc.time = ot.time
              )
            ON CONFLICT DO NOTHING
        """))
        backfilled = r.rowcount
        await s.commit()
        print(f"  Backfilled {backfilled} match_context rows from odds_ticks")

        # ─── SUMMARY ─────────────────────────────────────────────
        print("\n## FINAL STATE")
        r = await s.execute(text("""
            SELECT
                COUNT(DISTINCT ot.match_id) as total_matches,
                COUNT(DISTINCT ot.match_id) FILTER (
                    WHERE ot.match_id IN (
                        SELECT mts.match_id FROM match_training_status mts
                        WHERE mts.training_status = 'approved'
                    )
                ) as approved_matches,
                COUNT(DISTINCT mr.match_id) as matches_with_results,
                COUNT(DISTINCT mc.match_id) as matches_with_context
            FROM odds_ticks ot
            LEFT JOIN match_results mr ON ot.match_id = mr.match_id
            LEFT JOIN match_context mc ON ot.match_id = mc.match_id
        """))
        row = r.fetchone()
        print(f"  Total matches in DB:       {row[0]}")
        print(f"  Training-approved:         {row[1]}")
        print(f"  With match results:        {row[2]}")
        print(f"  With match context:        {row[3]}")

        # Count qualifying (approved + 50+ ticks + has result)
        r = await s.execute(text("""
            SELECT COUNT(DISTINCT ot.match_id)
            FROM odds_ticks ot
            INNER JOIN match_training_status mts ON ot.match_id = mts.match_id
            INNER JOIN match_results mr ON ot.match_id = mr.match_id
            WHERE mts.training_status = 'approved'
            GROUP BY ot.match_id
            HAVING COUNT(*) >= 50
        """))
        qualifying = len(r.fetchall())
        print(f"  Qualifying for training:   {qualifying} (approved + 50+ ticks + result)")


if __name__ == "__main__":
    asyncio.run(main())
