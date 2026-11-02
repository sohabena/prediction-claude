"""Graduation check handler — evaluates if agent meets graduation criteria.

Extracted from orchestrator._run_graduation_check (RGV #3).

Metrics are computed from the *current live model's* settled bets only
(``agent_version`` filter, holdout-eval rows excluded) using the canonical
definitions in ``shared.metrics``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backtesting.report import bootstrap_ci
from rl.graduation import GraduationEvaluator
from shared.config import get_settings
from shared.constants import KEY_GRADUATION_STATUS
from shared.db import get_session
from shared.logging import setup_logging
from shared.metrics import daily_series, max_drawdown, sharpe
from shared.redis_client import get_redis

logger = setup_logging("lifecycle_graduation")

WINDOW_DAYS = 30
PROFIT_DAYS_WINDOW = 14

# One row per settled bet of the current model; holdout rows are never included.
GRADUATION_BETS_SQL = """
    SELECT placed_at, match_id, stake, profit_loss, clv
    FROM virtual_bets
    WHERE placed_at > :since
      AND settled_at IS NOT NULL
      AND agent_version = :agent_version
      AND agent_version NOT LIKE 'holdout_eval:%'
    ORDER BY placed_at
"""


def compute_graduation_metrics(
    rows: list[Any],
    starting_bankroll: float,
    now: Optional[datetime] = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """Pure: turn bet rows ``(placed_at, match_id, stake, pnl, clv)`` into evaluator inputs."""
    now = now or datetime.now(timezone.utc)
    end = now.date()
    start = end - timedelta(days=window_days - 1)

    pnl_rows = [(r[0], float(r[3])) for r in rows if r[3] is not None]
    daily = daily_series(pnl_rows, start, end)  # calendar days, zeros included
    turnover = sum(float(r[2] or 0.0) for r in rows)
    total_pnl = sum(p for _, p in pnl_rows)
    clvs = [float(r[4]) for r in rows if r[4] is not None]

    equity = [starting_bankroll]
    for d in daily:
        equity.append(equity[-1] + d)
    _, dd_frac = max_drawdown(equity)

    ci = bootstrap_ci(clvs, n_bootstrap=2000) if clvs else None
    return {
        "roi": total_pnl / turnover if turnover > 0 else 0.0,
        "sharpe_ratio": sharpe(daily),
        "max_drawdown": dd_frac,
        "profitable_days": sum(1 for d in daily[-PROFIT_DAYS_WINDOW:] if d > 0),
        "total_bets": len(rows),
        "avg_clv": sum(clvs) / len(clvs) if clvs else 0.0,
        "clv_ci_lower": ci["ci_lower"] if ci else None,
        "n_matches": len({r[1] for r in rows if r[1] is not None}),
    }


class GraduationHandler:
    """Evaluates graduation criteria from virtual trading performance."""

    def __init__(self, evaluator: GraduationEvaluator) -> None:
        self._graduation = evaluator

    @staticmethod
    def _current_version() -> str:
        try:
            from rl.model_zoo import get_model_zoo

            return get_model_zoo().get_agent_version("default") or ""
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("graduation_version_lookup_failed", error=str(e))
            return ""

    async def check(self, agent_version: Optional[str] = None) -> bool:
        """Run daily graduation check for one model version. Returns True if graduated."""
        logger.info("graduation_check_starting")
        settings = get_settings()
        version = agent_version or self._current_version()
        if not version:
            logger.warning("graduation_check_no_agent_version")
            return False

        try:
            async with get_session() as session:
                from sqlalchemy import text

                since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
                result = await session.execute(
                    text(GRADUATION_BETS_SQL),
                    {"since": since, "agent_version": version},
                )
                rows = result.fetchall()
                if not rows:
                    return False

                metrics = compute_graduation_metrics(
                    rows, float(settings.rl.starting_bankroll)
                )
                status = self._graduation.evaluate(**metrics)

                # Store graduation status in Redis
                redis = await get_redis()
                await redis.set_json(KEY_GRADUATION_STATUS, {
                    "ready": status.ready,
                    "consecutive_days": status.consecutive_days,
                    "required_days": status.required_days,
                    "progress_pct": self._graduation.get_progress_pct(),
                    "criteria": [c.model_dump() for c in status.criteria],
                    "agent_version": version,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                })

                if status.ready:
                    logger.info(
                        "AGENT_GRADUATED",
                        consecutive_days=status.consecutive_days,
                        agent_version=version,
                    )
                    return True

        except Exception as e:
            logger.error("graduation_check_failed", error=str(e))

        return False
