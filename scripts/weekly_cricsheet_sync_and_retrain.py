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
import hashlib
import io
import json
import logging
import math
import os
import random
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # allow `python scripts/...` from the repo root
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.utils import get_schedule_fn

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
from shared.constants import MAX_DRAWDOWN_PERCENT
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


def index_existing_matches(corpus_dir: Optional[Path] = None) -> Tuple[Set[str], Dict[str, int]]:
    """Index all existing match signatures across all 12 specialist corpora."""
    corpus_dir = corpus_dir or CORPUS_DIR
    signatures: Set[str] = set()
    counts_by_spec: Dict[str, int] = {}

    for cfg in ALL_SPECIALISTS_CONFIG:
        k = cfg["key"]
        fpath = corpus_dir / f"corpus_{k}.json.gz"
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
            # Unique, week-independent id (the positional index restarts every run and would collide).
            new_id = f"cric_{spec_key}_{(date_str or '')[:10]}_{hashlib.sha1(sig.encode('utf-8')).hexdigest()[:8]}"
            parsed["match_id"] = new_id
            for tick in parsed.get("ticks", []):
                tick["match_id"] = new_id
            new_by_spec[spec_key].append(parsed)
            existing_signatures.add(sig)
            new_count += 1
        else:
            skipped += 1

    return new_by_spec, new_count, skipped



# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to a temp file in the same directory, then ``os.replace`` it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _metric(m: Any, name: str, default: Any = None) -> Any:
    """Read a metric from a dataclass or a dict."""
    if isinstance(m, dict):
        return m.get(name, default)
    return getattr(m, name, default)


def _metrics_dict(m: Any) -> Dict[str, Any]:
    if m is None:
        return {}
    if isinstance(m, dict):
        return dict(m)
    return asdict(m) if is_dataclass(m) else dict(vars(m))


def _match_time(m: Dict[str, Any]) -> str:
    """Chronological sort key: ``start_time`` else the first tick time."""
    st = m.get("start_time")
    if st:
        return str(st)
    ticks = m.get("ticks") or []
    if ticks:
        t = ticks[0].get("time", ticks[0].get("timestamp", ""))
        return str(t or "")
    return ""


def _dedupe_key(m: Dict[str, Any]) -> str:
    date = str(m.get("start_time") or "")[:10]
    if date:
        return get_match_signature(m.get("team_home", ""), m.get("team_away", ""), date)
    return f"id:{m.get('match_id')}"


