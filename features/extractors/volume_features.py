"""
Group 10: Volume/Liquidity Features (4 features)
Market depth signals from LotusBook volume data.

Volume is one of the most predictive signals in betting markets:
- Sudden volume spike = informed money entering
- Volume imbalance = smart money favoring one side
- Low volume = thin market, unreliable odds
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import OddsEvent


def compute_volume_features(
    event: OddsEvent,
    history: list[OddsEvent],
) -> list[float]:
    """
    Compute 4 volume/liquidity features.

    Features:
        0: volume_home_norm      (log-scaled, normalized)
        1: volume_away_norm      (log-scaled, normalized)
        2: volume_imbalance      (-1 to +1, home vs away ratio)
        3: volume_velocity       (rate of volume change over recent ticks)
    """
    import math

    vol_home = getattr(event, "volume_back_home", None) or 0.0
    vol_away = getattr(event, "volume_back_away", None) or 0.0

    # Log-scale normalization (volumes can span orders of magnitude)
    # log(1 + x) / log(1 + 100000) to get ~0-1 range
    log_scale = math.log(100001)
    vol_home_norm = math.log(1 + vol_home) / log_scale if vol_home > 0 else 0.0
    vol_away_norm = math.log(1 + vol_away) / log_scale if vol_away > 0 else 0.0

    # Volume imbalance: which side has more money?
    total_vol = vol_home + vol_away
    if total_vol > 0:
        imbalance = (vol_home - vol_away) / total_vol  # -1 to +1
    else:
        imbalance = 0.0

    # Volume velocity: rate of change over last few ticks
    velocity = 0.0
    if len(history) >= 3:
        recent_vols = []
        for e in history[-5:]:
            v_h = getattr(e, "volume_back_home", None) or 0.0
            v_a = getattr(e, "volume_back_away", None) or 0.0
            recent_vols.append(v_h + v_a)

        if len(recent_vols) >= 2:
            # Simple velocity: latest vs average of earlier ticks
            latest = recent_vols[-1]
            earlier_avg = sum(recent_vols[:-1]) / len(recent_vols[:-1])
            if earlier_avg > 0:
                velocity = (latest - earlier_avg) / earlier_avg
                velocity = max(-1.0, min(1.0, velocity))  # clamp

    return [
        vol_home_norm,
        vol_away_norm,
        imbalance,
        velocity,
    ]
