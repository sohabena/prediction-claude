"""
Group 1: Raw Odds Features (12 features)
Direct price information from LotusBook.
"""

from __future__ import annotations

from shared.schemas import OddsEvent


def compute_odds_features(event: OddsEvent) -> list[float]:
    """
    Extract 12 raw odds features from a single OddsEvent.

    Features:
        0: back_home
        1: lay_home
        2: back_away
        3: lay_away
        4: back_draw
        5: lay_draw
        6: implied_prob_home  (1 / back_home)
        7: implied_prob_away  (1 / back_away)
        8: implied_prob_draw  (1 / back_draw)
        9: overround          (sum of implied probs - 1)
        10: spread_home       (lay_home - back_home)
        11: spread_away       (lay_away - back_away)
    """
    back_home = event.back_home or 0.0
    lay_home = event.lay_home or 0.0
    back_away = event.back_away or 0.0
    lay_away = event.lay_away or 0.0
    back_draw = event.back_draw or 0.0
    lay_draw = event.lay_draw or 0.0

    # Implied probabilities
    ip_home = 1.0 / back_home if back_home > 1.0 else 0.0
    ip_away = 1.0 / back_away if back_away > 1.0 else 0.0
    ip_draw = 1.0 / back_draw if back_draw > 1.0 else 0.0

    # Overround (bookmaker margin)
    overround = ip_home + ip_away + ip_draw - 1.0

    # Spreads
    spread_home = (lay_home - back_home) if lay_home > 0 and back_home > 0 else 0.0
    spread_away = (lay_away - back_away) if lay_away > 0 and back_away > 0 else 0.0

    return [
        back_home, lay_home, back_away, lay_away, back_draw, lay_draw,
        ip_home, ip_away, ip_draw,
        overround, spread_home, spread_away,
    ]
