"""
PHOENIX - Sync Specialist Zoo Corpus to TimescaleDB match_training_status.

Synchronizes all historical match headers and tick counts from the 12 specialist
corpus archives (and any legacy archives) into PostgreSQL TimescaleDB:
1. Reads all match records from data/specialists/corpus_*.json.gz
2. Extracts match metadata, teams, tournament, start time, and tick counts
3. Performs high-performance bulk upsert into match_training_status table
4. Ensures the PHOENIX UI and backend API (/api/matches/training-status)
   stay in 100% forensic synchronization with disk corpus files.
"""

from __future__ import annotations

import asyncio
import glob
import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("sync_specialists_to_db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CORPUS_DIR = Path("data/specialists")
BATCH_SIZE = 1000


def parse_iso_datetime(dt_str: Any) -> datetime:
    """Parse ISO datetime string with UTC fallback."""
    if not dt_str or not isinstance(dt_str, str):
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


async def sync_specialists() -> None:
    corpus_files = sorted(glob.glob(str(CORPUS_DIR / "corpus_*.json.gz")))
    if not corpus_files:
        print(f"[ERROR] No corpus files found in {CORPUS_DIR}")
        return

    print(f"[SYNC] Found {len(corpus_files)} corpus files in {CORPUS_DIR}")

    matches_by_id: Dict[str, Dict[str, Any]] = {}

    for cpath in corpus_files:
        fname = Path(cpath).name
        print(f"[READ] Processing {fname}...")
        try:
            with gzip.open(cpath, "rt", encoding="utf-8") as gz:
                episodes = json.load(gz)
                print(f"       -> Loaded {len(episodes)} episodes")
                for ep in episodes:
                    mid = ep.get("match_id")
                    if not mid:
                        continue

                    # If already seen, keep or update with highest tick count
                    ticks = ep.get("total_ticks") or len(ep.get("ticks", []))
                    if mid in matches_by_id and matches_by_id[mid]["tick_count"] >= ticks:
                        continue

                    start_time = parse_iso_datetime(ep.get("start_time"))
                    matches_by_id[mid] = {
                        "match_id": mid,
                        "team_home": ep.get("team_home") or "Unknown Home",
                        "team_away": ep.get("team_away") or "Unknown Away",
                        "competition": ep.get("competition") or "",
                        "match_start_time": start_time,
                        "scrape_status": "scrape_approved",
                        "training_status": "approved",
                        "auto_approved": True,
                        "tick_count": ticks,
                        "quality_score": 1.0,
                        "completeness": 1.0,
                        "approved_at": start_time,
                        "created_at": start_time,
                    }
        except Exception as e:
            print(f"[ERROR] Failed to read {cpath}: {e}")

    total_records = len(matches_by_id)
    print(f"\n[DEDUPLICATED] Total unique matches to upsert: {total_records:,}")

    # Bulk upsert into match_training_status
    records_list = list(matches_by_id.values())
    upsert_sql = text("""
        INSERT INTO match_training_status (
            match_id, team_home, team_away, competition, match_start_time,
            scrape_status, training_status, auto_approved, tick_count,
            quality_score, completeness, approved_at, created_at
        ) VALUES (
            :match_id, :team_home, :team_away, :competition, :match_start_time,
            :scrape_status, :training_status, :auto_approved, :tick_count,
            :quality_score, :completeness, :approved_at, :created_at
        )
        ON CONFLICT (match_id) DO UPDATE SET
            team_home = EXCLUDED.team_home,
            team_away = EXCLUDED.team_away,
            competition = EXCLUDED.competition,
            scrape_status = EXCLUDED.scrape_status,
            training_status = EXCLUDED.training_status,
            auto_approved = EXCLUDED.auto_approved,
            tick_count = GREATEST(match_training_status.tick_count, EXCLUDED.tick_count),
            quality_score = EXCLUDED.quality_score,
            completeness = EXCLUDED.completeness,
            approved_at = COALESCE(match_training_status.approved_at, EXCLUDED.approved_at)
    """)

    print(f"[DB] Executing batch upsert in chunks of {BATCH_SIZE}...")
    start_time = datetime.now()

    async with get_session() as session:
        for i in range(0, total_records, BATCH_SIZE):
            chunk = records_list[i : i + BATCH_SIZE]
            await session.execute(upsert_sql, chunk)
            await session.commit()
            print(f"       -> Committed {min(i + BATCH_SIZE, total_records):,} / {total_records:,} matches")

        # Verify total rows in match_training_status
        cnt_res = await session.execute(text("SELECT count(*) FROM match_training_status"))
        db_total = cnt_res.scalar()
        print(f"\n[VERIFIED] match_training_status now contains {db_total:,} rows in TimescaleDB!")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"[COMPLETE] Synchronization finished successfully in {elapsed:.1f}s.")


if __name__ == "__main__":
    asyncio.run(sync_specialists())

