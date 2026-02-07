"""
Paper Trade Logger
------------------
Logs all paper trades with full context for analysis.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from loguru import logger


class PaperTradeLogger:
    """Logs paper trades to JSON files for later analysis."""
    
    def __init__(self, storage_dir: str = "paper_trading/storage"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.current_session = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_file = self.storage_dir / f"session_{self.current_session}.json"
        self.trades: List[Dict] = []
        logger.info(f"Paper trade logger initialized. Session: {self.current_session}")
    
    def log_signal(self, signal: Dict):
        """Log a signal that generated a paper trade."""
        trade_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "signal_received",
            "signal_id": signal.get("id"),
            "match_id": signal.get("match_id"),
            "match_name": signal.get("match_name"),
            "strategy": signal.get("strategy"),
            "market": signal.get("market"),
            "side": signal.get("side"),
            "entry_odds": signal.get("odds"),
            "confidence": signal.get("confidence"),
            "edge": signal.get("edge"),
            "gate_scores": signal.get("gate_scores", {}),
            "context": signal.get("context", {})
        }
        self.trades.append(trade_entry)
        self._save()
        logger.info(f"Logged signal: {signal.get('strategy')} on {signal.get('match_name')}")
    
    def log_outcome(self, signal_id: str, outcome: str, profit: float, actual_odds: float):
        """Log the outcome of a paper trade."""
        outcome_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "trade_outcome",
            "signal_id": signal_id,
            "outcome": outcome,  # "win", "loss", or "void"
            "profit": profit,
            "actual_odds": actual_odds
        }
        self.trades.append(outcome_entry)
        self._save()
        logger.info(f"Logged outcome for signal {signal_id}: {outcome} (P/L: {profit:+.2f})")
    
    def log_market_data(self, match_id: str, market: str, odds_snapshot: Dict):
        """Log market data snapshot for validation."""
        market_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "market_snapshot",
            "match_id": match_id,
            "market": market,
            "odds": odds_snapshot
        }
        self.trades.append(market_entry)
        self._save()
    
    def _save(self):
        """Save trades to disk."""
        try:
            with open(self.session_file, 'w') as f:
                json.dump({
                    "session": self.current_session,
                    "started_at": self.trades[0]["timestamp"] if self.trades else None,
                    "trade_count": len([t for t in self.trades if t["type"] == "signal_received"]),
                    "trades": self.trades
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper trades: {e}")
    
    def get_session_stats(self) -> Dict:
        """Get statistics for the current session."""
        signals = [t for t in self.trades if t["type"] == "signal_received"]
        outcomes = [t for t in self.trades if t["type"] == "trade_outcome"]
        
        wins = [o for o in outcomes if o["outcome"] == "win"]
        losses = [o for o in outcomes if o["outcome"] == "loss"]
        
        total_profit = sum(o["profit"] for o in outcomes)
        
        return {
            "session": self.current_session,
            "signals_count": len(signals),
            "completed_trades": len(outcomes),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(outcomes) if outcomes else 0,
            "total_profit": total_profit,
            "avg_profit_per_trade": total_profit / len(outcomes) if outcomes else 0,
            "roi": (total_profit / len(outcomes)) * 100 if outcomes else 0
        }
    
    def load_session(self, session_id: str) -> Optional[Dict]:
        """Load a previous session for analysis."""
        session_file = self.storage_dir / f"session_{session_id}.json"
        if session_file.exists():
            with open(session_file, 'r') as f:
                return json.load(f)
        return None
    
    def list_sessions(self) -> List[str]:
        """List all available sessions."""
        sessions = []
        for file in self.storage_dir.glob("session_*.json"):
            sessions.append(file.stem.replace("session_", ""))
        return sorted(sessions, reverse=True)

