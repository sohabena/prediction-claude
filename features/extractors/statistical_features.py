"""
Group 7: Statistical Pattern Features (8 features)
Pure mathematical analysis of the odds time series. NO human-labelled patterns.
"""

from __future__ import annotations

import numpy as np

from shared.schemas import OddsEvent


def compute_statistical_features(history: list[OddsEvent]) -> list[float]:
    """
    Compute 8 statistical pattern features from odds history.

    Features:
        0: mean_reversion_zscore_home  (how far from rolling mean, in std devs)
        1: mean_reversion_zscore_away
        2: trend_strength_home         (ADX-like directional index, 0-1)
        3: trend_strength_away
        4: volatility_percentile_home  (current vol rank in rolling window, 0-1)
        5: volatility_percentile_away
        6: odds_autocorrelation_home   (serial correlation of price changes, lag-1)
        7: odds_autocorrelation_away

    Args:
        history: Recent OddsEvent list, oldest first.

    Returns:
        8-element list of floats.
    """
    if len(history) < 5:
        return [0.0] * 8

    home_prices = np.array([e.back_home or 0.0 for e in history], dtype=np.float64)
    away_prices = np.array([e.back_away or 0.0 for e in history], dtype=np.float64)

    # Mean reversion z-scores
    zscore_home = _zscore(home_prices)
    zscore_away = _zscore(away_prices)

    # Trend strength (ADX-like)
    trend_home = _trend_strength(home_prices)
    trend_away = _trend_strength(away_prices)

    # Volatility percentile
    vol_pct_home = _volatility_percentile(home_prices)
    vol_pct_away = _volatility_percentile(away_prices)

    # Autocorrelation (lag-1)
    autocorr_home = _autocorrelation(home_prices)
    autocorr_away = _autocorrelation(away_prices)

    return [
        zscore_home, zscore_away,
        trend_home, trend_away,
        vol_pct_home, vol_pct_away,
        autocorr_home, autocorr_away,
    ]


def _zscore(prices: np.ndarray) -> float:
    """Z-score of the latest price relative to the rolling mean."""
    if len(prices) < 2:
        return 0.0
    mean = np.mean(prices)
    std = np.std(prices)
    if std < 1e-8:
        return 0.0
    return float((prices[-1] - mean) / std)


def _trend_strength(prices: np.ndarray) -> float:
    """
    ADX-like trend strength indicator (0.0 to 1.0).

    Measures the proportion of directional moves in the price series.
    """
    if len(prices) < 3:
        return 0.0

    changes = np.diff(prices)
    if len(changes) == 0:
        return 0.0

    # Directional strength: what fraction of moves are in the same direction?
    pos_moves = np.sum(changes > 0)
    neg_moves = np.sum(changes < 0)
    total_moves = len(changes)

    if total_moves == 0:
        return 0.0

    # Strength = abs(net direction) / total moves
    directional = abs(pos_moves - neg_moves) / total_moves
    return float(directional)


def _volatility_percentile(prices: np.ndarray, window: int = 10) -> float:
    """
    Where current volatility sits relative to recent history (0.0 to 1.0).

    Computes rolling volatilities and returns percentile rank of latest.
    """
    if len(prices) < window + 2:
        return 0.5  # Default: middle

    # Compute rolling volatilities
    changes = np.diff(prices)
    vols = []
    for i in range(len(changes) - window + 1):
        vol = np.std(changes[i:i + window])
        vols.append(vol)

    if not vols:
        return 0.5

    current_vol = vols[-1]
    rank = sum(1 for v in vols if v <= current_vol) / len(vols)
    return float(rank)


def _autocorrelation(prices: np.ndarray, lag: int = 1) -> float:
    """Compute lag-1 autocorrelation of price changes."""
    if len(prices) < lag + 3:
        return 0.0

    changes = np.diff(prices)
    if len(changes) < lag + 2:
        return 0.0

    x = changes[:-lag]
    y = changes[lag:]

    if len(x) < 2:
        return 0.0

    x_mean = np.mean(x)
    y_mean = np.mean(y)
    x_std = np.std(x)
    y_std = np.std(y)

    if x_std < 1e-8 or y_std < 1e-8:
        return 0.0

    correlation = np.mean((x - x_mean) * (y - y_mean)) / (x_std * y_std)
    return float(np.clip(correlation, -1.0, 1.0))
