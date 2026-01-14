"""
Database Queries for Analytics
Optimized TimescaleDB queries for pattern analysis
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, text
from backend.models.betting import VirtualBet, BetStatus
from datetime import datetime, timedelta
from typing import Dict, List
from loguru import logger


def get_strategy_performance(db: Session, days: int = 30) -> List[Dict]:
    """
    Get profit/loss performance by strategy
    
    Returns list of strategies with their performance metrics
    """
    try:
        start_date = datetime.utcnow() - timedelta(days=days)
        
        results = db.query(
            VirtualBet.strategy,
            func.count(VirtualBet.id).label('total_bets'),
            func.sum(VirtualBet.stake).label('total_staked'),
            func.sum(VirtualBet.profit_loss).label('total_profit_loss'),
            func.sum(func.case(
                (VirtualBet.status == BetStatus.WON, 1),
                else_=0
            )).label('bets_won'),
            func.sum(func.case(
                (VirtualBet.status == BetStatus.LOST, 1),
                else_=0
            )).label('bets_lost')
        ).filter(
            VirtualBet.placed_at >= start_date,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).group_by(
            VirtualBet.strategy
        ).all()
        
        performance_data = []
        for r in results:
            total_closed = r.bets_won + r.bets_lost
            win_rate = (r.bets_won / total_closed * 100) if total_closed > 0 else 0
            roi = (r.total_profit_loss / r.total_staked * 100) if r.total_staked > 0 else 0
            
            performance_data.append({
                'strategy': r.strategy,
                'total_bets': r.total_bets,
                'total_staked': round(r.total_staked or 0, 2),
                'total_profit_loss': round(r.total_profit_loss or 0, 2),
                'bets_won': r.bets_won,
                'bets_lost': r.bets_lost,
                'win_rate': round(win_rate, 2),
                'roi': round(roi, 2)
            })
        
        return performance_data
        
    except Exception as e:
        logger.error(f"Error in get_strategy_performance: {e}")
        return []


def get_confidence_accuracy(db: Session, days: int = 30) -> List[Dict]:
    """
    Analyze how confidence levels correlate with actual win rate
    
    Groups bets by confidence ranges and calculates actual win rate
    """
    try:
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Get all closed bets
        bets = db.query(
            VirtualBet.confidence,
            VirtualBet.status
        ).filter(
            VirtualBet.placed_at >= start_date,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).all()
        
        # Group by confidence ranges
        confidence_ranges = [
            (0.6, 0.7, '60-70%'),
            (0.7, 0.8, '70-80%'),
            (0.8, 0.9, '80-90%'),
            (0.9, 1.0, '90-100%')
        ]
        
        accuracy_data = []
        
        for min_conf, max_conf, label in confidence_ranges:
            range_bets = [b for b in bets if min_conf <= b.confidence < max_conf]
            
            if range_bets:
                wins = sum(1 for b in range_bets if b.status == BetStatus.WON)
                total = len(range_bets)
                actual_win_rate = (wins / total * 100) if total > 0 else 0
                expected_win_rate = ((min_conf + max_conf) / 2) * 100
                
                accuracy_data.append({
                    'confidence_range': label,
                    'expected_win_rate': round(expected_win_rate, 2),
                    'actual_win_rate': round(actual_win_rate, 2),
                    'accuracy_delta': round(actual_win_rate - expected_win_rate, 2),
                    'sample_size': total
                })
        
        return accuracy_data
        
    except Exception as e:
        logger.error(f"Error in get_confidence_accuracy: {e}")
        return []


def get_timing_patterns(db: Session, days: int = 30) -> List[Dict]:
    """
    Analyze profitability by time of day
    
    Identifies best times to place bets
    """
    try:
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Use TimescaleDB time_bucket if available
        query = text("""
            SELECT 
                EXTRACT(HOUR FROM placed_at) as hour,
                COUNT(*) as total_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as bets_won,
                SUM(profit_loss) as total_profit_loss
            FROM virtual_bets
            WHERE placed_at >= :start_date
              AND status IN ('won', 'lost')
            GROUP BY hour
            ORDER BY hour
        """)
        
        result = db.execute(query, {'start_date': start_date})
        
        timing_data = []
        for row in result:
            total_closed = row.total_bets
            win_rate = (row.bets_won / total_closed * 100) if total_closed > 0 else 0
            
            timing_data.append({
                'hour': int(row.hour),
                'total_bets': row.total_bets,
                'total_profit_loss': round(row.total_profit_loss or 0, 2),
                'win_rate': round(win_rate, 2)
            })
        
        return timing_data
        
    except Exception as e:
        logger.error(f"Error in get_timing_patterns: {e}")
        # Fallback to SQLAlchemy if raw SQL fails
        return []


def get_market_efficiency(db: Session, days: int = 30) -> List[Dict]:
    """
    Analyze which markets are most profitable
    """
    try:
        start_date = datetime.utcnow() - timedelta(days=days)
        
        results = db.query(
            VirtualBet.market,
            func.count(VirtualBet.id).label('total_bets'),
            func.sum(VirtualBet.profit_loss).label('total_profit_loss'),
            func.sum(func.case(
                (VirtualBet.status == BetStatus.WON, 1),
                else_=0
            )).label('bets_won')
        ).filter(
            VirtualBet.placed_at >= start_date,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).group_by(
            VirtualBet.market
        ).all()
        
        market_data = []
        for r in results:
            win_rate = (r.bets_won / r.total_bets * 100) if r.total_bets > 0 else 0
            
            market_data.append({
                'market': r.market,
                'total_bets': r.total_bets,
                'total_profit_loss': round(r.total_profit_loss or 0, 2),
                'win_rate': round(win_rate, 2)
            })
        
        return market_data
        
    except Exception as e:
        logger.error(f"Error in get_market_efficiency: {e}")
        return []


def get_daily_pnl_series(db: Session, days: int = 30) -> List[Dict]:
    """
    Get daily P&L time series for charting
    
    Uses TimescaleDB time_bucket for efficient aggregation
    """
    try:
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Try TimescaleDB time_bucket first
        query = text("""
            SELECT 
                time_bucket('1 day', placed_at) as day,
                SUM(profit_loss) as daily_pnl,
                COUNT(*) as daily_bets
            FROM virtual_bets
            WHERE placed_at >= :start_date
              AND status IN ('won', 'lost')
            GROUP BY day
            ORDER BY day
        """)
        
        result = db.execute(query, {'start_date': start_date})
        
        series_data = []
        for row in result:
            series_data.append({
                'date': row.day.isoformat() if hasattr(row.day, 'isoformat') else str(row.day),
                'profit_loss': round(row.daily_pnl or 0, 2),
                'bets_count': row.daily_bets
            })
        
        return series_data
        
    except Exception as e:
        logger.warning(f"TimescaleDB query failed, using fallback: {e}")
        
        # Fallback to standard SQL
        results = db.query(
            func.date(VirtualBet.placed_at).label('day'),
            func.sum(VirtualBet.profit_loss).label('daily_pnl'),
            func.count(VirtualBet.id).label('daily_bets')
        ).filter(
            VirtualBet.placed_at >= start_date,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).group_by(
            func.date(VirtualBet.placed_at)
        ).order_by(
            func.date(VirtualBet.placed_at)
        ).all()
        
        return [
            {
                'date': r.day.isoformat() if hasattr(r.day, 'isoformat') else str(r.day),
                'profit_loss': round(r.daily_pnl or 0, 2),
                'bets_count': r.daily_bets
            }
            for r in results
        ]

