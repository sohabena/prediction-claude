"""Training metrics and progress endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from shared.db import get_session
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_training")


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
    """Get a summary of the latest training state."""
    try:
        async with get_session() as session:
            result = await session.execute(
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
            row = result.fetchone()
            if row:
                return {
                    "total_timesteps": row[0] or 0,
                    "total_episodes": row[1] or 0,
                    "recent_win_rate": float(row[2] or 0),
                    "recent_roi": float(row[3] or 0),
                    "recent_sharpe": float(row[4] or 0),
                    "latest_version": row[5] or "none",
                }
    except Exception as e:
        logger.error("training_summary_error", error=str(e))

    return {
        "total_timesteps": 0,
        "total_episodes": 0,
        "recent_win_rate": 0.0,
        "recent_roi": 0.0,
        "recent_sharpe": 0.0,
        "latest_version": "none",
    }
