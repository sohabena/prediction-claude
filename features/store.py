"""
Feature store: Redis-backed caching of computed feature vectors.
Allows quick retrieval for RL agent without recomputation.
"""

from __future__ import annotations

import json
from typing import Optional

import numpy as np

from shared.constants import KEY_FEATURE_CACHE
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("feature_store")


class FeatureStore:
    """
    Cache computed feature vectors in Redis.

    Each match has its latest feature vector cached for quick access.
    """

    def __init__(self, ttl: int = 30) -> None:
        self.ttl = ttl  # Cache TTL in seconds

    async def store(self, match_id: str, features: np.ndarray) -> None:
        """Store feature vector for a match."""
        redis = await get_redis()
        key = KEY_FEATURE_CACHE.format(match_id=match_id)
        data = {
            "features": features.tolist(),
            "shape": list(features.shape),
            "dtype": str(features.dtype),
        }
        await redis.set_json(key, data, ttl=self.ttl)

    async def get(self, match_id: str) -> Optional[np.ndarray]:
        """Retrieve cached feature vector for a match."""
        redis = await get_redis()
        key = KEY_FEATURE_CACHE.format(match_id=match_id)
        data = await redis.get_json(key)
        if data is None:
            return None

        return np.array(data["features"], dtype=np.float32)

    async def has(self, match_id: str) -> bool:
        """Check if a feature vector exists in cache."""
        redis = await get_redis()
        key = KEY_FEATURE_CACHE.format(match_id=match_id)
        result = await redis.client.exists(key)
        return bool(result)
