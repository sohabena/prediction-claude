"""Training metrics, progress, and data quality endpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from features.data_quality import EpisodeQualityGate
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

        # Load recent approved match episodes
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT DISTINCT match_id
                    FROM odds_ticks
                    ORDER BY match_id DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            match_ids = [row[0] for row in result.fetchall()]

        # For each match, load ticks and evaluate quality
        for mid in match_ids:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT time, match_id, back_home, lay_home,
                               back_away, lay_away, back_draw, lay_draw,
                               implied_prob_home, implied_prob_away,
                               overround, is_live
                        FROM odds_ticks
                        WHERE match_id = :mid
                        ORDER BY time ASC
                    """),
                    {"mid": mid},
                )
                rows = result.fetchall()
                if not rows:
                    continue

                episode = [
                    {
                        "timestamp": r[0],
                        "match_id": r[1],
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
                    for r in rows
                ]

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
