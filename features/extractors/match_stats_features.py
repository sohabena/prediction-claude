"""
Group 4: Raw Match Statistics Features (8 features)
Direct from Cricbuzz. NO derived heuristics like 'match phase' or 'team strength'.
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import MatchContext


def compute_match_stats_features(context: Optional[MatchContext]) -> list[float]:
    """
    Extract 8 raw match statistics features.

    Features:
        0: is_live            (0 or 1)
        1: overs_normalized   (0.0 to 1.0 within innings)
        2: wickets_normalized (0.0 to 1.0, out of 10)
        3: score_normalized   (score / 300, rough cap)
        4: run_rate_normalized (capped at 1.0)
        5: req_rr_normalized   (0 in 1st innings, capped at 1.0)
        6: innings_normalized  (0.5 or 1.0)
        7: balls_remaining_normalized (0.0 to 1.0)
    """
    if context is None:
        return [0.0] * 8

    max_overs = context.max_overs if context.max_overs > 0 else 20.0
    max_balls = context.max_balls if context.max_balls > 0 else 120

    is_live = 1.0 if context.is_live else 0.0
    overs_norm = min(context.overs / max_overs, 1.0) if max_overs > 0 else 0.0
    wickets_norm = context.wickets / 10.0
    score_norm = min(context.score / 300.0, 1.0)
    rr_norm = min(context.run_rate / 15.0, 1.0)
    req_rr_norm = min(context.required_run_rate / 20.0, 1.0)
    innings_norm = float(context.innings) / 2.0
    balls_rem_norm = context.balls_remaining / max_balls if max_balls > 0 else 0.0

    return [
        is_live,
        overs_norm,
        wickets_norm,
        score_norm,
        rr_norm,
        req_rr_norm,
        innings_norm,
        balls_rem_norm,
    ]
