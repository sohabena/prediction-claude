"""
Group 2: Momentum Features (6 features)
Core price dynamics: velocity, volatility, trend direction.
Trimmed from 16 — removed redundant velocity windows, acceleration, score, swing.
"""

from __future__ import annotations

import numpy as np

from shared.schemas import OddsEvent


def compute_momentum_features(history: list[OddsEvent]) -> list[float]:
    """
    Compute 6 momentum features from recent odds history.

    Features (per team = home/away):
        0-1: velocity_30s     (price change rate, balanced window)
        2-3: volatility_30s   (rolling std dev of odds changes)
        4-5: ema_crossover    (short EMA - long EMA, trend direction)

    Args:
        history: Recent OddsEvent list, oldest first, newest last.

    Returns:
        6-element list of floats.
    """
    if len(history) < 2:
        return [0.0] * 6

    # Extract price series
    home_prices = np.array([e.back_home or 0.0 for e in history], dtype=np.float64)
    away_prices = np.array([e.back_away or 0.0 for e in history], dtype=np.float64)
    timestamps = np.array(
        [e.timestamp.timestamp() for e in history], dtype=np.float64
    )

    vel_30s_home = _velocity(home_prices, timestamps, window_sec=30.0)
    vel_30s_away = _velocity(away_prices, timestamps, window_sec=30.0)

    vol_home = _rolling_volatility(home_prices, timestamps, window_sec=30.0)
    vol_away = _rolling_volatility(away_prices, timestamps, window_sec=30.0)

    ema_cross_home = _ema_crossover(home_prices)
    ema_cross_away = _ema_crossover(away_prices)

    return [vel_30s_home, vel_30s_away, vol_home, vol_away, ema_cross_home, ema_cross_away]


def _velocity(prices: np.ndarray, timestamps: np.ndarray, window_sec: float) -> float:
    """Compute price velocity over a time window."""
    if len(prices) < 2:
        return 0.0

    current_time = timestamps[-1]
    mask = timestamps >= (current_time - window_sec)

    if mask.sum() < 2:
        # Use all available data
        dt = timestamps[-1] - timestamps[0]
        if dt > 0:
            return float((prices[-1] - prices[0]) / dt)
        return 0.0

    window_prices = prices[mask]
    window_times = timestamps[mask]
    dt = window_times[-1] - window_times[0]

    if dt > 0:
        return float((window_prices[-1] - window_prices[0]) / dt)
    return 0.0


def _rolling_volatility(prices: np.ndarray, timestamps: np.ndarray, window_sec: float) -> float:
    """Compute rolling standard deviation of price changes in a time window."""
    if len(prices) < 3:
        return 0.0

    current_time = timestamps[-1]
    mask = timestamps >= (current_time - window_sec)
    window_prices = prices[mask]

    if len(window_prices) < 3:
        window_prices = prices[-min(10, len(prices)):]

    changes = np.diff(window_prices)
    if len(changes) == 0:
        return 0.0

    return float(np.std(changes))


def _ema_crossover(prices: np.ndarray, short_span: int = 5, long_span: int = 15) -> float:
    """Compute EMA crossover signal (short EMA - long EMA)."""
    if len(prices) < long_span:
        if len(prices) < 2:
            return 0.0
        short_span = max(2, len(prices) // 3)
        long_span = len(prices)

    short_alpha = 2.0 / (short_span + 1)
    long_alpha = 2.0 / (long_span + 1)

    short_ema = prices[0]
    long_ema = prices[0]

    for p in prices[1:]:
        short_ema = short_alpha * p + (1 - short_alpha) * short_ema
        long_ema = long_alpha * p + (1 - long_alpha) * long_ema

    return float(short_ema - long_ema)


def _max_swing(prices: np.ndarray, timestamps: np.ndarray, window_sec: float) -> float:
    """Compute maximum price swing in a time window."""
    if len(prices) < 2:
        return 0.0

    current_time = timestamps[-1]
    mask = timestamps >= (current_time - window_sec)
    window_prices = prices[mask]

    if len(window_prices) < 2:
        window_prices = prices

    return float(np.max(window_prices) - np.min(window_prices))
