"""
Group 11: Match Momentum Features (2 features)
Captures the derivative of the match state: runs scored and wickets lost in the recent window.
This gives the agent true momentum awareness beyond just price action.
"""

from __future__ import annotations

import numpy as np

from shared.schemas import MatchContext


def compute_match_momentum_features(history: list[MatchContext], window_sec: float = 120.0) -> list[float]:
    """
    Compute 2 match momentum features from recent context history.

    Features:
        0: runs_momentum (runs scored in window / 30)
        1: wickets_momentum (wickets lost in window / 3)

    Args:
        history: Recent MatchContext list, oldest first, newest last.
        window_sec: Lookback window in seconds (default 2 mins).

    Returns:
        2-element list of floats.
    """
    if len(history) < 2:
        return [0.0, 0.0]

    current_context = history[-1]
    current_time = current_context.timestamp

    # Find the oldest context within the window
    # Because history is sorted oldest to newest, we can iterate forward
    window_start_context = history[0]
    for ctx in history:
        if (current_time - ctx.timestamp).total_seconds() <= window_sec:
            window_start_context = ctx
            break

    # If the window spans an innings break, reset momentum
    if current_context.innings != window_start_context.innings:
        return [0.0, 0.0]

    runs_scored = current_context.score - window_start_context.score
    wickets_lost = current_context.wickets - window_start_context.wickets

    # Normalize:
    # A great 2-minute stretch might be 30 runs. Max cap at 30.
    # A terrible stretch might be 3 wickets. Max cap at 3.
    runs_momentum = max(0.0, min(float(runs_scored) / 30.0, 1.0))
    wickets_momentum = max(0.0, min(float(wickets_lost) / 3.0, 1.0))

    return [runs_momentum, wickets_momentum]
