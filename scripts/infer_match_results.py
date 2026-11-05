"""
Infer match results from odds data.

Logic: Near the end of a match, the winning team's back odds collapse toward 1.01
while the losing team's odds spike to 20+. We use the final few ticks to determine
the winner with high confidence.
"""

import asyncio
from sqlalchemy import text
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("infer_results")


async def main():
    async with get_session() as s:
        # Get all matches with their final ticks
        r = await s.execute(text("""
            WITH ranked AS (
                SELECT match_id, team_home, team_away, competition,
                       back_home, back_away, lay_home, lay_away,
                       "time" as tick_time,
                       ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY "time" DESC) as rn
                FROM odds_ticks
            )
            SELECT match_id, team_home, team_away, competition,
                   back_home, back_away, lay_home, lay_away
            FROM ranked
            WHERE rn <= 5
            ORDER BY match_id, rn
        """))
        rows = r.fetchall()

        # Group by match_id, take average of last 5 ticks
        from collections import defaultdict
        matches = defaultdict(list)
        for row in rows:
            matches[row[0]].append(row)

        print(f"Found {len(matches)} matches to analyze\n")

        results = []
        for match_id, ticks in matches.items():
            team_home = ticks[0][1]
            team_away = ticks[0][2]
            competition = ticks[0][3]

            # Average final odds (last 5 ticks, ordered by most recent first)
            avg_back_home = sum(t[4] for t in ticks if t[4]) / max(len(ticks), 1)
            avg_back_away = sum(t[5] for t in ticks if t[5]) / max(len(ticks), 1)

            # Determine winner: team with lower final back odds won
            if avg_back_home < 1.5 and avg_back_away > 3.0:
                winner = team_home
                loser = team_away
                confidence = "HIGH"
            elif avg_back_away < 1.5 and avg_back_home > 3.0:
                winner = team_away
                loser = team_home
                confidence = "HIGH"
            elif avg_back_home < avg_back_away:
                winner = team_home
                loser = team_away
                confidence = "MEDIUM" if avg_back_home < 1.8 else "LOW"
            else:
                winner = team_away
                loser = team_home
                confidence = "MEDIUM" if avg_back_away < 1.8 else "LOW"

            result = {
                "match_id": match_id,
                "team_home": team_home,
                "team_away": team_away,
                "competition": competition,
                "winner": winner,
                "loser": loser,
                "confidence": confidence,
                "final_back_home": round(avg_back_home, 2),
                "final_back_away": round(avg_back_away, 2),
            }
            results.append(result)
            status = "✓" if confidence == "HIGH" else "?" if confidence == "MEDIUM" else "✗"
            print(f"  {status} {match_id}: {team_home} vs {team_away}")
            print(f"    Final odds: home={avg_back_home:.2f} away={avg_back_away:.2f}")
            print(f"    Winner: {winner} (confidence: {confidence})")
            print()

        # Get last tick time per match for completed_at
        r2 = await s.execute(text("""
            SELECT match_id, MAX("time") as last_tick
            FROM odds_ticks GROUP BY match_id
        """))
        last_ticks = {row[0]: row[1] for row in r2.fetchall()}

        # Only insert HIGH and MEDIUM confidence results
        inserted = 0
        for r in results:
            if r["confidence"] in ("HIGH", "MEDIUM"):
                completed = last_ticks.get(r["match_id"])
                if not completed:
                    continue
                await s.execute(text("""
                    INSERT INTO match_results (match_id, completed_at, winner, loser,
                                              result_type, margin, team_home, team_away, source)
                    VALUES (:match_id, :completed_at, :winner, :loser, 'win',
                            0, :team_home, :team_away, 'odds_inference')
                    ON CONFLICT (match_id) DO UPDATE
                    SET winner = EXCLUDED.winner, loser = EXCLUDED.loser,
                        source = EXCLUDED.source
                """), {
                    "match_id": r["match_id"],
                    "completed_at": completed,
                    "winner": r["winner"],
                    "loser": r["loser"],
                    "team_home": r["team_home"],
                    "team_away": r["team_away"],
                })
                inserted += 1

        await s.commit()
        print(f"\n=== INSERTED {inserted} match results (HIGH/MEDIUM confidence) ===")

        # Verify
        r = await s.execute(text("SELECT COUNT(*) FROM match_results"))
        print(f"Total match_results in DB: {r.scalar()}")


if __name__ == "__main__":
    asyncio.run(main())
