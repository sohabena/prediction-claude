"""
Gate 3: Context Validation Gate
Ensures signal makes sense in current match context
"""

from cortex.quality_gates.base import BaseQualityGate, GateResult
from loguru import logger


class ContextGate(BaseQualityGate):
    """
    Gate 3: Context Validation
    
    Checks:
    1. Match phase appropriateness
    2. Required run rate feasibility (if chasing)
    3. Wickets in hand sufficiency
    4. Overs remaining adequacy
    5. No conflicting signals
    """
    
    def __init__(self):
        super().__init__("context")
        self.recent_signals = []  # Track recent signals
        self.max_recent_signals = 10
    
    def evaluate(self, signal, match_state) -> GateResult:
        """Evaluate match context appropriateness"""
        
        try:
            # Check 1: Match phase validation
            phase_valid = self._validate_match_phase(signal, match_state)
            if not phase_valid[0]:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=phase_valid[1],
                    severity="warning"
                )
            
            # Check 2: Required run rate feasibility (if chasing)
            if match_state.required_run_rate is not None:
                if match_state.required_run_rate > 15.0:
                    # Too high - unlikely to chase
                    if signal.action == 'BACK' and signal.team == match_state.batting_team:
                        self.record_result(False)
                        return GateResult(
                            gate_name=self.name,
                            passed=False,
                            reason=f"RRR too high: {match_state.required_run_rate:.1f} (max: 15)",
                            severity="warning"
                        )
            
            # Check 3: Wickets in hand
            wickets_remaining = 10 - match_state.wickets
            if wickets_remaining < 3:
                # Very few wickets left - risky situation
                if signal.confidence < 0.80:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Only {wickets_remaining} wickets left, need high confidence",
                        severity="warning"
                    )
            
            # Check 4: Overs remaining
            overs_remaining = 20.0 - match_state.overs  # T20 assumption
            if overs_remaining < 2.0:
                # Late stage - unpredictable
                if signal.strategy in ['mean_reversion', 'panic_rebound']:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Too late in innings: {overs_remaining:.1f} overs left",
                        severity="info"
                    )
            
            # Check 5: Duplicate/conflicting signals
            conflict = self._check_signal_conflicts(signal, match_state)
            if conflict[0]:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=conflict[1],
                    severity="info"
                )
            
            # Context boost for optimal conditions
            confidence_boost = 0.0
            if 6 <= match_state.overs <= 15 and wickets_remaining >= 6:
                confidence_boost = 0.02  # Ideal match phase
            
            # Track this signal
            self.recent_signals.append({
                'signal_id': signal.signal_id,
                'match_id': match_state.match_id,
                'strategy': signal.strategy,
                'action': signal.action,
                'overs': match_state.overs
            })
            
            # Keep only recent signals
            if len(self.recent_signals) > self.max_recent_signals:
                self.recent_signals.pop(0)
            
            self.record_result(True)
            return GateResult(
                gate_name=self.name,
                passed=True,
                confidence_adjustment=confidence_boost,
                reason="Context validation passed",
                severity="info"
            )
        
        except Exception as e:
            logger.error(f"Context Gate error: {e}")
            self.record_result(False)
            return GateResult(
                gate_name=self.name,
                passed=False,
                reason=f"Gate evaluation error: {str(e)}",
                severity="critical"
            )
    
    def _validate_match_phase(self, signal, match_state):
        """Validate signal is appropriate for current match phase"""
        
        # Panic Rebound: Middle overs only (7-15)
        if signal.strategy == 'panic_rebound':
            if not (7 <= match_state.overs <= 15):
                return (False, f"Panic Rebound invalid for over {match_state.overs:.1f}")
        
        # Mean Reversion: Middle overs (8-14)
        if signal.strategy == 'mean_reversion':
            if not (8 <= match_state.overs <= 14):
                return (False, f"Mean Reversion invalid for over {match_state.overs:.1f}")
        
        # Whale Shadow: Any phase except very late
        if signal.strategy == 'whale_shadow':
            if match_state.overs > 18:
                return (False, f"Whale Shadow invalid for over {match_state.overs:.1f}")
        
        return (True, "Phase valid")
    
    def _check_signal_conflicts(self, signal, match_state):
        """Check for conflicting recent signals"""
        
        # Check for duplicate signals in same over
        for recent in self.recent_signals:
            if recent['match_id'] == match_state.match_id:
                # Same match
                over_diff = abs(match_state.overs - recent['overs'])
                
                if over_diff < 1.0:  # Within same over
                    if recent['strategy'] == signal.strategy:
                        return (True, f"Duplicate {signal.strategy} signal in same over")
                    
                    # Conflicting actions
                    if recent['action'] != signal.action and over_diff < 0.5:
                        return (True, f"Conflicting signal: {recent['action']} vs {signal.action}")
        
        return (False, "No conflicts")

