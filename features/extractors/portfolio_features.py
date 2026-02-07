"""
Group 6: Portfolio State Features (8 features)
Agent's own financial state.
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import PortfolioState


def compute_portfolio_features(portfolio: Optional[PortfolioState]) -> list[float]:
    """
    Compute 8 portfolio state features.

    Features:
        0: bankroll_pct           (current / initial balance)
        1: session_pnl_pct        (session P&L / initial balance)
        2: open_positions_norm    (open positions / 10)
        3: exposure_pct           (total exposure / current balance)
        4: recent_win_rate        (last 20 bets win rate)
        5: consecutive_streak     (signed streak / 10, normalized)
        6: daily_pnl_pct          (daily P&L / initial balance)
        7: time_since_last_bet    (capped at 300s, normalized)
    """
    if portfolio is None:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    initial = max(portfolio.initial_balance, 1.0)
    current = max(portfolio.current_balance, 1.0)

    bankroll_pct = current / initial
    session_pnl_pct = portfolio.session_pnl / initial
    open_pos_norm = portfolio.open_positions / 10.0
    exposure_pct = portfolio.total_exposure / current
    win_rate = portfolio.win_rate
    streak_norm = portfolio.consecutive_streak / 10.0
    daily_pnl_pct = portfolio.daily_pnl / initial
    time_since_bet = min(portfolio.time_since_last_bet / 300.0, 1.0)

    return [
        bankroll_pct,
        session_pnl_pct,
        open_pos_norm,
        exposure_pct,
        win_rate,
        streak_norm,
        daily_pnl_pct,
        time_since_bet,
    ]
