"""
Group 9: Position Features (4 features)
Agent's current exposure on THIS match — enables hedging/trading strategy.

The agent needs to know its open positions to decide whether to hedge,
increase exposure, or hold. Without this, it can't learn to trade both sides.
"""

from __future__ import annotations

from typing import Optional


def compute_position_features(
    position_state: Optional[dict] = None,
) -> list[float]:
    """
    Compute 4 position-awareness features for the current match.

    Args:
        position_state: Dict with keys:
            - net_home_exposure: net $ on home team (positive=long/back, negative=short/lay)
            - net_away_exposure: net $ on away team
            - bankroll: current balance for normalization
            - back_home_odds: current back odds for home (for hedge calc)
            - lay_home_odds: current lay odds for home
            - back_away_odds: current back odds for away
            - lay_away_odds: current lay odds for away
            - best_entry_home: best (lowest) entry odds on home BACK positions
            - best_entry_away: best (lowest) entry odds on away BACK positions

    Features:
        0: net_home_exposure_pct  (-1 to +1, normalized by bankroll)
        1: net_away_exposure_pct  (-1 to +1, normalized by bankroll)
        2: can_hedge_profit       (1.0 if hedging now locks in profit, else 0.0)
        3: hedge_profit_pct       (theoretical locked profit / bankroll, can be negative)
    """
    if position_state is None:
        return [0.0, 0.0, 0.0, 0.0]

    bankroll = max(position_state.get("bankroll", 1.0), 1.0)
    net_home = position_state.get("net_home_exposure", 0.0)
    net_away = position_state.get("net_away_exposure", 0.0)

    # Normalize exposure by bankroll, clamp to [-1, 1]
    home_pct = max(-1.0, min(1.0, net_home / bankroll))
    away_pct = max(-1.0, min(1.0, net_away / bankroll))

    # Can we hedge for profit?
    # If we're long home (backed), we can hedge by laying at lower odds
    # If we're short home (laid), we can hedge by backing at higher odds
    hedge_profit = 0.0
    can_hedge = 0.0

    back_home = position_state.get("back_home_odds", 0.0) or 0.0
    lay_home = position_state.get("lay_home_odds", 0.0) or 0.0
    back_away = position_state.get("back_away_odds", 0.0) or 0.0
    lay_away = position_state.get("lay_away_odds", 0.0) or 0.0

    # Check home side hedge potential
    if net_home > 0 and lay_home > 1.0:
        # Long home → can lay to hedge
        # Entry was effectively at implied prob of our back odds
        best_entry = position_state.get("best_entry_home", 0.0)
        if best_entry > 1.0 and lay_home < best_entry:
            # Odds shortened → profitable hedge
            # Approx locked profit per unit: (entry - current) / current
            hedge_profit += net_home * (best_entry - lay_home) / lay_home
    elif net_home < 0 and back_home > 1.0:
        # Short home → can back to hedge
        best_entry = position_state.get("best_entry_lay_home", 0.0)
        if best_entry > 1.0 and back_home > best_entry:
            # Odds drifted → profitable hedge for lay
            hedge_profit += abs(net_home) * (back_home - best_entry) / best_entry

    # Check away side
    if net_away > 0 and lay_away > 1.0:
        best_entry = position_state.get("best_entry_away", 0.0)
        if best_entry > 1.0 and lay_away < best_entry:
            hedge_profit += net_away * (best_entry - lay_away) / lay_away
    elif net_away < 0 and back_away > 1.0:
        best_entry = position_state.get("best_entry_lay_away", 0.0)
        if best_entry > 1.0 and back_away > best_entry:
            hedge_profit += abs(net_away) * (back_away - best_entry) / best_entry

    hedge_profit_pct = hedge_profit / bankroll
    can_hedge = 1.0 if hedge_profit > 0 else 0.0

    return [
        home_pct,
        away_pct,
        can_hedge,
        max(-1.0, min(1.0, hedge_profit_pct)),  # clamp
    ]
