"""
TITAN Cortex Processor
Main signal processing engine that orchestrates strategies and quality gates

Phase 1.4: Added auto paper trading support
"""

import asyncio
import redis
import json
import os
import sys
import random
import aiohttp
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

# Data flow logging and validation
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.flow_logger import get_flow_logger
from utils.data_validators import CortexDataValidator
from utils.data_fingerprint import create_fingerprint, track_data

# Initialize flow logger and validator for this component
flow_log = get_flow_logger("Cortex")
cortex_validator = CortexDataValidator()

from cortex.match_state import MatchState
from cortex.strategies import (
    PanicReboundStrategy,
    MeanReversionStrategy,
    WhaleShadowStrategy
)
from cortex.strategies.odds_velocity import OddsVelocityStrategy
from cortex.strategies.simple_odds_change import SimpleOddsChangeStrategy
from cortex.circuit_breaker import CircuitBreaker

# Phase 2.1: ML Confidence Model
from backend.ml.confidence_model import get_confidence_model, ConfidenceModel

# Phase 3.1: CLV Tracking
from cortex.clv_tracker import get_clv_tracker, CLVTracker

# Phase 3.2: Dynamic Kelly Manager
from backend.utils.kelly_criterion import get_kelly_manager, DynamicKellyManager

# Import quality gates
from cortex.quality_gates.data_quality import DataQualityGate
from cortex.quality_gates.statistical import StatisticalGate
from cortex.quality_gates.context import ContextGate
from cortex.quality_gates.bookmaker import BookmakerGate
from cortex.quality_gates.technical import TechnicalGate

# Phase 2.4: ML data collection
from cortex.data_collector import get_collector, MLDataCollector

load_dotenv()


