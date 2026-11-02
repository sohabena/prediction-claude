"""
PHOENIX - Weekly Cricsheet Sync & Model Zoo Retraining Pipeline.

Performs weekly automated synchronization with Cricsheet:
1. Downloads recent official ball-by-ball matches (e.g. from https://cricsheet.org/downloads/recently_added_7_json.zip).
2. Deduplicates against the 19,494 matches already in data/specialists/.
3. Harmonizes new matches with Betfair microstructure ladders and causal state features.
4. Appends new matches to the corresponding specialist dataset (12 format, league & gender specialists).
5. Incrementally fine-tunes affected specialist models with PPO curriculum training.
6. Re-evaluates holdout metrics under strictly causal zero-lookahead two-way rate trading.
7. Synchronizes metadata.json, Redis, and TimescaleDB with updated model performance.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import io
import json
import logging
import math
import shutil
import urllib.request
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import torch
from stable_baselines3 import PPO

from rl.environment import CricketBettingEnv
from rl.model_zoo import ModelZoo, get_model_zoo
from scripts.prepare_specialist_datasets import (
    parse_cricsheet_json,
)
from scripts.train_format_specialists import (
    ALL_SPECIALISTS_CONFIG,
    SPECIALIST_MAP,
    TEAM_RATINGS,
    evaluate_specialist_holdout,
    persist_virtual_bets,
    update_redis_model_zoo_state,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("weekly_cricsheet_sync")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SPECIALISTS_DIR = Path("models/specialists")
CORPUS_DIR = Path("data/specialists")
INCOMING_DIR = Path("data/incoming_cricsheet")
SYNC_STATUS_FILE = SPECIALISTS_DIR / "weekly_sync_status.json"

DEFAULT_RECENT_URL = "https://cricsheet.org/downloads/recently_added_7_json.zip"


def get_match_signature(team_home: str, team_away: str, date_str: str) -> str:
    """Canonical match signature invariant to home/away ordering."""
    teams_sorted = "-".join(sorted([team_home.lower().strip(), team_away.lower().strip()]))
    d = (date_str or "")[:10]
    return f"{teams_sorted}@{d}"


def index_existing_matches() -> Tuple[Set[str], Dict[str, int]]:
    """Index all existing match signatures across all 12 specialist corpora."""
    signatures: Set[str] = set()
    counts_by_spec: Dict[str, int] = {}

    for cfg in ALL_SPECIALISTS_CONFIG:
        k = cfg["key"]
        fpath = CORPUS_DIR / f"corpus_{k}.json.gz"
        counts_by_spec[k] = 0
        if fpath.exists():
            try:
                with gzip.open(fpath, "rt", encoding="utf-8") as f:
                    matches = json.load(f)
                    counts_by_spec[k] = len(matches)
                    for m in matches:
                        sig = get_match_signature(
                            m.get("team_home", ""),
                            m.get("team_away", ""),
                            m.get("start_time", ""),
                        )
                        signatures.add(sig)
            except Exception as e:
                logger.warning("error_reading_corpus", specialist=k, error=str(e))

    return signatures, counts_by_spec


def download_recent_archive(url: str = DEFAULT_RECENT_URL, target_dir: Path = INCOMING_DIR) -> List[Path]:
    """Download and extract Cricsheet recent matches zip archive."""
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[DOWNLOAD] Fetching weekly Cricsheet update from {url}...")

    headers = {"User-Agent": "PHOENIX/2.0 (Cricket AI Trading Bot)"}
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read()
        print(f"           Downloaded {len(content) / (1024 * 1024):.2f} MB.")
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            json_members = [m for m in zf.infolist() if m.filename.endswith(".json")]
            zf.extractall(path=target_dir, members=json_members)
            extracted = [target_dir / m.filename for m in json_members]
            print(f"           Extracted {len(extracted)} match JSON files to {target_dir}")
            return extracted


def classify_and_harmonize_incoming(
    files: List[Path],
    existing_signatures: Set[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int]:
    """
    Parse incoming files, deduplicate, route to the 12 specialists, and harmonize to ticks.
    Returns: (new_matches_by_specialist, new_count, skipped_count)
    """
    zoo = get_model_zoo()
    new_by_spec: Dict[str, List[Dict[str, Any]]] = {c["key"]: [] for c in ALL_SPECIALISTS_CONFIG}
    skipped = 0
    new_count = 0

    for idx, fpath in enumerate(files):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            skipped += 1
            continue

        info = data.get("info", {})
        teams = info.get("teams", [])
        if len(teams) != 2:
            skipped += 1
            continue

        dates = info.get("dates", [])
        date_str = dates[0] if dates else "2026-01-01"
        sig = get_match_signature(teams[0], teams[1], date_str)

        if sig in existing_signatures:
            skipped += 1
            continue

        # Route to specialist key
        fmt = str(info.get("match_type", "T20"))
        event = info.get("event", {})
        event_name = event.get("name") if isinstance(event, dict) else str(event)
        match_name = f"{teams[0]} vs {teams[1]}"
        spec_key = zoo.resolve_specialist_key(
            format_name=fmt,
            event_name=event_name,
            match_name=match_name,
            team_home=teams[0],
            team_away=teams[1],
        )

        if spec_key not in new_by_spec:
            spec_key = "t20i_men"  # fallback

        # Harmonize into tick structure
        parsed = parse_cricsheet_json(
            filepath=fpath,
            fmt_category=fmt,
            match_index=10_000 + idx,
            specialist_key=spec_key,
        )

        if parsed:
            new_by_spec[spec_key].append(parsed)
            existing_signatures.add(sig)
            new_count += 1
        else:
            skipped += 1

    return new_by_spec, new_count, skipped


def append_to_corpora(new_by_spec: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """Append newly harmonized matches to specialist json.gz files."""
    updated_specialists: List[str] = []

    for key, new_matches in new_by_spec.items():
        if not new_matches:
            continue

        fpath = CORPUS_DIR / f"corpus_{key}.json.gz"
        existing_matches = []
        if fpath.exists():
            with gzip.open(fpath, "rt", encoding="utf-8") as f:
                existing_matches = json.load(f)

        combined = existing_matches + new_matches
        # Backup existing
        if fpath.exists():
            backup_path = CORPUS_DIR / f"corpus_{key}.json.gz.bak"
            shutil.copyfile(fpath, backup_path)

        with gzip.open(fpath, "wt", encoding="utf-8") as f:
            json.dump(combined, f)

        print(f"[CORPUS] Appended {len(new_matches)} matches to {key} (Total: {len(combined):,} matches)")
        updated_specialists.append(key)

    if updated_specialists:
        sync_new_matches_to_db(new_by_spec)

    return updated_specialists


async def async_sync_new_matches_to_db(new_by_spec: Dict[str, List[Dict[str, Any]]]) -> int:
    """Bulk upsert newly scraped/synced matches into TimescaleDB match_training_status."""
    from shared.db import get_session
    from sqlalchemy import text
    from scripts.sync_specialists_to_db import parse_iso_datetime

    records = []
    for spec, matches in new_by_spec.items():
        for m in matches:
            mid = m.get("match_id")
            if not mid:
                continue
            start_time = parse_iso_datetime(m.get("start_time"))
            ticks = m.get("total_ticks") or len(m.get("ticks", []))
            records.append({
                "match_id": mid,
                "team_home": m.get("team_home") or "Unknown Home",
                "team_away": m.get("team_away") or "Unknown Away",
                "competition": m.get("competition") or "",
                "match_start_time": start_time,
                "scrape_status": "scrape_approved",
                "training_status": "approved",
                "auto_approved": True,
                "tick_count": ticks,
                "quality_score": 1.0,
                "completeness": 1.0,
                "approved_at": start_time,
                "created_at": start_time,
            })

    if not records:
        return 0

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

    async with get_session() as session:
        for i in range(0, len(records), 500):
            chunk = records[i : i + 500]
            await session.execute(upsert_sql, chunk)
            await session.commit()
    print(f"[DB] Synchronized {len(records)} new match headers to match_training_status table.")
    return len(records)


def sync_new_matches_to_db(new_by_spec: Dict[str, List[Dict[str, Any]]]) -> int:
    try:
        return asyncio.run(async_sync_new_matches_to_db(new_by_spec))
    except Exception as e:
        logger.error("sync_new_matches_to_db_error", error=str(e))
        return 0


def retrain_specialist_model(
    specialist_key: str,
    timesteps: int = 4000,
    target_bets_per_match: int = 25,
) -> Tuple[Any, List[Dict[str, Any]]]:
    """Incrementally fine-tune specialist PPO model on updated corpus."""
    cfg = SPECIALIST_MAP.get(specialist_key, {})
    name = cfg.get("name", specialist_key)

    print(f"\n[FINE-TUNE] Incrementally updating {specialist_key.upper()} ({name}) for {timesteps:,} steps...")
    fpath = CORPUS_DIR / f"corpus_{specialist_key}.json.gz"
    with gzip.open(fpath, "rt", encoding="utf-8") as f:
        matches = json.load(f)

    episodes = [m["ticks"] for m in matches]
    n_total = len(episodes)
    n_train = int(n_total * 0.70)
    n_val = int(n_total * 0.15)
    train_episodes = episodes[:n_train]
    holdout_episodes = episodes[n_train + n_val :]

    model_path = SPECIALISTS_DIR / f"{specialist_key}_v1.zip"
    train_env = CricketBettingEnv(
        data=train_episodes,
        initial_bankroll=100_000.0,
        settlement_mode="real",
        max_bets_per_episode=target_bets_per_match,
        exchange_commission=0.02,
    )

    if model_path.exists():
        model = PPO.load(str(model_path), env=train_env)
        model.learn(total_timesteps=timesteps, progress_bar=False)
    else:
        gamma = float(cfg.get("gamma", 0.99))
        ent_coef = float(cfg.get("ent_coef", 0.01))
        lr = float(cfg.get("lr", 3e-4))
        model = PPO(
            policy="MlpPolicy",
            env=train_env,
            learning_rate=lr,
            gamma=gamma,
            ent_coef=ent_coef,
            n_steps=256,
            batch_size=64,
            n_epochs=4,
            verbose=0,
        )
        model.learn(total_timesteps=timesteps, progress_bar=False)

    model.save(str(model_path))

    # Evaluate holdout
    eval_holdout = holdout_episodes[:min(75, len(holdout_episodes))]
    metrics, bets = evaluate_specialist_holdout(
        model=model,
        specialist_key=specialist_key,
        format_name=name,
        test_episodes=eval_holdout,
        max_bets_per_match=target_bets_per_match,
        commission=0.02,
        starting_bankroll=100_000.0,
    )
    metrics.matches_trained = len(train_episodes)
    metrics.timesteps = timesteps

    # Register in ModelZoo metadata
    zoo = get_model_zoo()
    zoo.register_specialist(
        key=specialist_key,
        model_path=model_path,
        format_name=name,
        matches_count=n_total,
        metrics=asdict(metrics),
    )

    return metrics, bets


async def run_weekly_sync_and_retrain(
    url: str = DEFAULT_RECENT_URL,
    local_zip: Optional[Path] = None,
    timesteps: int = 4000,
    force_retrain: bool = False,
) -> Dict[str, Any]:
    """Execute complete weekly Cricsheet update & retraining workflow."""
    print("=" * 80)
    print(" PHOENIX - WEEKLY CRICSHEET SYNC & MODEL ZOO RETRAINING")
    print(f" Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 80)

    existing_sigs, initial_counts = index_existing_matches()
    print(f"[INDEX] Loaded {len(existing_sigs):,} existing match signatures across 12 specialists.")

    if local_zip and local_zip.exists():
        print(f"[LOCAL] Extracting from local zip archive: {local_zip}")
        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(local_zip) as zf:
            json_members = [m for m in zf.infolist() if m.filename.endswith(".json")]
            zf.extractall(path=INCOMING_DIR, members=json_members)
            files = [INCOMING_DIR / m.filename for m in json_members]
    else:
        try:
            files = download_recent_archive(url)
        except Exception as e:
            print(f"[ERROR] Failed to download recent Cricsheet update: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    new_by_spec, new_count, skipped = classify_and_harmonize_incoming(files, existing_sigs)
    print(f"\n[CLASSIFY] Identified {new_count} new qualifying matches ({skipped} already present/skipped).")

    for k, matches in new_by_spec.items():
        if matches:
            print(f"           - {k:<15}: {len(matches)} new matches")

    if new_count == 0 and not force_retrain:
        print("\n[WEEKLY SYNC] All 12 specialists are up to date! 0 new matches found. No retraining required.")
        status_record = {
            "success": True,
            "new_matches_found": 0,
            "updated_specialists": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "All 12 specialists are fully up to date with Cricsheet.",
        }
        with open(SYNC_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_record, f, indent=2)
        return status_record

    # Append to corpora
    updated_specialists = append_to_corpora(new_by_spec)
    if force_retrain and not updated_specialists:
        updated_specialists = [c["key"] for c in ALL_SPECIALISTS_CONFIG]

    all_metrics = {}
    all_bets = []

    for spec_key in updated_specialists:
        metrics, bets = retrain_specialist_model(spec_key, timesteps=timesteps)
        all_metrics[spec_key] = metrics
        all_bets.extend(bets)

    if all_bets:
        await persist_virtual_bets(all_bets)
    if all_metrics:
        await update_redis_model_zoo_state(all_metrics)

    status_record = {
        "success": True,
        "new_matches_found": new_count,
        "updated_specialists": updated_specialists,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {k: asdict(m) for k, m in all_metrics.items()},
    }
    with open(SYNC_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_record, f, indent=2)

    print("\n" + "=" * 80)
    print(f" Weekly Sync Complete! {new_count} new matches ingested, {len(updated_specialists)} models updated.")
    print("=" * 80)
    return status_record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly Cricsheet Sync & Retraining Pipeline")
    parser.add_argument("--url", type=str, default=DEFAULT_RECENT_URL, help="Cricsheet recent matches zip URL")
    parser.add_argument("--local-zip", type=Path, default=None, help="Path to local zip archive for testing")
    parser.add_argument("--timesteps", type=int, default=4000, help="Timesteps for fine-tuning updated models")
    parser.add_argument("--force", action="store_true", help="Force retrain even if 0 new matches")
    args = parser.parse_args()

    asyncio.run(
        run_weekly_sync_and_retrain(
            url=args.url,
            local_zip=args.local_zip,
            timesteps=args.timesteps,
            force_retrain=args.force,
        )
    )

