"""
ML Training Data Collector
Collects and stores signal data for ML model training

Phase 2.4: Logs every signal with full context for ML training
"""

import os
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from loguru import logger
import redis
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TrainingDataPoint:
    """A single data point for ML training"""
    # Signal identification
    signal_id: str
    strategy: str
    timestamp: str
    
    # Signal details
    action: str  # BACK or LAY
    team: str
    odds_at_signal: float
    confidence: float
    stake_recommended: float
    edge_window_seconds: int
    reasoning: str
    
    # Match context
    match_id: str
    match_phase: str
    overs: float
    score: int
    wickets: int
    run_rate: float
    required_run_rate: Optional[float]
    
    # Market context
    odds_velocity: float
    previous_odds: float
    is_suspended: bool
    stake_limit: Optional[int]
    
    # Event context
    is_wicket: bool
    is_boundary: bool
    
    # Temporal features
    hour_of_day: int
    day_of_week: int
    
    # Outcome (filled later by backfill script)
    outcome: Optional[str] = None  # win, loss, void
    odds_at_close: Optional[float] = None
    profit_loss: Optional[float] = None
    actual_edge: Optional[float] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return asdict(self)


class MLDataCollector:
    """
    Collects signal data for ML training
    
    Stores data in both:
    - Local CSV/JSON files for easy access
    - Redis for persistence and backfill queries
    
    The Brain (AI Scientist): "Data is the fuel for our ML models.
    Every signal is a potential training sample."
    """
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir or os.getenv('ML_DATA_DIR', 'data/ml_training'))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Redis for persistence
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        # Initialize files
        self._init_csv_file()
        
        # Statistics
        self.stats = {
            'signals_collected': 0,
            'outcomes_backfilled': 0,
            'started_at': datetime.utcnow().isoformat()
        }
        
        logger.info(f"ML Data Collector initialized. Output: {self.output_dir}")
    
    def _init_csv_file(self):
        """Initialize CSV file with headers"""
        self.csv_path = self.output_dir / f"signals_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        
        if not self.csv_path.exists():
            headers = [
                'signal_id', 'strategy', 'timestamp', 'action', 'team',
                'odds_at_signal', 'confidence', 'stake_recommended', 
                'edge_window_seconds', 'match_id', 'match_phase',
                'overs', 'score', 'wickets', 'run_rate', 'required_run_rate',
                'odds_velocity', 'previous_odds', 'is_suspended', 'stake_limit',
                'is_wicket', 'is_boundary', 'hour_of_day', 'day_of_week',
                'outcome', 'odds_at_close', 'profit_loss', 'actual_edge'
            ]
            
            with open(self.csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
            
            logger.info(f"Created new CSV file: {self.csv_path}")
    
    def collect_signal(self, signal, match_state, event: Dict) -> TrainingDataPoint:
        """
        Collect a signal and its context for ML training
        
        Args:
            signal: The generated signal
            match_state: Current match state
            event: The event that triggered the signal
        
        Returns:
            TrainingDataPoint object
        """
        try:
            now = datetime.utcnow()
            
            # Build training data point
            data_point = TrainingDataPoint(
                # Signal identification
                signal_id=signal.signal_id,
                strategy=signal.strategy,
                timestamp=signal.created_at.isoformat() if hasattr(signal.created_at, 'isoformat') else str(signal.created_at),
                
                # Signal details
                action=signal.action,
                team=signal.team,
                odds_at_signal=signal.odds,
                confidence=signal.confidence,
                stake_recommended=signal.stake_recommended,
                edge_window_seconds=signal.edge_window_seconds,
                reasoning=signal.reasoning[:500] if signal.reasoning else '',  # Truncate long reasoning
                
                # Match context
                match_id=match_state.match_id,
                match_phase=match_state.match_phase,
                overs=match_state.overs,
                score=match_state.score,
                wickets=match_state.wickets,
                run_rate=match_state.run_rate,
                required_run_rate=match_state.required_run_rate,
                
                # Market context
                odds_velocity=match_state.odds_velocity(),
                previous_odds=match_state.previous_odds,
                is_suspended=match_state.is_suspended,
                stake_limit=match_state.stake_limit,
                
                # Event context
                is_wicket=event.get('is_wicket', False),
                is_boundary=event.get('is_boundary', False),
                
                # Temporal features
                hour_of_day=now.hour,
                day_of_week=now.weekday()
            )
            
            # Store in CSV
            self._write_to_csv(data_point)
            
            # Store in Redis for backfill queries
            self._store_in_redis(data_point)
            
            # Store in JSON (one file per day for easy loading)
            self._append_to_json(data_point)
            
            self.stats['signals_collected'] += 1
            
            logger.debug(f"📊 ML Data collected for signal {signal.signal_id}")
            
            return data_point
            
        except Exception as e:
            logger.error(f"Error collecting signal data: {e}")
            return None
    
    def _write_to_csv(self, data_point: TrainingDataPoint):
        """Append data point to CSV file"""
        try:
            row = [
                data_point.signal_id, data_point.strategy, data_point.timestamp,
                data_point.action, data_point.team, data_point.odds_at_signal,
                data_point.confidence, data_point.stake_recommended,
                data_point.edge_window_seconds, data_point.match_id,
                data_point.match_phase, data_point.overs, data_point.score,
                data_point.wickets, data_point.run_rate, data_point.required_run_rate,
                data_point.odds_velocity, data_point.previous_odds,
                data_point.is_suspended, data_point.stake_limit,
                data_point.is_wicket, data_point.is_boundary,
                data_point.hour_of_day, data_point.day_of_week,
                data_point.outcome, data_point.odds_at_close,
                data_point.profit_loss, data_point.actual_edge
            ]
            
            with open(self.csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
                
        except Exception as e:
            logger.error(f"Error writing to CSV: {e}")
    
    def _store_in_redis(self, data_point: TrainingDataPoint):
        """Store data point in Redis for backfill queries"""
        try:
            key = f"ml_signal:{data_point.signal_id}"
            self.redis_client.setex(
                key, 
                86400 * 30,  # 30 day TTL
                json.dumps(data_point.to_dict())
            )
            
            # Also add to sorted set for range queries
            self.redis_client.zadd(
                'ml_signals_by_time',
                {data_point.signal_id: datetime.utcnow().timestamp()}
            )
            
        except Exception as e:
            logger.error(f"Error storing in Redis: {e}")
    
    def _append_to_json(self, data_point: TrainingDataPoint):
        """Append to daily JSON file"""
        try:
            json_path = self.output_dir / f"signals_{datetime.utcnow().strftime('%Y%m%d')}.json"
            
            # Load existing data
            existing_data = []
            if json_path.exists():
                with open(json_path, 'r') as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        existing_data = []
            
            # Append new data point
            existing_data.append(data_point.to_dict())
            
            # Write back
            with open(json_path, 'w') as f:
                json.dump(existing_data, f, indent=2, default=str)
                
        except Exception as e:
            logger.error(f"Error appending to JSON: {e}")
    
    def backfill_outcome(self, signal_id: str, outcome: str, odds_at_close: float, profit_loss: float):
        """
        Backfill outcome for a signal
        
        Called when outcome tracker records a result
        """
        try:
            # Update Redis
            key = f"ml_signal:{signal_id}"
            data_json = self.redis_client.get(key)
            
            if data_json:
                data = json.loads(data_json)
                data['outcome'] = outcome
                data['odds_at_close'] = odds_at_close
                data['profit_loss'] = profit_loss
                
                # Calculate actual edge
                if data['odds_at_signal'] and odds_at_close:
                    data['actual_edge'] = (odds_at_close - data['odds_at_signal']) / data['odds_at_signal']
                
                # Store back
                self.redis_client.setex(key, 86400 * 30, json.dumps(data))
                
                self.stats['outcomes_backfilled'] += 1
                logger.info(f"📊 Backfilled outcome for {signal_id}: {outcome}")
            else:
                logger.warning(f"Signal {signal_id} not found for backfill")
                
        except Exception as e:
            logger.error(f"Error backfilling outcome: {e}")
    
    def get_training_data(self, days: int = 7) -> List[Dict]:
        """
        Get training data from the last N days
        
        Returns list of data points with outcomes filled
        """
        try:
            data_points = []
            
            # Get all signal IDs from sorted set
            cutoff = (datetime.utcnow() - timedelta(days=days)).timestamp()
            signal_ids = self.redis_client.zrangebyscore('ml_signals_by_time', cutoff, '+inf')
            
            for signal_id in signal_ids:
                key = f"ml_signal:{signal_id}"
                data_json = self.redis_client.get(key)
                
                if data_json:
                    data = json.loads(data_json)
                    # Only include data points with outcomes
                    if data.get('outcome'):
                        data_points.append(data)
            
            logger.info(f"Retrieved {len(data_points)} training data points with outcomes")
            return data_points
            
        except Exception as e:
            logger.error(f"Error getting training data: {e}")
            return []
    
    def get_stats(self) -> Dict:
        """Get collector statistics"""
        return {
            **self.stats,
            'csv_path': str(self.csv_path),
            'output_dir': str(self.output_dir)
        }


# Import timedelta for the method above
from datetime import timedelta


# Singleton instance
_collector: Optional[MLDataCollector] = None


def get_collector() -> MLDataCollector:
    """Get or create the singleton collector instance"""
    global _collector
    if _collector is None:
        _collector = MLDataCollector()
    return _collector


if __name__ == "__main__":
    # Test the collector
    logger.info("Testing ML Data Collector...")
    
    collector = MLDataCollector(output_dir="data/ml_training_test")
    
    # Mock signal and state
    from dataclasses import dataclass
    
    @dataclass
    class MockSignal:
        signal_id: str = "test_001"
        strategy: str = "panic_rebound"
        action: str = "BACK"
        team: str = "India"
        odds: float = 2.50
        confidence: float = 0.75
        stake_recommended: float = 500
        edge_window_seconds: int = 45
        reasoning: str = "Test signal"
        created_at: datetime = datetime.utcnow()
    
    @dataclass
    class MockState:
        match_id: str = "test_match"
        match_phase: str = "middle"
        overs: float = 12.3
        score: int = 95
        wickets: int = 3
        run_rate: float = 7.7
        required_run_rate: float = 8.5
        previous_odds: float = 2.30
        is_suspended: bool = False
        stake_limit: int = 50000
        
        def odds_velocity(self):
            return 0.087
    
    signal = MockSignal()
    state = MockState()
    event = {'is_wicket': True, 'is_boundary': False}
    
    # Collect the signal
    data_point = collector.collect_signal(signal, state, event)
    
    if data_point:
        logger.success(f"✅ Collected test signal: {data_point.signal_id}")
        logger.info(f"Stats: {collector.get_stats()}")
    else:
        logger.error("❌ Failed to collect test signal")
