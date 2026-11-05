"""
Group 3: Market Microstructure Features (5 features)
Market quality signals. Spread widths moved to odds group; kept unique signals only.
"""

from __future__ import annotations

from shared.schemas import OddsEvent


def compute_market_features(
    event: OddsEvent, history: list[OddsEvent]
) -> list[float]:
    """
    Compute 5 market microstructure features.

    Features:
        0: spread_velocity       (rate of spread change, avg of home+away)
        1: market_efficiency     (overround / expected baseline)
        2: relative_price_level  (where home odds sit in recent range, 0-1)
        3: time_since_change_home (normalized 0-1, seconds/60)
        4: time_since_change_away (normalized 0-1, seconds/60)
    """
    back_home = event.back_home or 0.0
    lay_home = event.lay_home or 0.0
    back_away = event.back_away or 0.0
    lay_away = event.lay_away or 0.0

    spread_home = (lay_home - back_home) if lay_home > 0 and back_home > 0 else 0.0
    spread_away = (lay_away - back_away) if lay_away > 0 and back_away > 0 else 0.0

    # Spread velocity (average rate of spread change)
    spread_vel = 0.0
    if len(history) >= 2:
        prev = history[-2]
        prev_sh = ((prev.lay_home or 0) - (prev.back_home or 0)) if prev.back_home and prev.lay_home else 0.0
        prev_sa = ((prev.lay_away or 0) - (prev.back_away or 0)) if prev.back_away and prev.lay_away else 0.0
        dt = (event.timestamp - prev.timestamp).total_seconds()
        if dt > 0:
            spread_vel = ((spread_home - prev_sh) + (spread_away - prev_sa)) / (2.0 * dt)

    # Market efficiency (overround relative to typical ~5%)
    ip_home = 1.0 / back_home if back_home > 1.0 else 0.0
    ip_away = 1.0 / back_away if back_away > 1.0 else 0.0
    overround = ip_home + ip_away - 1.0
    efficiency = overround / 0.05 if overround > 0 else 1.0

    # Relative price level (where current home odds sit in recent range)
    price_level = 0.5
    if history:
        home_prices = [e.back_home for e in history if e.back_home is not None]
        if home_prices:
            min_p = min(home_prices)
            max_p = max(home_prices)
            rng = max_p - min_p
            if rng > 0 and back_home > 0:
                price_level = (back_home - min_p) / rng

    # Time since last odds change (normalized, cap at 60s)
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
    tsince_home = min(tsince_home / 60.0, 1.0)
    tsince_away = min(tsince_away / 60.0, 1.0)

    return [spread_vel, efficiency, price_level, tsince_home, tsince_away]
