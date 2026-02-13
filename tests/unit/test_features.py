"""Unit tests for the feature pipeline and extractors."""

from __future__ import annotations

import numpy as np
import pytest

from features.extractors.market_features import compute_market_features
from features.extractors.match_stats_features import compute_match_stats_features
from features.extractors.momentum_features import compute_momentum_features
from features.extractors.odds_features import compute_odds_features
from features.extractors.portfolio_features import compute_portfolio_features
from features.normalizer import OnlineNormalizer
from features.pipeline import FeaturePipeline
from shared.constants import OBSERVATION_SIZE
from shared.schemas import OddsEvent, MatchContext, PortfolioState


class TestOddsFeatures:
    """Tests for Group 1: Raw Odds features."""

    def test_output_size(self, sample_odds_event: OddsEvent) -> None:
        features = compute_odds_features(sample_odds_event)
        assert len(features) == 7

    def test_overround_positive(self, sample_odds_event: OddsEvent) -> None:
        features = compute_odds_features(sample_odds_event)
        overround = features[4]
        assert overround > 0  # Bookmaker margin

    def test_spread_positive(self, sample_odds_event: OddsEvent) -> None:
        features = compute_odds_features(sample_odds_event)
        spread_home = features[5]
        assert spread_home > 0  # lay > back


class TestMomentumFeatures:
    """Tests for Group 2: Odds Momentum features."""

    def test_output_size(self, sample_odds_history: list[OddsEvent]) -> None:
        features = compute_momentum_features(sample_odds_history)
        assert len(features) == 6

    def test_empty_history(self) -> None:
        features = compute_momentum_features([])
        assert len(features) == 6
        assert all(f == 0.0 for f in features)

    def test_no_nan(self, sample_odds_history: list[OddsEvent]) -> None:
        features = compute_momentum_features(sample_odds_history)
        assert all(not np.isnan(f) for f in features)


class TestMarketFeatures:
    """Tests for Group 3: Market Microstructure features."""

    def test_output_size(
        self, sample_odds_event: OddsEvent, sample_odds_history: list[OddsEvent]
    ) -> None:
        features = compute_market_features(sample_odds_event, sample_odds_history)
        assert len(features) == 5


class TestMatchStatsFeatures:
    """Tests for Group 4: Raw Match Statistics features."""

    def test_output_size(self, sample_match_context: MatchContext) -> None:
        features = compute_match_stats_features(sample_match_context)
        assert len(features) == 7

    def test_none_context(self) -> None:
        features = compute_match_stats_features(None)
        assert len(features) == 7
        assert all(f == 0.0 for f in features)

    def test_normalization_ranges(self, sample_match_context: MatchContext) -> None:
        features = compute_match_stats_features(sample_match_context)
        for f in features:
            assert 0.0 <= f <= 1.0


class TestPortfolioFeatures:
    """Tests for Group 5: Portfolio State features."""

    def test_output_size(self, sample_portfolio: PortfolioState) -> None:
        features = compute_portfolio_features(sample_portfolio)
        assert len(features) == 5

    def test_none_portfolio(self) -> None:
        features = compute_portfolio_features(None)
        assert len(features) == 5
        assert features[0] == 1.0  # bankroll_pct default


class TestOnlineNormalizer:
    """Tests for the OnlineNormalizer."""

    def test_initial_passthrough(self) -> None:
        norm = OnlineNormalizer(size=4)
        x = np.array([1.0, 2.0, 3.0, 4.0])
        # Before min_count, should return input as-is
        result = norm.transform(x)
        np.testing.assert_array_almost_equal(result, x)

    def test_normalization_mean_zero(self) -> None:
        norm = OnlineNormalizer(size=2)
        # Feed many identical samples
        for _ in range(100):
            norm.update(np.array([5.0, 10.0]))
        result = norm.transform(np.array([5.0, 10.0]))
        # Should be approximately 0 (at the mean)
        np.testing.assert_array_almost_equal(result, [0.0, 0.0], decimal=1)


class TestFeaturePipeline:
    """Tests for the full feature pipeline."""

    def test_output_shape(
        self,
        sample_odds_event: OddsEvent,
        sample_match_context: MatchContext,
        sample_portfolio: PortfolioState,
    ) -> None:
        pipeline = FeaturePipeline()
        obs = pipeline.compute(
            "test_match",
            sample_odds_event,
            sample_match_context,
            sample_portfolio,
        )
        assert obs.shape == (OBSERVATION_SIZE,)
        assert obs.dtype == np.float32

    def test_no_nan(
        self,
        sample_odds_event: OddsEvent,
        sample_match_context: MatchContext,
    ) -> None:
        pipeline = FeaturePipeline()
        obs = pipeline.compute("test_match", sample_odds_event, sample_match_context)
        assert not np.any(np.isnan(obs))
        assert not np.any(np.isinf(obs))

    def test_output_observation_size(
        self,
        sample_odds_event: OddsEvent,
    ) -> None:
        """Feature pipeline output must match OBSERVATION_SIZE (48)."""
        from shared.constants import OBSERVATION_SIZE

        pipeline = FeaturePipeline()
        obs = pipeline.compute("test_match", sample_odds_event)
        assert obs.shape == (OBSERVATION_SIZE,)
