"""
Group 4: Match State Features (7 features)
Match state from LotusBook context — data only, no cricket heuristics.
Removed balls_remaining (derivable from overs).
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import MatchContext


def compute_match_stats_features(context: Optional[MatchContext] = None) -> list[float]:
    """
    Compute 7 match state features.

    Features:
        0: is_live             (1.0 if match is live, 0.0 otherwise)
        1: overs_normalized    (overs / max_overs)
        2: wickets_normalized  (wickets / 10)
        3: score_normalized    (score / 400)
        4: run_rate_normalized (run_rate / 15)
        5: req_run_rate_norm   (required_run_rate / 20)
        6: innings             (1 or 2, normalized to 0-1)
    """
    if context is None:
        return [0.0] * 7

    is_live = 1.0 if context.is_live else 0.0
    max_overs = context.max_overs if context.max_overs > 0 else 20.0
    overs = min(context.overs / max_overs, 1.0)
    wickets = min(context.wickets / 10.0, 1.0)
    score = min(context.score / 400.0, 1.0)
    run_rate = min(context.run_rate / 15.0, 1.0)
    req_rr = min(context.required_run_rate / 20.0, 1.0)
    innings = min(context.innings / 2.0, 1.0)

    return [is_live, overs, wickets, score, run_rate, req_rr, innings]
