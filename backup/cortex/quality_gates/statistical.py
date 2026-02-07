"""
Gate 2: Statistical Confidence Gate
Validates statistical significance and confidence levels
"""

from cortex.quality_gates.base import BaseQualityGate, GateResult
from loguru import logger


class StatisticalGate(BaseQualityGate):
    """
    Gate 2: Statistical Confidence
    
    Checks:
    1. Confidence meets minimum threshold (70%)
    2. Statistical significance of patterns
    3. Sample size adequacy
    4. Variance within acceptable range
    """
    
    def __init__(self):
        super().__init__("statistical")
        self.min_confidence = 0.70
        self.min_data_points = 6  # Minimum balls tracked
        self.max_variance_multiplier = 3.0
    
    def evaluate(self, signal, match_state) -> GateResult:
        """Evaluate statistical confidence"""
        
        try:
            # Check 1: Minimum confidence threshold
            if signal.confidence < self.min_confidence:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Confidence {signal.confidence:.1%} below threshold {self.min_confidence:.0%}",
                    severity="warning"
                )
            
            # Check 2: Sample size (for mean reversion and momentum strategies)
            if signal.strategy in ['mean_reversion', 'panic_rebound']:
                data_points = len(match_state.last_12_balls)
                if data_points < self.min_data_points:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Insufficient data: {data_points} balls (min: {self.min_data_points})",
                        severity="warning"
                    )
            
            # Check 3: Variance check (for mean reversion)
            if signal.strategy == 'mean_reversion':
                variance = match_state.calculate_std_dev()
                if variance > self.max_variance_multiplier * match_state.run_rate:
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Excessive variance: {variance:.2f} (run rate: {match_state.run_rate:.2f})",
                        severity="warning"
                    )
            
            # Check 4: Odds movement validation (for panic rebound)
            if signal.strategy == 'panic_rebound':
                velocity = match_state.odds_velocity()
                if velocity < 0.10:  # Less than 10% movement
                    self.record_result(False)
                    return GateResult(
                        gate_name=self.name,
                        passed=False,
                        reason=f"Insufficient odds movement: {velocity:.1%}",
                        severity="info"
                    )
            
            # Confidence boost for high-confidence signals
            confidence_boost = 0.0
            if signal.confidence >= 0.85:
                confidence_boost = 0.03
            elif signal.confidence >= 0.80:
                confidence_boost = 0.02
            
            self.record_result(True)
            return GateResult(
                gate_name=self.name,
                passed=True,
                confidence_adjustment=confidence_boost,
                reason="Statistical checks passed",
                severity="info"
            )
        
        except Exception as e:
            logger.error(f"Statistical Gate error: {e}")
            self.record_result(False)
            return GateResult(
                gate_name=self.name,
                passed=False,
                reason=f"Gate evaluation error: {str(e)}",
                severity="critical"
            )

