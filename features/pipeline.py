"""
Feature pipeline: computes the 66-dim observation vector from raw data.
Orchestrates all 7 feature extractors and applies normalization.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from features.extractors.market_features import compute_market_features
from features.extractors.match_stats_features import compute_match_stats_features
from features.extractors.momentum_features import compute_momentum_features
from features.extractors.odds_features import compute_odds_features
from features.extractors.portfolio_features import compute_portfolio_features
from features.extractors.statistical_features import compute_statistical_features
from features.extractors.temporal_features import compute_temporal_features
from features.normalizer import OnlineNormalizer
from features.store import FeatureStore
from shared.constants import OBSERVATION_SIZE
from shared.logging import setup_logging
from shared.schemas import MatchContext, OddsEvent, PortfolioState

logger = setup_logging("feature_pipeline")


class FeaturePipeline:
    """
    Computes RL observation vector from raw data. DATA-ONLY approach.

    Input: match_id + latest OddsEvent + MatchContext + PortfolioState
    Output: np.ndarray of shape (66,)

    Feature groups (66 total, all data-backed):
    1. Raw odds (12) -- prices, implied probs, overround, spreads
    2. Odds momentum (16) -- velocity, acceleration, volatility
    3. Market microstructure (8) -- spread dynamics, efficiency, staleness
    4. Raw match statistics (8) -- is_live, overs, wickets, score, run_rate
    5. Temporal (6) -- cyclical time encoding
    6. Portfolio state (8) -- bankroll, exposure, streak, win_rate
    7. Statistical patterns (8) -- z-scores, trend strength, autocorrelation
    """

    def __init__(self, lookback_seconds: int = 120) -> None:
        self.lookback_seconds = lookback_seconds
        self.normalizer = OnlineNormalizer(OBSERVATION_SIZE)
        self.store = FeatureStore()

        # In-memory history buffer per match
        self._history: dict[str, list[OddsEvent]] = defaultdict(list)
        self._match_start_times: dict[str, datetime] = {}
        self._last_tick_times: dict[str, datetime] = {}
        self._max_history = 500  # Max events to keep per match

    def add_event(self, event: OddsEvent) -> None:
        """Add a new odds event to the history buffer."""
        match_id = event.match_id
        self._history[match_id].append(event)

        # Track match start time
        if match_id not in self._match_start_times:
            self._match_start_times[match_id] = event.timestamp

        # Trim history if too long
        if len(self._history[match_id]) > self._max_history:
            self._history[match_id] = self._history[match_id][-self._max_history:]

    def compute(
        self,
        match_id: str,
        event: OddsEvent,
        context: Optional[MatchContext] = None,
        portfolio: Optional[PortfolioState] = None,
    ) -> np.ndarray:
        """
        Compute the full 66-feature observation vector.

        Args:
            match_id: Unique match identifier.
            event: Latest odds event.
            context: Optional match context from Cricbuzz.
            portfolio: Optional portfolio state.

        Returns:
            Normalized float32 array of shape (66,).
        """
        # Add event to history
        self.add_event(event)

        # Get recent history within lookback window
        history = self._get_recent_history(match_id)

        # Get timing info
        match_start = self._match_start_times.get(match_id)
        last_tick = self._last_tick_times.get(match_id)
        self._last_tick_times[match_id] = event.timestamp

        # Compute all feature groups
        features: list[float] = []

        # Group 1: Raw Odds (12)
        odds_feats = compute_odds_features(event)
        features.extend(odds_feats)

        # Group 2: Odds Momentum (16)
        momentum_feats = compute_momentum_features(history)
        features.extend(momentum_feats)

        # Group 3: Market Microstructure (8)
        market_feats = compute_market_features(event, history)
        features.extend(market_feats)

        # Group 4: Raw Match Statistics (8)
        stats_feats = compute_match_stats_features(context)
        features.extend(stats_feats)

        # Group 5: Temporal (6)
        temporal_feats = compute_temporal_features(
            event.timestamp, match_start, last_tick
        )
        features.extend(temporal_feats)

        # Group 6: Portfolio State (8)
        portfolio_feats = compute_portfolio_features(portfolio)
        features.extend(portfolio_feats)

        # Group 7: Statistical Patterns (8)
        stat_pattern_feats = compute_statistical_features(history)
        features.extend(stat_pattern_feats)

        # Convert to numpy
        obs = np.array(features, dtype=np.float64)

        # Sanity check
        assert obs.shape == (OBSERVATION_SIZE,), (
            f"Feature vector has {obs.shape[0]} features, expected {OBSERVATION_SIZE}"
        )

        # Replace NaN/Inf with 0
        obs = np.nan_to_num(obs, nan=0.0, posinf=10.0, neginf=-10.0)

        # Normalize
        obs = self.normalizer.update_and_transform(obs)

        return obs

    def _get_recent_history(self, match_id: str) -> list[OddsEvent]:
        """Get events within the lookback window."""
        history = self._history.get(match_id, [])
        if not history:
            return []

        cutoff = history[-1].timestamp.timestamp() - self.lookback_seconds
        return [e for e in history if e.timestamp.timestamp() >= cutoff]

    def clear_match(self, match_id: str) -> None:
        """Clear history for a completed match."""
        self._history.pop(match_id, None)
        self._match_start_times.pop(match_id, None)
        self._last_tick_times.pop(match_id, None)

    def save_normalizer(self, path: str) -> None:
        """Save normalizer state for persistence."""
        self.normalizer.save(path)

    def load_normalizer(self, path: str) -> None:
        """Load normalizer state from file."""
        self.normalizer = OnlineNormalizer.load(path)
