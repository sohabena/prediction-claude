"""
Match context resolver: fetches match context from Redis cache for live path.
Context is populated by LiveMatchTracker (derived from LotusBook score data)
directly from LotusBook score data.
Used by LiveTradingLoop, FeatureCacheConsumer, and orchestrator._generate_signal.
"""

from __future__ import annotations

from typing import Optional

from shared.constants import KEY_MATCH_CONTEXT
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import MatchContext

logger = setup_logging("context_resolver")


async def get_match_context(match_id: str) -> Optional[MatchContext]:
    """
    Fetch latest MatchContext for a match from Redis cache.

    Cache is populated by LiveMatchTracker (from LotusBook score data) inline
    with each scraper tick.
    Returns None if not found (fallback: pipeline uses zeros for match_stats features).
    """
    try:
        redis = await get_redis()
        key = KEY_MATCH_CONTEXT.format(match_id=match_id)
        data = await redis.get_json(key)
        if data is None:
            return None
        return MatchContext.model_validate(data)
    except Exception as e:
        logger.debug("context_resolve_error", match_id=match_id, error=str(e))
        return None
