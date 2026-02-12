"""Training metrics, progress, and data quality endpoints."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from features.data_quality import EpisodeQualityGate
from shared.db import get_session
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_training")

# Progress file: try PHOENIX_PROJECT_ROOT env, then __file__, then CWD
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


@router.get("/steps")
async def get_training_progress() -> dict[str, Any]:
    """
    Get current offline training progress (steps, % complete).
    Written by TrainingProgressCallback during training.
    """
    try:
        path = _find_progress_file()
        if path:
            return json.loads(path.read_text())
    except Exception as e:
        logger.warning("training_progress_read_error", error=str(e))
    return {
        "current_step": 0,
        "total_steps": 0,
        "pct_complete": 0,
        "status": "idle",
        "updated_at": None,
    }


@router.get("/metrics")
async def get_training_metrics(
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Get recent training metrics."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, episode, total_timesteps, episode_reward,
                           episode_length, win_rate, roi, sharpe_ratio,
                           max_drawdown, policy_loss, value_loss, entropy,
                           agent_version
                    FROM training_metrics
                    ORDER BY time DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            rows = result.fetchall()
            return {
                "count": len(rows),
                "metrics": [
                    {
                        "time": row[0].isoformat() if row[0] else None,
                        "episode": row[1],
                        "total_timesteps": row[2],
                        "episode_reward": row[3],
                        "episode_length": row[4],
                        "win_rate": row[5],
                        "roi": row[6],
                        "sharpe_ratio": row[7],
                        "max_drawdown": row[8],
                        "policy_loss": row[9],
                        "value_loss": row[10],
                        "entropy": row[11],
                        "agent_version": row[12],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("training_metrics_error", error=str(e))
        return {"count": 0, "metrics": [], "error": str(e)}


@router.get("/summary")
async def get_training_summary() -> dict[str, Any]:
    """Get a summary of the latest training state.
    Includes live progress from training_progress.json when available.
    """
    result: dict[str, Any] = {}
    # Progress from file - ensure key exists for frontend
    try:
        path = _find_progress_file()
        if path:
            result["progress"] = json.loads(path.read_text())
        else:
            result["progress"] = None
    except Exception as e:
        logger.warning("training_progress_read_error", error=str(e))
        result["progress"] = None

    try:
        async with get_session() as session:
            db_result = await session.execute(
                text("""
                    SELECT
                        MAX(total_timesteps) as total_steps,
                        MAX(episode) as total_episodes,
                        AVG(win_rate) FILTER (WHERE time > NOW() - INTERVAL '1 hour') as recent_win_rate,
                        AVG(roi) FILTER (WHERE time > NOW() - INTERVAL '1 hour') as recent_roi,
                        AVG(sharpe_ratio) FILTER (WHERE time > NOW() - INTERVAL '1 hour') as recent_sharpe,
                        MAX(agent_version) as latest_version
                    FROM training_metrics
                """)
            )
            row = db_result.fetchone()
            if row:
                result.update({
                    "total_timesteps": row[0] or 0,
                    "total_episodes": row[1] or 0,
                    "recent_win_rate": float(row[2] or 0),
                    "recent_roi": float(row[3] or 0),
                    "recent_sharpe": float(row[4] or 0),
                    "latest_version": row[5] or "none",
                })
                return result
    except Exception as e:
        logger.error("training_summary_error", error=str(e))

    result.setdefault("total_timesteps", 0)
    result.setdefault("total_episodes", 0)
    result.setdefault("recent_win_rate", 0.0)
    result.setdefault("recent_roi", 0.0)
    result.setdefault("recent_sharpe", 0.0)
    result.setdefault("latest_version", "none")
    return result


@router.get("/data-quality")
async def get_data_quality(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Run the episode quality gate on recent approved matches.

    Loads the most recent approved-match episodes from the DB,
    evaluates each through the quality gate, and returns per-episode
    scores plus an aggregate summary.
    """
    try:
        gate = EpisodeQualityGate()
        reports: list[dict[str, Any]] = []

        async with get_session() as session:
            # Single query: get all ticks for the most recent N matches
            result = await session.execute(
                text("""
                    WITH recent_matches AS (
                        SELECT DISTINCT match_id
                        FROM odds_ticks
                        ORDER BY match_id DESC
                        LIMIT :limit
                    )
                    SELECT t.time, t.match_id, t.back_home, t.lay_home,
                           t.back_away, t.lay_away, t.back_draw, t.lay_draw,
                           t.implied_prob_home, t.implied_prob_away,
                           t.overround, t.is_live
                    FROM odds_ticks t
                    INNER JOIN recent_matches rm ON rm.match_id = t.match_id
                    ORDER BY t.match_id, t.time ASC
                """),
                {"limit": limit},
            )
            rows = result.fetchall()

        # Group ticks by match_id in Python
        episodes_by_match: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            mid = r[1]
            tick = {
                "timestamp": r[0],
                "match_id": mid,
                "back_home": r[2],
                "lay_home": r[3],
                "back_away": r[4],
                "lay_away": r[5],
                "back_draw": r[6],
                "lay_draw": r[7],
                "implied_prob_home": r[8],
                "implied_prob_away": r[9],
                "overround": r[10],
                "is_live": r[11],
            }
            episodes_by_match.setdefault(mid, []).append(tick)

        for episode in episodes_by_match.values():
            if episode:
                report = gate.evaluate(episode)
                reports.append(asdict(report))

        # Aggregate summary
        total = len(reports)
        passed = sum(1 for r in reports if r["passed"])
        avg_score = (
            sum(r["quality_score"] for r in reports) / total if total else 0.0
        )

        return {
            "total_episodes": total,
            "passed": passed,
            "failed": total - passed,
            "avg_quality_score": round(avg_score, 4),
            "reports": reports,
        }
    except Exception as e:
        logger.error("data_quality_error", error=str(e))
        return {
            "total_episodes": 0,
            "passed": 0,
            "failed": 0,
            "avg_quality_score": 0.0,
            "reports": [],
            "error": str(e),
        }
