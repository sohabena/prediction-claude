"""
Feature pipeline: computes the 74-dim observation vector from raw data.
Orchestrates all 8 feature extractors and applies normalization.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from features.data_quality import validate_observation
from features.extractors.category_features import (
    MatchCategoryClassifier,
    compute_category_features,
)
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
    Output: np.ndarray of shape (74,)

    Feature groups (74 total, all data-backed):
    1. Raw odds (12) -- prices, implied probs, overround, spreads
    2. Odds momentum (16) -- velocity, acceleration, volatility
    3. Market microstructure (8) -- spread dynamics, efficiency, staleness
    4. Raw match statistics (8) -- is_live, overs, wickets, score, run_rate
    5. Temporal (6) -- cyclical time encoding
    6. Portfolio state (8) -- bankroll, exposure, streak, win_rate
    7. Statistical patterns (8) -- z-scores, trend strength, autocorrelation
    8. Match category (8) -- format, tier, gender (one-hot encoded)
    """

    # Maximum gap between ticks before resetting momentum accumulators
    GAP_THRESHOLD_SECONDS = 30.0

    def __init__(self, lookback_seconds: int = 120) -> None:
        self.lookback_seconds = lookback_seconds
        self.normalizer = OnlineNormalizer(OBSERVATION_SIZE)
        self.store = FeatureStore()
        self.category_classifier = MatchCategoryClassifier()

        # In-memory history buffer per match
        self._history: dict[str, list[OddsEvent]] = defaultdict(list)
        self._match_start_times: dict[str, datetime] = {}
        self._last_tick_times: dict[str, datetime] = {}
        self._gap_detected: dict[str, bool] = {}  # Per-match gap flag
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
        Compute the full 74-feature observation vector.

        Args:
            match_id: Unique match identifier.
            event: Latest odds event.
            context: Optional match context from LotusBook.
            portfolio: Optional portfolio state.

        Returns:
            Normalized float32 array of shape (74,).
        """
        # Add event to history
        self.add_event(event)

        # Get recent history within lookback window
        history = self._get_recent_history(match_id)

        # Get timing info and detect gaps
        match_start = self._match_start_times.get(match_id)
        last_tick = self._last_tick_times.get(match_id)
        self._last_tick_times[match_id] = event.timestamp

        # Gap detection: if time since last tick exceeds threshold,
        # flag to reset momentum/velocity accumulators
        gap_detected = False
        if last_tick is not None:
            gap_seconds = (event.timestamp - last_tick).total_seconds()
            if gap_seconds > self.GAP_THRESHOLD_SECONDS:
                gap_detected = True
                logger.debug(
                    "tick_gap_detected",
                    match_id=match_id,
                    gap_seconds=round(gap_seconds, 1),
                )
        self._gap_detected[match_id] = gap_detected

        # If gap detected, clear history to reset momentum accumulators
        # (keep only the latest event to restart fresh)
        if gap_detected:
            history = [event]

        # Compute all feature groups
        features: list[float] = []

        # Group 1: Raw Odds (12)
        odds_feats = compute_odds_features(event)
        features.extend(odds_feats)

        # Group 2: Odds Momentum (16) -- zeroed if gap detected
        momentum_feats = compute_momentum_features(history)
        if gap_detected:
            momentum_feats = [0.0] * len(momentum_feats)
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

        # Group 8: Match Category (8)
        category = self.category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )
        category_feats = compute_category_features(category)
        features.extend(category_feats)

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

        # Validate the final observation before handing to the agent
        is_valid, reason = validate_observation(obs)
        if not is_valid:
            logger.warning(
                "invalid_observation",
                match_id=match_id,
                reason=reason,
            )
            # Return a safe zero vector rather than garbage data
            return np.zeros(OBSERVATION_SIZE, dtype=np.float32)

        # Ensure float32 to match Gymnasium observation space dtype
        return obs.astype(np.float32)

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
        """Load normalizer state from file, handling dimension changes."""
        self.normalizer = OnlineNormalizer.load(path, expected_size=OBSERVATION_SIZE)