class CortexProcessor:
    """
    Main signal processing engine
    
    Responsibilities:
    1. Subscribe to Redis match_events channel
    2. Maintain in-memory match states
    3. Evaluate all strategies in parallel
    4. Apply quality gates
    5. Publish approved signals
    6. Monitor circuit breakers
    """
    
    def __init__(self):
        # Redis connection
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        # In-memory match states
        self.match_states: Dict[str, MatchState] = {}
        
        # Phase 2.1: Initialize ML Confidence Model
        self.ml_model: Optional[ConfidenceModel] = None
        self.ml_enabled = os.getenv('ML_CONFIDENCE_ENABLED', 'true').lower() == 'true'
        
        if self.ml_enabled:
            try:
                self.ml_model = get_confidence_model()
                logger.info("🧠 ML Confidence Model LOADED")
            except Exception as e:
                logger.warning(f"⚠️ ML model load failed, using rule-based confidence: {e}")
        
        # Initialize strategies
        self.strategies = [
            SimpleOddsChangeStrategy(),  # Fastest signals (2% threshold, 2 samples)
            OddsVelocityStrategy(),      # Quick signals from odds movements
            PanicReboundStrategy(),      # Full context strategy
            MeanReversionStrategy(),     # Full context strategy
            WhaleShadowStrategy()        # Full context strategy
        ]
        
        # Phase 2.1: Attach ML model to all strategies
        if self.ml_model:
            for strategy in self.strategies:
                strategy.set_ml_model(self.ml_model)
            logger.info(f"🧠 ML model attached to {len(self.strategies)} strategies")
        
        # Phase 3.2: Attach Kelly manager to all strategies
        if self.kelly_manager:
            for strategy in self.strategies:
                strategy.set_kelly_manager(self.kelly_manager)
            logger.info(f"💰 Kelly manager attached to {len(self.strategies)} strategies")
        
        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            starting_bankroll=int(os.getenv('STARTING_BANKROLL', 100000))
        )
        
        # Phase 2.4: ML Data Collector
        self.ml_collect_enabled = os.getenv('ML_DATA_COLLECTION', 'true').lower() == 'true'
        self.data_collector: Optional[MLDataCollector] = None
        if self.ml_collect_enabled:
            self.data_collector = get_collector()
            logger.info("📊 ML Data Collection ENABLED")
        
        # Phase 3.1: CLV Tracker
        self.clv_enabled = os.getenv('CLV_TRACKING_ENABLED', 'true').lower() == 'true'
        self.clv_tracker: Optional[CLVTracker] = None
        if self.clv_enabled:
            self.clv_tracker = get_clv_tracker()
            logger.info("📊 CLV Tracking ENABLED")
        
        # Phase 3.2: Dynamic Kelly Manager
        self.kelly_enabled = os.getenv('DYNAMIC_KELLY_ENABLED', 'true').lower() == 'true'
        self.kelly_manager: Optional[DynamicKellyManager] = None
        if self.kelly_enabled:
            starting_bankroll = int(os.getenv('STARTING_BANKROLL', 100000))
            self.kelly_manager = get_kelly_manager(starting_bankroll)
            logger.info("💰 Dynamic Kelly Risk Management ENABLED")
        
        # Initialize quality gates (in order of evaluation)
        self.quality_gates = [
            DataQualityGate(),      # Gate 1: Data freshness and validity
            StatisticalGate(),      # Gate 2: Statistical confidence
            ContextGate(),          # Gate 3: Match context appropriateness
            BookmakerGate(),        # Gate 4: Bookmaker reality check
            TechnicalGate(),        # Gate 5: System health
        ]
        
        # Whether to enforce all gates or just log warnings
        self.enforce_gates = os.getenv('ENFORCE_QUALITY_GATES', 'true').lower() == 'true'
        
        # Phase 3.3: Win rate throttling configuration
        self.max_win_rate_threshold = float(os.getenv('MAX_WIN_RATE_THRESHOLD', '0.58'))
        self.win_rate_window = int(os.getenv('WIN_RATE_WINDOW', '50'))  # Rolling window size
        self.signal_outcomes: List[bool] = []  # Track recent outcomes (True=win, False=loss)
        self.throttled_signals = 0
        
        # Statistics
        self.total_events_processed = 0
        self.total_signals_generated = 0
        self.total_signals_suppressed = 0
        
        self.is_running = False
        
        logger.info(f"Cortex Processor initialized with {len(self.strategies)} strategies and {len(self.quality_gates)} quality gates")
    
    async def start(self):
        """Start the processor"""
        self.is_running = True
        logger.info("🧠 Cortex Processor starting...")
        
        # Subscribe to Redis pub/sub for market events
        market_pubsub = self.redis_client.pubsub()
        market_pubsub.subscribe('match_events')
        
        # Subscribe to circuit breaker events (threshold breaches)
        cb_pubsub = self.redis_client.pubsub()
        cb_pubsub.subscribe('circuit_breaker_events')
        
        # Phase 1.3: Subscribe to ALL signal outcomes for real-time tracking
        outcome_pubsub = self.redis_client.pubsub()
        outcome_pubsub.subscribe('signal_outcome_events')
        
        logger.info("Subscribed to match_events, circuit_breaker_events, and signal_outcome_events channels")
        
        try:
            while self.is_running:
                # Check circuit breaker
                if self.circuit_breaker.is_open:
                    logger.warning("⚠️  Circuit breaker is OPEN - pausing signal generation")
                    await asyncio.sleep(60)  # Wait 1 minute
                    continue
                
                # Check for circuit breaker events (threshold breaches)
                cb_message = cb_pubsub.get_message()
                if cb_message and cb_message['type'] == 'message':
                    try:
                        event = json.loads(cb_message['data'])
                        await self._handle_circuit_breaker_event(event)
                    except Exception as e:
                        logger.error(f"Error processing circuit breaker event: {e}")
                
                # Phase 1.3: Check for signal outcome events
                outcome_message = outcome_pubsub.get_message()
                if outcome_message and outcome_message['type'] == 'message':
                    try:
                        outcome_event = json.loads(outcome_message['data'])
                        await self._handle_outcome_event(outcome_event)
                    except Exception as e:
                        logger.error(f"Error processing outcome event: {e}")
                
                # Get market message from Redis
                message = market_pubsub.get_message()
                
                if message and message['type'] == 'message':
                    try:
                        event_data = json.loads(message['data'])
                        await self._process_event(event_data)
                    except Exception as e:
                        logger.error(f"Error processing event: {e}")
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.01)
        
        except KeyboardInterrupt:
            logger.info("Received stop signal")
        finally:
            market_pubsub.unsubscribe('match_events')
            cb_pubsub.unsubscribe('circuit_breaker_events')
            outcome_pubsub.unsubscribe('signal_outcome_events')
            await self.stop()
    
    async def _process_event(self, event_data: Dict):
        """Process a single market event"""
        try:
            self.total_events_processed += 1
            
            # Unwrap data if it's wrapped in a 'data' field
            if 'data' in event_data and isinstance(event_data['data'], dict):
                match_data = event_data['data']
            else:
                match_data = event_data
            
            # Phase 1.1: Check for metadata events (Whale Shadow triggers)
            metadata_events = event_data.get('metadata_events', [])
            if metadata_events:
                for meta_event in metadata_events:
                    logger.warning(
                        f"🐋 WHALE SHADOW METADATA RECEIVED in Cortex:\n"
                        f"   match_id: {meta_event.get('match_id')}\n"
                        f"   is_suspension_event: {meta_event.get('is_suspension_event')}\n"
                        f"   is_limit_change: {meta_event.get('is_limit_change')}\n"
                        f"   stake_limit_drop_pct: {meta_event.get('stake_limit_drop_pct', 0):.1%}"
                    )
                    # Merge metadata into match_data for strategy evaluation
                    match_data['is_suspension_event'] = meta_event.get('is_suspension_event', False)
                    match_data['is_limit_change'] = meta_event.get('is_limit_change', False)
                    match_data['stake_limit'] = meta_event.get('stake_limit')
                    match_data['stake_limit_drop_pct'] = meta_event.get('stake_limit_drop_pct', 0)
            
            # Extract match ID and price
            match_id = match_data.get('match_id', 'unknown')
            back_price = match_data.get('back_price') or match_data.get('odds')
            
            # Stage 3 Flow Logging: Received match event
            # Log every 20th event to avoid spam
            if self.total_events_processed % 20 == 1:
                flow_log.stage3_received(
                    match_id=match_id,
                    back_price=float(back_price) if back_price else 0
                )
            
            # Get or create match state
            if match_id not in self.match_states:
                self.match_states[match_id] = MatchState(match_id=match_id)
                logger.info(f"[STAGE-3] Created new match state for {match_id}")
            
            state = self.match_states[match_id]
            
            # Update match state
            self._update_match_state(state, match_data)
            
            # Phase 3.1: Update CLV tracker with current odds
            if self.clv_tracker and back_price:
                try:
                    self.clv_tracker.update_closing_odds(match_id, float(back_price))
                except Exception as e:
                    logger.debug(f"CLV update error: {e}")
            
            # Evaluate all strategies
            signals = await self._evaluate_strategies(state, match_data)
            
            # Stage 3 Flow Logging: Strategy evaluation
            if signals:
                flow_log.stage3_evaluated(
                    strategy_count=len(self.strategies),
                    signal_count=len(signals)
                )
            
            # Process signals through quality gates
            approved_count = 0
            for signal in signals:
                approved = await self._apply_quality_gates(signal, state)
                
                if approved:
                    # Phase 3.3: Check win rate throttling
                    if self._should_throttle_signal(signal):
                        self.total_signals_suppressed += 1
                        continue
                    
                    # Stage 3 Flow Logging: Signal generated
                    flow_log.stage3_signal(
                        strategy=signal.get('strategy', 'unknown'),
                        action=signal.get('action', '?'),
                        team=signal.get('team', '?'),
                        odds=signal.get('odds', 0),
                        confidence=signal.get('confidence', 0)
                    )
                    
                    await self._publish_signal(signal, state, match_data)
                    self.total_signals_generated += 1
                    approved_count += 1
                else:
                    self.total_signals_suppressed += 1
            
            # Log progress
            if self.total_events_processed % 100 == 0:
                logger.info(
                    f"[STAGE-3] Processed {self.total_events_processed} events, "
                    f"Generated {self.total_signals_generated} signals, "
                    f"Suppressed {self.total_signals_suppressed}"
                )
        
        except Exception as e:
            logger.error(f"Error in event processing: {e}")
            import traceback
            traceback.print_exc()
    
    def _update_match_state(self, state: MatchState, event_data: Dict):
        """Update match state from event data"""
        
        # Update odds (check multiple possible field names)
        odds_value = event_data.get('odds') or event_data.get('back_price')
        back_price = event_data.get('back_price')
        lay_price = event_data.get('lay_price')
        
        if odds_value:
            try:
                state.update_odds(
                    float(odds_value),
                    back_price=float(back_price) if back_price else None,
                    lay_price=float(lay_price) if lay_price else None
                )
                if len(state.odds_history) <= 3:  # Debug first few
                    logger.debug(f"✅ Odds updated: {odds_value}, history size: {len(state.odds_history)}")
            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ Invalid odds value: {odds_value}, error: {e}")
        
        # Update score (optional)
        if 'score' in event_data and event_data['score'] is not None:
            try:
                state.score = int(event_data['score'])
            except (ValueError, TypeError):
                pass  # Skip invalid score
        
        # Update wickets (optional)
        if 'wickets' in event_data and event_data['wickets'] is not None:
            try:
                prev_wickets = state.wickets
                state.wickets = int(event_data['wickets'])
                
                # Phase 1.1: Log wicket changes for Panic Rebound
                if state.wickets > prev_wickets:
                    logger.warning(
                        f"🎯 WICKET DETECTED in Cortex for {state.match_id}! "
                        f"Wickets: {prev_wickets} -> {state.wickets}"
                    )
            except (ValueError, TypeError):
                pass  # Skip invalid wickets
        
        # Phase 1.1: Handle is_wicket flag directly from enricher
        if event_data.get('is_wicket'):
            logger.warning(
                f"🎯 WICKET FLAG RECEIVED from enricher for {state.match_id}! "
                f"Score: {state.score}/{state.wickets}"
            )
        
        # Update overs (optional)
        if 'overs' in event_data and event_data['overs'] is not None:
            try:
                state.update_overs(float(event_data['overs']))
            except (ValueError, TypeError):
                pass  # Skip invalid overs
        
        # Update suspension status (optional)
        if 'is_suspended' in event_data and event_data.get('is_suspended') is not None:
            try:
                was_suspended = state.is_suspended
                state.update_suspension(bool(event_data['is_suspended']))
                
                # Phase 1.1: Log suspension changes for Whale Shadow
                if was_suspended != state.is_suspended:
                    if state.is_suspended:
                        logger.warning(f"🚫 SUSPENSION STARTED for {state.match_id}")
                    else:
                        logger.warning(
                            f"✅ SUSPENSION ENDED for {state.match_id} "
                            f"(duration: {state.suspension_duration:.1f}s)"
                        )
            except (ValueError, TypeError):
                pass
        
        # Update stake limit (optional)
        if 'stake_limit' in event_data and event_data.get('stake_limit') is not None:
            try:
                prev_limit = state.stake_limit
                state.update_stake_limit(int(event_data['stake_limit']))
                
                # Phase 1.1: Log stake limit changes for Whale Shadow
                if prev_limit is not None and prev_limit != state.stake_limit:
                    drop_pct = state.stake_limit_drop_percentage()
                    logger.warning(
                        f"💰 STAKE LIMIT CHANGED for {state.match_id}: "
                        f"{prev_limit} -> {state.stake_limit} (drop: {drop_pct:.1%})"
                    )
            except (ValueError, TypeError):
                pass
        
        # Update target (if chasing) (optional)
        if 'target' in event_data and event_data.get('target') is not None:
            try:
                state.target = int(event_data['target'])
            except (ValueError, TypeError):
                pass
        
        # Update teams (optional)
        if 'batting_team' in event_data and event_data.get('batting_team'):
            state.batting_team = event_data['batting_team']
        
        # Update bowler (optional)
        if 'current_bowler' in event_data and event_data.get('current_bowler'):
            if state.current_bowler != event_data['current_bowler']:
                state.bowler_changed_recently = True
                state.current_bowler = event_data['current_bowler']
    
    async def _evaluate_strategies(self, state: MatchState, event_data: Dict) -> List:
        """Evaluate all strategies in parallel"""
        signals = []
        
        for strategy in self.strategies:
            try:
                signal = strategy.evaluate(state, event_data)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error in strategy {strategy.name}: {e}")
        
        return signals
    
    async def _apply_quality_gates(self, signal, state: MatchState) -> bool:
        """
        Apply all quality gates to signal
        
        Gates are evaluated in order:
        1. Data Quality - data freshness and validity
        2. Statistical - confidence and significance
        3. Context - match context appropriateness
        4. Bookmaker - market reality checks
        5. Technical - system health
        
        Returns True if signal passes all gates (or if gates disabled)
        """
        
        # Check circuit breaker first
        if self.circuit_breaker.is_open:
            logger.debug("Signal suppressed: circuit breaker is open")
            return False
        
        # Track confidence adjustments from gates
        total_confidence_adjustment = 0.0
        gate_results = []
        
        # Evaluate all quality gates
        for gate in self.quality_gates:
            try:
                result = gate.evaluate(signal, state)
                gate_results.append(result)
                
                if not result.passed:
                    if self.enforce_gates:
                        logger.info(f"🚫 Signal blocked by {result.gate_name}: {result.reason}")
                        return False
                    else:
                        # Just log warning but allow signal
                        logger.warning(f"⚠️ Quality gate warning ({result.gate_name}): {result.reason}")
                else:
                    # Accumulate confidence adjustments from passed gates
                    total_confidence_adjustment += result.confidence_adjustment
                    
            except Exception as e:
                logger.error(f"Error in quality gate {gate.name}: {e}")
                # Don't block on gate errors, but log them
                continue
        
        # Apply confidence adjustments (for logging purposes)
        adjusted_confidence = signal.confidence + total_confidence_adjustment
        if total_confidence_adjustment != 0:
            logger.debug(f"Confidence adjusted: {signal.confidence:.2%} -> {adjusted_confidence:.2%}")
        
        # Log gate summary for passed signals
        passed_gates = [r.gate_name for r in gate_results if r.passed]
        logger.debug(f"Signal passed {len(passed_gates)}/{len(self.quality_gates)} gates")
        
        return True
    
    async def _publish_signal(self, signal, state: MatchState = None, event_data: Dict = None):
        """Publish approved signal to Redis"""
        try:
            signal_data = signal.to_dict()
            
            # Stage 4 Data Validation: Validate signal before publishing
            validation_result = cortex_validator.validate(
                signal_data,
                context={'event': event_data or {}}
            )
            
            if not validation_result.is_valid():
                logger.warning(f"[STAGE-4] Signal validation failed: {validation_result}")
                if validation_result.details.get('issues'):
                    for issue in validation_result.details['issues'][:2]:
                        logger.warning(f"  - {issue}")
                return  # Don't publish invalid signal
            
            # Track signal with fingerprint
            fingerprint = track_data(
                stage=4,
                component="cortex_signal",
                data={
                    'match_id': event_data.get('match_id') if event_data else 'unknown',
                    'back_price': signal.odds,
                    'timestamp': datetime.utcnow().isoformat()
                },
                validation_result=validation_result
            )
            logger.debug(f"[STAGE-4] Signal validated [FP:{fingerprint}]")
            
            # Publish to signals channel (for frontend)
            self.redis_client.publish('signals', json.dumps(signal_data))
            
            # Store in signals list (for history)
            self.redis_client.lpush('signal_history', json.dumps(signal_data))
            self.redis_client.ltrim('signal_history', 0, 99)  # Keep last 100
            
            logger.info(
                f"📡 Signal Published: {signal.strategy} - "
                f"{signal.action} {signal.team} @ {signal.odds:.2f} "
                f"({signal.confidence:.1%}) [FP:{fingerprint}]"
            )
            
            # Phase 2.4: Collect data for ML training
            if self.data_collector and state and event_data:
                self.data_collector.collect_signal(signal, state, event_data)
            
            # Phase 3.1: Register signal for CLV tracking
            if self.clv_tracker:
                self.clv_tracker.register_signal(signal_data)
            
            # Phase 1.4: Auto paper trading if enabled
            if os.getenv('AUTO_PAPER_TRADE', 'false').lower() == 'true':
                await self._open_paper_position(signal)
        
        except Exception as e:
            logger.error(f"Error publishing signal: {e}")
    
    async def _open_paper_position(self, signal):
        """
        Phase 1.4: Automatically open a paper trading position for this signal
        
        This allows us to track strategy performance without manual intervention.
        """
        try:
            # Prepare paper trading request
            paper_trade_data = {
                'signal_id': signal.signal_id,
                'match_id': signal.match_context.get('match_id', 'unknown'),
                'strategy': signal.strategy,
                'side': signal.action,
                'odds': signal.odds,
                'confidence': signal.confidence,
                'stake': signal.stake_recommended
            }
            
            # Call the paper trading API
            paper_trading_url = os.getenv('PAPER_TRADING_URL', 'http://localhost:8000')
            url = f"{paper_trading_url}/api/paper-trading/auto-open-from-signal"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=paper_trade_data, timeout=5) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"📝 Auto paper trade opened for {signal.signal_id}")
                    else:
                        error_text = await response.text()
                        logger.warning(f"⚠️ Paper trade failed: {response.status} - {error_text}")
        
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ Paper trading API timeout for {signal.signal_id}")
        except aiohttp.ClientError as e:
            logger.warning(f"⚠️ Paper trading API error for {signal.signal_id}: {e}")
        except Exception as e:
            logger.error(f"Error opening paper position: {e}")
    
    async def _handle_circuit_breaker_event(self, event: Dict):
        """
        Handle circuit breaker events from the outcome tracker
        
        Events are published when:
        1. Consecutive losses detected
        2. Session drawdown exceeded
        3. Win rate degradation
        """
        try:
            action = event.get('action', {})
            
            if action.get('triggered'):
                reason = action.get('reason', 'Unknown')
                severity = action.get('severity', 'warning')
                
                logger.warning(f"🚨 Circuit breaker event received: {reason}")
                
                if severity == 'critical':
                    # Open the circuit breaker
                    self.circuit_breaker.open_circuit(f"External trigger: {reason}")
                else:
                    logger.warning(f"⚠️ Circuit breaker warning: {reason}")
        
        except Exception as e:
            logger.error(f"Error handling circuit breaker event: {e}")
    
    async def _handle_outcome_event(self, event: Dict):
        """
        Phase 1.3: Handle signal outcome events from outcome tracker
        
        Updates the local circuit breaker with outcome data.
        Also tracks win rate for Phase 3.3 throttling.
        """
        try:
            if event.get('type') != 'outcome':
                return
            
            signal_id = event.get('signal_id', 'unknown')
            won = event.get('won', False)
            profit = event.get('profit', 0.0)
            strategy = event.get('strategy', 'unknown')
            
            # Update circuit breaker
            self.circuit_breaker.record_result(won, profit)
            
            # Phase 3.3: Track outcome for win rate throttling
            self._record_outcome_for_throttling(won)
            
            # Log the outcome
            result_str = "✅ WIN" if won else "❌ LOSS"
            current_win_rate = self._get_rolling_win_rate()
            logger.info(
                f"📊 Outcome received via Redis: {signal_id} ({strategy}) - {result_str} "
                f"(P&L: {profit:+.2f}, Win Rate: {current_win_rate:.1%}, CB stats: {self.circuit_breaker.get_stats()})"
            )
            
        except Exception as e:
            logger.error(f"Error handling outcome event: {e}")
    
    def _record_outcome_for_throttling(self, won: bool):
        """Phase 3.3: Record outcome for win rate tracking"""
        self.signal_outcomes.append(won)
        # Keep only the last N outcomes
        if len(self.signal_outcomes) > self.win_rate_window:
            self.signal_outcomes = self.signal_outcomes[-self.win_rate_window:]
    
    def _get_rolling_win_rate(self) -> float:
        """Phase 3.3: Calculate rolling win rate"""
        if not self.signal_outcomes:
            return 0.5  # Default to 50% if no data
        return sum(self.signal_outcomes) / len(self.signal_outcomes)
    
    def _should_throttle_signal(self, signal) -> bool:
        """
        Phase 3.3: Determine if signal should be throttled
        
        If win rate exceeds threshold, randomly suppress some signals
        to avoid detection.
        """
        if len(self.signal_outcomes) < 20:  # Need at least 20 outcomes
            return False
        
        current_win_rate = self._get_rolling_win_rate()
        
        if current_win_rate > self.max_win_rate_threshold:
            # Calculate how much over threshold
            excess = current_win_rate - self.max_win_rate_threshold
            
            # Probability of throttling increases with excess
            # If 5% over threshold, throttle ~50% of signals
            throttle_probability = min(excess * 10, 0.70)  # Max 70% throttle rate
            
            if random.random() < throttle_probability:
                self.throttled_signals += 1
                logger.warning(
                    f"⚠️ Win rate throttling: Signal {signal.signal_id} suppressed. "
                    f"Current win rate: {current_win_rate:.1%} (threshold: {self.max_win_rate_threshold:.0%}). "
                    f"Total throttled: {self.throttled_signals}"
                )
                return True
        
        return False
    
    def record_signal_outcome(self, signal_id: str, won: bool, profit_loss: float):
        """
        Record the outcome of a signal (called from API or auto-settlement)
        
        This updates the circuit breaker with the result.
        """
        try:
            self.circuit_breaker.record_result(won, profit_loss)
            
            logger.info(f"Signal outcome recorded: {signal_id} - {'WIN' if won else 'LOSS'} ({profit_loss:+.2f})")
            
            return self.circuit_breaker.get_stats()
        
        except Exception as e:
            logger.error(f"Error recording signal outcome: {e}")
            return None
    
    async def stop(self):
        """Stop the processor"""
        self.is_running = False
        
        # Get final stats
        logger.info("Cortex Processor stopping...")
        logger.info(f"Total events processed: {self.total_events_processed}")
        logger.info(f"Total signals generated: {self.total_signals_generated}")
        logger.info(f"Total signals suppressed: {self.total_signals_suppressed}")
        
        for strategy in self.strategies:
            stats = strategy.get_stats()
            logger.info(f"Strategy {stats['name']}: {stats}")
    
    def get_stats(self) -> Dict:
        """Get processor statistics"""
        return {
            'total_events_processed': self.total_events_processed,
            'total_signals_generated': self.total_signals_generated,
            'total_signals_suppressed': self.total_signals_suppressed,
            'active_matches': len(self.match_states),
            'circuit_breaker_open': self.circuit_breaker.is_open,
            'enforce_quality_gates': self.enforce_gates,
            'strategies': [s.get_stats() for s in self.strategies],
            'quality_gates': [g.get_stats() for g in self.quality_gates],
            # Phase 2.1: ML confidence stats
            'ml_enabled': self.ml_enabled,
            'ml_model_loaded': self.ml_model is not None,
            # Phase 3.1: CLV tracking stats
            'clv_enabled': self.clv_enabled,
            'clv_stats': self.clv_tracker.get_clv_stats() if self.clv_tracker else None,
            # Phase 3.2: Dynamic Kelly stats
            'kelly_enabled': self.kelly_enabled,
            'kelly_status': self.kelly_manager.get_status() if self.kelly_manager else None,
            # Phase 3.3: Win rate throttling stats
            'rolling_win_rate': self._get_rolling_win_rate(),
            'win_rate_window_size': len(self.signal_outcomes),
            'throttled_signals': self.throttled_signals,
            'max_win_rate_threshold': self.max_win_rate_threshold
        }


async def main():
    """Main entry point"""
    logger.info("🚀 TITAN Cortex Processor Starting...")
    
    processor = CortexProcessor()
    
    try:
        await processor.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await processor.stop()


if __name__ == "__main__":
    asyncio.run(main())

