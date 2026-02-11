"""
Bet settlement: determines outcomes of virtual bets based on match results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from shared.logging import setup_logging
from shared.schemas import BetOutcome, BettingAction, OddsEvent, VirtualBet
from virtual_trading.portfolio import PortfolioManager

logger = setup_logging("settlement")


class SettlementEngine:
    """
    Settles virtual bets based on match outcomes.

    Settlement methods:
    1. Match result settlement (when match ends)
    2. Odds-based settlement (based on closing odds vs entry odds)
    3. Time-based settlement (after N steps for training)
    """

    def __init__(self, portfolio: PortfolioManager) -> None:
        self.portfolio = portfolio

    @staticmethod
    def compute_clv(placement_odds: float, closing_odds: float) -> float:
        """
        Compute Closing Line Value.

        CLV = (closing_implied_prob / placement_implied_prob) - 1

        Positive CLV means you got better odds than the closing line,
        which is the industry gold standard for measuring betting skill.

        Args:
            placement_odds: Odds when the bet was placed.
            closing_odds: Last odds before the match ended.

        Returns:
            CLV as a decimal (e.g., 0.05 means 5% positive CLV).
        """
        if placement_odds <= 1.0 or closing_odds <= 1.0:
            return 0.0

        placement_implied = 1.0 / placement_odds
        closing_implied = 1.0 / closing_odds

        return (closing_implied / placement_implied) - 1.0

    def settle_on_match_result(
        self, bet: VirtualBet, winning_team: str,
        closing_odds: float | None = None,
    ) -> float:
        """
        Settle a bet based on the match result.

        Args:
            bet: The virtual bet to settle.
            winning_team: Name of the winning team.

        Returns:
            P&L amount.
        """
        is_back = bet.action in (
            BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
            BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
        )
        is_lay = bet.action in (
            BettingAction.LAY_HOME_SM, BettingAction.LAY_AWAY_SM,
            BettingAction.LAY_HOME_LG, BettingAction.LAY_AWAY_LG,
        )

        team_won = bet.team == winning_team

        if is_back:
            if team_won:
                pnl = bet.stake * (bet.odds - 1.0)
                bet.outcome = BetOutcome.WIN
            else:
                pnl = -bet.stake
                bet.outcome = BetOutcome.LOSS
        elif is_lay:
            if team_won:
                # Lay bet loses when the team wins
                pnl = -bet.stake * (bet.odds - 1.0)
                bet.outcome = BetOutcome.LOSS
            else:
                pnl = bet.stake
                bet.outcome = BetOutcome.WIN
        else:
            pnl = 0.0
            bet.outcome = BetOutcome.VOID

        bet.profit_loss = pnl
        bet.settled_at = datetime.now(timezone.utc)

        # Compute CLV if closing odds provided
        if closing_odds is not None and closing_odds > 1.0:
            bet.closing_odds = closing_odds
            bet.clv = self.compute_clv(bet.odds, closing_odds)

        # Close position in portfolio
        if bet.id:
            self.portfolio.close_position(bet.id, pnl)

        logger.info(
            "bet_settled",
            bet_id=bet.id,
            outcome=bet.outcome.value,
            pnl=pnl,
            odds=bet.odds,
            clv=getattr(bet, "clv", None),
        )
        return pnl

    def settle_on_closing_odds(
        self, bet: VirtualBet, closing_odds: float
    ) -> float:
        """
        Settle based on closing odds comparison (CLV-based).

        If agent got better odds than closing, it's a theoretical win.

        Args:
            bet: The virtual bet.
            closing_odds: The final odds at market close.

        Returns:
            P&L amount.
        """
        is_back = bet.action in (
            BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
            BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
        )

        if is_back:
            # Back bet: won CLV if entry odds > closing odds
            clv = (bet.odds - closing_odds) / closing_odds
            if bet.odds > closing_odds:
                # Positive CLV: simulate win proportional to edge
                edge = 1.0 / closing_odds - 1.0 / bet.odds
                pnl = bet.stake * edge * bet.odds
                bet.outcome = BetOutcome.WIN
            else:
                pnl = -bet.stake * abs(clv)
                bet.outcome = BetOutcome.LOSS
        else:
            # Lay bet: won CLV if entry odds < closing odds
            if bet.odds < closing_odds:
                edge = 1.0 / bet.odds - 1.0 / closing_odds
                pnl = bet.stake * edge
                bet.outcome = BetOutcome.WIN
            else:
                pnl = -bet.stake * abs((closing_odds - bet.odds) / bet.odds)
                bet.outcome = BetOutcome.LOSS

        bet.profit_loss = pnl
        bet.settled_at = datetime.now(timezone.utc)

        if bet.id:
            self.portfolio.close_position(bet.id, pnl)

        return pnl

    def void_bet(self, bet: VirtualBet) -> None:
        """Void a bet (no P&L impact, return stake)."""
        bet.outcome = BetOutcome.VOID
        bet.profit_loss = 0.0
        bet.settled_at = datetime.now(timezone.utc)

        if bet.id:
            self.portfolio.close_position(bet.id, 0.0)

        logger.info("bet_voided", bet_id=bet.id)

    def settle_all_open(self, winning_team: str) -> float:
        """Settle all open bets for a match result."""
        total_pnl = 0.0
        for bet_id, bet in list(self.portfolio.open_bets.items()):
            pnl = self.settle_on_match_result(bet, winning_team)
            total_pnl += pnl
        return total_pnl
