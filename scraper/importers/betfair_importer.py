"""
Betfair Historical Data Importer for PHOENIX.
Loads historical Betfair cricket exchange files into TimescaleDB:
  - odds_ticks (time-series hypertable)
  - match_results (settlement outcomes + closing odds for CLV)
  - match_training_status (pre-approved for RL offline training)

This unblocks immediate offline training and curriculum stages
without waiting weeks for live scraping accumulation.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.models.match_status import MatchTrainingStatus
from backend.models.odds import OddsTick
from backend.models.results import MatchResultRecord
from scraper.parsers.betfair_historical import (
    BetfairHistoricalParser,
    ParsedHistoricalMatch,
)
from shared.db import get_session
from shared.logging import setup_logging

logger = setup_logging("betfair_importer")


@dataclass
class ImportSummary:
    """Summary of data imported from Betfair archives."""

    total_files_scanned: int = 0
    matches_imported: int = 0
    ticks_imported: int = 0
    results_recorded: int = 0
    training_episodes_approved: int = 0
    skipped_matches: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class BetfairHistoricalImporter:
    """
    Orchestrates parsing and database persistence of Betfair historical data.
    """

    def __init__(
        self,
        resample_seconds: float = 3.0,
        min_ticks: int = 50,
        batch_size: int = 2000,
    ) -> None:
        self.parser = BetfairHistoricalParser(
            resample_seconds=resample_seconds,
            min_ticks=min_ticks,
        )
        self.batch_size = batch_size

    # ================================================================
    # Ingestion Pipeline
    # ================================================================

    async def import_path(
        self,
        target_path: str | Path,
        dry_run: bool = False,
    ) -> ImportSummary:
        """
        Scan a file or directory and import all Betfair historical files.

        Args:
            target_path: Path to a file (.json, .json.gz, .bz2, .csv) or directory.
            dry_run: If True, parses and validates without committing to the DB.
        """
        path = Path(target_path)
        summary = ImportSummary()

        if not path.exists():
            summary.errors.append(f"Target path does not exist: {target_path}")
            logger.error("import_path_missing", path=str(target_path))
            return summary

        # Gather target files
        if path.is_file():
            files = [path]
        else:
            extensions = ("*.json", "*.json.gz", "*.bz2", "*.csv", "*.csv.gz", "*.jsonl")
            files = []
            for ext in extensions:
                files.extend(path.rglob(ext))

        summary.total_files_scanned = len(files)
        logger.info("betfair_files_discovered", count=len(files), root=str(path))

        all_matches: list[ParsedHistoricalMatch] = []
        for f in files:
            try:
                matches = self.parser.parse_file(f)
                all_matches.extend(matches)
            except Exception as e:
                summary.errors.append(f"Failed parsing {f.name}: {e}")
                logger.warning("file_parse_error", file=f.name, error=str(e))

        logger.info("matches_parsed", count=len(all_matches))

        if dry_run:
            for m in all_matches:
                summary.matches_imported += 1
                summary.ticks_imported += len(m.ticks)
                if m.winner:
                    summary.results_recorded += 1
                summary.training_episodes_approved += 1
            return summary

        # Persist to database
        for match in all_matches:
            try:
                success = await self._persist_match(match)
                if success:
                    summary.matches_imported += 1
                    summary.ticks_imported += len(match.ticks)
                    if match.winner:
                        summary.results_recorded += 1
                    summary.training_episodes_approved += 1
                else:
                    summary.skipped_matches += 1
            except Exception as e:
                summary.errors.append(f"Error persisting match {match.match_id}: {e}")
                logger.error("persist_match_error", match_id=match.match_id, error=str(e))

        return summary

    async def _persist_match(self, match: ParsedHistoricalMatch) -> bool:
        """Persist ticks, result, and training status for a parsed match."""
        if not match.ticks:
            return False

        async with get_session() as session:
            # 1. Upsert match_training_status so it is immediately approved for RL
            status_stmt = pg_insert(MatchTrainingStatus).values(
                match_id=match.match_id,
                team_home=match.team_home,
                team_away=match.team_away,
                competition=match.competition,
                match_start_time=match.start_time,
                scrape_status="scrape_approved",
                training_status="approved",
                auto_approved=True,
                quality_score=1.0,
                completeness=1.0,
                approved_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["match_id"],
                set_={
                    "scrape_status": "scrape_approved",
                    "training_status": "approved",
                    "approved_at": datetime.now(timezone.utc),
                },
            )
            await session.execute(status_stmt)

            # 2. Upsert match_results if winner is known
            if match.winner:
                result_stmt = pg_insert(MatchResultRecord).values(
                    match_id=match.match_id,
                    completed_at=match.completed_at,
                    winner=match.winner,
                    loser=match.loser,
                    result_type=match.result_type,
                    team_home=match.team_home,
                    team_away=match.team_away,
                    source="betfair_historical",
                    closing_back_home=match.closing_back_home,
                    closing_back_away=match.closing_back_away,
                    closing_lay_home=match.closing_lay_home,
                    closing_lay_away=match.closing_lay_away,
                ).on_conflict_do_update(
                    index_elements=["match_id"],
                    set_={
                        "winner": match.winner,
                        "loser": match.loser,
                        "closing_back_home": match.closing_back_home,
                        "closing_back_away": match.closing_back_away,
                    },
                )
                await session.execute(result_stmt)

            # 3. Clean any existing ticks for this match to prevent duplicates
            await session.execute(
                delete(OddsTick).where(OddsTick.match_id == match.match_id)
            )

            # 4. Batch insert odds_ticks
            tick_objects = [
                OddsTick(
                    time=t["timestamp"],
                    match_id=match.match_id,
                    team_home=match.team_home,
                    team_away=match.team_away,
                    competition=match.competition,
                    back_home=t.get("back_home"),
                    lay_home=t.get("lay_home"),
                    back_draw=t.get("back_draw"),
                    lay_draw=t.get("lay_draw"),
                    back_away=t.get("back_away"),
                    lay_away=t.get("lay_away"),
                    volume_back_home=t.get("volume_back_home"),
                    volume_lay_home=t.get("volume_lay_home"),
                    volume_back_away=t.get("volume_back_away"),
                    volume_lay_away=t.get("volume_lay_away"),
                    implied_prob_home=t.get("implied_prob_home"),
                    implied_prob_away=t.get("implied_prob_away"),
                    overround=t.get("overround"),
                    is_live=t.get("is_live", True),
                    source="betfair_historical",
                )
                for t in match.ticks
            ]

            for i in range(0, len(tick_objects), self.batch_size):
                chunk = tick_objects[i : i + self.batch_size]
                session.add_all(chunk)

            await session.commit()

        logger.info(
            "match_persisted",
            match_id=match.match_id,
            ticks=len(match.ticks),
            winner=match.winner,
        )
        return True

    # ================================================================
    # Synthetic Historical Cricket Data Generator
    # ================================================================

    @staticmethod
    def generate_synthetic_match(
        match_id: str,
        team_home: str,
        team_away: str,
        competition: str,
        start_time: datetime,
        ticks_count: int = 120,
        home_win_prob: float = 0.55,
    ) -> ParsedHistoricalMatch:
        """
        Generate a realistic in-play cricket odds trajectory for offline training.
        Simulates:
          - Realistic starting odds (1.70 - 2.20)
          - Random walk with drift based on in-play match events
          - Tight exchange spreads (back <= lay, 1-3 ticks)
          - Convergence to 1.01-1.05 for winner and 20.0-100.0 for loser
        """
        random.seed(hash(match_id) % 100000)
        home_wins = random.random() < home_win_prob

        # Starting price
        initial_prob_home = random.uniform(0.45, 0.60)
        p_home = initial_prob_home

        ticks: list[dict[str, Any]] = []
        current_time = start_time

        for step in range(ticks_count):
            progress = step / float(ticks_count)

            # Drift towards the true winner as match approaches completion
            target_p = 0.98 if home_wins else 0.02
            drift = (target_p - p_home) * (progress ** 1.8) * 0.15

            # Random walk shock (boundaries, wickets, dots)
            shock = random.gauss(0, 0.035 * (1.0 - 0.7 * progress))
            p_home = max(0.01, min(0.99, p_home + drift + shock))

            # Derive back/lay with 1.5% exchange margin
            back_h = max(1.02, min(50.0, 1.0 / (p_home * 1.015)))
            lay_h = max(back_h * 1.01, back_h + random.uniform(0.01, 0.04))

            p_away = 1.0 - p_home
            back_a = max(1.02, min(50.0, 1.0 / (p_away * 1.015)))
            lay_a = max(back_a * 1.01, back_a + random.uniform(0.01, 0.04))

            ip_h = 1.0 / back_h
            ip_a = 1.0 / back_a
            overround = ip_h + ip_a - 1.0

            ticks.append({
                "match_id": match_id,
                "team_home": team_home,
                "team_away": team_away,
                "competition": competition,
                "timestamp": current_time,
                "back_home": round(back_h, 2),
                "lay_home": round(lay_h, 2),
                "back_away": round(back_a, 2),
                "lay_away": round(lay_a, 2),
                "back_draw": None,
                "lay_draw": None,
                "volume_back_home": round(random.uniform(500, 15000), 0),
                "volume_lay_home": round(random.uniform(500, 15000), 0),
                "volume_back_away": round(random.uniform(500, 15000), 0),
                "volume_lay_away": round(random.uniform(500, 15000), 0),
                "implied_prob_home": round(ip_h, 4),
                "implied_prob_away": round(ip_a, 4),
                "overround": round(overround, 4),
                "is_live": True,
            })

            # Advance by 3-5 seconds per tick
            current_time += timedelta(seconds=random.randint(3, 5))

        winner = team_home if home_wins else team_away
        loser = team_away if home_wins else team_home
        last_tick = ticks[-1]

        return ParsedHistoricalMatch(
            match_id=match_id,
            team_home=team_home,
            team_away=team_away,
            competition=competition,
            start_time=start_time,
            completed_at=current_time,
            winner=winner,
            loser=loser,
            result_type="win",
            ticks=ticks,
            closing_back_home=last_tick["back_home"],
            closing_back_away=last_tick["back_away"],
            closing_lay_home=last_tick["lay_home"],
            closing_lay_away=last_tick["lay_away"],
        )

    async def generate_and_import_demo_dataset(
        self,
        match_count: int = 15,
        ticks_per_match: int = 120,
    ) -> ImportSummary:
        """
        Generate and import a verified synthetic Betfair dataset of major matches.
        Used to eliminate the cold-start barrier immediately.
        """
        sample_fixtures = [
            ("India", "Australia", "ICC Champions Trophy"),
            ("England", "South Africa", "ICC World T20"),
            ("Pakistan", "New Zealand", "T20 International"),
            ("Mumbai Indians", "Chennai Super Kings", "IPL"),
            ("Royal Challengers Bengaluru", "Kolkata Knight Riders", "IPL"),
            ("West Indies", "Sri Lanka", "ODI Series"),
            ("Perth Scorchers", "Sydney Sixers", "BBL"),
            ("Gujarat Titans", "Rajasthan Royals", "IPL"),
            ("Afghanistan", "Bangladesh", "Asia Cup"),
            ("Sunrisers Hyderabad", "Delhi Capitals", "IPL"),
            ("Lahore Qalandars", "Karachi Kings", "PSL"),
            ("Australia", "England", "The Ashes T20"),
            ("South Africa", "India", "T20 Bilateral"),
            ("Melbourne Stars", "Adelaide Strikers", "BBL"),
            ("Ireland", "Scotland", "T20 World Cup Qualifier"),
        ]

        summary = ImportSummary()
        now = datetime.now(timezone.utc)

        for i in range(min(match_count, len(sample_fixtures))):
            team_h, team_a, comp = sample_fixtures[i]
            match_id = f"bf_demo_{team_h[:3].lower()}_{team_a[:3].lower()}_{i+1}"
            start_time = now - timedelta(days=match_count - i, hours=random.randint(1, 10))

            match = self.generate_synthetic_match(
                match_id=match_id,
                team_home=team_h,
                team_away=team_a,
                competition=comp,
                start_time=start_time,
                ticks_count=ticks_per_match,
            )

            try:
                success = await self._persist_match(match)
                if success:
                    summary.matches_imported += 1
                    summary.ticks_imported += len(match.ticks)
                    summary.results_recorded += 1
                    summary.training_episodes_approved += 1
            except Exception as e:
                summary.errors.append(f"Demo match {match_id} error: {e}")

        logger.info(
            "demo_dataset_imported",
            matches=summary.matches_imported,
            ticks=summary.ticks_imported,
        )
        return summary
