"""Business logic service for training metrics and data quality."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.repositories.training_repository import TrainingRepository
from features.data_quality import EpisodeQualityGate
from shared.logging import setup_logging

logger = setup_logging("service_training")
repo = TrainingRepository()


def _progress_paths() -> list[Path]:
    roots: list[Path] = []
    if env_root := os.environ.get("PHOENIX_PROJECT_ROOT"):
        roots.append(Path(env_root))
    roots.append(Path(__file__).resolve().parent.parent.parent)
    roots.append(Path.cwd())
    return [r / "logs" / "training_progress.json" for r in roots]


def _find_progress_file() -> Path | None:
    for p in _progress_paths():
        if p.exists():
            return p
    return None


class TrainingService:
    """Business logic for training metrics and data quality."""

    @staticmethod
    async def get_training_progress() -> dict[str, Any]:
        try:
            path = _find_progress_file()
            if path:
                return json.loads(path.read_text())
        except Exception as e:
            logger.warning("training_progress_read_error", error=str(e))
        return {"current_step": 0, "total_steps": 0, "pct_complete": 0, "status": "idle", "updated_at": None}

    @staticmethod
    async def get_training_metrics(limit: int) -> dict[str, Any]:
        metrics = await repo.get_training_metrics(limit)
        return {"count": len(metrics), "metrics": metrics}

    @staticmethod
    async def get_training_summary() -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            path = _find_progress_file()
            result["progress"] = json.loads(path.read_text()) if path else None
        except Exception as e:
            logger.warning("training_progress_read_error", error=str(e))
            result["progress"] = None
        try:
            result.update(await repo.get_training_summary())
        except Exception as e:
            logger.error("training_summary_error", error=str(e))
        result.setdefault("total_timesteps", 0)
        result.setdefault("total_episodes", 0)
        result.setdefault("recent_win_rate", 0.0)
        result.setdefault("recent_roi", 0.0)
        result.setdefault("recent_sharpe", 0.0)
        result.setdefault("latest_version", "none")

        if not result.get("latest_version") or result["latest_version"] == "none":
            try:
                from shared.constants import KEY_AGENT_VERSION
                from shared.redis_client import get_redis
                redis = await get_redis()
                v_data = await redis.get_json(KEY_AGENT_VERSION)
                if isinstance(v_data, dict) and "version" in v_data:
                    result["latest_version"] = f"v{v_data['version']} (twoway_v3)"
            except Exception:
                pass
        return result

    @staticmethod
    async def get_data_quality(limit: int) -> dict[str, Any]:
        gate = EpisodeQualityGate()
        reports: list[dict[str, Any]] = []
        rows = await repo.get_recent_match_ticks(limit)
        episodes_by_match: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            mid = r[1]
            tick = {
                "timestamp": r[0], "match_id": mid,
                "back_home": r[2], "lay_home": r[3],
                "back_away": r[4], "lay_away": r[5],
                "back_draw": r[6], "lay_draw": r[7],
                "implied_prob_home": r[8], "implied_prob_away": r[9],
                "overround": r[10], "is_live": r[11],
            }
            episodes_by_match.setdefault(mid, []).append(tick)
        for episode in episodes_by_match.values():
            if episode:
                reports.append(asdict(gate.evaluate(episode)))
        total = len(reports)
        passed = sum(1 for r in reports if r["passed"])
        avg_score = sum(r["quality_score"] for r in reports) / total if total else 0.0
        return {
            "total_episodes": total, "passed": passed,
            "failed": total - passed, "avg_quality_score": round(avg_score, 4),
            "reports": reports,
        }

    @staticmethod
    async def get_graduation_history(limit: int) -> dict[str, Any]:
        snapshots = await repo.get_graduation_history(limit)
        return {"count": len(snapshots), "snapshots": snapshots}
