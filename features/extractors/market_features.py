"""
Group 3: Market Microstructure Features (8 features)
Information about market quality and efficiency, derived from odds.
"""

from __future__ import annotations

import numpy as np

from shared.schemas import OddsEvent


def compute_market_features(
    event: OddsEvent, history: list[OddsEvent]
) -> list[float]:
    """
    Compute 8 market microstructure features.

    Features:
        0: spread_width_home       (lay - back for home)
        1: spread_width_away       (lay - back for away)
        2: spread_velocity_home    (rate of spread change)
        3: spread_velocity_away    (rate of spread change)
        4: market_efficiency_score (overround / expected baseline)
        5: relative_price_level    (where current odds sit in recent range, 0-1)
        6: time_since_last_change_home (seconds since home odds changed)
        7: time_since_last_change_away (seconds since away odds changed)
    """
    # Spread widths
    back_home = event.back_home or 0.0
    lay_home = event.lay_home or 0.0
    back_away = event.back_away or 0.0
    lay_away = event.lay_away or 0.0

    spread_home = (lay_home - back_home) if lay_home > 0 and back_home > 0 else 0.0
    spread_away = (lay_away - back_away) if lay_away > 0 and back_away > 0 else 0.0

    # Spread velocity (change in spread over recent ticks)
    spread_vel_home = 0.0
    spread_vel_away = 0.0
    if len(history) >= 2:
        prev = history[-2]
        prev_spread_home = ((prev.lay_home or 0) - (prev.back_home or 0)) if prev.back_home and prev.lay_home else 0.0
        prev_spread_away = ((prev.lay_away or 0) - (prev.back_away or 0)) if prev.back_away and prev.lay_away else 0.0
        dt = (event.timestamp - prev.timestamp).total_seconds()
        if dt > 0:
            spread_vel_home = (spread_home - prev_spread_home) / dt
            spread_vel_away = (spread_away - prev_spread_away) / dt

    # Market efficiency score
    ip_home = 1.0 / back_home if back_home > 1.0 else 0.0
    ip_away = 1.0 / back_away if back_away > 1.0 else 0.0
    ip_draw = 1.0 / (event.back_draw or 1.0) if event.back_draw and event.back_draw > 1.0 else 0.0
    overround = ip_home + ip_away + ip_draw - 1.0
    expected_overround = 0.05  # ~5% is typical for LotusBook
    efficiency = overround / expected_overround if expected_overround > 0 else 1.0

    # Relative price level (where current home odds sit in recent range)
    price_level = 0.5  # Default: middle of range
    if history:
        home_prices = [e.back_home for e in history if e.back_home is not None]
        if home_prices:
            min_p = min(home_prices)
            max_p = max(home_prices)
            rng = max_p - min_p
            if rng > 0 and back_home > 0:
                price_level = (back_home - min_p) / rng

    # Time since last odds change
    tsince_home = 0.0
    tsince_away = 0.0
    current_time = event.timestamp
    for prev_event in reversed(history):
        if tsince_home == 0.0 and prev_event.back_home != event.back_home:
            tsince_home = (current_time - prev_event.timestamp).total_seconds()
        if tsince_away == 0.0 and prev_event.back_away != event.back_away:
            tsince_away = (current_time - prev_event.timestamp).total_seconds()
        if tsince_home > 0 and tsince_away > 0:
            break

    # Normalize staleness (cap at 60s)
    tsince_home = min(tsince_home / 60.0, 1.0)
    tsince_away = min(tsince_away / 60.0, 1.0)

    return [
        spread_home, spread_away,
        spread_vel_home, spread_vel_away,
        efficiency,
        price_level,
        tsince_home, tsince_away,
    ]
