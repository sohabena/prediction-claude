"""
Group 8: Bookmaker Data Pattern Features (4 features)

Pure statistical observations of how the bookmaker prices odds.
The bookmaker's algorithm follows consistent patterns to ensure profit.
Over many matches, the agent learns these patterns for trading decisions.

Trimmed from 6: removed spread_consistency (overlaps market group)
and margin_level (overlaps overround in odds group).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from shared.schemas import MatchContext, OddsEvent


def compute_bookmaker_pattern_features(
    event: OddsEvent,
    history: list[OddsEvent],
    context: Optional[MatchContext] = None,
) -> list[float]:
    """
    Compute 4 features describing the bookmaker's pricing patterns.

    Features:
        0: overround_trend       — is bookmaker margin widening or narrowing?
        1: odds_move_symmetry    — do home/away odds move proportionally?
        2: pricing_intensity     — how frequently is the bookmaker re-pricing?
        3: odds_context_coupling — how tightly do odds track match progress?

    Returns:
        4-element list of floats.
    """
    if len(history) < 3:
        return [0.0] * 4

    # --- 0: Overround Trend ---
    overrounds = []
    for e in history:
        bh = e.back_home or 0.0
        ba = e.back_away or 0.0
        if bh > 1.0 and ba > 1.0:
            overrounds.append((1.0 / bh) + (1.0 / ba) - 1.0)
    overround_trend = 0.0
    if len(overrounds) >= 3:
        recent = np.mean(overrounds[-3:])
        earlier = np.mean(overrounds[:max(1, len(overrounds) // 2)])
        overround_trend = float(np.clip(recent - earlier, -0.2, 0.2))

    # --- 1: Odds Move Symmetry ---
    symmetry = 0.0
    if len(history) >= 2:
        home_changes = []
        away_changes = []
        for i in range(1, len(history)):
            h_prev = history[i - 1].back_home or 0.0
            h_curr = history[i].back_home or 0.0
            a_prev = history[i - 1].back_away or 0.0
            a_curr = history[i].back_away or 0.0
            if h_prev > 1.0 and a_prev > 1.0:
                home_changes.append((h_curr - h_prev) / h_prev)
                away_changes.append((a_curr - a_prev) / a_prev)
        if len(home_changes) >= 2:
            hc = np.array(home_changes)
            ac = np.array(away_changes)
            h_std = np.std(hc)
            a_std = np.std(ac)
            if h_std > 1e-8 and a_std > 1e-8:
                corr = float(np.corrcoef(hc, ac)[0, 1])
                symmetry = corr if not np.isnan(corr) else 0.0

    # --- 2: Pricing Intensity ---
    pricing_intensity = 0.0
    if len(history) >= 2:
        change_count = 0
        for i in range(1, len(history)):
            h_prev = history[i - 1].back_home
            h_curr = history[i].back_home
            if h_prev is not None and h_curr is not None and h_prev != h_curr:
                change_count += 1
        pricing_intensity = change_count / (len(history) - 1)

    # --- 3: Odds-Context Coupling ---
    odds_context_coupling = 0.0
    if context is not None and len(history) >= 5:
        first_home = history[0].back_home or 0.0
        last_home = history[-1].back_home or 0.0
        if first_home > 1.0:
            cumulative_odds_change = abs(last_home - first_home) / first_home
            max_overs = context.max_overs if context.max_overs > 0 else 20.0
            match_progress = min(context.overs / max_overs, 1.0)
            if match_progress > 0.05:
                odds_context_coupling = float(np.clip(
                    cumulative_odds_change / match_progress, 0.0, 2.0
                ))

    return [overround_trend, symmetry, pricing_intensity, odds_context_coupling]
