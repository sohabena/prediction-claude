"""
Audit virtual trading: check pending bets, void bets, and settlement correctness.
Also settle any orphaned pending bets that have match results available.
"""

import asyncio
from sqlalchemy import text
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("audit_vt")


async def main():
    async with get_session() as s:
        # 1. Pending bets
        r = await s.execute(text("""
            SELECT vb.match_id, vb.action, vb.team, vb.odds, vb.stake, vb.outcome,
                   mr.winner, mr.result_type
            FROM virtual_bets vb
            LEFT JOIN match_results mr ON vb.match_id = mr.match_id
            WHERE vb.settled_at IS NULL
        """))
        rows = r.fetchall()
        print(f"\n## PENDING BETS: {len(rows)}")
        for row in rows:
            winner = row[6] or "N/A"
            rtype = row[7] or "N/A"
            print(f"  {row[0]:20s} {row[1]:16s} {row[2]:20s} "
                  f"odds={float(row[3]):.2f} stake={float(row[4]):.0f} "
                  f"winner={winner} type={rtype}")

        # 2. Void bets audit
        r2 = await s.execute(text("""
            SELECT vb.match_id, vb.action, vb.team, vb.odds, vb.stake,
                   mr.winner, mr.result_type
            FROM virtual_bets vb
            LEFT JOIN match_results mr ON vb.match_id = mr.match_id
            WHERE vb.outcome = 'void'
        """))
        rows2 = r2.fetchall()
        print(f"\n## VOID BETS AUDIT: {len(rows2)}")
        for row in rows2:
            winner = row[5] or "N/A"
            rtype = row[6] or "N/A"
            is_back = "BACK" in row[1]
            team_won = (row[2] == row[5])
            if rtype == "win":
                if is_back:
                    expected = "win" if team_won else "loss"
                    expected_pnl = float(row[4]) * (float(row[3]) - 1) if team_won else -float(row[4])
                else:
                    expected = "loss" if team_won else "win"
                    expected_pnl = -float(row[4]) * (float(row[3]) - 1) if team_won else float(row[4])
                print(f"  {row[0]:20s} {row[1]:16s} {row[2]:20s} "
                      f"odds={float(row[3]):.2f} winner={winner} "
                      f"VOID_WRONG: should be {expected} pnl={expected_pnl:.0f}")
            else:
                print(f"  {row[0]:20s} {row[1]:16s} void correct (result_type={rtype})")

        # 3. Settle orphaned pending bets
        print(f"\n## SETTLING ORPHANED PENDING BETS")
        r3 = await s.execute(text("""
            SELECT vb.placed_at, vb.match_id, vb.action, vb.team,
                   vb.odds, vb.stake, mr.winner, mr.result_type
            FROM virtual_bets vb
            INNER JOIN match_results mr ON vb.match_id = mr.match_id
            WHERE vb.settled_at IS NULL
        """))
        rows3 = r3.fetchall()
        settled = 0
        for row in rows3:
            placed_at, mid, action, team = row[0], row[1], row[2], row[3]
            odds, stake, winner, rtype = float(row[4]), float(row[5]), row[6], row[7]

            if rtype in ("tie", "no_result", "draw", "abandoned"):
                outcome, pnl = "void", 0.0
            else:
                is_back = "BACK" in action
                team_won = (team == winner)
                if is_back:
                    outcome = "win" if team_won else "loss"
                    pnl = stake * (odds - 1.0) if team_won else -stake
                else:
                    outcome = "loss" if team_won else "win"
                    pnl = -stake * (odds - 1.0) if team_won else stake

            await s.execute(text("""
                UPDATE virtual_bets
                SET settled_at = NOW(), outcome = :outcome, profit_loss = :pnl
                WHERE match_id = :mid AND placed_at = :placed_at AND settled_at IS NULL
            """), {"mid": mid, "placed_at": placed_at, "outcome": outcome, "pnl": pnl})
            settled += 1
            print(f"  Settled: {mid} {action} {team} -> {outcome} pnl={pnl:.0f}")

        await s.commit()
        print(f"  Total settled: {settled}")

        # 4. Fix void bets that should have been win/loss
        print(f"\n## FIXING INCORRECTLY VOIDED BETS")
        r4 = await s.execute(text("""
            SELECT vb.placed_at, vb.match_id, vb.action, vb.team,
                   vb.odds, vb.stake, mr.winner, mr.result_type
            FROM virtual_bets vb
            INNER JOIN match_results mr ON vb.match_id = mr.match_id
            WHERE vb.outcome = 'void' AND mr.result_type = 'win'
        """))
        rows4 = r4.fetchall()
        fixed = 0
        for row in rows4:
            placed_at, mid, action, team = row[0], row[1], row[2], row[3]
            odds, stake, winner = float(row[4]), float(row[5]), row[6]

            is_back = "BACK" in action
            team_won = (team == winner)
            if is_back:
                outcome = "win" if team_won else "loss"
                pnl = stake * (odds - 1.0) if team_won else -stake
            else:
                outcome = "loss" if team_won else "win"
                pnl = -stake * (odds - 1.0) if team_won else stake

            await s.execute(text("""
                UPDATE virtual_bets
                SET outcome = :outcome, profit_loss = :pnl
                WHERE match_id = :mid AND placed_at = :placed_at
            """), {"mid": mid, "placed_at": placed_at, "outcome": outcome, "pnl": pnl})
            fixed += 1
            print(f"  Fixed: {mid} {action} {team} void->{outcome} pnl={pnl:.0f}")

        await s.commit()
        print(f"  Total fixed: {fixed}")

        # 5. Final summary
        print(f"\n## FINAL STATE")
        r5 = await s.execute(text("""
            SELECT outcome, COUNT(*), SUM(profit_loss)
            FROM virtual_bets
            GROUP BY outcome
        """))
        for row in r5.fetchall():
            total_pnl = float(row[2]) if row[2] else 0
            print(f"  {row[0]:10s}: {row[1]} bets, P&L={total_pnl:.0f}")

        r6 = await s.execute(text("SELECT SUM(profit_loss) FROM virtual_bets"))
        total = r6.scalar()
        print(f"  TOTAL P&L: {float(total) if total else 0:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
