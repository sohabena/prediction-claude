"""
Feature pipeline: computes the 50-dim observation vector from raw data.

Expert cricket-trading observation space — every feature is something a
professional bettor actually looks at on their screen. No noise, no redundancy.

Groups (50 total):
  1. Core Odds (7)       — prices, margin, spreads
  2. Momentum (6)        — velocity, volatility, trend (zeroed on gap)
  3. Market Quality (5)  — spread dynamics, efficiency, staleness
  4. Match State (7)     — live status, overs, wickets, score, rates
  5. Portfolio (5)       — bankroll, exposure, positions, win rate
  6. Position (4)        — net exposure, hedge potential
  7. Volume (4)          — liquidity depth, imbalance
  8. Bookmaker (4)       — pricing patterns the agent learns to read
  9. Format (4)          — T20i / ODI / Test / Franchise one-hot
  10. Timing (2)         — match elapsed %, tick freshness
  11. Match Momentum (2) — recent score & wicket acceleration
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from features.data_quality import validate_observation
from features.extractors.bookmaker_pattern_features import compute_bookmaker_pattern_features
from features.extractors.category_features import (
    MatchCategoryClassifier,
    compute_category_features,
)
from features.extractors.market_features import compute_market_features
from features.extractors.match_stats_features import compute_match_stats_features
from features.extractors.momentum_features import compute_momentum_features
from features.extractors.odds_features import compute_odds_features
from features.extractors.portfolio_features import compute_portfolio_features
from features.extractors.position_features import compute_position_features
from features.extractors.volume_features import compute_volume_features
from features.normalizer import OnlineNormalizer
from features.store import FeatureStore
from shared.constants import OBSERVATION_SIZE
from shared.logging import setup_logging
from shared.schemas import MatchContext, OddsEvent, PortfolioState

logger = setup_logging("feature_pipeline")


class FeaturePipeline:
    """
    Computes RL observation vector from raw data. DATA-ONLY approach.

    Input: match_id + latest OddsEvent + MatchContext + PortfolioState + position_state
    Output: np.ndarray of shape (50,)

    Every feature maps to something a professional cricket bettor
    actually monitors. No cyclical time encoding, no z-scores,
    no autocorrelation — just what matters for trading.
    """

    # Default gap threshold (seconds) before resetting momentum accumulators.
    # Must exceed the largest training inter-tick gap (synthetic: 45-90 s).
    GAP_THRESHOLD_SECONDS = 600.0

    # Names of the 50 features, in vector order (groups documented above).
    _FEATURE_NAMES: tuple[str, ...] = (
        "back_home", "lay_home", "back_away", "lay_away", "overround",
        "spread_home", "spread_away",
        "velocity_home", "velocity_away", "volatility_home", "volatility_away",
        "ema_crossover_home", "ema_crossover_away",
        "spread_velocity", "market_efficiency", "relative_price_level",
        "time_since_change_home", "time_since_change_away",
        "is_live", "overs_normalized", "wickets_normalized", "score_normalized",
        "run_rate_normalized", "req_run_rate_normalized", "innings",
        "bankroll_pct", "exposure_pct", "open_positions_norm", "win_rate", "streak_norm",
        "net_home_exposure_pct", "net_away_exposure_pct", "can_hedge_profit",
        "hedge_profit_pct",
        "volume_home_norm", "volume_away_norm", "volume_imbalance", "volume_velocity",
        "overround_trend", "odds_move_symmetry", "pricing_intensity",
        "odds_context_coupling",
        "is_t20i", "is_odi", "is_test", "is_franchise",
        "match_elapsed_pct", "tick_freshness",
        "runs_momentum", "wickets_momentum",
    )

    def __init__(
        self,
        lookback_seconds: int = 120,
        *,
        gap_threshold_seconds: float = GAP_THRESHOLD_SECONDS,
        normalize: bool = False,
        min_history_ticks: int = 5,
    ) -> None:
        """
        Args:
            lookback_seconds: Time window for history features.
            gap_threshold_seconds: Gap that resets history; must exceed the
                largest training inter-tick gap.
            normalize: Apply the inner OnlineNormalizer. Default False because
                VecNormalize is the single source of observation scaling.
            min_history_ticks: History features always see at least this many
                recent events, so a 45-90 s cadence and a 3 s cadence get a
                comparable number of history points (tick-count AND time based).
        """
        self.lookback_seconds = lookback_seconds
        self.gap_threshold_seconds = gap_threshold_seconds
        self.normalize = normalize
        self.min_history_ticks = min_history_ticks
        self.normalizer = OnlineNormalizer(OBSERVATION_SIZE)
        self.store = FeatureStore()
        self.category_classifier = MatchCategoryClassifier()

        # In-memory history buffer per match
        self._history: dict[str, list[OddsEvent]] = defaultdict(list)
        self._context_history: dict[str, list[MatchContext]] = defaultdict(list)
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
        position_state: Optional[dict] = None,
    ) -> np.ndarray:
        """
        Compute the full 50-feature observation vector.

        Args:
            match_id: Unique match identifier.
            event: Latest odds event.
            context: Optional match context from LotusBook.
            portfolio: Optional portfolio state.
            position_state: Optional dict with net exposure per team for hedging.

        Returns:
            Raw (unless normalize=True) float32 array of shape (50,).
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
            if gap_seconds > self.gap_threshold_seconds:
                gap_detected = True
                logger.debug(
                    "tick_gap_detected",
                    match_id=match_id,
                    gap_seconds=round(gap_seconds, 1),
                )
        self._gap_detected[match_id] = gap_detected

        # Add context to history
        if context is not None:
            self._context_history[match_id].append(context)
            if len(self._context_history[match_id]) > self._max_history:
                self._context_history[match_id] = self._context_history[match_id][-self._max_history:]
        
        ctx_history = self._context_history[match_id]

        # If gap detected, clear context history too
        if gap_detected:
            history = [event]
            if context is not None:
                self._context_history[match_id] = [context]
                ctx_history = [context]

        # ── Build the 50-feature vector ─────────────────────────

        features: list[float] = []

        # Group 1: Core Odds (7)
        features.extend(compute_odds_features(event))

        # Group 2: Momentum (6) — zeroed if gap detected
        momentum_feats = compute_momentum_features(history)
        if gap_detected:
            momentum_feats = [0.0] * len(momentum_feats)
        features.extend(momentum_feats)

        # Group 3: Market Quality (5)
        features.extend(compute_market_features(event, history))

        # Group 4: Match State (7)
        features.extend(compute_match_stats_features(context))

        # Group 5: Portfolio (5)
        features.extend(compute_portfolio_features(portfolio))

        # Group 6: Position / Hedge Awareness (4)
        features.extend(compute_position_features(position_state))

        # Group 7: Volume / Liquidity (4)
        features.extend(compute_volume_features(event, history))

        # Group 8: Bookmaker Behavior (4)
        features.extend(compute_bookmaker_pattern_features(event, history, context))

        # Group 9: Match Format (4) — T20i / ODI / Test / Franchise
        category = self.category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )
        features.extend(compute_category_features(category))

        # Group 10: Timing (2) — match elapsed %, tick freshness
        features.extend(self._compute_timing(event, match_start, last_tick))

        # Group 11: Match Momentum (2) — score/wicket changes in recent window
        from features.extractors.match_momentum_features import compute_match_momentum_features
        features.extend(compute_match_momentum_features(ctx_history))

        # ── Post-processing ─────────────────────────────────────

        obs = np.array(features, dtype=np.float64)

        assert obs.shape == (OBSERVATION_SIZE,), (
            f"Feature vector has {obs.shape[0]} features, expected {OBSERVATION_SIZE}"
        )

        obs = np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)
        if self.normalize:
            obs = self.normalizer.update_and_transform(obs)
        else:
            obs = np.clip(obs, -1e6, 1e6)

        is_valid, reason = validate_observation(obs)
        if not is_valid:
            logger.warning(
                "invalid_observation",
                match_id=match_id,
                reason=reason,
            )
            return np.zeros(OBSERVATION_SIZE, dtype=np.float32)

        return obs.astype(np.float32)

    @staticmethod
    def _compute_timing(
        event: OddsEvent,
        match_start: Optional[datetime],
        last_tick: Optional[datetime],
    ) -> list[float]:
        """
        Compute 2 timing features inline (no separate extractor needed).

        Features:
            0: match_elapsed_pct  — fraction of typical match duration elapsed (cap 1.0)
            1: tick_freshness     — seconds since last tick, normalized (cap 1.0)
        """
        # Match elapsed as fraction of ~3.5 hours (typical T20+ODI average)
        match_elapsed = 0.0
        if match_start is not None:
            elapsed_sec = (event.timestamp - match_start).total_seconds()
            match_elapsed = min(elapsed_sec / 12600.0, 1.0)  # 3.5h = 12600s

        # Tick freshness (seconds since last tick, normalized by 60s)
        tick_fresh = 0.0
        if last_tick is not None:
            tick_fresh = min(
                (event.timestamp - last_tick).total_seconds() / 60.0, 1.0
            )

        return [match_elapsed, tick_fresh]

    def _get_recent_history(self, match_id: str) -> list[OddsEvent]:
        """Get events within the lookback window."""
        history = self._history.get(match_id, [])
        if not history:
            return []

        cutoff = history[-1].timestamp.timestamp() - self.lookback_seconds
        in_window = [e for e in history if e.timestamp.timestamp() >= cutoff]
        n = max(len(in_window), self.min_history_ticks)
        return history[-n:]

    @classmethod
    def feature_names(cls) -> list[str]:
        """Return the 50 feature names in observation-vector order."""
        return list(cls._FEATURE_NAMES)

    def clear_match(self, match_id: str) -> None:
        """Clear history for a completed match."""
        self._history.pop(match_id, None)
        self._match_start_times.pop(match_id, None)
        self._last_tick_times.pop(match_id, None)
        self._context_history.pop(match_id, None)
        self._gap_detected.pop(match_id, None)

    def save_normalizer(self, path: str) -> None:
        """Save normalizer state for persistence."""
        self.normalizer.save(path)

    def load_normalizer(self, path: str) -> None:
        """Load normalizer state from file, handling dimension changes."""
        self.normalizer = OnlineNormalizer.load(path, expected_size=OBSERVATION_SIZE)