def merge_corpus(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine corpora, de-duplicate by match signature (first seen wins), sort chronologically."""
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for m in list(existing) + list(new):
        k = _dedupe_key(m)
        if k in seen:
            continue
        seen.add(k)
        out.append(m)
    out.sort(key=_match_time)  # stable
    return out


# ---------------------------------------------------------------------------
# Frozen split manifest
# ---------------------------------------------------------------------------

def split_manifest_path(key: str, corpus_dir: Optional[Path] = None) -> Path:
    return (corpus_dir or CORPUS_DIR) / f"splits_{key}.json"


def build_split_manifest(
    matches: List[Dict[str, Any]],
    existing: Optional[Dict[str, Any]] = None,
    holdout_frac: float = 0.15,
    val_frac: float = 0.15,
    holdout_refresh: int = 0,
) -> Dict[str, Any]:
    """Create (once) or update a frozen train/val/holdout manifest.

    Created from the chronologically latest ``holdout_frac`` (holdout) and the
    ``val_frac`` before it (val). Existing IDs never move; every other match is train.
    ``holdout_refresh=N`` appends the newest N unassigned matches as a *new* holdout
    generation (old generations stay in the holdout).
    """
    ordered = sorted(matches, key=_match_time)
    ids = [str(m.get("match_id")) for m in ordered]

    if not existing or not existing.get("holdout_ids"):
        n = len(ordered)
        n_hold = max(1, int(round(n * holdout_frac))) if n >= 3 else 0
        n_val = max(1, int(round(n * val_frac))) if n >= 6 else 0
        hold = ids[n - n_hold:] if n_hold else []
        val = ids[n - n_hold - n_val: n - n_hold] if n_val else []
        cutoff = _match_time(ordered[n - n_hold])[:10] if n_hold else ""
        return {
            "holdout_ids": hold,
            "val_ids": val,
            "holdout_generations": [list(hold)],
            "created_at": _utc_now().isoformat(),
            "cutoff_date": cutoff,
            "pending_ids": [],
        }

    manifest = json.loads(json.dumps(existing))  # deep copy
    manifest.setdefault("holdout_generations", [list(manifest["holdout_ids"])])
    manifest.setdefault("pending_ids", [])
    if holdout_refresh > 0:
        assigned = set(manifest["holdout_ids"]) | set(manifest.get("val_ids", []))
        free = [i for i in ids if i not in assigned]
        gen = free[-holdout_refresh:]
        if gen:
            manifest["holdout_generations"].append(gen)
            manifest["holdout_ids"] = list(manifest["holdout_ids"]) + gen
            manifest["refreshed_at"] = _utc_now().isoformat()
    return manifest


def load_or_create_split(
    key: str,
    matches: List[Dict[str, Any]],
    corpus_dir: Optional[Path] = None,
    holdout_refresh: int = 0,
    persist: bool = True,
) -> Dict[str, Any]:
    path = split_manifest_path(key, corpus_dir)
    existing = _read_json(path, None)
    manifest = build_split_manifest(matches, existing, holdout_refresh=holdout_refresh)
    if persist and manifest != existing:
        _atomic_write_json(path, manifest)
    return manifest


def select_finetune_matches(
    matches: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    new_ids: Set[str],
    replay_min: int = 200,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """All new train matches plus an equal-size (min ``replay_min``) uniform sample of older ones."""
    excluded = set(manifest.get("holdout_ids", [])) | set(manifest.get("val_ids", []))
    new = [m for m in matches if str(m.get("match_id")) in new_ids and str(m.get("match_id")) not in excluded]
    older = [m for m in matches if str(m.get("match_id")) not in new_ids and str(m.get("match_id")) not in excluded]
    rng = random.Random(seed)
    k = min(len(older), max(len(new), replay_min))
    mix = new + rng.sample(older, k)
    rng.shuffle(mix)  # env samples episodes randomly too; shuffle keeps order unbiased regardless
    return mix


# ---------------------------------------------------------------------------
# Corpus append (async, awaits the DB sync directly)
# ---------------------------------------------------------------------------

async def append_to_corpora(
    new_by_spec: Dict[str, List[Dict[str, Any]]],
    corpus_dir: Optional[Path] = None,
    dry_run: bool = False,
    db_sync_fn: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], Any]] = None,
    status: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Merge new matches into specialist corpora (chronological, de-duplicated).

    The DB sync is awaited directly (no nested event loop). Failures are logged and
    reflected in ``status["db_sync"]`` (``ok`` / ``failed`` / ``skipped``).
    """
    corpus_dir = Path(corpus_dir or CORPUS_DIR)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    updated_specialists: List[str] = []

    for key, new_matches in new_by_spec.items():
        if not new_matches:
            continue

        fpath = corpus_dir / f"corpus_{key}.json.gz"
        existing_matches: List[Dict[str, Any]] = []
        if fpath.exists():
            with gzip.open(fpath, "rt", encoding="utf-8") as f:
                existing_matches = json.load(f)
            shutil.copyfile(fpath, corpus_dir / f"corpus_{key}.json.gz.bak")

        combined = merge_corpus(existing_matches, new_matches)
        with gzip.open(fpath, "wt", encoding="utf-8") as f:
            json.dump(combined, f)

        print(f"[CORPUS] Appended {len(new_matches)} matches to {key} (Total: {len(combined):,} matches)")
        updated_specialists.append(key)

    db_status = "skipped"
    if updated_specialists and not dry_run:
        sync = db_sync_fn or async_sync_new_matches_to_db
        try:
            await sync(new_by_spec)
            db_status = "ok"
        except Exception as e:
            logger.error("sync_new_matches_to_db_error", error=str(e))
            db_status = "failed"
    if status is not None:
        status["db_sync"] = db_status
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


# ---------------------------------------------------------------------------
# Update-health guard
# ---------------------------------------------------------------------------

class UpdateHealthCallback(BaseCallback):
    """Tracks SB3 PPO update health and aborts training when it degrades.

    Aborts when the mean ``train/approx_kl`` over the last ``window`` updates exceeds
    ``kl_limit`` or when ``train/value_loss`` is NaN/inf.
    """

    KEYS = {"approx_kl": "train/approx_kl", "clip_fraction": "train/clip_fraction", "value_loss": "train/value_loss"}

    def __init__(self, kl_limit: float = 0.05, window: int = 5) -> None:
        super().__init__()
        self.kl_limit = kl_limit
        self.window = window
        self.history: Dict[str, List[float]] = {k: [] for k in self.KEYS}
        self.aborted = False
        self.abort_reason = ""

    def record(self, values: Dict[str, Any]) -> None:
        for short, full in self.KEYS.items():
            if full in values:
                self.history[short].append(float(values[full]))
        vl = self.history["value_loss"]
        if vl and not math.isfinite(vl[-1]):
            self.aborted, self.abort_reason = True, "value_loss_not_finite"
            return
        kl = self.history["approx_kl"][-self.window:]
        if kl and float(np.mean(kl)) > self.kl_limit:
            self.aborted, self.abort_reason = True, f"approx_kl_mean_{float(np.mean(kl)):.4f}_gt_{self.kl_limit}"

    def summary(self) -> Dict[str, Any]:
        def mean(k: str) -> Optional[float]:
            v = [x for x in self.history[k] if math.isfinite(x)]
            return float(np.mean(v)) if v else None
        return {
            "n_updates": len(self.history["approx_kl"]),
            "approx_kl_mean": mean("approx_kl"),
            "clip_fraction_mean": mean("clip_fraction"),
            "value_loss_last": self.history["value_loss"][-1] if self.history["value_loss"] else None,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
        }

    def _on_step(self) -> bool:
        return not self.aborted

    def _on_rollout_end(self) -> None:
        self.record(dict(self.model.logger.name_to_value))


# ---------------------------------------------------------------------------
# Champion / candidate registry (metadata.json) and promotion gate
# ---------------------------------------------------------------------------

def _version_of(path: Any) -> int:
    stem = Path(str(path)).stem
    if "_v" in stem:
        tail = stem.rsplit("_v", 1)[1]
        if tail.isdigit():
            return int(tail)
    return 1


def read_metadata(models_dir: Path) -> Dict[str, Any]:
    return _read_json(Path(models_dir) / "metadata.json", {})


def champion_path_for(key: str, models_dir: Path, metadata: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    meta = (metadata if metadata is not None else read_metadata(models_dir)).get(key, {})
    cand = meta.get("champion_path") or meta.get("path")
    p = Path(cand) if cand else Path(models_dir) / f"{key}_v1.zip"
    if not p.exists() and not p.is_absolute():
        alt = Path(models_dir) / p.name
        p = alt if alt.exists() else p
    return p if p.exists() else None


def decide_promotion(
    candidate: Any, champion: Any, max_drawdown_frac: float = MAX_DRAWDOWN_PERCENT
) -> Tuple[bool, List[str]]:
    """Promotion gate. Returns (promote, failed_reasons)."""
    reasons: List[str] = []
    c_mean = float(_metric(candidate, "mean_match_pnl", 0.0) or 0.0)
    if champion is not None and c_mean - float(_metric(champion, "mean_match_pnl", 0.0) or 0.0) < 0:
        reasons.append("candidate_mean_pnl_below_champion")
    if not float(_metric(candidate, "pnl_ci95_low", 0.0) or 0.0) > 0:
        reasons.append("pnl_ci95_low_not_positive")
    if float(_metric(candidate, "max_drawdown_pct", 0.0) or 0.0) / 100.0 > max_drawdown_frac:
        reasons.append("max_drawdown_above_limit")
    edge = _metric(candidate, "edge_vs_random", None)
    if edge is None or not float(edge) > 0:
        reasons.append("edge_vs_random_not_positive")
    return (not reasons), reasons


def promote_candidate(
    key: str,
    candidate_path: Path,
    candidate_metrics: Any,
    models_dir: Path,
    format_name: str = "",
    matches_count: int = 0,
) -> Dict[str, Any]:
    """Copy candidate to ``<key>_v<N+1>.zip`` and atomically update metadata. Never overwrites."""
    models_dir = Path(models_dir)
    metadata = read_metadata(models_dir)
    entry = dict(metadata.get(key, {}))
    prev_path = champion_path_for(key, models_dir, metadata)
    prev_version = int(entry.get("version") or (_version_of(prev_path) if prev_path else 0))
    existing_versions = [_version_of(p) for p in models_dir.glob(f"{key}_v*.zip")]
    new_version = max([prev_version] + existing_versions) + 1
    dest = models_dir / f"{key}_v{new_version}.zip"
    while dest.exists():
        new_version += 1
        dest = models_dir / f"{key}_v{new_version}.zip"
    shutil.copyfile(candidate_path, dest)

    history = list(entry.get("history", []))
    if prev_path is not None:
        history.append({
            "version": prev_version,
            "path": str(prev_path),
            "metrics": entry.get("metrics", {}),
            "retired_at": _utc_now().isoformat(),
        })
    entry.update({
        "key": key,
        "path": str(dest),
        "champion_path": str(dest),
        "version": new_version,
        "format": format_name or entry.get("format", ""),
        "matches_trained_on": matches_count or entry.get("matches_trained_on", 0),
        "metrics": _metrics_dict(candidate_metrics),
        "active": True,
        "history": history,
        "promoted_at": _utc_now().isoformat(),
    })
    metadata[key] = entry
    _atomic_write_json(models_dir / "metadata.json", metadata)
    return {"version": new_version, "path": str(dest), "previous_path": str(prev_path) if prev_path else None}


# ---------------------------------------------------------------------------
# Training / evaluation
# ---------------------------------------------------------------------------

def default_timesteps(train_matches: List[Dict[str, Any]]) -> int:
    ticks = sum(len(m.get("ticks", [])) for m in train_matches)
    return max(50_000, 20 * ticks)


def train_candidate(
    specialist_key: str,
    train_matches: List[Dict[str, Any]],
    timesteps: int,
    champion_path: Optional[Path],
    candidate_path: Path,
    target_bets_per_match: int = 25,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """Fine-tune the champion (or train fresh) and save to ``candidate_path``.

    Returns (path, health summary); path is None if the update-health guard aborted.
    """
    cfg = SPECIALIST_MAP.get(specialist_key, {})
    episodes = [m["ticks"] for m in train_matches]
    env = CricketBettingEnv(
        data=episodes,
        initial_bankroll=100_000.0,
        settlement_mode="real",
        max_bets_per_episode=target_bets_per_match,
        exchange_commission=0.02,
        episode_sampling="random",
    )
    base_lr = float(cfg.get("lr", 3e-4))
    guard = UpdateHealthCallback()
    if champion_path is not None and Path(champion_path).exists():
        model = PPO.load(str(champion_path), env=env)
        lr = base_lr * 0.3  # lower LR for fine-tunes
        model.learning_rate = lr
        model.lr_schedule = get_schedule_fn(lr)
    else:
        model = PPO(
            policy="MlpPolicy", env=env, learning_rate=base_lr,
            gamma=float(cfg.get("gamma", 0.99)), ent_coef=float(cfg.get("ent_coef", 0.01)),
            n_steps=256, batch_size=64, n_epochs=4, verbose=0,
        )
    model.learn(total_timesteps=timesteps, callback=guard, progress_bar=False, reset_num_timesteps=False)
    health = guard.summary()
    if guard.aborted:
        return None, health
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(candidate_path))
    return candidate_path, health


def evaluate_model_path(
    model_path: Path,
    specialist_key: str,
    format_name: str,
    holdout_episodes: List[List[Dict[str, Any]]],
    target_bets_per_match: int = 25,
) -> Tuple[Any, List[Dict[str, Any]]]:
    model = PPO.load(str(model_path))
    return evaluate_specialist_holdout(
        model=model,
        specialist_key=specialist_key,
        format_name=format_name,
        test_episodes=holdout_episodes,
        max_bets_per_match=target_bets_per_match,
        commission=0.02,
        starting_bankroll=100_000.0,
    )


def retrain_specialist_model(
    specialist_key: str,
    timesteps: Optional[int] = None,
    target_bets_per_match: int = 25,
    *,
    corpus_dir: Optional[Path] = None,
    models_dir: Optional[Path] = None,
    new_ids: Optional[Set[str]] = None,
    dry_run: bool = False,
    holdout_refresh: int = 0,
    train_fn: Optional[Callable[..., Tuple[Optional[Path], Dict[str, Any]]]] = None,
    evaluate_fn: Optional[Callable[..., Tuple[Any, List[Dict[str, Any]]]]] = None,
) -> Dict[str, Any]:
    """Fine-tune on new + replay matches, evaluate candidate vs champion on the frozen holdout, gate promotion."""
    corpus_dir = Path(corpus_dir or CORPUS_DIR)
    models_dir = Path(models_dir or SPECIALISTS_DIR)
    train_fn = train_fn or train_candidate
    evaluate_fn = evaluate_fn or evaluate_model_path
    name = SPECIALIST_MAP.get(specialist_key, {}).get("name", specialist_key)

    with gzip.open(corpus_dir / f"corpus_{specialist_key}.json.gz", "rt", encoding="utf-8") as f:
        matches = json.load(f)

    manifest = load_or_create_split(specialist_key, matches, corpus_dir, holdout_refresh, persist=True)
    pending = set(manifest.get("pending_ids", [])) | set(new_ids or set())
    mix = select_finetune_matches(matches, manifest, pending)
    if not mix:
        return {"key": specialist_key, "promoted": False, "reasons": ["no_training_matches"], "bets": []}
    steps = int(timesteps) if timesteps else default_timesteps(mix)
    holdout_set = set(manifest["holdout_ids"])
    holdout_episodes = [m["ticks"] for m in matches if str(m.get("match_id")) in holdout_set]

    champion = champion_path_for(specialist_key, models_dir)
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    cand_path = models_dir / "candidates" / f"{specialist_key}_{stamp}.zip"
    print(f"\n[FINE-TUNE] {specialist_key.upper()} ({name}): {len(mix)} matches in mix, {steps:,} steps")

    result: Dict[str, Any] = {
        "key": specialist_key, "timesteps": steps, "n_train_mix": len(mix),
        "n_holdout": len(holdout_episodes), "holdout_generations": len(manifest.get("holdout_generations", [])),
        "promoted": False, "reasons": [], "bets": [],
    }
    saved, health = train_fn(specialist_key, mix, steps, champion, cand_path, target_bets_per_match)
    result["health"] = health
    manifest["pending_ids"] = []  # consumed by this training run
    _atomic_write_json(split_manifest_path(specialist_key, corpus_dir), manifest)
    if saved is None:
        result["reasons"] = [f"update_health_guard:{health.get('abort_reason', '')}"]
        return result
    result["candidate_path"] = str(saved)

    cand_metrics, bets = evaluate_fn(saved, specialist_key, name, holdout_episodes, target_bets_per_match)
    champ_metrics = None
    if champion is not None:
        champ_metrics, _ = evaluate_fn(champion, specialist_key, name, holdout_episodes, target_bets_per_match)
    ok, reasons = decide_promotion(cand_metrics, champ_metrics)
    result.update({
        "candidate_metrics": _metrics_dict(cand_metrics),
        "champion_metrics": _metrics_dict(champ_metrics),
        "reasons": reasons,
        "bets": bets,
    })
    if ok and not dry_run:
        info = promote_candidate(specialist_key, saved, cand_metrics, models_dir, name, len(matches))
        result.update({"promoted": True, **{f"promotion_{k}": v for k, v in info.items()}})
        try:
            zoo = get_model_zoo()
            zoo._load_metadata()
            zoo._models.pop(specialist_key, None)
        except Exception as e:  # cache refresh is best-effort
            logger.warning("zoo_cache_refresh_failed", error=str(e))
    elif ok:
        result["reasons"] = ["dry_run"]
    return result


async def run_weekly_sync_and_retrain(
    url: str = DEFAULT_RECENT_URL,
    local_zip: Optional[Path] = None,
    timesteps: Optional[int] = None,
    force_retrain: bool = False,
    *,
    dry_run: bool = False,
    holdout_refresh: int = 0,
    corpus_dir: Optional[Path] = None,
    models_dir: Optional[Path] = None,
    incoming_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute complete weekly Cricsheet update & retraining workflow."""
    corpus_dir = Path(corpus_dir or CORPUS_DIR)
    models_dir = Path(models_dir or SPECIALISTS_DIR)
    incoming_dir = Path(incoming_dir or INCOMING_DIR)
    status_file = models_dir / "weekly_sync_status.json"
    models_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(" PHOENIX - WEEKLY CRICSHEET SYNC & MODEL ZOO RETRAINING" + (" [DRY RUN]" if dry_run else ""))
    print(f" Timestamp: {_utc_now().isoformat()}")
    print("=" * 80)

    existing_sigs, _ = index_existing_matches(corpus_dir)
    print(f"[INDEX] Loaded {len(existing_sigs):,} existing match signatures across 12 specialists.")

    if local_zip and Path(local_zip).exists():
        print(f"[LOCAL] Extracting from local zip archive: {local_zip}")
        incoming_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(local_zip) as zf:
            json_members = [m for m in zf.infolist() if m.filename.endswith(".json")]
            zf.extractall(path=incoming_dir, members=json_members)
            files = [incoming_dir / m.filename for m in json_members]
    else:
        try:
            files = download_recent_archive(url, incoming_dir)
        except Exception as e:
            print(f"[ERROR] Failed to download recent Cricsheet update: {e}")
            return {"success": False, "error": str(e), "timestamp": _utc_now().isoformat()}

    new_by_spec, new_count, skipped = classify_and_harmonize_incoming(files, existing_sigs)
    print(f"\n[CLASSIFY] Identified {new_count} new qualifying matches ({skipped} already present/skipped).")
    for k, ms in new_by_spec.items():
        if ms:
            print(f"           - {k:<15}: {len(ms)} new matches")

    status_record: Dict[str, Any] = {
        "success": True, "dry_run": dry_run, "new_matches_found": new_count,
        "updated_specialists": [], "timestamp": _utc_now().isoformat(), "db_sync": "skipped",
    }
    if new_count == 0 and not force_retrain:
        status_record["message"] = "All 12 specialists are fully up to date with Cricsheet."
        _atomic_write_json(status_file, status_record)
        return status_record

    updated = await append_to_corpora(new_by_spec, corpus_dir, dry_run=dry_run, status=status_record)
    if force_retrain and not updated:
        updated = [c["key"] for c in ALL_SPECIALISTS_CONFIG if (corpus_dir / f"corpus_{c['key']}.json.gz").exists()]
    status_record["updated_specialists"] = updated

    # New ids stay pending until a training run consumes them (survives failed runs).
    for key in updated:
        ids = {str(m.get("match_id")) for m in new_by_spec.get(key, [])}
        with gzip.open(corpus_dir / f"corpus_{key}.json.gz", "rt", encoding="utf-8") as f:
            corpus = json.load(f)
        manifest = load_or_create_split(key, corpus, corpus_dir, holdout_refresh)
        manifest["pending_ids"] = sorted(set(manifest.get("pending_ids", [])) | ids)
        _atomic_write_json(split_manifest_path(key, corpus_dir), manifest)

    results: Dict[str, Any] = {}
    all_bets: List[Dict[str, Any]] = []
    promoted_metrics: Dict[str, Any] = {}
    for spec_key in updated:
        try:
            res = retrain_specialist_model(
                spec_key, timesteps=timesteps, corpus_dir=corpus_dir, models_dir=models_dir,
                dry_run=dry_run,
            )
        except Exception as e:
            logger.error("retrain_failed", specialist=spec_key, error=str(e))
            res = {"key": spec_key, "promoted": False, "reasons": [f"error:{e}"], "bets": []}
        if res.get("promoted"):
            all_bets.extend(res.get("bets", []))
            promoted_metrics[spec_key] = res["candidate_metrics"]
        results[spec_key] = {k: v for k, v in res.items() if k != "bets"}

    status_record["results"] = results
    if not dry_run:
        try:
            if all_bets:
                await persist_virtual_bets(all_bets)
            if promoted_metrics:
                await update_redis_model_zoo_state(promoted_metrics)
        except Exception as e:
            logger.error("post_promotion_sync_failed", error=str(e))
            status_record["post_promotion_sync"] = "failed"
    _atomic_write_json(status_file, status_record)

    n_prom = sum(1 for r in results.values() if r.get("promoted"))
    print("\n" + "=" * 80)
    print(f" Weekly Sync Complete! {new_count} new matches, {len(updated)} candidates trained, {n_prom} promoted.")
    print("=" * 80)
    return status_record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly Cricsheet Sync & Retraining Pipeline")
    parser.add_argument("--url", type=str, default=DEFAULT_RECENT_URL, help="Cricsheet recent matches zip URL")
    parser.add_argument("--local-zip", type=Path, default=None, help="Path to local zip archive for testing")
    parser.add_argument("--timesteps", type=int, default=None,
                        help="Fine-tune steps (default max(50000, 20 x ticks in the training mix))")
    parser.add_argument("--force", action="store_true", help="Force retrain even if 0 new matches")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except promotion and DB writes")
    parser.add_argument("--holdout-refresh", type=int, default=0,
                        help="Append the newest N matches as a new holdout generation")
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    parser.add_argument("--models-dir", type=Path, default=SPECIALISTS_DIR)
    parser.add_argument("--incoming-dir", type=Path, default=INCOMING_DIR)
    args = parser.parse_args()

    asyncio.run(
        run_weekly_sync_and_retrain(
            url=args.url,
            local_zip=args.local_zip,
            timesteps=args.timesteps,
            force_retrain=args.force,
            dry_run=args.dry_run,
            holdout_refresh=args.holdout_refresh,
            corpus_dir=args.corpus_dir,
            models_dir=args.models_dir,
            incoming_dir=args.incoming_dir,
        )
    )
