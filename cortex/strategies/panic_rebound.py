"""
Panic Rebound Strategy
Exploits overreactions to wickets - 64% win rate target

Strategy: When a wicket falls and odds spike dramatically, bet on the batting team
to recover if conditions suggest the panic is overblown.

Phase 2.1: Enhanced logging for validation
"""

from typing import Optional, Dict
from datetime import datetime
from cortex.strategies.base import BaseStrategy, Signal
from cortex.match_state import MatchState
from loguru import logger


class PanicReboundStrategy(BaseStrategy):
    """
    Panic Rebound Strategy - Counter-punching on wicket-induced panic
    
    Target Win Rate: 64%
    Signals Per Match: 3-4
    Edge Window: 30-60 seconds
    
    REQUIRED DATA (from Cricbuzz enricher):
    - is_wicket: True when wicket falls
    - overs: Current overs (for phase detection)
    - wickets: Current wickets (for filter)
    """
    
    def __init__(self):
        super().__init__("panic_rebound")
        
        # Strategy parameters (tuned from Master Debate)
        self.min_odds_velocity = 0.15  # 15% odds movement
        self.min_confidence = 0.70
        self.target_confidence = 0.80
        
        # Validation tracking
        self.events_with_wicket = 0
        self.events_without_overs = 0
        self.events_without_odds_history = 0
        
        # Star players list (would be loaded from database in production)
        self.star_players = [
            'Virat Kohli', 'Rohit Sharma', 'MS Dhoni', 'KL Rahul',
            'Babar Azam', 'Kane Williamson', 'Steve Smith', 'David Warner'
        ]
    
    def evaluate(self, state: MatchState, event: Dict) -> Optional[Signal]:
        """
        Evaluate if panic rebound conditions are met
        
        Triggers:
        1. Wicket just fell
        2. Odds moved rapidly (>15%)
        3. Match phase is middle overs (7-15)
        4. Batsman quality not critical
        5. RRR not too high (<9.5)
        6. Momentum was building
        7. Wicket type not catastrophic
        """
        
        # Check if this is a wicket event
        is_wicket = event.get('is_wicket', False)
        if not is_wicket:
            return None
        
        # Phase 2.1: WICKET DETECTED - Log all details for validation
        self.events_with_wicket += 1
        logger.warning(
            f"🎯 PANIC REBOUND: WICKET DETECTED! "
            f"match={state.match_id}, "
            f"overs={state.overs}, "
            f"wickets={state.wickets}, "
            f"score={state.score}, "
            f"odds={state.current_odds}, "
            f"prev_odds={state.previous_odds}, "
            f"odds_history_size={len(state.odds_history)}"
        )
        
        # Validate data availability
        if state.overs == 0 and state.wickets == 0:
            self.events_without_overs += 1
            logger.warning(
                f"⚠️ PANIC REBOUND: Missing cricket data! "
                f"overs={state.overs}, wickets={state.wickets}. "
                f"Cricbuzz enricher may not be running."
            )
        
        if len(state.odds_history) < 2:
            self.events_without_odds_history += 1
            logger.warning(
                f"⚠️ PANIC REBOUND: Insufficient odds history ({len(state.odds_history)}). "
                f"Need at least 2 data points for velocity calculation."
            )
        
        # Filter 1: Check odds velocity (rapid movement)
        odds_velocity = state.odds_velocity()
        if odds_velocity < self.min_odds_velocity:
            logger.debug(f"Panic Rebound: Odds velocity {odds_velocity:.2%} below threshold")
            return None
        
        # Filter 2: Match phase (overs 7-15 only)
        if not (7 <= state.overs <= 15):
            logger.debug(f"Panic Rebound: Overs {state.overs} outside target range")
            return None
        
        # Filter 3: Batsman quality check
        dismissed_batsman = event.get('batsman', '')
        confidence_penalty = 0.0
        
        if self._is_star_player(dismissed_batsman):
            confidence_penalty = 0.25  # Major penalty for star player
            logger.debug(f"Panic Rebound: Star player {dismissed_batsman} dismissed")
        
        # Filter 4: RRR check (if chasing)
        if state.required_run_rate is not None:
            if state.required_run_rate > 9.5:
                logger.debug(f"Panic Rebound: RRR {state.required_run_rate} too high")
                return None
        
        # Filter 5: Momentum check (was building before wicket?)
        recent_run_rate = state.get_rolling_run_rate(overs=2)
        if recent_run_rate < state.run_rate * 0.8:
            # Momentum was already slowing
            confidence_penalty += 0.15
            logger.debug(f"Panic Rebound: Momentum already slowing")
        
        # Filter 6: Wicket type check
        wicket_type = event.get('wicket_type', 'unknown')
        if wicket_type in ['run_out', 'hit_wicket']:
            # Less panic-inducing wickets
            confidence_penalty += 0.10
        
        # Filter 7: Wickets in hand
        wickets_remaining = 10 - state.wickets
        if wickets_remaining < 4:
            # Too few wickets left
            logger.debug(f"Panic Rebound: Only {wickets_remaining} wickets remaining")
            return None
        
        # Calculate base confidence
        base_confidence = 0.75
        
        # Adjust for odds velocity (higher velocity = higher confidence)
        velocity_bonus = min(odds_velocity - self.min_odds_velocity, 0.15)
        
        # Adjust for match phase (middle overs are best)
        phase_bonus = 0.05 if 10 <= state.overs <= 13 else 0.0
        
        # Adjust for wickets in hand
        wickets_bonus = 0.05 if wickets_remaining >= 7 else 0.0
        
        # Calculate final confidence
        final_confidence = (
            base_confidence 
            + velocity_bonus 
            + phase_bonus 
            + wickets_bonus 
            - confidence_penalty
        )
        
        # Ensure confidence is within bounds
        final_confidence = max(0.0, min(1.0, final_confidence))
        
        # Check if confidence meets minimum threshold
        if final_confidence < self.min_confidence:
            logger.debug(f"Panic Rebound: Confidence {final_confidence:.2%} below threshold")
            self.signals_suppressed += 1
            return None
        
        # Calculate base stake and noise
        base_stake = self._calculate_kelly_stake(final_confidence, state.current_odds)
        stake_min, stake_max = self._calculate_stake_range(base_stake)
        delay = self._generate_random_delay()
        
        # Generate signal with anti-detection features
        signal = Signal(
            signal_id=self._generate_signal_id(state),
            strategy=self.name,
            action='BACK',
            team=state.batting_team,
            odds=state.current_odds,
            confidence=final_confidence,
            stake_recommended=base_stake,
            stake_min=stake_min,
            stake_max=stake_max,
            edge_window_seconds=45,  # 45 second window
            recommended_delay_seconds=delay,
            reasoning=self._generate_reasoning(
                state, event, odds_velocity, dismissed_batsman, 
                confidence_penalty, final_confidence
            ),
            match_context=self._build_match_context(state, event),
            created_at=datetime.utcnow()
        )
        
        self.signals_generated += 1
        logger.info(f"✅ Panic Rebound Signal: {signal.team} @ {signal.odds:.2f} ({signal.confidence:.1%})")
        
        return signal
    
    def _is_star_player(self, player_name: str) -> bool:
        """Check if player is a star player"""
        return any(star.lower() in player_name.lower() for star in self.star_players)
    
    def _generate_reasoning(
        self, 
        state: MatchState, 
        event: Dict, 
        odds_velocity: float,
        dismissed_batsman: str,
        confidence_penalty: float,
        final_confidence: float
    ) -> str:
        """Generate human-readable reasoning for the signal"""
        
        reasoning_parts = [
            f"Wicket fell at {state.overs} overs, odds spiked {odds_velocity:.1%}.",
            f"Market overreacting - {state.batting_team} has {10 - state.wickets} wickets in hand.",
        ]
        
        if state.required_run_rate:
            reasoning_parts.append(f"RRR manageable at {state.required_run_rate:.1f}.")
        
        if confidence_penalty > 0:
            if self._is_star_player(dismissed_batsman):
                reasoning_parts.append(f"⚠️ Star player {dismissed_batsman} dismissed.")
            else:
                reasoning_parts.append(f"Dismissed: {dismissed_batsman}.")
        else:
            reasoning_parts.append(f"Non-critical batsman dismissed.")
        
        recent_rr = state.get_rolling_run_rate(overs=2)
        reasoning_parts.append(f"Recent run rate: {recent_rr:.1f}.")
        
        reasoning_parts.append(f"Confidence: {final_confidence:.1%}. Edge window: 45s.")
        
        return " ".join(reasoning_parts)
    
    def _build_match_context(self, state: MatchState, event: Dict) -> Dict:
        """Build match context for signal"""
        return {
            'match_id': state.match_id,
            'overs': state.overs,
            'score': f"{state.score}/{state.wickets}",
            'run_rate': state.run_rate,
            'required_run_rate': state.required_run_rate,
            'wickets_remaining': 10 - state.wickets,
            'match_phase': state.match_phase,
            'dismissed_batsman': event.get('batsman', 'Unknown'),
            'wicket_type': event.get('wicket_type', 'unknown'),
            'odds_before': state.previous_odds,
            'odds_after': state.current_odds,
            'odds_velocity': state.odds_velocity()
        }
    
    def get_stats(self) -> Dict:
        """Get strategy statistics including validation metrics"""
        base_stats = super().get_stats()
        
        # Phase 2.1: Add validation metrics
        base_stats['validation'] = {
            'events_with_wicket': self.events_with_wicket,
            'events_without_overs': self.events_without_overs,
            'events_without_odds_history': self.events_without_odds_history,
            'data_quality_issues': self.events_without_overs + self.events_without_odds_history
        }
        
        return base_stats

