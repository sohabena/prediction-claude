#!/usr/bin/env python3
"""
Show accumulated data for approved matches with 100+ ticks.

Usage:
    python scripts/show_accumulated_data.py
    python scripts/show_accumulated_data.py --min-ticks 50
    python scripts/show_accumulated_data.py --sample-ticks 5  # show first 5 ticks per match
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from shared.db import get_session


async def main(min_ticks: int = 100, sample_ticks: int = 10) -> None:
    async with get_session() as session:
        # 1. List approved matches with 100+ ticks
        result = await session.execute(
            text("""
                SELECT ot.match_id, mts.team_home, mts.team_away, mts.competition,
                       COUNT(*) as tick_count
                FROM odds_ticks ot
                INNER JOIN match_training_status mts ON ot.match_id = mts.match_id
                WHERE mts.training_status = 'approved'
                GROUP BY ot.match_id, mts.team_home, mts.team_away, mts.competition
                HAVING COUNT(*) >= :min_ticks
                ORDER BY tick_count DESC
            """),
            {"min_ticks": min_ticks},
        )
        matches = result.fetchall()

        if not matches:
            print(f"No approved matches with >={min_ticks} ticks found.")
            return

        print(f"\nApproved matches with >={min_ticks} ticks: {len(matches)}")
        print("=" * 80)
        for m in matches:
            match_id, home, away, comp, count = m
            print(f"  {match_id}")
            print(f"    {home} vs {away} | {comp or 'N/A'}")
            print(f"    Ticks: {count}")
            print()

        # 2. For each match, show sample ticks
        print("\nSample ticks (first {0} per match):".format(sample_ticks))
        print("=" * 80)
        for m in matches:
            match_id, home, away, comp, count = m
            print(f"\n--- {home} vs {away} ({match_id}) ---")
            ticks_result = await session.execute(
                text("""
                    SELECT time, back_home, lay_home, back_away, lay_away,
                           back_draw, lay_draw, implied_prob_home, implied_prob_away,
                           overround, is_live
                    FROM odds_ticks
                    WHERE match_id = :match_id
                    ORDER BY time ASC
                    LIMIT :limit
                """),
                {"match_id": match_id, "limit": sample_ticks},
            )
            ticks = ticks_result.fetchall()
            for i, row in enumerate(ticks):
                time_, bh, lh, ba, la, bd, ld, iph, ipa, ovr, live = row
                print(f"  [{i+1}] {time_}")
                print(f"       back_home={bh} lay_home={lh} | back_away={ba} lay_away={la}")
                print(f"       implied_home={iph} implied_away={ipa} overround={ovr} is_live={live}")
                print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-ticks", type=int, default=100, help="Minimum ticks per match")
    parser.add_argument("--sample-ticks", type=int, default=10, help="Ticks to show per match")
    args = parser.parse_args()
    asyncio.run(main(min_ticks=args.min_ticks, sample_ticks=args.sample_ticks))
