"""
Group 2: Odds Momentum Features (16 features)
Mathematical analysis of price time series: velocity, acceleration, volatility.
"""

from __future__ import annotations

import numpy as np

from shared.schemas import OddsEvent


def compute_momentum_features(history: list[OddsEvent]) -> list[float]:
    """
    Compute 16 momentum features from recent odds history.

    Features (per team = home/away):
        0-1:  odds_velocity_5s   (price change rate, ultra-short window)
        2-3:  odds_velocity_30s  (short window)
        4-5:  odds_velocity_60s  (medium window)
        6-7:  odds_acceleration  (rate of velocity change)
        8-9:  volatility_30s     (rolling std dev of odds)
        10-11: ema_crossover     (short EMA - long EMA, directional signal)
        12-13: momentum_score    (velocity * magnitude)
        14-15: max_swing_60s     (max price change in window)

    Args:
        history: Recent OddsEvent list, oldest first, newest last.

    Returns:
        16-element list of floats.
    """
    if len(history) < 2:
        return [0.0] * 16

    # Extract price series
    home_prices = np.array([e.back_home or 0.0 for e in history], dtype=np.float64)
    away_prices = np.array([e.back_away or 0.0 for e in history], dtype=np.float64)
    timestamps = np.array(
        [e.timestamp.timestamp() for e in history], dtype=np.float64
    )

    # Velocity (price change per second) at different windows
    vel_5s_home = _velocity(home_prices, timestamps, window_sec=5.0)
    vel_5s_away = _velocity(away_prices, timestamps, window_sec=5.0)
    vel_30s_home = _velocity(home_prices, timestamps, window_sec=30.0)
    vel_30s_away = _velocity(away_prices, timestamps, window_sec=30.0)
    vel_60s_home = _velocity(home_prices, timestamps, window_sec=60.0)
    vel_60s_away = _velocity(away_prices, timestamps, window_sec=60.0)

    # Acceleration (rate of velocity change)
    accel_home = vel_5s_home - vel_30s_home  # Short vs medium velocity
    accel_away = vel_5s_away - vel_30s_away

    # Volatility (rolling std dev over ~30s window)
    vol_home = _rolling_volatility(home_prices, timestamps, window_sec=30.0)
    vol_away = _rolling_volatility(away_prices, timestamps, window_sec=30.0)

    # EMA crossover (short EMA - long EMA)
    ema_cross_home = _ema_crossover(home_prices)
    ema_cross_away = _ema_crossover(away_prices)

    # Momentum score (velocity * magnitude of change)
    mom_home = vel_30s_home * abs(home_prices[-1] - home_prices[0]) if len(home_prices) > 1 else 0.0
    mom_away = vel_30s_away * abs(away_prices[-1] - away_prices[0]) if len(away_prices) > 1 else 0.0

    # Max swing in 60s window
    swing_home = _max_swing(home_prices, timestamps, window_sec=60.0)
    swing_away = _max_swing(away_prices, timestamps, window_sec=60.0)

    return [
        vel_5s_home, vel_5s_away,
        vel_30s_home, vel_30s_away,
        vel_60s_home, vel_60s_away,
        accel_home, accel_away,
        vol_home, vol_away,
        ema_cross_home, ema_cross_away,
        mom_home, mom_away,
        swing_home, swing_away,
    ]


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
