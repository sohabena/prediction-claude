"""
Odds History Tracking System
Stores odds movements at 5-second intervals for ML training and analysis

The Math (Quant Analyst): "Odds velocity and acceleration are predictive gold.
We need granular time-series data to train LSTM models on market microstructure."

The Ghost (Data Engineer): "This is our competitive moat. Competitors without
historical odds data can't build sophisticated momentum models."
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from loguru import logger
import redis
import json
from sqlalchemy import Column, Integer, Float, String, DateTime, Index
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.models.betting import Base
from backend.db.connection import get_db_context


@dataclass
class OddsSnapshot:
    """Single odds observation at a point in time"""
    match_id: str
    market: str  # match_odds, innings_runs, over_under, etc.
    selection: str  # Team name or outcome
    odds: float
    
    # Liquidity indicators (if available from exchange data)
    back_liquidity: Optional[float] = None  # Amount available to back
    lay_liquidity: Optional[float] = None  # Amount available to lay
    
    # Metadata
    timestamp: datetime = None
    source: str = "micro999"  # Data source
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    @property
    def snapshot_key(self) -> str:
        """Unique key for this snapshot"""
        return f"{self.match_id}:{self.market}:{self.selection}"


class OddsSnapshotModel(Base):
    """
    SQLAlchemy model for storing odds snapshots
    
    Optimized for time-series queries with TimescaleDB
    """
    __tablename__ = 'odds_snapshots'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(100), nullable=False, index=True)
    market = Column(String(50), nullable=False)
    selection = Column(String(100), nullable=False)
    
    odds = Column(Float, nullable=False)
    back_liquidity = Column(Float, nullable=True)
    lay_liquidity = Column(Float, nullable=True)
    
    timestamp = Column(DateTime, nullable=False, index=True)
    source = Column(String(20), default='micro999')
    
    # Create composite index for efficient querying
    __table_args__ = (
        Index('idx_odds_match_time', 'match_id', 'timestamp'),
        Index('idx_odds_market_selection', 'market', 'selection'),
    )


@dataclass
class OddsMovement:
    """Calculated odds movement metrics"""
    match_id: str
    market: str
    selection: str
    
    # Current state
    current_odds: float
    previous_odds: float
    
    # Movement metrics
    odds_change: float  # Absolute change
    odds_change_percent: float  # Percentage change
    
    # Velocity and acceleration
    velocity: float  # Change per second
    acceleration: float  # Change in velocity
    
    # Time context
    time_window_seconds: int
    measured_at: datetime
    
    @property
    def direction(self) -> str:
        """Movement direction"""
        if self.odds_change > 0.01:
            return "drifting"  # Odds increasing (selection less likely)
        elif self.odds_change < -0.01:
            return "shortening"  # Odds decreasing (selection more likely)
        return "stable"
    
    @property
    def magnitude(self) -> str:
        """Movement magnitude"""
        abs_change_pct = abs(self.odds_change_percent)
        if abs_change_pct > 10:
            return "dramatic"
        elif abs_change_pct > 5:
            return "significant"
        elif abs_change_pct > 2:
            return "moderate"
        return "minor"
    
    @property
    def is_sharp_move(self) -> bool:
        """Detect sharp movement (potential whale bet or insider info)"""
        # Sharp = > 5% move in < 60 seconds
        return abs(self.odds_change_percent) > 5 and self.time_window_seconds < 60


class OddsHistoryTracker:
    """
    Tracks and analyzes odds movements in real-time
    
    Architecture:
    - Redis for real-time caching (last 1 hour)
    - TimescaleDB for long-term storage (compressed)
    - Celery tasks for periodic snapshots
    
    The Architect (Full-Stack): "This needs to handle 1000s of markets
    simultaneously. Use Redis for hot data, Postgres for cold storage."
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.snapshot_interval = 5  # seconds
        self.running = False
        
        # Redis key patterns
        self.SNAPSHOT_KEY = "odds:snapshot:{match_id}:{market}:{selection}"
        self.HISTORY_KEY = "odds:history:{match_id}:{market}:{selection}"
        self.HISTORY_TTL = 3600  # Keep 1 hour in Redis
    
    async def record_snapshot(self, snapshot: OddsSnapshot, db: AsyncSession):
        """
        Record a single odds snapshot
        
        Args:
            snapshot: OddsSnapshot to record
            db: Database session for persistent storage
        """
        # Store in Redis for real-time access
        redis_key = self.SNAPSHOT_KEY.format(
            match_id=snapshot.match_id,
            market=snapshot.market,
            selection=snapshot.selection
        )
        
        snapshot_data = {
            "odds": snapshot.odds,
            "back_liquidity": snapshot.back_liquidity,
            "lay_liquidity": snapshot.lay_liquidity,
            "timestamp": snapshot.timestamp.isoformat(),
            "source": snapshot.source
        }
        
        self.redis.setex(redis_key, 300, json.dumps(snapshot_data))  # 5 min TTL
        
        # Store in TimescaleDB for historical analysis
        db_snapshot = OddsSnapshotModel(
            match_id=snapshot.match_id,
            market=snapshot.market,
            selection=snapshot.selection,
            odds=snapshot.odds,
            back_liquidity=snapshot.back_liquidity,
            lay_liquidity=snapshot.lay_liquidity,
            timestamp=snapshot.timestamp,
            source=snapshot.source
        )
        
        db.add(db_snapshot)
        await db.commit()
        
        # Append to history list (for velocity calculations)
        history_key = self.HISTORY_KEY.format(
            match_id=snapshot.match_id,
            market=snapshot.market,
            selection=snapshot.selection
        )
        
        history_entry = json.dumps({
            "odds": snapshot.odds,
            "timestamp": snapshot.timestamp.isoformat()
        })
        
        self.redis.lpush(history_key, history_entry)
        self.redis.ltrim(history_key, 0, 719)  # Keep last 720 snapshots (1 hour at 5s intervals)
        self.redis.expire(history_key, self.HISTORY_TTL)
    
    async def record_batch_snapshots(self, snapshots: List[OddsSnapshot], db: AsyncSession):
        """
        Record multiple snapshots efficiently
        
        The Ghost (Data Engineer): "Batch inserts are 10x faster than individual
        inserts. Critical for high-frequency data ingestion."
        """
        if not snapshots:
            return
        
        # Batch insert to database
        db_snapshots = [
            OddsSnapshotModel(
                match_id=s.match_id,
                market=s.market,
                selection=s.selection,
                odds=s.odds,
                back_liquidity=s.back_liquidity,
                lay_liquidity=s.lay_liquidity,
                timestamp=s.timestamp,
                source=s.source
            )
            for s in snapshots
        ]
        
        db.add_all(db_snapshots)
        await db.commit()
        
        logger.info(f"📊 Recorded {len(snapshots)} odds snapshots")
    
    def get_latest_snapshot(self, match_id: str, market: str, selection: str) -> Optional[Dict]:
        """Get most recent odds snapshot from Redis"""
        redis_key = self.SNAPSHOT_KEY.format(
            match_id=match_id,
            market=market,
            selection=selection
        )
        
        data = self.redis.get(redis_key)
        if data:
            return json.loads(data)
        return None
    
    def calculate_movement(
        self,
        match_id: str,
        market: str,
        selection: str,
        time_window_seconds: int = 60
    ) -> Optional[OddsMovement]:
        """
        Calculate odds movement over a time window
        
        The Math (Quant Analyst): "Velocity and acceleration capture momentum.
        Sharp moves (high velocity) often precede value opportunities."
        
        Args:
            match_id: Match identifier
            market: Market type
            selection: Team or outcome
            time_window_seconds: Time window for movement calculation
        
        Returns:
            OddsMovement with velocity and acceleration metrics
        """
        history_key = self.HISTORY_KEY.format(
            match_id=match_id,
            market=market,
            selection=selection
        )
        
        # Get history from Redis (newest first)
        history_raw = self.redis.lrange(history_key, 0, -1)
        if len(history_raw) < 2:
            return None
        
        # Parse history
        history = []
        for item in history_raw:
            data = json.loads(item)
            history.append({
                "odds": data["odds"],
                "timestamp": datetime.fromisoformat(data["timestamp"])
            })
        
        # Current odds (most recent)
        current = history[0]
        current_time = current["timestamp"]
        current_odds = current["odds"]
        
        # Find comparison point (closest to time_window_seconds ago)
        target_time = current_time - timedelta(seconds=time_window_seconds)
        
        previous = history[-1]  # Default to oldest
        for snapshot in history:
            if snapshot["timestamp"] <= target_time:
                previous = snapshot
                break
        
        previous_odds = previous["odds"]
        time_diff = (current_time - previous["timestamp"]).total_seconds()
        
        if time_diff == 0:
            return None
        
        # Calculate movement metrics
        odds_change = current_odds - previous_odds
        odds_change_percent = (odds_change / previous_odds) * 100 if previous_odds > 0 else 0
        velocity = odds_change / time_diff  # Change per second
        
        # Calculate acceleration (if we have enough history)
        acceleration = 0.0
        if len(history) >= 3:
            mid_point = history[len(history) // 2]
            mid_odds = mid_point["odds"]
            mid_time = mid_point["timestamp"]
            
            # Velocity in first half vs. second half
            v1 = (mid_odds - previous_odds) / (mid_time - previous["timestamp"]).total_seconds()
            v2 = (current_odds - mid_odds) / (current_time - mid_time).total_seconds()
            
            acceleration = (v2 - v1) / time_diff if time_diff > 0 else 0
        
        movement = OddsMovement(
            match_id=match_id,
            market=market,
            selection=selection,
            current_odds=current_odds,
            previous_odds=previous_odds,
            odds_change=odds_change,
            odds_change_percent=odds_change_percent,
            velocity=velocity,
            acceleration=acceleration,
            time_window_seconds=int(time_diff),
            measured_at=current_time
        )
        
        return movement
    
    async def get_historical_odds(
        self,
        match_id: str,
        market: str,
        selection: str,
        start_time: datetime,
        end_time: datetime,
        db: AsyncSession
    ) -> List[Tuple[datetime, float]]:
        """
        Retrieve historical odds from database for ML training
        
        Returns:
            List of (timestamp, odds) tuples
        """
        query = select(OddsSnapshotModel).where(
            OddsSnapshotModel.match_id == match_id,
            OddsSnapshotModel.market == market,
            OddsSnapshotModel.selection == selection,
            OddsSnapshotModel.timestamp >= start_time,
            OddsSnapshotModel.timestamp <= end_time
        ).order_by(OddsSnapshotModel.timestamp)
        
        result = await db.execute(query)
        snapshots = result.scalars().all()
        
        return [(s.timestamp, s.odds) for s in snapshots]
    
    async def detect_anomalies(
        self,
        match_id: str,
        market: str,
        selection: str,
        threshold_percent: float = 10.0
    ) -> List[Dict]:
        """
        Detect anomalous odds movements (potential whale bets, insider info)
        
        The House (Bookmaker): "Sharp odds moves = smart money. When odds
        crash 10% in 30 seconds, someone knows something. Either follow or fade."
        
        Args:
            threshold_percent: Movement % to trigger anomaly alert
        
        Returns:
            List of anomaly events with details
        """
        history_key = self.HISTORY_KEY.format(
            match_id=match_id,
            market=market,
            selection=selection
        )
        
        history_raw = self.redis.lrange(history_key, 0, -1)
        if len(history_raw) < 2:
            return []
        
        history = [json.loads(item) for item in history_raw]
        anomalies = []
        
        # Check each consecutive pair for sharp moves
        for i in range(len(history) - 1):
            current = history[i]
            previous = history[i + 1]
            
            odds_change_pct = ((current["odds"] - previous["odds"]) / previous["odds"]) * 100
            
            if abs(odds_change_pct) > threshold_percent:
                anomalies.append({
                    "timestamp": current["timestamp"],
                    "previous_odds": previous["odds"],
                    "current_odds": current["odds"],
                    "change_percent": odds_change_pct,
                    "direction": "shortening" if odds_change_pct < 0 else "drifting",
                    "magnitude": "dramatic" if abs(odds_change_pct) > 20 else "significant"
                })
        
        return anomalies


class OddsSnapshotScheduler:
    """
    Background scheduler for continuous odds snapshots
    
    The Architect (Full-Stack): "Use Celery Beat for scheduling, workers for
    execution. This must run 24/7 without manual intervention."
    """
    
    def __init__(self, tracker: OddsHistoryTracker):
        self.tracker = tracker
        self.running = False
    
    async def start_continuous_snapshots(self, scraper_data_source):
        """
        Start continuous odds snapshots from scraper data
        
        Args:
            scraper_data_source: Source of odds data (e.g., Micro999 scraper)
        """
        self.running = True
        logger.info("🚀 Starting continuous odds snapshot scheduler (5-second intervals)")
        
        while self.running:
            try:
                # Get current odds from scraper
                current_odds = await scraper_data_source.get_all_current_odds()
                
                # Convert to OddsSnapshot objects
                snapshots = []
                for odds_data in current_odds:
                    snapshot = OddsSnapshot(
                        match_id=odds_data['match_id'],
                        market=odds_data['market'],
                        selection=odds_data['selection'],
                        odds=odds_data['odds'],
                        back_liquidity=odds_data.get('back_liquidity'),
                        lay_liquidity=odds_data.get('lay_liquidity')
                    )
                    snapshots.append(snapshot)
                
                # Record batch
                async with get_db_context() as db:
                    await self.tracker.record_batch_snapshots(snapshots, db)
                
                # Wait for next interval
                await asyncio.sleep(5)
            
            except Exception as e:
                logger.error(f"❌ Odds snapshot scheduler error: {e}")
                await asyncio.sleep(5)
    
    def stop(self):
        """Stop the continuous snapshots"""
        self.running = False
        logger.info("🛑 Odds snapshot scheduler stopped")


# Example usage
if __name__ == "__main__":
    async def test_odds_tracker():
        """Test odds history tracking"""
        import redis
        from backend.db.connection import engine
        
        # Initialize
        redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        tracker = OddsHistoryTracker(redis_client)
        
        # Simulate odds snapshots
        match_id = "TEST-MATCH-001"
        market = "match_odds"
        selection = "India"
        
        print("\n📊 Simulating odds movements...")
        
        async with get_db_context() as db:
            # Simulate 20 snapshots with varying odds
            base_odds = 2.50
            for i in range(20):
                # Add some random movement
                import random
                movement = random.uniform(-0.05, 0.05)
                odds = base_odds + movement * i
                
                snapshot = OddsSnapshot(
                    match_id=match_id,
                    market=market,
                    selection=selection,
                    odds=odds,
                    timestamp=datetime.utcnow() - timedelta(seconds=(20-i) * 5)
                )
                
                await tracker.record_snapshot(snapshot, db)
                print(f"   Snapshot {i+1}: {odds:.3f}")
                await asyncio.sleep(0.1)
            
            # Calculate movement
            print("\n📈 Calculating odds movement...")
            movement = tracker.calculate_movement(match_id, market, selection, time_window_seconds=60)
            
            if movement:
                print(f"   Current Odds: {movement.current_odds:.3f}")
                print(f"   Previous Odds: {movement.previous_odds:.3f}")
                print(f"   Change: {movement.odds_change:+.3f} ({movement.odds_change_percent:+.2f}%)")
                print(f"   Direction: {movement.direction}")
                print(f"   Magnitude: {movement.magnitude}")
                print(f"   Velocity: {movement.velocity:.4f} per second")
                print(f"   Is Sharp Move: {movement.is_sharp_move}")
    
    # Run test
    asyncio.run(test_odds_tracker())

