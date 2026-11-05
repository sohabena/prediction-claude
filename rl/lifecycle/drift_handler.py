"""Drift detection handler — monitors post-graduation performance drift.

Extracted from orchestrator._handle_drift_check (RGV #1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.constants import KEY_AGENT_STATE, KEY_DRIFT_STATUS
from shared.logging import setup_logging
from shared.redis_client import get_redis
from virtual_trading.shadow_trader import ShadowTrader

logger = setup_logging("lifecycle_drift")


class DriftHandler:
    """Checks for performance drift and triggers auto-demotion."""

    def __init__(self, shadow_trader: ShadowTrader) -> None:
        self._shadow_trader = shadow_trader

    async def check(
        self, model_version: int, stats: dict[str, Any],
    ) -> bool:
        """Check for performance drift. Returns True if demotion is needed."""
        drift = self._shadow_trader.check_drift()

        # Publish drift status to Redis for dashboard
        try:
            redis = await get_redis()
            await redis.set_json(KEY_DRIFT_STATUS, drift.to_dict())
        except Exception as e:
            logger.debug("drift_publish_failed", error=str(e))

        if drift.should_demote:
            logger.warning(
                "AUTO_DEMOTION_TRIGGERED",
                consecutive_drift_days=drift.consecutive_drift_days,
                violations=drift.violations,
            )

            redis = await get_redis()
            await redis.set_json(KEY_AGENT_STATE, {
                "mode": "virtual_trading",
                "demoted_at": datetime.now(timezone.utc).isoformat(),
                "reason": "performance_drift",
                "violations": drift.violations,
                "model_version": model_version,
            })

            stats["demotions"] = stats.get("demotions", 0) + 1
            stats["last_demotion"] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": drift.violations,
            }
            return True

        elif drift.is_drifting:
            logger.warning(
                "drift_warning",
                consecutive_days=drift.consecutive_drift_days,
                violations=drift.violations,
            )

        return False
