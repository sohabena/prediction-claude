"""
Simple Odds Change Strategy
Fast signal generation for Micro999 odds-only data

Author: TITAN System
Purpose: Quick signals on any significant odds movement

Phase 2.2: Made disableable via ENABLE_SIMPLE_ODDS_CHANGE env var
WARNING: The House advises against this strategy - too detectable, triggers on every 2% move
"""

import os
import random
from typing import Optional, Dict
from datetime import datetime, timedelta
from cortex.strategies.base import BaseStrategy, Signal
from cortex.match_state import MatchState
from loguru import logger


class SimpleOddsChangeStrategy(BaseStrategy):
    """
    Simple strategy that triggers on any significant odds change
    
    Signal Conditions:
    1. Odds change >= 2% from previous sample
    2. Works with just 2-3 data points
    3. Fast signal generation (10-15 seconds)
    
    Win Rate Target: 55-60% (lower threshold, more signals, still profitable)
    
    ⚠️ WARNING (from The House): This strategy is too detectable!
    - Triggers on EVERY 2% move (obvious pattern)
    - Bookmakers can easily identify this behavior
    - Recommend using OddsVelocity instead
    
    Can be disabled via ENABLE_SIMPLE_ODDS_CHANGE=false environment variable
    """
    
    def __init__(self):
        super().__init__(name="simple_odds_change")
        
        # Configuration
        self.change_threshold = 0.02  # 2% movement
        self.min_samples = 2  # Just need current and previous
        
        # Phase 2.2: Strategy can be disabled
        self.enabled = os.getenv('ENABLE_SIMPLE_ODDS_CHANGE', 'false').lower() == 'true'
        
        # Phase 2.2: Add randomization to reduce detectability (only fires 30% of qualifying events)
        self.fire_probability = float(os.getenv('SIMPLE_ODDS_FIRE_PROBABILITY', '0.30'))
        
        if not self.enabled:
            logger.warning(
                "⚠️ SimpleOddsChange strategy DISABLED (ENABLE_SIMPLE_ODDS_CHANGE=false). "
                "The House recommends using OddsVelocity instead."
            )
        else:
            logger.warning(
                f"⚠️ SimpleOddsChange strategy ENABLED with {self.fire_probability*100:.0f}% fire probability. "
                "This strategy is detectable - consider disabling."
            )
        
    def evaluate(self, match_state: MatchState, event: Dict) -> Optional[Signal]:
        """
        Evaluate simple odds change for trading opportunities
        
        Logic:
        1. Compare current odds with previous odds
        2. If change >= 2%, generate signal
        3. Signal direction opposite to movement (contrarian)
        """
        try:
            # Phase 2.2: Check if strategy is enabled
            if not self.enabled:
                return None
            
            # Need at least 2 samples
            if len(match_state.odds_history) < self.min_samples:
                return None
            
            # Get current and previous odds
            current_odds_record = match_state.odds_history[-1]
            previous_odds_record = match_state.odds_history[-2]
            
            current_odds = current_odds_record.get('back_price')
            previous_odds = previous_odds_record.get('back_price')
            
            if not current_odds or not previous_odds:
                return None
            
            # Calculate percentage change
            pct_change = (current_odds - previous_odds) / previous_odds
            
            # Check if change exceeds threshold
            if abs(pct_change) < self.change_threshold:
                return None
            
            # Phase 2.2: Randomization to reduce detectability
            if random.random() > self.fire_probability:
                logger.debug(
                    f"SimpleOddsChange: Signal suppressed by randomization "
                    f"(fire_prob={self.fire_probability:.0%})"
                )
                self.signals_suppressed += 1
                return None
            
            # Generate contrarian signal
            # If odds increased (team less favored), BACK them
            # If odds decreased (team more favored), LAY them
            action = "BACK" if pct_change > 0 else "LAY"
            
            # Calculate confidence (55-65% range)
            # Higher percentage change = higher confidence
            base_confidence = 0.55
            confidence_boost = min(abs(pct_change) * 5, 0.10)  # Max 10% boost
            confidence = base_confidence + confidence_boost
            
            # Calculate edge (expected value advantage)
            edge = abs(pct_change) * 0.3  # Conservative: 30% of movement
            
            # Get time since last change
            current_time = current_odds_record.get('timestamp')
            previous_time = previous_odds_record.get('timestamp')
            
            if isinstance(current_time, str):
                current_time = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            if isinstance(previous_time, str):
                previous_time = datetime.fromisoformat(previous_time.replace('Z', '+00:00'))
            
            time_diff_seconds = (current_time - previous_time).total_seconds()
            
            signal = self.create_signal(
                match_state=match_state,
                action=action,
                odds=current_odds,
                confidence=confidence,
                edge=edge,
                reasoning=f"Odds {'increased' if pct_change > 0 else 'decreased'} by {abs(pct_change)*100:.1f}% "
                         f"in {time_diff_seconds:.0f}s. Market may have overreacted. "
                         f"Contrarian {action} signal with {confidence*100:.0f}% confidence. "
                         f"Simple momentum-based entry."
            )
            
            logger.info(f"🎯 Simple Odds Change Signal: {action} {match_state.match_id} at {current_odds:.2f} "
                       f"(change: {pct_change:+.1%}, confidence: {confidence:.0%})")
            return signal
            
        except Exception as e:
            logger.error(f"Simple Odds Change error: {e}")
            return None

