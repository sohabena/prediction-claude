"""
Group 1: Core Odds Features (7 features)
Direct price and spread information from LotusBook.
No draw odds (zero in T20/ODI), no implied probs (redundant with 1/odds).
"""

from __future__ import annotations

from shared.schemas import OddsEvent


def compute_odds_features(event: OddsEvent) -> list[float]:
    """
    Extract 7 core odds features from a single OddsEvent.

    Features:
        0: back_home
        1: lay_home
        2: back_away
        3: lay_away
        4: overround          (bookmaker margin)
        5: spread_home        (lay_home - back_home)
        6: spread_away        (lay_away - back_away)
    """
    back_home = event.back_home or 0.0
    lay_home = event.lay_home or 0.0
    back_away = event.back_away or 0.0
    lay_away = event.lay_away or 0.0

    # Overround (bookmaker margin)
    ip_home = 1.0 / back_home if back_home > 1.0 else 0.0
    ip_away = 1.0 / back_away if back_away > 1.0 else 0.0
    overround = ip_home + ip_away - 1.0

    # Spreads (liquidity indicator)
    spread_home = (lay_home - back_home) if lay_home > 0 and back_home > 0 else 0.0
    spread_away = (lay_away - back_away) if lay_away > 0 and back_away > 0 else 0.0

    return [back_home, lay_home, back_away, lay_away, overround, spread_home, spread_away]
