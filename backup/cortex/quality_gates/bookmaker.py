"""
Gate 4: Bookmaker Reality Check Gate
Validates signal against bookmaker behavior and market realities
"""

from cortex.quality_gates.base import BaseQualityGate, GateResult
from loguru import logger


class BookmakerGate(BaseQualityGate):
    """
    Gate 4: Bookmaker Reality Check
    
    Checks:
    1. Odds movement patterns are realistic
    2. Market isn't suspended too frequently
    3. Stake limits aren't too restrictive
    4. No signs of market manipulation
    5. Edge window is sufficient for execution
    """
    
    def __init__(self):
        super().__init__("bookmaker")
        self.min_edge_window = 15  # Minimum seconds to execute
        self.max_suspension_rate = 0.5  # Max 50% of time suspended
        self.min_stake_limit = 1000  # Minimum viable stake
    
    def evaluate(self, signal, match_state) -> GateResult:
        """Evaluate bookmaker reality"""
        
        try:
            # Check 1: Edge window sufficiency
            if signal.edge_window_seconds < self.min_edge_window:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Edge window too short: {signal.edge_window_seconds}s (min: {self.min_edge_window}s)",
                    severity="critical"
                )
            
            # Check 2: Market suspension frequency
            if match_state.suspension_count > 10:
                # Too many suspensions - unstable market
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Market unstable: {match_state.suspension_count} suspensions",
                    severity="warning"
                )
            
            # Check 3: Currently suspended check
            if match_state.is_suspended:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason="Market currently suspended",
                    severity="warning"
                )
            
            # Check 4: Stake limit check
            if match_state.stake_limit is not None:
                if match_state.stake_limit < self.min_stake_limit:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Stake limit too low: {match_state.stake_limit} (min: {self.min_stake_limit})",
                        severity="warning"
                    )
            
            # Check 5: Odds movement realism
            velocity = match_state.odds_velocity()
            if velocity > 0.50:  # More than 50% movement
                # Extreme movement - possible error or manipulation
                if signal.strategy != 'whale_shadow':  # Whale Shadow expects large moves
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Extreme odds movement: {velocity:.1%} (possible error)",
                        severity="warning"
                    )
            
            # Check 6: Odds history consistency
            if len(match_state.odds_history) > 5:
                # Check for suspicious patterns (flat lines, spikes)
                recent_odds = [h['odds'] for h in list(match_state.odds_history)[-5:]]
                
                # All identical = suspicious
                if len(set(recent_odds)) == 1:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason="Odds not updating (stale data)",
                        severity="warning"
                    )
            
            # Confidence boost for good market conditions
            confidence_boost = 0.0
            
            # Long edge window = more time to execute
            if signal.edge_window_seconds >= 45:
                confidence_boost += 0.02
            
            # Stable market (few suspensions)
            if match_state.suspension_count < 3:
                confidence_boost += 0.01
            
            # High stake limit available
            if match_state.stake_limit and match_state.stake_limit >= 10000:
                confidence_boost += 0.01
            
            self.record_result(True)
            return GateResult(
                gate_name=self.name,
                passed=True,
                confidence_adjustment=confidence_boost,
                reason="Bookmaker reality check passed",
                severity="info"
            )
        
        except Exception as e:
            logger.error(f"Bookmaker Gate error: {e}")
            self.record_result(False)
            return GateResult(
                gate_name=self.name,
                passed=False,
                reason=f"Gate evaluation error: {str(e)}",
                severity="critical"
            )

