"""
P&L (Profit & Loss) Calculator Service
Handles bet outcome calculations and budget updates
"""

from sqlalchemy.orm import Session
from backend.models.betting import VirtualBet, BettingBudget, BetStatus
from loguru import logger
from datetime import datetime


def calculate_bet_outcome(
    bet: VirtualBet,
    won: bool,
    db: Session
) -> dict:
    """
    Calculate profit/loss for a bet and update budget
    
    Args:
        bet: VirtualBet instance
        won: Boolean indicating if bet was won
        db: Database session
    
    Returns:
        dict: {
            'profit_loss': float,
            'new_balance': float,
            'roi': float
        }
    """
    try:
        # Calculate P&L using the bet's close_bet method
        profit_loss = bet.close_bet(won=won)
        
        # Update budget
        budget = db.query(BettingBudget).filter_by(id=bet.budget_id).first()
        
        if not budget:
            logger.error(f"Budget not found for bet {bet.id}")
            raise ValueError("Budget not found")
        
        # Update budget balance and statistics
        budget.current_balance += profit_loss
        budget.total_profit_loss += profit_loss
        
        if won:
            budget.total_bets_won += 1
        else:
            budget.total_bets_lost += 1
        
        budget.updated_at = datetime.utcnow()
        
        # Commit changes
        db.commit()
        db.refresh(bet)
        db.refresh(budget)
        
        result = {
            'profit_loss': profit_loss,
            'new_balance': budget.current_balance,
            'roi': budget.roi,
            'win_rate': budget.win_rate,
            'bet_status': bet.status.value
        }
        
        logger.info(f"Bet {bet.id} closed: {'WON' if won else 'LOST'}, "
                   f"P&L: ₹{profit_loss:.2f}, New balance: ₹{budget.current_balance:.2f}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error calculating bet outcome: {e}")
        db.rollback()
        raise


def close_multiple_bets(
    match_id: str,
    winner: str,
    db: Session
) -> dict:
    """
    Close all open bets for a completed match
    
    Args:
        match_id: Match identifier
        winner: Winning team name
        db: Database session
    
    Returns:
        dict: {
            'bets_closed': int,
            'total_profit_loss': float,
            'bets_won': int,
            'bets_lost': int
        }
    """
    try:
        # Find all pending bets for this match
        pending_bets = db.query(VirtualBet).filter(
            VirtualBet.match_id == match_id,
            VirtualBet.status == BetStatus.PENDING
        ).all()
        
        if not pending_bets:
            logger.info(f"No pending bets found for match {match_id}")
            return {
                'bets_closed': 0,
                'total_profit_loss': 0.0,
                'bets_won': 0,
                'bets_lost': 0
            }
        
        total_pnl = 0.0
        bets_won_count = 0
        bets_lost_count = 0
        
        for bet in pending_bets:
            # Determine if bet won based on team and action
            # This is simplified - in real scenario, need to match exact bet type
            bet_won = (bet.team == winner and bet.action == 'BACK') or \
                     (bet.team != winner and bet.action == 'LAY')
            
            result = calculate_bet_outcome(bet, bet_won, db)
            total_pnl += result['profit_loss']
            
            if bet_won:
                bets_won_count += 1
            else:
                bets_lost_count += 1
        
        logger.info(f"Match {match_id} completed: {len(pending_bets)} bets closed, "
                   f"Total P&L: ₹{total_pnl:.2f}")
        
        return {
            'bets_closed': len(pending_bets),
            'total_profit_loss': total_pnl,
            'bets_won': bets_won_count,
            'bets_lost': bets_lost_count
        }
        
    except Exception as e:
        logger.error(f"Error closing multiple bets: {e}")
        db.rollback()
        raise


def calculate_potential_profit(stake: float, odds: float) -> float:
    """
    Calculate potential profit for a bet (excluding original stake)
    
    Args:
        stake: Bet amount
        odds: Decimal odds
    
    Returns:
        float: Potential profit
    """
    return stake * (odds - 1)


def calculate_required_odds(stake: float, target_profit: float) -> float:
    """
    Calculate required odds to achieve target profit
    
    Args:
        stake: Bet amount
        target_profit: Desired profit
    
    Returns:
        float: Required decimal odds
    """
    if stake <= 0:
        return 0.0
    return (target_profit / stake) + 1


def get_budget_statistics(budget: BettingBudget) -> dict:
    """
    Get comprehensive statistics for a budget
    
    Args:
        budget: BettingBudget instance
    
    Returns:
        dict: Comprehensive stats
    """
    return {
        'user_id': budget.user_id,
        'current_balance': budget.current_balance,
        'initial_amount': budget.initial_amount,
        'total_profit_loss': budget.total_profit_loss,
        'roi': budget.roi,
        'total_bets_placed': budget.total_bets_placed,
        'total_bets_won': budget.total_bets_won,
        'total_bets_lost': budget.total_bets_lost,
        'win_rate': budget.win_rate,
        'average_bet_size': budget.total_profit_loss / budget.total_bets_placed if budget.total_bets_placed > 0 else 0,
        'created_at': budget.created_at.isoformat(),
        'updated_at': budget.updated_at.isoformat()
    }


def validate_sufficient_balance(budget: BettingBudget, stake: float) -> tuple[bool, str]:
    """
    Check if budget has sufficient balance for a bet
    
    Args:
        budget: BettingBudget instance
        stake: Proposed bet amount
    
    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if stake <= 0:
        return False, "Stake must be greater than 0"
    
    if stake > budget.current_balance:
        return False, f"Insufficient funds. Required: ₹{stake:.2f}, Available: ₹{budget.current_balance:.2f}"
    
    # Warning if stake is more than 20% of balance
    if stake > budget.current_balance * 0.2:
        return True, f"Warning: Stake is {(stake/budget.current_balance)*100:.1f}% of balance"
    
    return True, "Balance sufficient"

