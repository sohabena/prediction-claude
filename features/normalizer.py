"""
Online normalization for the feature vector.
Running mean/std normalization updated incrementally -- no look-ahead bias.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class OnlineNormalizer:
    """
    Running mean/std normalization updated incrementally.

    Critical: Never use future data to normalize past observations.
    Uses Welford's algorithm for numerical stability.
    """

    def __init__(self, size: int) -> None:
        self.size = size
        self.mean = np.zeros(size, dtype=np.float64)
        self.var = np.ones(size, dtype=np.float64)
        self.count = 0
        self._min_count = 10  # Don't normalize until we have enough data

    def update(self, x: np.ndarray) -> None:
        """Update running statistics with a new observation."""
        if x.shape != (self.size,):
            raise ValueError(f"Expected shape ({self.size},), got {x.shape}")

        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.var += (delta * delta2 - self.var) / self.count

    def transform(self, x: np.ndarray) -> np.ndarray:
        """Normalize observation using current running statistics."""
        if self.count < self._min_count:
            return x.astype(np.float32)

        result = (x - self.mean) / (np.sqrt(np.maximum(self.var, 1e-8)) + 1e-8)

        # Clip extreme values to prevent NaN propagation
        result = np.clip(result, -10.0, 10.0)

        return result.astype(np.float32)

    def update_and_transform(self, x: np.ndarray) -> np.ndarray:
        """Update statistics and return normalized observation."""
        self.update(x)
        return self.transform(x)

    def save(self, path: str | Path) -> None:
        """Save normalizer state to JSON file."""
        state = {
            "size": self.size,
            "mean": self.mean.tolist(),
            "var": self.var.tolist(),
            "count": self.count,
        }
        with open(path, "w") as f:
            json.dump(state, f)

    @classmethod
    def load(cls, path: str | Path, expected_size: int | None = None) -> "OnlineNormalizer":
        """
        Load normalizer state from JSON file.

        If ``expected_size`` is provided and differs from the saved size,
        returns a fresh normalizer with the expected size (dimension
        mismatch due to feature vector change).
        """
        with open(path) as f:
            state = json.load(f)

        saved_size = state["size"]
        if expected_size is not None and saved_size != expected_size:
            import logging
            logging.getLogger("phoenix.normalizer").warning(
                "Normalizer dimension mismatch: saved=%d, expected=%d. "
                "Re-initializing fresh normalizer.",
                saved_size,
                expected_size,
            )
            return cls(expected_size)

        normalizer = cls(saved_size)
        normalizer.mean = np.array(state["mean"], dtype=np.float64)
        normalizer.var = np.array(state["var"], dtype=np.float64)
        normalizer.count = state["count"]
        return normalizer
