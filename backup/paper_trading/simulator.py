"""
Paper Trading Simulator
-----------------------
Simulates bet execution and tracks P&L without risking real money.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger
from paper_trading.trade_logger import PaperTradeLogger


class Position:
    """Represents an open paper trading position."""
    
    def __init__(self, signal: Dict, stake: float = 100.0):
        self.signal_id = signal.get("id")
        self.match_id = signal.get("match_id")
        self.match_name = signal.get("match_name")
        self.strategy = signal.get("strategy")
        self.market = signal.get("market")
        self.side = signal.get("side")
        self.entry_odds = signal.get("odds")
        self.stake = stake
        self.confidence = signal.get("confidence")
        self.edge = signal.get("edge")
        self.opened_at = datetime.now()
        self.closed_at: Optional[datetime] = None
        self.outcome: Optional[str] = None
        self.profit: float = 0.0
        self.exit_odds: Optional[float] = None
    
    def close(self, outcome: str, exit_odds: float):
        """Close the position with an outcome."""
        self.closed_at = datetime.now()
        self.outcome = outcome
        self.exit_odds = exit_odds
        
        if outcome == "win":
            self.profit = self.stake * (exit_odds - 1)
        elif outcome == "loss":
            self.profit = -self.stake
        else:  # void
            self.profit = 0.0
        
        logger.info(f"Position closed: {self.strategy} on {self.match_name} - {outcome} (P/L: {self.profit:+.2f})")
    
    def to_dict(self) -> Dict:
        """Convert position to dictionary."""
        return {
            "signal_id": self.signal_id,
            "match_id": self.match_id,
            "match_name": self.match_name,
            "strategy": self.strategy,
            "market": self.market,
            "side": self.side,
            "entry_odds": self.entry_odds,
            "exit_odds": self.exit_odds,
            "stake": self.stake,
            "confidence": self.confidence,
            "edge": self.edge,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "outcome": self.outcome,
            "profit": self.profit,
            "duration_seconds": (self.closed_at - self.opened_at).total_seconds() if self.closed_at else None
        }


class PaperTradingSimulator:
    """Simulates paper trading with realistic execution."""
    
    def __init__(self, initial_balance: float = 10000.0, stake_per_trade: float = 100.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.stake_per_trade = stake_per_trade
        self.positions: Dict[str, Position] = {}  # signal_id -> Position
        self.closed_positions: List[Position] = []
        self.trade_logger = PaperTradeLogger()
        self.max_drawdown = 0.0
        self.peak_balance = initial_balance
        logger.info(f"Paper trading simulator initialized. Balance: ${self.balance:.2f}, Stake: ${self.stake_per_trade:.2f}")
    
    def open_position(self, signal: Dict) -> bool:
        """Open a new paper trading position."""
        signal_id = signal.get("id")
        
        # Check if we already have a position for this signal
        if signal_id in self.positions:
            logger.warning(f"Position already exists for signal {signal_id}")
            return False
        
        # Check if we have enough balance
        if self.balance < self.stake_per_trade:
            logger.warning(f"Insufficient balance for new position. Balance: ${self.balance:.2f}")
            return False
        
        # Create position
        position = Position(signal, self.stake_per_trade)
        self.positions[signal_id] = position
        self.balance -= self.stake_per_trade
        
        # Log to trade logger
        self.trade_logger.log_signal(signal)
        
        logger.info(f"Opened position: {position.strategy} on {position.match_name} @ {position.entry_odds:.2f}")
        return True
    
    def close_position(self, signal_id: str, outcome: str, exit_odds: float) -> bool:
        """Close an existing position."""
        if signal_id not in self.positions:
            logger.warning(f"No position found for signal {signal_id}")
            return False
        
        position = self.positions.pop(signal_id)
        position.close(outcome, exit_odds)
        
        # Update balance
        self.balance += self.stake_per_trade + position.profit
        
        # Track peak and drawdown
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_drawdown = (self.peak_balance - self.balance) / self.peak_balance
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
        
        # Store closed position
        self.closed_positions.append(position)
        
        # Log outcome
        self.trade_logger.log_outcome(signal_id, outcome, position.profit, exit_odds)
        
        return True
    
    def get_stats(self) -> Dict:
        """Get comprehensive statistics."""
        if not self.closed_positions:
            return {
                "total_trades": 0,
                "open_positions": len(self.positions),
                "balance": self.balance,
                "profit": 0.0,
                "roi": 0.0,
                "win_rate": 0.0
            }
        
        wins = [p for p in self.closed_positions if p.outcome == "win"]
        losses = [p for p in self.closed_positions if p.outcome == "loss"]
        
        total_profit = sum(p.profit for p in self.closed_positions)
        avg_win = sum(p.profit for p in wins) / len(wins) if wins else 0
        avg_loss = sum(p.profit for p in losses) / len(losses) if losses else 0
        
        # Strategy breakdown
        strategy_stats = {}
        for position in self.closed_positions:
            if position.strategy not in strategy_stats:
                strategy_stats[position.strategy] = {
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "profit": 0.0
                }
            strategy_stats[position.strategy]["trades"] += 1
            strategy_stats[position.strategy]["profit"] += position.profit
            if position.outcome == "win":
                strategy_stats[position.strategy]["wins"] += 1
            elif position.outcome == "loss":
                strategy_stats[position.strategy]["losses"] += 1
        
        # Calculate win rates for each strategy
        for strategy, stats in strategy_stats.items():
            completed = stats["wins"] + stats["losses"]
            stats["win_rate"] = stats["wins"] / completed if completed > 0 else 0
        
        return {
            "total_trades": len(self.closed_positions),
            "open_positions": len(self.positions),
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "profit": total_profit,
            "roi": (total_profit / self.initial_balance) * 100,
            "win_rate": len(wins) / len(self.closed_positions),
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(sum(p.profit for p in wins) / sum(p.profit for p in losses)) if losses and sum(p.profit for p in losses) != 0 else 0,
            "max_drawdown": self.max_drawdown * 100,
            "sharpe_estimate": self._calculate_sharpe(),
            "strategy_breakdown": strategy_stats
        }
    
    def _calculate_sharpe(self) -> float:
        """Calculate Sharpe ratio estimate."""
        if len(self.closed_positions) < 2:
            return 0.0
        
        returns = [p.profit / self.stake_per_trade for p in self.closed_positions]
        avg_return = sum(returns) / len(returns)
        
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = variance ** 0.5
        
        if std_dev == 0:
            return 0.0
        
        # Annualized Sharpe (assuming ~50 trades per day during cricket season)
        sharpe = (avg_return / std_dev) * (50 ** 0.5)
        return sharpe
    
    def export_positions(self) -> List[Dict]:
        """Export all positions for analysis."""
        return [p.to_dict() for p in self.closed_positions]

