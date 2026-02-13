"""
Comprehensive audit of scraped data vs training pipeline requirements.
Identifies all gaps that need fixing.
"""

import asyncio
from datetime import datetime, timezone
from sqlalchemy import text
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("data_audit")


async def main():
    async with get_session() as s:
        print("=" * 70)
        print("PHOENIX DATA AUDIT — Scraping vs Training Gap Analysis")
        print("=" * 70)

        # ─── 1. ODDS TICKS OVERVIEW ───────────────────────────────
        print("\n## 1. ODDS TICKS (raw scraped data)")
        r = await s.execute(text("""
            SELECT COUNT(*) as total_ticks,
                   COUNT(DISTINCT match_id) as total_matches,
                   MIN("time") as earliest,
                   MAX("time") as latest
            FROM odds_ticks
        """))
        row = r.fetchone()
        print(f"  Total ticks:   {row[0]:,}")
        print(f"  Total matches: {row[1]}")
        print(f"  Date range:    {row[2]} → {row[3]}")

        # ─── 2. PER-MATCH TICK COUNTS ─────────────────────────────
        print("\n## 2. PER-MATCH DETAIL")
        r = await s.execute(text("""
            SELECT match_id, team_home, team_away, competition,
                   COUNT(*) as ticks,
                   MIN("time") as first_tick,
                   MAX("time") as last_tick,
                   EXTRACT(EPOCH FROM MAX("time") - MIN("time"))/60.0 as duration_min
            FROM odds_ticks
            GROUP BY match_id, team_home, team_away, competition
            ORDER BY ticks DESC
        """))
        rows = r.fetchall()
        print(f"  {'Match ID':<20} {'Home':<20} {'Away':<20} {'Ticks':>6} {'Dur(min)':>10} {'Competition'}")
        print("  " + "-" * 100)
        for row in rows:
            dur = f"{row[7]:.0f}" if row[7] else "?"
            print(f"  {row[0]:<20} {row[1]:<20} {row[2]:<20} {row[4]:>6} {dur:>10} {row[3][:30]}")

        # ─── 3. FIELD COMPLETENESS ────────────────────────────────
        print("\n## 3. FIELD COMPLETENESS (% non-null)")
        r = await s.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(back_home) as has_back_home,
                COUNT(lay_home) as has_lay_home,
                COUNT(back_away) as has_back_away,
                COUNT(lay_away) as has_lay_away,
                COUNT(back_draw) as has_back_draw,
                COUNT(volume_back_home) as has_vol_bh,
                COUNT(volume_lay_home) as has_vol_lh,
                COUNT(volume_back_away) as has_vol_ba,
                COUNT(volume_lay_away) as has_vol_la,
                COUNT(implied_prob_home) as has_ip_home,
                COUNT(implied_prob_away) as has_ip_away,
                COUNT(overround) as has_overround,
                COUNT(score) as has_score,
                COUNT(wickets) as has_wickets,
                COUNT(overs) as has_overs,
                COUNT(is_live) as has_is_live,
                COUNT(scrape_latency_ms) as has_latency
            FROM odds_ticks
        """))
        row = r.fetchone()
        total = row[0]
        fields = [
            ("back_home", row[1]), ("lay_home", row[2]),
            ("back_away", row[3]), ("lay_away", row[4]),
            ("back_draw", row[5]),
            ("volume_back_home", row[6]), ("volume_lay_home", row[7]),
            ("volume_back_away", row[8]), ("volume_lay_away", row[9]),
            ("implied_prob_home", row[10]), ("implied_prob_away", row[11]),
            ("overround", row[12]),
            ("score", row[13]), ("wickets", row[14]), ("overs", row[15]),
            ("is_live", row[16]), ("scrape_latency_ms", row[17]),
        ]
        for name, count in fields:
            pct = 100 * count / total if total else 0
            bar = "#" * int(pct / 5)
            gap = "*** GAP ***" if pct < 50 else ("* LOW *" if pct < 80 else "")
            print(f"  {name:<22} {count:>7}/{total:>7} ({pct:>5.1f}%) {bar} {gap}")

        # ─── 4. MATCH CONTEXT TABLE ───────────────────────────────
        print("\n## 4. MATCH CONTEXT (derived from score_text)")
        r = await s.execute(text("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT match_id) as matches_with_ctx
            FROM match_context
        """))
        row = r.fetchone()
        print(f"  Total context rows:   {row[0]:,}")
        print(f"  Matches with context: {row[1]}")

        # Per-match context coverage
        r = await s.execute(text("""
            SELECT ot.match_id,
                   COUNT(DISTINCT ot.time) as tick_count,
                   COUNT(DISTINCT mc.time) as ctx_count
            FROM odds_ticks ot
            LEFT JOIN match_context mc ON ot.match_id = mc.match_id
            GROUP BY ot.match_id
            ORDER BY tick_count DESC
        """))
        rows = r.fetchall()
        print(f"\n  {'Match ID':<20} {'Ticks':>6} {'Context':>8} {'Ratio':>8}")
        print("  " + "-" * 50)
        for row in rows:
            ratio = f"{100*row[2]/max(row[1],1):.0f}%" if row[2] else "0%"
            gap = "*** NO CONTEXT ***" if row[2] == 0 else ""
            print(f"  {row[0]:<20} {row[1]:>6} {row[2]:>8} {ratio:>8} {gap}")

        # ─── 5. MATCH RESULTS ─────────────────────────────────────
        print("\n## 5. MATCH RESULTS (needed for settlement)")
        r = await s.execute(text("""
            SELECT match_id, winner, loser, result_type, source, team_home, team_away
            FROM match_results ORDER BY match_id
        """))
        result_rows = r.fetchall()
        result_ids = {row[0] for row in result_rows}

        r2 = await s.execute(text("SELECT DISTINCT match_id FROM odds_ticks"))
        all_ids = {row[0] for row in r2.fetchall()}

        missing_results = all_ids - result_ids
        print(f"  Matches with results:    {len(result_ids)}/{len(all_ids)}")
        print(f"  Matches WITHOUT results: {len(missing_results)}")
        if missing_results:
            print(f"  Missing: {sorted(missing_results)}")

        for row in result_rows:
            src = row[4] or "?"
            print(f"  {row[0]:<20} {row[5]:<18} vs {row[6]:<18} → {row[1]:<18} ({row[3]}, src={src})")

        # ─── 6. MATCH TRAINING STATUS ─────────────────────────────
        print("\n## 6. TRAINING STATUS")
        r = await s.execute(text("""
            SELECT match_id, team_home, team_away, scrape_status,
                   training_status, auto_approved
            FROM match_training_status
            ORDER BY match_id
        """))
        rows = r.fetchall()
        approved = sum(1 for row in rows if row[4] == "approved")
        pending = sum(1 for row in rows if row[4] == "pending")
        print(f"  Total tracked:     {len(rows)}")
        print(f"  Training approved: {approved}")
        print(f"  Training pending:  {pending}")
        for row in rows:
            print(f"  {row[0]:<20} {row[1]:<18} vs {row[2]:<18} scrape={row[3]:<16} train={row[4]:<10} auto={row[5]}")

        # ─── 7. TRAINING PIPELINE REQUIREMENTS ────────────────────
        print("\n## 7. TRAINING PIPELINE DATA REQUIREMENTS")
        print("  The RL training pipeline needs for each episode:")
        print("  [REQUIRED] odds_ticks: back_home, lay_home, back_away, lay_away")
        print("  [REQUIRED] match_results: winner, loser (for real settlement)")
        print("  [REQUIRED] match_training_status: training_status='approved'")
        print("  [IMPORTANT] match_context: score, wickets, overs, innings (for features)")
        print("  [USEFUL] volume data: volume_back/lay_home/away (for volume features)")
        print("  [USEFUL] score_text parsing: feeds match_context table")
        print("  [MIN] 50 ticks per match to qualify")

        # ─── 8. GAP SUMMARY ──────────────────────────────────────
        print("\n" + "=" * 70)
        print("## 8. GAP SUMMARY")
        print("=" * 70)

        gaps = []

        # Gap 1: Missing match results
        if missing_results:
            gaps.append(f"CRITICAL: {len(missing_results)} matches have NO match results "
                       f"(needed for real settlement)")

        # Gap 2: Volume data
        vol_fields = [f for f in fields if "volume" in f[0]]
        low_vol = [f for f in vol_fields if (100 * f[1] / total if total else 0) < 10]
        if low_vol:
            gaps.append(f"IMPORTANT: Volume data very sparse — "
                       f"{', '.join(f[0] for f in low_vol)} all <10% filled")

        # Gap 3: Match context
        no_ctx_matches = sum(1 for row in rows if row[2] == 0)  # reuse previous query
        # Actually need to re-check
        r = await s.execute(text("""
            SELECT COUNT(DISTINCT ot.match_id)
            FROM odds_ticks ot
            LEFT JOIN match_context mc ON ot.match_id = mc.match_id
            WHERE mc.match_id IS NULL
        """))
        no_ctx = r.scalar()
        if no_ctx:
            gaps.append(f"IMPORTANT: {no_ctx} matches have NO match_context rows "
                       f"(score/wickets/overs will be 0 in features)")

        # Gap 4: Training approval
        if pending > 0:
            gaps.append(f"ACTION: {pending} matches are 'pending' training approval "
                       f"(won't be used for training until approved)")

        # Gap 5: Total data volume
        if len(all_ids) < 50:
            gaps.append(f"CRITICAL: Only {len(all_ids)} matches total. Need 50-100+ "
                       f"for robust RL training")

        for i, gap in enumerate(gaps, 1):
            print(f"  {i}. {gap}")

        if not gaps:
            print("  No critical gaps found!")


if __name__ == "__main__":
    import sys
    # Write to file to avoid PowerShell truncation
    with open("scripts/audit_output.txt", "w", encoding="utf-8") as f:
        old_stdout = sys.stdout
        sys.stdout = f
        asyncio.run(main())
        sys.stdout = old_stdout
    print("Audit written to scripts/audit_output.txt")
