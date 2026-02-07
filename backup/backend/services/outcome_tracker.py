"""
Signal Outcome Tracker
Tracks signal outcomes and feeds back to circuit breaker and strategy calibration
"""

import redis
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


class SignalOutcomeTracker:
    """
    Tracks signal outcomes and provides feedback for:
    1. Circuit breaker activation
    2. Strategy performance calibration
    3. Quality gate adjustment
    
    The Ghost: "What we measure, we improve."
    """
    
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        # In-memory tracking
        self.outcomes: Dict[str, Dict] = {}  # signal_id -> outcome data
        self.strategy_stats: Dict[str, Dict] = defaultdict(lambda: {
            'total': 0,
            'wins': 0,
            'losses': 0,
            'profit': 0.0,
            'last_10': []
        })
        
        # Circuit breaker thresholds
        self.max_consecutive_losses = 5
        self.max_session_drawdown_pct = 0.05  # 5%
        self.min_win_rate_threshold = 0.45  # Below 45% triggers warning
        
        # Session tracking
        self.session_start = datetime.utcnow()
        self.session_starting_balance = float(os.getenv('STARTING_BANKROLL', 100000))
        self.session_current_balance = self.session_starting_balance
        
        logger.info("Signal Outcome Tracker initialized")
    
    def record_outcome(
        self,
        signal_id: str,
        strategy: str,
        action: str,
        odds: float,
        stake: float,
        won: bool,
        match_id: str = None,
        match_context: Dict = None
    ) -> Dict:
        """
        Record the outcome of a signal
        
        Returns:
            Dict with outcome summary and any circuit breaker actions
        """
        timestamp = datetime.utcnow()
        
        # Calculate P&L
        if won:
            profit = stake * (odds - 1)
        else:
            profit = -stake
        
        # Store outcome
        outcome_data = {
            'signal_id': signal_id,
            'strategy': strategy,
            'action': action,
            'odds': odds,
            'stake': stake,
            'won': won,
            'profit': profit,
            'match_id': match_id,
            'match_context': match_context or {},
            'timestamp': timestamp.isoformat()
        }
        
        self.outcomes[signal_id] = outcome_data
        
        # Update strategy stats
        stats = self.strategy_stats[strategy]
        stats['total'] += 1
        if won:
            stats['wins'] += 1
        else:
            stats['losses'] += 1
        stats['profit'] += profit
        
        # Track last 10 outcomes for streak detection
        stats['last_10'].append(won)
        if len(stats['last_10']) > 10:
            stats['last_10'].pop(0)
        
        # Update session balance
        self.session_current_balance += profit
        
        # Store in Redis for persistence
        self._persist_outcome(outcome_data)
        
        # Check circuit breaker conditions
        circuit_breaker_action = self._check_circuit_breaker(strategy)
        
        # Calculate running stats
        running_stats = self.get_running_stats()
        
        logger.info(
            f"📊 Outcome recorded: {strategy} - {'WIN' if won else 'LOSS'} "
            f"(P&L: {profit:+.2f}, Session: {self.session_current_balance:.2f})"
        )
        
        return {
            'outcome': outcome_data,
            'strategy_stats': stats,
            'running_stats': running_stats,
            'circuit_breaker': circuit_breaker_action
        }
    
    def _persist_outcome(self, outcome: Dict):
        """Persist outcome to Redis and publish for Cortex"""
        try:
            # Store individual outcome
            key = f"outcome:{outcome['signal_id']}"
            self.redis_client.setex(key, 86400 * 7, json.dumps(outcome))  # 7 day TTL
            
            # Add to outcomes list
            self.redis_client.lpush('signal_outcomes', json.dumps(outcome))
            self.redis_client.ltrim('signal_outcomes', 0, 999)  # Keep last 1000
            
            # Update strategy stats in Redis
            strategy = outcome['strategy']
            stats_key = f"strategy_stats:{strategy}"
            stats = self.strategy_stats[strategy]
            self.redis_client.set(stats_key, json.dumps(stats))
            
            # Phase 1.3: Publish ALL outcomes to signal_outcome_events channel
            # This allows Cortex to track outcomes in real-time
            outcome_event = {
                'type': 'outcome',
                'signal_id': outcome['signal_id'],
                'strategy': outcome['strategy'],
                'won': outcome['won'],
                'profit': outcome['profit'],
                'odds': outcome['odds'],
                'stake': outcome['stake'],
                'timestamp': outcome['timestamp']
            }
            self.redis_client.publish('signal_outcome_events', json.dumps(outcome_event))
            logger.debug(f"📡 Published outcome event for {outcome['signal_id']}")
            
        except Exception as e:
            logger.error(f"Error persisting outcome: {e}")
    
    def _check_circuit_breaker(self, strategy: str) -> Dict:
        """Check if circuit breaker should be triggered"""
        action = {
            'triggered': False,
            'reason': None,
            'severity': 'info'
        }
        
        stats = self.strategy_stats[strategy]
        
        # Check 1: Consecutive losses
        if len(stats['last_10']) >= self.max_consecutive_losses:
            recent = stats['last_10'][-self.max_consecutive_losses:]
            if all(not won for won in recent):
                action = {
                    'triggered': True,
                    'reason': f"{self.max_consecutive_losses} consecutive losses in {strategy}",
                    'severity': 'critical'
                }
                self._publish_circuit_breaker_event(action)
                return action
        
        # Check 2: Session drawdown
        drawdown = (self.session_starting_balance - self.session_current_balance) / self.session_starting_balance
        if drawdown > self.max_session_drawdown_pct:
            action = {
                'triggered': True,
                'reason': f"Session drawdown {drawdown:.1%} exceeds threshold {self.max_session_drawdown_pct:.1%}",
                'severity': 'critical'
            }
            self._publish_circuit_breaker_event(action)
            return action
        
        # Check 3: Win rate warning
        if stats['total'] >= 20:  # Need enough samples
            win_rate = stats['wins'] / stats['total']
            if win_rate < self.min_win_rate_threshold:
                action = {
                    'triggered': False,
                    'reason': f"Win rate {win_rate:.1%} below threshold {self.min_win_rate_threshold:.1%}",
                    'severity': 'warning'
                }
                # Don't trigger but log warning
                logger.warning(f"⚠️ Strategy {strategy} underperforming: {win_rate:.1%} win rate")
        
        return action
    
    def _publish_circuit_breaker_event(self, action: Dict):
        """Publish circuit breaker event to Redis"""
        try:
            event = {
                'type': 'circuit_breaker',
                'action': action,
                'timestamp': datetime.utcnow().isoformat()
            }
            self.redis_client.publish('circuit_breaker_events', json.dumps(event))
            logger.warning(f"🚨 CIRCUIT BREAKER: {action['reason']}")
        except Exception as e:
            logger.error(f"Error publishing circuit breaker event: {e}")
    
    def get_running_stats(self) -> Dict:
        """Get current running statistics"""
        total_outcomes = sum(s['total'] for s in self.strategy_stats.values())
        total_wins = sum(s['wins'] for s in self.strategy_stats.values())
        total_profit = sum(s['profit'] for s in self.strategy_stats.values())
        
        session_duration = (datetime.utcnow() - self.session_start).total_seconds() / 3600  # hours
        
        return {
            'session_start': self.session_start.isoformat(),
            'session_duration_hours': round(session_duration, 2),
            'starting_balance': self.session_starting_balance,
            'current_balance': self.session_current_balance,
            'session_profit': self.session_current_balance - self.session_starting_balance,
            'session_roi': ((self.session_current_balance - self.session_starting_balance) / self.session_starting_balance) * 100,
            'total_signals': total_outcomes,
            'total_wins': total_wins,
            'total_losses': total_outcomes - total_wins,
            'win_rate': (total_wins / total_outcomes * 100) if total_outcomes > 0 else 0,
            'total_profit': total_profit,
            'strategy_breakdown': dict(self.strategy_stats)
        }
    
    def get_strategy_performance(self, strategy: str) -> Dict:
        """Get detailed performance for a specific strategy"""
        stats = self.strategy_stats.get(strategy, {
            'total': 0, 'wins': 0, 'losses': 0, 'profit': 0.0, 'last_10': []
        })
        
        return {
            'strategy': strategy,
            'total_signals': stats['total'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': (stats['wins'] / stats['total'] * 100) if stats['total'] > 0 else 0,
            'total_profit': stats['profit'],
            'avg_profit_per_trade': stats['profit'] / stats['total'] if stats['total'] > 0 else 0,
            'recent_streak': self._calculate_streak(stats['last_10']),
            'last_10_outcomes': ['W' if w else 'L' for w in stats['last_10']]
        }
    
    def _calculate_streak(self, outcomes: List[bool]) -> str:
        """Calculate current streak from outcomes"""
        if not outcomes:
            return "No trades"
        
        current = outcomes[-1]
        count = 0
        
        for outcome in reversed(outcomes):
            if outcome == current:
                count += 1
            else:
                break
        
        return f"{'W' if current else 'L'}{count}"
    
    def get_recent_outcomes(self, limit: int = 20) -> List[Dict]:
        """Get recent outcomes from Redis"""
        try:
            outcomes_json = self.redis_client.lrange('signal_outcomes', 0, limit - 1)
            return [json.loads(o) for o in outcomes_json]
        except Exception as e:
            logger.error(f"Error fetching recent outcomes: {e}")
            return []
    
    def reset_session(self):
        """Reset session statistics"""
        self.session_start = datetime.utcnow()
        self.session_current_balance = self.session_starting_balance
        self.outcomes.clear()
        self.strategy_stats.clear()
        
        logger.info("Session statistics reset")
        
        return {
            'status': 'reset',
            'new_session_start': self.session_start.isoformat(),
            'starting_balance': self.session_starting_balance
        }


# Global instance
outcome_tracker = SignalOutcomeTracker()
