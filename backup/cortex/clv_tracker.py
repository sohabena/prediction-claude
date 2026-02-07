"""
CLV (Closing Line Value) Tracker
Tracks edge validation by comparing entry odds to closing odds

Phase 3.1: Edge validation via CLV tracking

CLV Formula: ((entry_odds / closing_odds) - 1) * 100
- Positive CLV = We beat the closing line (edge confirmed)
- Negative CLV = Closing line moved against us

The Math (Quant Analyst): "CLV is the gold standard for measuring
true betting edge. If we consistently beat the closing line, we have
a long-term edge regardless of short-term variance."
"""

import asyncio
import json
import os
import redis
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import deque
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


@dataclass
class CLVRecord:
    """Record of CLV for a single signal"""
    signal_id: str
    strategy: str
    entry_odds: float
    closing_odds: float
    clv_percentage: float
    action: str
    match_id: str
    timestamp: str
    edge_window_seconds: int
    signal_confidence: float
    
    def to_dict(self) -> Dict:
        return asdict(self)


class CLVTracker:
    """
    Tracks Closing Line Value for signals
    
    Flow:
    1. When signal is generated, store entry odds and expiry time
    2. After edge_window expires, capture closing odds from market
    3. Calculate CLV and store for analysis
    
    CLV is the best indicator of true edge - better than win rate
    because it removes variance.
    """
    
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        # Pending signals waiting for closing odds
        # {signal_id: {signal_data, expiry_time, match_id}}
        self.pending_signals: Dict[str, Dict] = {}
        
        # Recent CLV records for quick stats
        self.recent_clv: deque = deque(maxlen=100)
        
        # Statistics
        self.stats = {
            'signals_tracked': 0,
            'clv_calculated': 0,
            'positive_clv': 0,
            'negative_clv': 0,
            'total_clv': 0.0,
            'started_at': datetime.utcnow().isoformat()
        }
        
        self.is_running = False
        
        logger.info("📊 CLV Tracker initialized")
    
    def register_signal(self, signal_data: Dict):
        """
        Register a signal for CLV tracking
        
        Args:
            signal_data: Signal dictionary with odds, strategy, etc.
        """
        try:
            signal_id = signal_data.get('signal_id')
            if not signal_id:
                return
            
            # Calculate expiry time
            edge_window = signal_data.get('edge_window_seconds', 45)
            expiry_time = datetime.utcnow() + timedelta(seconds=edge_window)
            
            # Store pending signal
            self.pending_signals[signal_id] = {
                'signal_data': signal_data,
                'expiry_time': expiry_time,
                'match_id': signal_data.get('match_context', {}).get('match_id', 'unknown'),
                'entry_odds': signal_data.get('odds', 0),
                'strategy': signal_data.get('strategy', 'unknown'),
                'action': signal_data.get('action', 'unknown'),
                'confidence': signal_data.get('confidence', 0)
            }
            
            # Also store in Redis for persistence
            redis_key = f"clv_pending:{signal_id}"
            self.redis_client.setex(
                redis_key,
                edge_window + 60,  # TTL = edge window + 1 minute buffer
                json.dumps({
                    'signal_id': signal_id,
                    'entry_odds': signal_data.get('odds', 0),
                    'match_id': signal_data.get('match_context', {}).get('match_id', 'unknown'),
                    'expiry_time': expiry_time.isoformat(),
                    'strategy': signal_data.get('strategy', 'unknown'),
                    'action': signal_data.get('action', 'unknown'),
                    'confidence': signal_data.get('confidence', 0),
                    'edge_window_seconds': edge_window
                })
            )
            
            self.stats['signals_tracked'] += 1
            logger.debug(f"📊 CLV: Registered signal {signal_id} (expires {edge_window}s)")
            
        except Exception as e:
            logger.error(f"Error registering signal for CLV: {e}")
    
    def update_closing_odds(self, match_id: str, current_odds: float):
        """
        Update closing odds for a match
        
        Called when market data is received. Checks if any pending
        signals have expired and calculates their CLV.
        
        Args:
            match_id: Match identifier
            current_odds: Current market odds
        """
        try:
            now = datetime.utcnow()
            expired_signals = []
            
            # Check for expired signals
            for signal_id, data in self.pending_signals.items():
                if data['match_id'] == match_id and now >= data['expiry_time']:
                    expired_signals.append((signal_id, data))
            
            # Process expired signals
            for signal_id, data in expired_signals:
                self._calculate_and_store_clv(signal_id, data, current_odds)
                del self.pending_signals[signal_id]
            
        except Exception as e:
            logger.error(f"Error updating closing odds: {e}")
    
    def _calculate_and_store_clv(self, signal_id: str, signal_data: Dict, closing_odds: float):
        """
        Calculate CLV and store the record
        
        CLV = ((entry_odds / closing_odds) - 1) * 100
        
        For BACK bets: Lower closing odds = positive CLV (we got better odds)
        For LAY bets: Higher closing odds = positive CLV (we got better odds)
        """
        try:
            entry_odds = signal_data['entry_odds']
            action = signal_data['action']
            
            if entry_odds <= 0 or closing_odds <= 0:
                logger.warning(f"Invalid odds for CLV calculation: entry={entry_odds}, close={closing_odds}")
                return
            
            # Calculate CLV
            if action == 'BACK':
                # For BACK: we want entry_odds > closing_odds
                # Positive CLV if we got better odds than closing
                clv = ((entry_odds / closing_odds) - 1) * 100
            else:  # LAY
                # For LAY: we want entry_odds < closing_odds
                # Positive CLV if closing odds are higher (worse for backers)
                clv = ((closing_odds / entry_odds) - 1) * 100
            
            # Create CLV record
            record = CLVRecord(
                signal_id=signal_id,
                strategy=signal_data['strategy'],
                entry_odds=entry_odds,
                closing_odds=closing_odds,
                clv_percentage=round(clv, 2),
                action=action,
                match_id=signal_data['match_id'],
                timestamp=datetime.utcnow().isoformat(),
                edge_window_seconds=signal_data.get('edge_window_seconds', 45),
                signal_confidence=signal_data.get('confidence', 0)
            )
            
            # Store record
            self._store_clv_record(record)
            
            # Update statistics
            self.stats['clv_calculated'] += 1
            self.stats['total_clv'] += clv
            
            if clv > 0:
                self.stats['positive_clv'] += 1
                logger.info(f"✅ CLV: {signal_id} = +{clv:.2f}% (POSITIVE EDGE)")
            else:
                self.stats['negative_clv'] += 1
                logger.warning(f"⚠️ CLV: {signal_id} = {clv:.2f}% (negative)")
            
            # Add to recent records
            self.recent_clv.append(record)
            
        except Exception as e:
            logger.error(f"Error calculating CLV: {e}")
    
    def _store_clv_record(self, record: CLVRecord):
        """Store CLV record in Redis"""
        try:
            # Store individual record
            key = f"clv_record:{record.signal_id}"
            self.redis_client.setex(
                key,
                86400 * 30,  # 30 days TTL
                json.dumps(record.to_dict())
            )
            
            # Add to sorted set for range queries
            self.redis_client.zadd(
                'clv_by_time',
                {record.signal_id: datetime.utcnow().timestamp()}
            )
            
            # Add to strategy-specific set
            self.redis_client.zadd(
                f'clv_by_strategy:{record.strategy}',
                {record.signal_id: record.clv_percentage}
            )
            
            # Publish CLV event
            self.redis_client.publish('clv_events', json.dumps({
                'type': 'clv_calculated',
                **record.to_dict()
            }))
            
        except Exception as e:
            logger.error(f"Error storing CLV record: {e}")
    
    def get_clv_stats(self) -> Dict:
        """Get CLV statistics"""
        total_signals = self.stats['clv_calculated']
        
        if total_signals == 0:
            avg_clv = 0
            positive_rate = 0
        else:
            avg_clv = self.stats['total_clv'] / total_signals
            positive_rate = self.stats['positive_clv'] / total_signals
        
        return {
            **self.stats,
            'pending_signals': len(self.pending_signals),
            'average_clv': round(avg_clv, 2),
            'positive_clv_rate': round(positive_rate, 4),
            'recent_clv_count': len(self.recent_clv)
        }
    
    def get_clv_by_strategy(self) -> Dict[str, Dict]:
        """Get CLV breakdown by strategy"""
        try:
            strategies = ['panic_rebound', 'mean_reversion', 'whale_shadow', 
                         'odds_velocity', 'simple_odds_change']
            
            results = {}
            
            for strategy in strategies:
                key = f'clv_by_strategy:{strategy}'
                
                # Get all CLV values for this strategy
                clv_data = self.redis_client.zrange(key, 0, -1, withscores=True)
                
                if clv_data:
                    clv_values = [score for _, score in clv_data]
                    results[strategy] = {
                        'count': len(clv_values),
                        'average_clv': round(sum(clv_values) / len(clv_values), 2),
                        'positive_count': len([v for v in clv_values if v > 0]),
                        'positive_rate': round(len([v for v in clv_values if v > 0]) / len(clv_values), 4),
                        'max_clv': round(max(clv_values), 2),
                        'min_clv': round(min(clv_values), 2)
                    }
                else:
                    results[strategy] = {
                        'count': 0,
                        'average_clv': 0,
                        'positive_count': 0,
                        'positive_rate': 0,
                        'max_clv': 0,
                        'min_clv': 0
                    }
            
            return results
            
        except Exception as e:
            logger.error(f"Error getting CLV by strategy: {e}")
            return {}
    
    def get_recent_clv(self, limit: int = 20) -> List[Dict]:
        """Get recent CLV records"""
        records = list(self.recent_clv)[-limit:]
        return [r.to_dict() for r in records]


# Singleton instance
_clv_tracker: Optional[CLVTracker] = None


def get_clv_tracker() -> CLVTracker:
    """Get or create singleton CLV tracker"""
    global _clv_tracker
    if _clv_tracker is None:
        _clv_tracker = CLVTracker()
    return _clv_tracker


if __name__ == "__main__":
    # Test the CLV tracker
    logger.info("Testing CLV Tracker...")
    
    tracker = CLVTracker()
    
    # Simulate a signal
    test_signal = {
        'signal_id': 'test_clv_001',
        'strategy': 'panic_rebound',
        'odds': 2.50,
        'action': 'BACK',
        'confidence': 0.75,
        'edge_window_seconds': 5,  # Short for testing
        'match_context': {'match_id': 'test_match'}
    }
    
    # Register signal
    tracker.register_signal(test_signal)
    logger.info(f"Registered signal. Stats: {tracker.get_clv_stats()}")
    
    # Wait for expiry
    import time
    time.sleep(6)
    
    # Simulate closing odds (lower = good for BACK)
    tracker.update_closing_odds('test_match', 2.30)
    
    # Check results
    logger.info(f"Final stats: {tracker.get_clv_stats()}")
    logger.info(f"Recent CLV: {tracker.get_recent_clv()}")

