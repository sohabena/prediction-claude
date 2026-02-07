"""
Circuit Breaker
Risk management system that pauses trading during adverse conditions

Phase 3.4: Added Redis persistence (survives restarts)
"""

import os
import json
import redis
from typing import Dict, Optional
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

load_dotenv()


class CircuitBreaker:
    """
    Circuit breaker for risk management
    
    Opens (stops trading) when:
    1. Session loss exceeds 8% of bankroll
    2. 5 consecutive losses
    3. Win rate drops below 50% (last 30 signals)
    
    Phase 3.4: State is persisted to Redis
    """
    
    REDIS_KEY = "circuit_breaker_state"
    
    def __init__(self, starting_bankroll: int = 100000, redis_client: redis.Redis = None):
        self.starting_bankroll = starting_bankroll
        self.current_bankroll = starting_bankroll
        self.session_pnl = 0.0
        self.losing_streak = 0
        self.winning_streak = 0
        
        # Configuration
        self.max_session_loss = float(os.getenv('MAX_SESSION_LOSS', 0.08))
        self.max_losing_streak = int(os.getenv('MAX_LOSING_STREAK', 5))
        self.min_win_rate = float(os.getenv('MIN_WIN_RATE', 0.50))
        
        # State
        self.is_open = False
        self.open_reason = None
        self.opened_at = None
        
        # History
        self.recent_results = []  # Last 30 results
        self.total_signals = 0
        self.total_wins = 0
        self.total_losses = 0
        
        # Phase 3.4: Redis connection for persistence
        self.redis_client = redis_client
        if self.redis_client is None:
            try:
                self.redis_client = redis.Redis(
                    host=os.getenv('REDIS_HOST', 'localhost'),
                    port=int(os.getenv('REDIS_PORT', 6379)),
                    decode_responses=True
                )
            except Exception as e:
                logger.warning(f"Could not connect to Redis for persistence: {e}")
                self.redis_client = None
        
        # Try to restore state from Redis
        self._restore_state()
        
        logger.info(f"Circuit Breaker initialized with bankroll: {starting_bankroll}")
    
    def record_result(self, won: bool, profit_loss: float):
        """
        Record a signal result and check breakers
        
        Args:
            won: True if signal won, False if lost
            profit_loss: Profit (positive) or loss (negative)
        """
        
        # Update bankroll
        self.current_bankroll += profit_loss
        self.session_pnl += profit_loss
        
        # Update streaks
        if won:
            self.winning_streak += 1
            self.losing_streak = 0
            self.total_wins += 1
        else:
            self.losing_streak += 1
            self.winning_streak = 0
            self.total_losses += 1
        
        self.total_signals += 1
        
        # Update recent results (keep last 30)
        self.recent_results.append(won)
        if len(self.recent_results) > 30:
            self.recent_results.pop(0)
        
        # Check breakers
        self.check_breakers()
        
        # Phase 3.4: Persist state to Redis
        self._persist_state()
        
        # Log result
        result_str = "✅ WON" if won else "❌ LOST"
        logger.info(
            f"{result_str} - P&L: {profit_loss:+.2f}, "
            f"Session P&L: {self.session_pnl:+.2f}, "
            f"Bankroll: {self.current_bankroll:.2f}, "
            f"Streak: {self.losing_streak if not won else self.winning_streak}"
        )
    
    def check_breakers(self):
        """Check if any circuit breaker conditions are met"""
        
        if self.is_open:
            return  # Already open
        
        # Breaker 1: Session loss exceeds threshold
        max_loss = self.starting_bankroll * self.max_session_loss
        if self.session_pnl < -max_loss:
            self.open_circuit(
                f"Session loss {self.session_pnl:.2f} exceeds {max_loss:.2f} "
                f"({self.max_session_loss:.0%} of bankroll)"
            )
            return
        
        # Breaker 2: Consecutive losses
        if self.losing_streak >= self.max_losing_streak:
            self.open_circuit(
                f"{self.losing_streak} consecutive losses "
                f"(threshold: {self.max_losing_streak})"
            )
            return
        
        # Breaker 3: Win rate degradation
        if len(self.recent_results) >= 30:
            recent_win_rate = sum(self.recent_results) / len(self.recent_results)
            if recent_win_rate < self.min_win_rate:
                self.open_circuit(
                    f"Win rate {recent_win_rate:.1%} below threshold "
                    f"{self.min_win_rate:.0%} (last 30 signals)"
                )
                return
    
    def open_circuit(self, reason: str):
        """Open the circuit breaker"""
        self.is_open = True
        self.open_reason = reason
        self.opened_at = datetime.utcnow()
        
        logger.error(f"🚨 CIRCUIT BREAKER OPENED: {reason}")
        logger.error(f"Trading paused. Manual review required.")
        
        # Phase 3.4: Persist state
        self._persist_state()
    
    def close_circuit(self, manual: bool = True):
        """Close the circuit breaker (resume trading)"""
        if not self.is_open:
            return
        
        self.is_open = False
        self.open_reason = None
        self.opened_at = None
        close_reason = "Manual reset" if manual else "Automatic reset"
        
        logger.info(f"✅ Circuit breaker closed: {close_reason}")
        logger.info(f"Trading resumed. Session P&L: {self.session_pnl:+.2f}")
        
        # Reset session stats
        self.session_pnl = 0.0
        self.losing_streak = 0
        self.winning_streak = 0
        
        # Phase 3.4: Persist state
        self._persist_state()
    
    def reset_session(self):
        """Reset session statistics"""
        self.session_pnl = 0.0
        self.losing_streak = 0
        self.winning_streak = 0
        logger.info("Session statistics reset")
    
    def calculate_win_rate(self, last_n: int = None) -> float:
        """Calculate win rate"""
        if last_n:
            results = self.recent_results[-last_n:]
        else:
            if self.total_signals == 0:
                return 0.0
            return self.total_wins / self.total_signals
        
        if not results:
            return 0.0
        
        return sum(results) / len(results)
    
    def get_stats(self) -> Dict:
        """Get circuit breaker statistics"""
        return {
            'is_open': self.is_open,
            'open_reason': self.open_reason,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'starting_bankroll': self.starting_bankroll,
            'current_bankroll': self.current_bankroll,
            'session_pnl': self.session_pnl,
            'losing_streak': self.losing_streak,
            'winning_streak': self.winning_streak,
            'total_signals': self.total_signals,
            'total_wins': self.total_wins,
            'total_losses': self.total_losses,
            'overall_win_rate': self.calculate_win_rate(),
            'recent_win_rate': self.calculate_win_rate(last_n=30) if len(self.recent_results) >= 30 else None
        }
    
    def _persist_state(self):
        """Phase 3.4: Persist state to Redis"""
        if not self.redis_client:
            return
        
        try:
            state = {
                'is_open': self.is_open,
                'open_reason': self.open_reason,
                'opened_at': self.opened_at.isoformat() if self.opened_at else None,
                'current_bankroll': self.current_bankroll,
                'session_pnl': self.session_pnl,
                'losing_streak': self.losing_streak,
                'winning_streak': self.winning_streak,
                'recent_results': self.recent_results,
                'total_signals': self.total_signals,
                'total_wins': self.total_wins,
                'total_losses': self.total_losses,
                'last_updated': datetime.utcnow().isoformat()
            }
            
            self.redis_client.set(self.REDIS_KEY, json.dumps(state))
            
            # Also publish state change for frontend
            self.redis_client.publish('circuit_breaker_state', json.dumps(state))
            
            logger.debug(f"Circuit breaker state persisted to Redis")
            
        except Exception as e:
            logger.error(f"Error persisting circuit breaker state: {e}")
    
    def _restore_state(self):
        """Phase 3.4: Restore state from Redis"""
        if not self.redis_client:
            return
        
        try:
            state_json = self.redis_client.get(self.REDIS_KEY)
            
            if state_json:
                state = json.loads(state_json)
                
                self.is_open = state.get('is_open', False)
                self.open_reason = state.get('open_reason')
                
                opened_at = state.get('opened_at')
                if opened_at:
                    self.opened_at = datetime.fromisoformat(opened_at)
                
                self.current_bankroll = state.get('current_bankroll', self.starting_bankroll)
                self.session_pnl = state.get('session_pnl', 0.0)
                self.losing_streak = state.get('losing_streak', 0)
                self.winning_streak = state.get('winning_streak', 0)
                self.recent_results = state.get('recent_results', [])
                self.total_signals = state.get('total_signals', 0)
                self.total_wins = state.get('total_wins', 0)
                self.total_losses = state.get('total_losses', 0)
                
                logger.info(
                    f"Circuit breaker state restored from Redis. "
                    f"is_open={self.is_open}, session_pnl={self.session_pnl:+.2f}, "
                    f"total_signals={self.total_signals}"
                )
            else:
                logger.info("No circuit breaker state in Redis, starting fresh")
                
        except Exception as e:
            logger.warning(f"Could not restore circuit breaker state: {e}")

