"""
Group 5: Temporal Features (6 features)
Cyclical time encoding to capture time-of-day and day-of-week effects.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional


def compute_temporal_features(
    timestamp: datetime,
    match_start_time: Optional[datetime] = None,
    last_tick_time: Optional[datetime] = None,
) -> list[float]:
    """
    Compute 6 temporal features with cyclical encoding.

    Features:
        0: hour_sin            (sin of hour / 24)
        1: hour_cos            (cos of hour / 24)
        2: day_sin             (sin of weekday / 7)
        3: day_cos             (cos of weekday / 7)
        4: minutes_since_start (normalized by 300 min, capped at 1.0)
        5: seconds_since_tick  (normalized by 30 sec, capped at 1.0)
    """
    hour = timestamp.hour + timestamp.minute / 60.0
    day = timestamp.weekday()

    hour_sin = math.sin(2 * math.pi * hour / 24.0)
    hour_cos = math.cos(2 * math.pi * hour / 24.0)
    day_sin = math.sin(2 * math.pi * day / 7.0)
    day_cos = math.cos(2 * math.pi * day / 7.0)

    # Minutes since match start
    if match_start_time is not None:
        minutes_since = (timestamp - match_start_time).total_seconds() / 60.0
        minutes_norm = min(minutes_since / 300.0, 1.0)  # 5 hours max
    else:
        minutes_norm = 0.0

    # Seconds since last tick
    if last_tick_time is not None:
        seconds_since = (timestamp - last_tick_time).total_seconds()
        seconds_norm = min(seconds_since / 30.0, 1.0)  # 30 sec max
    else:
        seconds_norm = 0.0

    return [
        hour_sin, hour_cos,
        day_sin, day_cos,
        minutes_norm,
        seconds_norm,
    ]
