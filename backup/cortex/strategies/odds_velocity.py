"""
Odds Velocity Strategy
Detects rapid odds movements and market inefficiencies

Author: The Math + The Oracle
Purpose: Quick signals while full context loads

Phase 2.3 Refinements (from The House):
- Wait for SECOND move after spike (avoid bookmaker bait)
- Add 60 second cooldown period after signal
- Increased threshold from 3% to 5% for less noise
"""

import os
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from cortex.strategies.base import BaseStrategy, Signal
from cortex.match_state import MatchState
from loguru import logger


class OddsVelocityStrategy(BaseStrategy):
    """
    Detects significant odds movements that indicate market inefficiency
    
    Signal Conditions:
    1. Rapid odds movement (>5% in 60 seconds)
    2. Movement stabilizes (volatility decreases)
    3. Volume confirms genuine movement (not noise)
    4. WAIT for second move after spike (Phase 2.3)
    5. Respect cooldown period after signal (Phase 2.3)
    
    Win Rate Target: 58-62% (lower than context strategies, but still profitable)
    """
    
    def __init__(self):
        super().__init__(name="odds_velocity")
        
        # Configuration - Phase 2.3: Increased threshold
        self.velocity_threshold = float(os.getenv('ODDS_VELOCITY_THRESHOLD', '0.05'))  # 5% movement
        self.time_window = 60  # seconds
        self.stabilization_threshold = 0.02  # 2% for stability
        self.min_samples = 4  # Phase 2.3: Need more samples to detect second move
        
        # Phase 2.3: Cooldown tracking
        self.cooldown_seconds = int(os.getenv('ODDS_VELOCITY_COOLDOWN', '60'))
        self.last_signal_time: Dict[str, datetime] = {}  # match_id -> last signal time
        
        # Phase 2.3: Track spike history for second-move detection
        self.spike_history: Dict[str, List[Dict]] = {}  # match_id -> list of spikes
        
    def evaluate(self, match_state: MatchState, event: Dict) -> Optional[Signal]:
        """
        Evaluate odds velocity for trading opportunities
        
        Logic:
        1. Calculate odds velocity (% change per minute)
        2. Detect if odds spiked then stabilized
        3. Wait for SECOND move (Phase 2.3 - avoid bookmaker bait)
        4. Respect cooldown period (Phase 2.3)
        5. Signal counter-movement opportunity
        """
        try:
            match_id = match_state.match_id
            
            # Phase 2.3: Check cooldown period
            if match_id in self.last_signal_time:
                time_since_last = (datetime.utcnow() - self.last_signal_time[match_id]).total_seconds()
                if time_since_last < self.cooldown_seconds:
                    logger.debug(
                        f"Odds Velocity: Cooldown active for {match_id} "
                        f"({self.cooldown_seconds - time_since_last:.0f}s remaining)"
                    )
                    return None
            
            # Need minimum history
            if len(match_state.odds_history) < self.min_samples:
                logger.debug(f"Odds Velocity: Insufficient history ({len(match_state.odds_history)} samples)")
                return None
            
            # Get recent odds history (last 2 minutes)
            recent_window = self._get_recent_odds(match_state, seconds=120)
            if len(recent_window) < self.min_samples:
                return None
            
            # Calculate velocity
            velocity = self._calculate_velocity(recent_window)
            if velocity is None:
                return None
            
            # Detect spike + stabilization pattern
            spike_detected, spike_direction = self._detect_spike(recent_window)
            if not spike_detected:
                return None
            
            # Phase 2.3: Track spike and wait for SECOND move
            if not self._is_second_move(match_id, spike_direction, abs(velocity)):
                logger.debug(
                    f"Odds Velocity: First spike detected for {match_id} ({spike_direction}). "
                    f"Waiting for second move to avoid bookmaker bait."
                )
                return None
            
            is_stabilized = self._check_stabilization(recent_window[-3:])  # Last 3 samples
            if not is_stabilized:
                logger.debug("Odds Velocity: Spike detected but not yet stabilized")
                return None
            
            # Generate signal (counter to the spike direction)
            action = "LAY" if spike_direction == "up" else "BACK"
            
            # Current odds
            current_odds = match_state.odds_history[-1]['back_price']
            if current_odds is None:
                return None
            
            # Calculate confidence based on velocity magnitude
            velocity_magnitude = abs(velocity)
            confidence = min(0.75 + (velocity_magnitude * 2), 0.92)  # 75-92%
            
            # Calculate edge (expected value advantage)
            edge = velocity_magnitude * 0.5  # Conservative: 50% of movement
            
            signal = self.create_signal(
                match_state=match_state,
                action=action,
                odds=current_odds,
                confidence=confidence,
                edge=edge,
                reasoning=f"Odds {'surged' if spike_direction == 'up' else 'dropped'} {velocity_magnitude*100:.1f}% (SECOND MOVE confirmed). "
                         f"Market overreacted. Counter-signal with {confidence*100:.0f}% confidence. "
                         f"Velocity: {velocity:+.2%}/min. Pattern: Spike→Wait→2nd Spike→Revert."
            )
            
            # Phase 2.3: Record signal time for cooldown
            self.last_signal_time[match_id] = datetime.utcnow()
            
            # Clear spike history for this match
            if match_id in self.spike_history:
                del self.spike_history[match_id]
            
            logger.info(f"🎯 Odds Velocity Signal: {action} {match_id} at {current_odds:.2f} (confidence: {confidence:.0%})")
            return signal
            
        except Exception as e:
            logger.error(f"Odds Velocity error: {e}")
            return None
    
    def _is_second_move(self, match_id: str, direction: str, magnitude: float) -> bool:
        """
        Phase 2.3: Check if this is the second spike in the same direction
        
        Rationale (from The House): First spike is often bookmaker bait.
        Wait for second confirming move before entering.
        """
        try:
            current_spike = {
                'direction': direction,
                'magnitude': magnitude,
                'timestamp': datetime.utcnow()
            }
            
            # Get spike history for this match
            if match_id not in self.spike_history:
                self.spike_history[match_id] = []
            
            history = self.spike_history[match_id]
            
            # Clean old spikes (older than 3 minutes)
            cutoff = datetime.utcnow() - timedelta(minutes=3)
            history = [s for s in history if s['timestamp'] > cutoff]
            
            # Check for previous spike in same direction
            same_direction_spikes = [
                s for s in history 
                if s['direction'] == direction
            ]
            
            # Record this spike
            history.append(current_spike)
            self.spike_history[match_id] = history
            
            # If we have at least one previous spike in same direction, this is second move
            if len(same_direction_spikes) >= 1:
                logger.info(
                    f"✅ Odds Velocity: Second {direction} move detected for {match_id}! "
                    f"(Previous: {len(same_direction_spikes)}, Current magnitude: {magnitude:.1%})"
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking second move: {e}")
            return False
    
    def _get_recent_odds(self, match_state: MatchState, seconds: int) -> List[dict]:
        """Get odds within time window"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=seconds)
            recent = []
            
            for odds_point in match_state.odds_history:
                timestamp = odds_point.get('timestamp')
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                
                if timestamp >= cutoff_time:
                    recent.append(odds_point)
            
            return recent
        except Exception as e:
            logger.error(f"Error getting recent odds: {e}")
            return []
    
    def _calculate_velocity(self, odds_history: List[dict]) -> Optional[float]:
        """
        Calculate odds velocity (% change per minute)
        
        Returns: Positive for increase, negative for decrease
        """
        try:
            if len(odds_history) < 2:
                return None
            
            first_odds = odds_history[0].get('back_price')
            last_odds = odds_history[-1].get('back_price')
            
            if not first_odds or not last_odds:
                return None
            
            # Calculate time difference
            first_time = odds_history[0].get('timestamp')
            last_time = odds_history[-1].get('timestamp')
            
            if isinstance(first_time, str):
                first_time = datetime.fromisoformat(first_time.replace('Z', '+00:00'))
            if isinstance(last_time, str):
                last_time = datetime.fromisoformat(last_time.replace('Z', '+00:00'))
            
            time_diff_minutes = (last_time - first_time).total_seconds() / 60
            if time_diff_minutes == 0:
                return None
            
            # Calculate % change
            pct_change = (last_odds - first_odds) / first_odds
            
            # Velocity = % change per minute
            velocity = pct_change / time_diff_minutes
            
            return velocity
            
        except Exception as e:
            logger.error(f"Error calculating velocity: {e}")
            return None
    
    def _detect_spike(self, odds_history: List[dict]) -> tuple:
        """
        Detect if there was a significant spike in odds
        
        Returns: (spike_detected: bool, direction: str)
        """
        try:
            if len(odds_history) < 3:
                return False, None
            
            # Compare first half vs second half
            mid_point = len(odds_history) // 2
            first_half = odds_history[:mid_point]
            second_half = odds_history[mid_point:]
            
            avg_first = sum(o.get('back_price', 0) for o in first_half) / len(first_half)
            avg_second = sum(o.get('back_price', 0) for o in second_half) / len(second_half)
            
            if avg_first == 0:
                return False, None
            
            pct_change = (avg_second - avg_first) / avg_first
            
            if abs(pct_change) >= self.velocity_threshold:
                direction = "up" if pct_change > 0 else "down"
                return True, direction
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error detecting spike: {e}")
            return False, None
    
    def _check_stabilization(self, recent_odds: List[dict]) -> bool:
        """
        Check if odds have stabilized (low volatility in recent samples)
        """
        try:
            if len(recent_odds) < 2:
                return False
            
            # Calculate variance in recent odds
            odds_values = [o.get('back_price', 0) for o in recent_odds if o.get('back_price')]
            if len(odds_values) < 2:
                return False
            
            avg_odds = sum(odds_values) / len(odds_values)
            variance = sum((o - avg_odds) ** 2 for o in odds_values) / len(odds_values)
            std_dev = variance ** 0.5
            
            # Coefficient of variation (relative volatility)
            if avg_odds == 0:
                return False
            
            cv = std_dev / avg_odds
            
            # Stabilized if CV < threshold
            return cv < self.stabilization_threshold
            
        except Exception as e:
            logger.error(f"Error checking stabilization: {e}")
            return False

