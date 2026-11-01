"""Data access layer for training metrics and data quality."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from shared.db import get_session


class TrainingRepository:
    """Repository for training metrics and data quality queries."""

    @staticmethod
    async def get_training_metrics(limit: int) -> list[dict[str, Any]]:
        """Fetch recent training metrics."""
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
            return [
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
                for row in result.fetchall()
            ]

    @staticmethod
    async def get_training_summary() -> dict[str, Any]:
        """Fetch aggregate training summary from DB."""
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
        return {
            "total_timesteps": 0,
            "total_episodes": 0,
            "recent_win_rate": 0.0,
            "recent_roi": 0.0,
            "recent_sharpe": 0.0,
            "latest_version": "none",
        }

    @staticmethod
    async def get_recent_match_ticks(limit: int) -> list[Any]:
        """Fetch all ticks for recent matches (for data quality evaluation)."""
        async with get_session() as session:
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
            return result.fetchall()

    @staticmethod
    async def get_graduation_history(limit: int) -> list[dict[str, Any]]:
        """Fetch graduation snapshot history."""
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT time, win_rate, roi, sharpe_ratio, max_drawdown,
                           profitable_days, total_bets, all_criteria_met,
                           consecutive_days
                    FROM graduation_snapshots
                    ORDER BY time DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            return [
                {
                    "time": row[0].isoformat() if row[0] else None,
                    "win_rate": row[1],
                    "roi": row[2],
                    "sharpe_ratio": row[3],
                    "max_drawdown": row[4],
                    "profitable_days": row[5],
                    "total_bets": row[6],
                    "all_criteria_met": row[7],
                    "consecutive_days": row[8],
                }
                for row in result.fetchall()
            ]
