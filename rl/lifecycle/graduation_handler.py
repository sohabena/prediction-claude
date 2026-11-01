"""Graduation check handler — evaluates if agent meets graduation criteria.

Extracted from orchestrator._run_graduation_check (RGV #3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

from rl.graduation import GraduationEvaluator
from shared.config import get_settings
from shared.constants import KEY_GRADUATION_STATUS
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("lifecycle_graduation")


class GraduationHandler:
    """Evaluates graduation criteria from virtual trading performance."""

    def __init__(self, evaluator: GraduationEvaluator) -> None:
        self._graduation = evaluator

    async def check(self) -> bool:
        """Run daily graduation check. Returns True if agent graduated."""
        logger.info("graduation_check_starting")
        settings = get_settings()

        try:
            async with get_session() as session:
                from sqlalchemy import text

                result = await session.execute(
                    text("""
                        WITH daily AS (
                            SELECT DATE(placed_at) as day, SUM(profit_loss) as day_pnl
                            FROM virtual_bets
                            WHERE placed_at > NOW() - INTERVAL '30 days'
                              AND settled_at IS NOT NULL
                            GROUP BY DATE(placed_at)
                        )
                        SELECT
                            COUNT(*) FILTER (WHERE outcome = 'win') * 1.0 /
                                NULLIF(COUNT(*), 0) as win_rate,
                            COALESCE(SUM(profit_loss), 0) /
                                NULLIF(SUM(stake), 0) as roi,
                            COUNT(*) as total_bets,
                            (SELECT COUNT(*) FROM daily WHERE day_pnl > 0) as profitable_days,
                            AVG(clv) FILTER (WHERE clv IS NOT NULL) as avg_clv
                        FROM virtual_bets
                        WHERE placed_at > NOW() - INTERVAL '30 days'
                          AND settled_at IS NOT NULL
                    """)
                )
                row = result.fetchone()

                if not row or row[2] is None:
                    return False

                win_rate = float(row[0] or 0)
                roi = float(row[1] or 0)
                total_bets = int(row[2] or 0)
                profitable_days = int(row[3] or 0)
                avg_clv = float(row[4] or 0)

                # Compute Sharpe ratio from daily returns
                daily_result = await session.execute(
                    text("""
                        SELECT DATE(placed_at) as day, SUM(profit_loss) as daily_pnl
                        FROM virtual_bets
                        WHERE placed_at > NOW() - INTERVAL '30 days'
                          AND settled_at IS NOT NULL
                        GROUP BY DATE(placed_at)
                        ORDER BY day
                    """)
                )
                daily_rows = daily_result.fetchall()
                initial_bankroll = float(settings.rl.starting_bankroll)
                daily_returns = [
                    float(r[1]) / initial_bankroll
                    for r in daily_rows if r[1] is not None
                ]

                if len(daily_returns) >= 5:
                    mean_ret = np.mean(daily_returns)
                    std_ret = np.std(daily_returns)
                    sharpe = float(
                        mean_ret / std_ret * np.sqrt(252)
                    ) if std_ret > 0 else 0.0
                else:
                    sharpe = 0.0

                # Max drawdown
                cumulative = np.cumsum(daily_returns) if daily_returns else np.array([0.0])
                running_max = np.maximum.accumulate(cumulative)
                drawdowns = running_max - cumulative
                max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

                status = self._graduation.evaluate(
                    win_rate=win_rate,
                    roi=roi,
                    sharpe_ratio=sharpe,
                    max_drawdown=max_drawdown,
                    profitable_days=profitable_days,
                    total_bets=total_bets,
                    avg_clv=avg_clv,
                )

                # Store graduation status in Redis
                redis = await get_redis()
                await redis.set_json(KEY_GRADUATION_STATUS, {
                    "ready": status.ready,
                    "consecutive_days": status.consecutive_days,
                    "required_days": status.required_days,
                    "progress_pct": self._graduation.get_progress_pct(),
                    "criteria": [c.model_dump() for c in status.criteria],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                })

                if status.ready:
                    logger.info(
                        "AGENT_GRADUATED",
                        consecutive_days=status.consecutive_days,
                    )
                    return True

        except Exception as e:
            logger.error("graduation_check_failed", error=str(e))

        return False
