"""
Feature cache consumer: subscribes to match_events and populates the feature cache.
Enables advisor fast path (orchestrator._generate_signal) without DB fetch.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import numpy as np

from features.context_resolver import get_match_context
from features.pipeline import FeaturePipeline
from features.store import FeatureStore
from shared.constants import CHANNEL_MATCH_EVENTS
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import OddsEvent, PortfolioState

logger = setup_logging("feature_cache_consumer")


def _default_portfolio(initial_balance: float = 100_000.0) -> PortfolioState:
    """Neutral portfolio for cache population (no positions)."""
    return PortfolioState(
        initial_balance=initial_balance,
        current_balance=initial_balance,
        open_positions=0,
        total_exposure=0.0,
        session_pnl=0.0,
        daily_pnl=0.0,
        total_bets=0,
        total_wins=0,
        consecutive_streak=0,
        time_since_last_bet=0.0,
    )




class FeatureCacheConsumer:
    """
    Subscribes to match_events, computes features, and stores in Redis.
    Runs for orchestrator lifetime; enables advisor fast path in all states.
    """

    def __init__(self, initial_balance: float = 100_000.0) -> None:
        self._initial_balance = initial_balance
        self._pipeline = FeaturePipeline()
        self._store = FeatureStore()
        self._running = False
        self._pubsub: Optional[Any] = None

    async def start(self) -> None:
        """Subscribe and process match_events."""
        self._running = True
        logger.info("feature_cache_consumer_started")

        redis = await get_redis()
        self._pubsub = await redis.subscribe(CHANNEL_MATCH_EVENTS)

        try:
            async for raw_message in self._pubsub.listen():
                if not self._running:
                    break
                if raw_message.get("type") != "message":
                    continue

                data = raw_message.get("data")
                if not data:
                    continue

                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                try:
                    await self._handle_event(data)
                except Exception as e:
                    logger.debug("feature_cache_error", error=str(e))

        except asyncio.CancelledError:
            pass
        finally:
            logger.info("feature_cache_consumer_stopped")

    async def stop(self) -> None:
        """Stop the consumer."""
        self._running = False
        if self._pubsub:
            await self._pubsub.close()

    async def _handle_event(self, data: dict[str, Any]) -> None:
        """Compute features and store in cache."""
        event = OddsEvent.from_redis_data(data)
        if event is None:
            return

        context = await get_match_context(event.match_id)
        portfolio = _default_portfolio(self._initial_balance)
        obs = self._pipeline.compute(
            match_id=event.match_id,
            event=event,
            context=context,
            portfolio=portfolio,
        )

        await self._store.store(
            event.match_id,
            np.asarray(obs, dtype=np.float32),
        )
