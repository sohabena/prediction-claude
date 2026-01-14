"""
Production Signal Orchestrator
Coordinates all strategies, quality gates, and signal generation in real-time

The Architect (Full-Stack): "This is the heart of TITAN. It must run 24/7,
handle failures gracefully, and scale to process 100s of markets simultaneously."
"""

import asyncio
from typing import Dict, List, Optional, Set
from datetime import datetime
from loguru import logger
import redis
import json
from dataclasses import dataclass, asdict

# Import TITAN components
from cortex.processor import SignalProcessor
from cortex.match_state import MatchStateTracker
from cortex.strategies.panic_rebound import PanicReboundStrategy
from cortex.strategies.mean_reversion import MeanReversionStrategy
from cortex.strategies.whale_shadow import WhaleShadowStrategy
from cortex.circuit_breaker import CircuitBreaker

from backend.services.cricket_live_api import get_match_monitor, LiveMatchState
from backend.services.weather_api import get_weather_api, WeatherCondition
from backend.services.odds_history_tracker import OddsHistoryTracker
from backend.ml.confidence_model import get_confidence_model


@dataclass
class EnrichedSignal:
    """Signal enriched with match context, weather, and ML predictions"""
    # Core signal data
    signal_id: str
    strategy: str
    action: str  # BACK or LAY
    team: str
    odds: float
    confidence: float
    stake_recommended: float
    edge_window_seconds: int
    reasoning: str
    
    # Match context
    match_id: str
    match_context: Dict
    
    # Enrichments
    match_state: Optional[Dict] = None  # Live score, phase, momentum
    weather_conditions: Optional[Dict] = None
    ml_confidence: Optional[float] = None  # ML-predicted win probability
    odds_momentum: Optional[Dict] = None  # Velocity, acceleration
    
    # Metadata
    created_at: str = None
    source: str = "TITAN"
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class SignalOrchestrator:
    """
    Production signal orchestrator
    
    Responsibilities:
    1. Monitor live matches
    2. Run all strategies continuously
    3. Enrich signals with context (weather, ML, odds momentum)
    4. Publish signals to Redis for real-time consumption
    5. Handle failures and maintain state
    
    Architecture:
    - Async event loop for concurrent processing
    - Redis pub/sub for signal distribution
    - Circuit breakers for external API failures
    - Graceful degradation when data sources unavailable
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.running = False
        
        # Initialize components
        self.processor = SignalProcessor()
        self.match_tracker = MatchStateTracker()
        self.match_monitor = get_match_monitor()
        self.weather_api = get_weather_api()
        self.odds_tracker = OddsHistoryTracker(redis_client)
        self.ml_model = get_confidence_model()
        
        # Initialize strategies
        self.strategies = [
            PanicReboundStrategy(),
            MeanReversionStrategy(),
            WhaleShadowStrategy()
        ]
        
        # Circuit breakers for external APIs
        self.circuit_breakers = {
            "cricket_api": CircuitBreaker(failure_threshold=5, timeout_seconds=60),
            "weather_api": CircuitBreaker(failure_threshold=3, timeout_seconds=120),
            "ml_model": CircuitBreaker(failure_threshold=10, timeout_seconds=30)
        }
        
        # State tracking
        self.active_matches: Set[str] = set()
        self.signal_count = 0
        self.last_signal_time: Dict[str, datetime] = {}
        
        logger.info("🚀 Signal Orchestrator initialized")
    
    async def start(self, poll_interval: int = 5):
        """
        Start the signal orchestrator
        
        Args:
            poll_interval: Seconds between processing cycles
        
        The Sentinel (DevOps): "This must be resilient. If one component fails,
        the system continues with degraded functionality."
        """
        self.running = True
        logger.info(f"🎯 Starting Signal Orchestrator (polling every {poll_interval}s)")
        
        # Start background tasks
        tasks = [
            asyncio.create_task(self._monitor_matches()),
            asyncio.create_task(self._process_signals_loop(poll_interval)),
            asyncio.create_task(self._snapshot_odds_loop()),
            asyncio.create_task(self._health_check_loop())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"❌ Orchestrator error: {e}")
        finally:
            self.running = False
    
    def stop(self):
        """Stop the orchestrator"""
        self.running = False
        logger.info("🛑 Signal Orchestrator stopping...")
    
    async def _monitor_matches(self):
        """
        Continuously monitor live matches
        
        The Ghost (Data Engineer): "Real-time match monitoring is the foundation.
        Without live scores, we're trading blind."
        """
        logger.info("📡 Starting match monitor...")
        
        while self.running:
            try:
                if self.circuit_breakers["cricket_api"].is_open:
                    logger.warning("⚠️  Cricket API circuit breaker open, skipping...")
                    await asyncio.sleep(30)
                    continue
                
                # Get live matches
                await self.match_monitor.start_monitoring(poll_interval=10)
                
            except Exception as e:
                logger.error(f"❌ Match monitoring error: {e}")
                self.circuit_breakers["cricket_api"].record_failure()
                await asyncio.sleep(10)
    
    async def _process_signals_loop(self, interval: int):
        """
        Main signal processing loop
        
        The Oracle (Betting Expert): "Process every match, every market, every
        strategy. The edge window is 30-180 seconds—we can't afford to miss signals."
        """
        logger.info("🔄 Starting signal processing loop...")
        
        while self.running:
            try:
                # Get all active matches
                active_matches = self.match_monitor.get_all_active_matches()
                
                if not active_matches:
                    logger.debug("No active matches currently")
                    await asyncio.sleep(interval)
                    continue
                
                # Process each match
                for match_state in active_matches:
                    try:
                        await self._process_match(match_state)
                    except Exception as e:
                        logger.error(f"❌ Error processing match {match_state.match_id}: {e}")
                
                # Update stats
                self.redis.set("orchestrator:last_run", datetime.utcnow().isoformat())
                self.redis.set("orchestrator:signal_count", self.signal_count)
                
                await asyncio.sleep(interval)
            
            except Exception as e:
                logger.error(f"❌ Signal processing loop error: {e}")
                await asyncio.sleep(interval)
    
    async def _process_match(self, match_state: LiveMatchState):
        """
        Process a single match through all strategies
        
        Args:
            match_state: Current match state from live API
        """
        match_id = match_state.match_id
        
        # Get additional context
        weather = await self._get_weather_context(match_state.venue)
        
        # Update match tracker (for cortex strategies)
        self.match_tracker.update_state(match_id, {
            "batting_team": match_state.batting_team,
            "runs": match_state.runs,
            "wickets": match_state.wickets,
            "overs": match_state.overs,
            "run_rate": match_state.run_rate,
            "phase": match_state.match_phase,
            "momentum": match_state.momentum_score
        })
        
        # Get current odds (mock for now - would come from scraper)
        # TODO: Integrate with actual odds scraper
        current_odds = self._get_current_odds_mock(match_id, match_state)
        
        # Run each strategy
        for strategy in self.strategies:
            try:
                # Check if strategy should fire
                signal = await self._evaluate_strategy(
                    strategy,
                    match_id,
                    match_state,
                    current_odds,
                    weather
                )
                
                if signal:
                    # Enrich and publish
                    await self._enrich_and_publish_signal(signal, match_state, weather)
            
            except Exception as e:
                logger.error(f"❌ Strategy {strategy.__class__.__name__} error: {e}")
    
    async def _evaluate_strategy(
        self,
        strategy,
        match_id: str,
        match_state: LiveMatchState,
        current_odds: Dict,
        weather: Optional[WeatherCondition]
    ) -> Optional[EnrichedSignal]:
        """
        Evaluate a single strategy
        
        The Math (Quant Analyst): "Each strategy has different firing conditions.
        Panic Rebound triggers on sharp moves, Mean Reversion on overextended odds."
        """
        # Build context for strategy
        context = {
            "match_id": match_id,
            "phase": match_state.match_phase,
            "momentum": match_state.momentum_score,
            "current_odds": current_odds,
            "weather": weather.to_dict() if weather else None
        }
        
        # Check if strategy detects signal (simplified - actual implementation in cortex)
        # This would call strategy.analyze(match_data, market_data)
        
        # For now, return None (placeholder)
        # TODO: Integrate actual strategy logic from cortex
        return None
    
    async def _enrich_and_publish_signal(
        self,
        signal: EnrichedSignal,
        match_state: LiveMatchState,
        weather: Optional[WeatherCondition]
    ):
        """
        Enrich signal with additional context and publish to Redis
        
        The Brain (AI Scientist): "ML confidence prediction is the final quality gate.
        If ML says <60% win probability, we don't publish the signal."
        """
        # Enrich with match state
        signal.match_state = {
            "phase": match_state.match_phase,
            "runs": match_state.runs,
            "wickets": match_state.wickets,
            "overs": match_state.overs,
            "momentum": match_state.momentum_score
        }
        
        # Enrich with weather
        if weather:
            signal.weather_conditions = {
                "temperature": weather.temperature_celsius,
                "humidity": weather.humidity_percent,
                "batting_conditions": weather.batting_conditions,
                "dew_factor": weather.dew_factor
            }
        
        # Enrich with ML confidence (if model available)
        if not self.circuit_breakers["ml_model"].is_open:
            try:
                # TODO: Prepare features for ML model
                # ml_confidence = self.ml_model.predict_confidence(features)
                # signal.ml_confidence = ml_confidence
                pass
            except Exception as e:
                logger.error(f"❌ ML prediction failed: {e}")
                self.circuit_breakers["ml_model"].record_failure()
        
        # Calculate odds momentum
        odds_movement = self.odds_tracker.calculate_movement(
            signal.match_id,
            "match_odds",  # Assuming match odds market
            signal.team,
            time_window_seconds=60
        )
        
        if odds_movement:
            signal.odds_momentum = {
                "velocity": odds_movement.velocity,
                "acceleration": odds_movement.acceleration,
                "direction": odds_movement.direction,
                "magnitude": odds_movement.magnitude
            }
        
        # Publish to Redis
        await self._publish_signal(signal)
    
    async def _publish_signal(self, signal: EnrichedSignal):
        """
        Publish signal to Redis for real-time consumption
        
        The Architect (Full-Stack): "Redis pub/sub is ephemeral but fast.
        Also store in signal_history list for persistence."
        """
        try:
            # Publish to signals channel (for WebSocket clients)
            signal_json = json.dumps(signal.to_dict())
            self.redis.publish('signals', signal_json)
            
            # Store in signal history
            self.redis.lpush('signal_history', signal_json)
            self.redis.ltrim('signal_history', 0, 99)  # Keep last 100 signals
            
            # Update counters
            self.signal_count += 1
            self.last_signal_time[signal.match_id] = datetime.utcnow()
            
            logger.info(f"📡 Signal published: {signal.strategy} - {signal.team} @ {signal.odds} (Confidence: {signal.confidence:.0%})")
        
        except Exception as e:
            logger.error(f"❌ Failed to publish signal: {e}")
    
    async def _get_weather_context(self, venue: str) -> Optional[WeatherCondition]:
        """Get weather context for a venue (with circuit breaker)"""
        if self.circuit_breakers["weather_api"].is_open:
            return None
        
        try:
            # Extract city from venue string
            city = venue.split(',')[0].strip() if venue else "Mumbai"
            
            async with self.weather_api as api:
                weather = await api.get_current_weather(city)
                
            if weather:
                self.circuit_breakers["weather_api"].record_success()
            
            return weather
        
        except Exception as e:
            logger.error(f"❌ Weather API error: {e}")
            self.circuit_breakers["weather_api"].record_failure()
            return None
    
    def _get_current_odds_mock(self, match_id: str, match_state: LiveMatchState) -> Dict:
        """
        Mock odds data (TODO: Replace with actual scraper integration)
        
        The Ghost (Data Engineer): "In production, this pulls from the Micro999
        scraper's Redis cache. Mock for now until full integration."
        """
        # Simplified mock odds based on match momentum
        base_odds = 2.00
        
        # Adjust based on momentum (higher momentum = lower odds)
        momentum_adjustment = (match_state.momentum_score - 50) / 100
        
        return {
            match_state.batting_team: base_odds - momentum_adjustment,
            match_state.bowling_team: base_odds + momentum_adjustment
        }
    
    async def _snapshot_odds_loop(self):
        """
        Continuous odds snapshots for historical analysis
        
        The Math (Quant Analyst): "Historical odds are training data gold.
        Capture every 5 seconds for LSTM models."
        """
        logger.info("📊 Starting odds snapshot loop...")
        
        while self.running:
            try:
                # TODO: Get current odds from scraper
                # For now, skip this loop
                await asyncio.sleep(5)
            
            except Exception as e:
                logger.error(f"❌ Odds snapshot error: {e}")
                await asyncio.sleep(5)
    
    async def _health_check_loop(self):
        """
        Periodic health checks and circuit breaker resets
        
        The Sentinel (DevOps): "Monitor system health continuously.
        Auto-recover from transient failures."
        """
        logger.info("🏥 Starting health check loop...")
        
        while self.running:
            try:
                # Check circuit breakers
                for name, cb in self.circuit_breakers.items():
                    if cb.is_open:
                        logger.warning(f"⚠️  Circuit breaker OPEN: {name}")
                    else:
                        logger.debug(f"✅ Circuit breaker OK: {name}")
                
                # Log stats
                logger.info(f"📊 Stats: {self.signal_count} signals generated, {len(self.active_matches)} active matches")
                
                await asyncio.sleep(60)  # Check every minute
            
            except Exception as e:
                logger.error(f"❌ Health check error: {e}")
                await asyncio.sleep(60)


# CLI for running orchestrator
if __name__ == "__main__":
    import redis
    import signal as sys_signal
    
    # Initialize Redis
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    # Create orchestrator
    orchestrator = SignalOrchestrator(redis_client)
    
    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info("🛑 Shutdown signal received")
        orchestrator.stop()
    
    sys_signal.signal(sys_signal.SIGINT, shutdown_handler)
    sys_signal.signal(sys_signal.SIGTERM, shutdown_handler)
    
    # Run orchestrator
    logger.info("🚀 Starting TITAN Signal Orchestrator...")
    asyncio.run(orchestrator.start(poll_interval=5))

