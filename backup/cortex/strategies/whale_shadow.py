"""
Whale Shadow Strategy
Follows smart money via metadata signals - 77% win rate target

Strategy: When bookmaker shows defensive behavior (limit cuts, long suspensions),
follow the direction of smart money.
"""

from typing import Optional, Dict
from datetime import datetime
from cortex.strategies.base import BaseStrategy, Signal
from cortex.match_state import MatchState
from loguru import logger


class WhaleShadowStrategy(BaseStrategy):
    """
    Whale Shadow Strategy - Following smart money via metadata
    
    Target Win Rate: 77%
    Signals Per Match: 0-2 (rare but high confidence)
    Edge Window: 20-40 seconds
    """
    
    def __init__(self):
        super().__init__("whale_shadow")
        
        # Strategy parameters (very selective)
        self.min_suspension_duration = 7  # 7+ seconds
        self.min_stake_limit_drop = 0.70  # 70%+ drop
        self.min_confidence = 0.75
    
    def evaluate(self, state: MatchState, event: Dict) -> Optional[Signal]:
        """
        Evaluate if whale shadow conditions are met
        
        Triggers:
        1. Long suspension (>7 seconds) OR
        2. Significant stake limit drop (>70%) OR
        3. Combination of both
        
        Classification:
        - Sharp Money: Long suspension + big limit drop
        - Technical: Short suspension + no limit change
        - Suspicious: Pattern suggests informed betting
        """
        
        # Check if this is a metadata event
        is_suspension_event = event.get('is_suspension_event', False)
        is_limit_change = event.get('is_limit_change', False)
        
        if not (is_suspension_event or is_limit_change):
            return None
        
        # Analyze suspension
        suspension_duration = state.suspension_duration
        has_long_suspension = suspension_duration > self.min_suspension_duration
        
        # Analyze stake limit
        stake_limit_drop = state.stake_limit_drop_percentage()
        has_big_limit_drop = stake_limit_drop > self.min_stake_limit_drop
        
        # Classify event type
        signal_type = self._classify_event(
            has_long_suspension, 
            has_big_limit_drop,
            suspension_duration,
            stake_limit_drop
        )
        
        if signal_type == 'IGNORE':
            logger.debug("Whale Shadow: Event classified as technical/noise")
            return None
        
        # Determine action based on odds movement
        odds_increased = state.current_odds > state.previous_odds
        
        if signal_type == 'SHARP_MONEY':
            # Follow the money direction
            action = 'BACK' if odds_increased else 'LAY'
            base_confidence = 0.85
            reasoning_prefix = "Sharp money detected."
        
        elif signal_type == 'SUSPICIOUS':
            # Likely informed betting, but less certain
            action = 'BACK' if odds_increased else 'LAY'
            base_confidence = 0.78
            reasoning_prefix = "Suspicious betting pattern detected."
        
        else:  # DEFENSIVE
            # Bookmaker being cautious
            action = 'BACK' if odds_increased else 'LAY'
            base_confidence = 0.76
            reasoning_prefix = "Bookmaker defensive behavior detected."
        
        # Adjust confidence based on match context
        final_confidence = self._adjust_confidence(
            base_confidence, state, suspension_duration, stake_limit_drop
        )
        
        if final_confidence < self.min_confidence:
            logger.debug(f"Whale Shadow: Confidence {final_confidence:.2%} below threshold")
            self.signals_suppressed += 1
            return None
        
        # Generate signal
        signal = Signal(
            signal_id=self._generate_signal_id(state),
            strategy=self.name,
            action=action,
            team=state.batting_team if action == 'BACK' else state.team2,
            odds=state.current_odds,
            confidence=final_confidence,
            stake_recommended=self._calculate_kelly_stake(final_confidence, state.current_odds),
            edge_window_seconds=30,  # Very short window
            reasoning=self._generate_reasoning(
                reasoning_prefix, state, signal_type, suspension_duration,
                stake_limit_drop, odds_increased, final_confidence
            ),
            match_context=self._build_match_context(
                state, event, signal_type, suspension_duration, stake_limit_drop
            ),
            created_at=datetime.utcnow()
        )
        
        self.signals_generated += 1
        logger.info(f"✅ Whale Shadow Signal: {action} {signal.team} @ {signal.odds:.2f} ({signal.confidence:.1%})")
        
        return signal
    
    def _classify_event(
        self,
        has_long_suspension: bool,
        has_big_limit_drop: bool,
        suspension_duration: float,
        stake_limit_drop: float
    ) -> str:
        """
        Classify metadata event type
        
        Returns:
            'SHARP_MONEY' - High confidence smart money
            'SUSPICIOUS' - Likely informed betting
            'DEFENSIVE' - Bookmaker being cautious
            'IGNORE' - Technical/noise
        """
        
        # Sharp money: Both indicators present
        if has_long_suspension and has_big_limit_drop:
            return 'SHARP_MONEY'
        
        # Suspicious: One strong indicator
        if has_long_suspension and stake_limit_drop > 0.50:
            return 'SUSPICIOUS'
        
        if has_big_limit_drop and suspension_duration > 5:
            return 'SUSPICIOUS'
        
        # Defensive: Moderate indicators
        if suspension_duration > 5 or stake_limit_drop > 0.50:
            return 'DEFENSIVE'
        
        # Ignore: Likely technical
        return 'IGNORE'
    
    def _adjust_confidence(
        self,
        base_confidence: float,
        state: MatchState,
        suspension_duration: float,
        stake_limit_drop: float
    ) -> float:
        """Adjust confidence based on match context"""
        
        confidence = base_confidence
        
        # Bonus for extreme values
        if suspension_duration > 10:
            confidence += 0.05
        
        if stake_limit_drop > 0.85:
            confidence += 0.05
        
        # Penalty for late match (less reliable)
        if state.overs > 17:
            confidence -= 0.10
        
        # Bonus for middle overs (most reliable)
        if 8 <= state.overs <= 15:
            confidence += 0.05
        
        # Penalty if too many wickets down (chaotic situation)
        if state.wickets > 7:
            confidence -= 0.08
        
        return max(0.0, min(1.0, confidence))
    
    def _generate_reasoning(
        self,
        prefix: str,
        state: MatchState,
        signal_type: str,
        suspension_duration: float,
        stake_limit_drop: float,
        odds_increased: bool,
        final_confidence: float
    ) -> str:
        """Generate human-readable reasoning"""
        
        reasoning_parts = [prefix]
        
        if suspension_duration > 0:
            reasoning_parts.append(f"Suspension: {suspension_duration:.1f}s.")
        
        if stake_limit_drop > 0:
            reasoning_parts.append(f"Stake limit dropped {stake_limit_drop:.0%}.")
        
        direction = "increased" if odds_increased else "decreased"
        reasoning_parts.append(f"Odds {direction} during suspension.")
        
        reasoning_parts.append(f"Following smart money direction: {signal_type}.")
        
        reasoning_parts.append(f"Confidence: {final_confidence:.1%}. Edge window: 30s.")
        
        return " ".join(reasoning_parts)
    
    def _build_match_context(
        self,
        state: MatchState,
        event: Dict,
        signal_type: str,
        suspension_duration: float,
        stake_limit_drop: float
    ) -> Dict:
        """Build match context"""
        return {
            'match_id': state.match_id,
            'overs': state.overs,
            'score': f"{state.score}/{state.wickets}",
            'signal_type': signal_type,
            'suspension_duration': suspension_duration,
            'stake_limit_drop': stake_limit_drop,
            'suspension_count': state.suspension_count,
            'odds_before': state.previous_odds,
            'odds_after': state.current_odds,
            'stake_limit_before': state.previous_stake_limit,
            'stake_limit_after': state.stake_limit,
            'match_phase': state.match_phase
        }

