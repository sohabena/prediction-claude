"""
Group 5: Portfolio State Features (5 features)
Agent's own financial state. Trimmed: removed redundant P&L fields and time_since_bet.
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import PortfolioState


def compute_portfolio_features(portfolio: Optional[PortfolioState]) -> list[float]:
    """
    Compute 5 portfolio state features.

    Features:
        0: bankroll_pct        (current / initial balance — captures cumulative P&L)
        1: exposure_pct        (total exposure / current balance)
        2: open_positions_norm (open positions / 10)
        3: win_rate            (last 20 bets win rate)
        4: streak_norm         (signed consecutive streak / 10)
    """
    if portfolio is None:
        return [1.0, 0.0, 0.0, 0.0, 0.0]

    initial = max(portfolio.initial_balance, 1.0)
    current = max(portfolio.current_balance, 1.0)

    bankroll_pct = current / initial
    exposure_pct = portfolio.total_exposure / current
    open_pos_norm = portfolio.open_positions / 10.0
    win_rate = portfolio.win_rate
    streak_norm = portfolio.consecutive_streak / 10.0

    return [bankroll_pct, exposure_pct, open_pos_norm, win_rate, streak_norm]
