"""
Mean Reversion Strategy
Exploits temporary run rate surges - 66% win rate target

Strategy: When run rate surges above normal, bet UNDER on session lines
as scoring typically reverts to mean.
"""

from typing import Optional, Dict
from datetime import datetime
from cortex.strategies.base import BaseStrategy, Signal
from cortex.match_state import MatchState
from loguru import logger


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy - Betting against temporary surges
    
    Target Win Rate: 66%
    Signals Per Match: 2-3
    Edge Window: 45-90 seconds
    """
    
    def __init__(self):
        super().__init__("mean_reversion")
        
        # Strategy parameters
        self.min_surge_threshold = 2.0  # 2 standard deviations
        self.min_value_gap = 5  # Minimum 5 runs value gap
        self.min_confidence = 0.70
    
    def evaluate(self, state: MatchState, event: Dict) -> Optional[Signal]:
        """
        Evaluate if mean reversion conditions are met
        
        Triggers:
        1. Middle overs (8-14)
        2. Recent run rate surge (>2 std dev above mean)
        3. Bowler rotation (better bowler likely)
        4. Session line value gap (>5 runs)
        5. No wicket just fell
        """
        
        # Filter 1: Match phase (middle overs only)
        if not (8 <= state.overs <= 14):
            logger.debug(f"Mean Reversion: Overs {state.overs} outside target range")
            return None
        
        # Filter 2: Check for recent surge
        recent_rr = state.get_rolling_run_rate(overs=3)
        match_avg = state.run_rate
        volatility = state.calculate_std_dev()
        
        if volatility == 0:
            logger.debug("Mean Reversion: Insufficient data for volatility calculation")
            return None
        
        surge_magnitude = (recent_rr - match_avg) / volatility
        
        if surge_magnitude < self.min_surge_threshold:
            logger.debug(f"Mean Reversion: Surge {surge_magnitude:.2f} below threshold")
            return None
        
        # Filter 3: No wicket just fell (would complicate analysis)
        if event.get('is_wicket', False):
            logger.debug("Mean Reversion: Wicket just fell, skipping")
            return None
        
        # Filter 4: Bowler rotation check
        bowler_advantage = 1.0
        if state.bowler_changed_recently:
            bowler_advantage = 1.15  # Better bowler likely
            logger.debug("Mean Reversion: Bowler changed recently, bonus applied")
        
        # Filter 5: Calculate session projection
        our_projection = self._calculate_session_projection(state, recent_rr, match_avg)
        dafabet_line = event.get('session_line', our_projection + 10)  # Placeholder
        
        value_gap = dafabet_line - our_projection
        
        if value_gap < self.min_value_gap:
            logger.debug(f"Mean Reversion: Value gap {value_gap:.1f} too small")
            return None
        
        # Calculate confidence
        base_confidence = 0.72
        
        # Adjust for surge magnitude (higher surge = higher confidence in reversion)
        surge_bonus = min((surge_magnitude - self.min_surge_threshold) * 0.05, 0.10)
        
        # Adjust for value gap
        value_bonus = min((value_gap - self.min_value_gap) * 0.01, 0.08)
        
        # Adjust for bowler advantage
        bowler_bonus = 0.05 if bowler_advantage > 1.0 else 0.0
        
        # Adjust for match phase (overs 10-12 are best)
        phase_bonus = 0.05 if 10 <= state.overs <= 12 else 0.0
        
        final_confidence = (
            base_confidence 
            + surge_bonus 
            + value_bonus 
            + bowler_bonus 
            + phase_bonus
        )
        
        # Ensure confidence is within bounds
        final_confidence = max(0.0, min(1.0, final_confidence))
        
        if final_confidence < self.min_confidence:
            logger.debug(f"Mean Reversion: Confidence {final_confidence:.2%} below threshold")
            self.signals_suppressed += 1
            return None
        
        # Generate signal
        signal = Signal(
            signal_id=self._generate_signal_id(state),
            strategy=self.name,
            action='UNDER',  # Betting UNDER on session line
            team=state.batting_team,
            odds=event.get('session_odds', 1.90),  # Placeholder
            confidence=final_confidence,
            stake_recommended=self._calculate_kelly_stake(final_confidence, 1.90),
            edge_window_seconds=60,  # 60 second window
            reasoning=self._generate_reasoning(
                state, recent_rr, match_avg, surge_magnitude, 
                value_gap, our_projection, dafabet_line, final_confidence
            ),
            match_context=self._build_match_context(
                state, event, recent_rr, match_avg, surge_magnitude, value_gap
            ),
            created_at=datetime.utcnow()
        )
        
        self.signals_generated += 1
        logger.info(f"✅ Mean Reversion Signal: UNDER {our_projection:.0f} runs ({signal.confidence:.1%})")
        
        return signal
    
    def _calculate_session_projection(
        self, 
        state: MatchState, 
        recent_rr: float, 
        match_avg: float
    ) -> float:
        """
        Calculate projected runs for next session (typically 5 overs)
        
        Assumes reversion to mean with some momentum carry-over
        """
        # Weight recent performance but expect reversion
        reversion_weight = 0.70  # 70% weight to match average
        momentum_weight = 0.30   # 30% weight to recent rate
        
        projected_rr = (match_avg * reversion_weight) + (recent_rr * momentum_weight)
        
        # Project for next 5 overs (typical session)
        session_overs = 5
        projected_runs = projected_rr * session_overs
        
        return round(projected_runs, 1)
    
    def _generate_reasoning(
        self,
        state: MatchState,
        recent_rr: float,
        match_avg: float,
        surge_magnitude: float,
        value_gap: float,
        our_projection: float,
        dafabet_line: float,
        final_confidence: float
    ) -> str:
        """Generate human-readable reasoning"""
        
        reasoning_parts = [
            f"Run rate surge detected: {recent_rr:.1f} vs match avg {match_avg:.1f}.",
            f"Surge magnitude: {surge_magnitude:.1f} std deviations.",
            f"Expect reversion to mean in middle overs.",
        ]
        
        if state.bowler_changed_recently:
            reasoning_parts.append("Bowler rotation suggests tighter bowling ahead.")
        
        reasoning_parts.append(
            f"Our projection: {our_projection:.0f} runs, "
            f"Dafabet line: {dafabet_line:.0f}, "
            f"Value gap: {value_gap:.0f} runs."
        )
        
        reasoning_parts.append(f"Confidence: {final_confidence:.1%}. Edge window: 60s.")
        
        return " ".join(reasoning_parts)
    
    def _build_match_context(
        self,
        state: MatchState,
        event: Dict,
        recent_rr: float,
        match_avg: float,
        surge_magnitude: float,
        value_gap: float
    ) -> Dict:
        """Build match context"""
        return {
            'match_id': state.match_id,
            'overs': state.overs,
            'score': f"{state.score}/{state.wickets}",
            'match_run_rate': match_avg,
            'recent_run_rate': recent_rr,
            'surge_magnitude': surge_magnitude,
            'value_gap': value_gap,
            'match_phase': state.match_phase,
            'bowler_changed': state.bowler_changed_recently,
            'volatility': state.calculate_std_dev()
        }

