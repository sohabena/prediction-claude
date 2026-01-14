"""
Gate 1: Data Quality Gate
Ensures input data is fresh, complete, and valid
"""

from datetime import datetime, timedelta
from cortex.quality_gates.base import BaseQualityGate, GateResult
from loguru import logger


class DataQualityGate(BaseQualityGate):
    """
    Gate 1: Data Quality
    
    Checks:
    1. Data freshness (<5 seconds old)
    2. Required fields present
    3. Data consistency (odds in valid range)
    4. No suspicious data patterns
    """
    
    def __init__(self):
        super().__init__("data_quality")
        self.max_data_age_seconds = 5
        self.min_odds = 1.01
        self.max_odds = 100.0
    
    def evaluate(self, signal, match_state) -> GateResult:
        """Evaluate signal data quality"""
        
        try:
            # Check 1: Data freshness
            data_age = (datetime.utcnow() - match_state.last_update).total_seconds()
            if data_age > self.max_data_age_seconds:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Stale data: {data_age:.1f}s old (max {self.max_data_age_seconds}s)",
                    severity="critical"
                )
            
            # Check 2: Required fields present
            required_fields = ['odds', 'team', 'action', 'confidence']
            missing_fields = [f for f in required_fields if not hasattr(signal, f) or getattr(signal, f) is None]
            
            if missing_fields:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Missing required fields: {', '.join(missing_fields)}",
                    severity="critical"
                )
            
            # Check 3: Odds validity
            if not (self.min_odds <= signal.odds <= self.max_odds):
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Invalid odds: {signal.odds} (range: {self.min_odds}-{self.max_odds})",
                    severity="critical"
                )
            
            # Check 4: Match state consistency
            if match_state.wickets > 10:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Invalid match state: {match_state.wickets} wickets",
                    severity="critical"
                )
            
            if match_state.overs > 20:  # T20 max
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Invalid overs: {match_state.overs} (T20 max: 20)",
                    severity="warning"
                )
            
            # Check 5: Confidence bounds
            if not (0.0 <= signal.confidence <= 1.0):
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Invalid confidence: {signal.confidence} (range: 0.0-1.0)",
                    severity="critical"
                )
            
            # All checks passed
            confidence_boost = 0.0
            if data_age < 1.0:  # Very fresh data
                confidence_boost = 0.02
            
            self.record_result(True)
            return GateResult(
                gate_name=self.name,
                passed=True,
                confidence_adjustment=confidence_boost,
                reason="All data quality checks passed",
                severity="info"
            )
        
        except Exception as e:
            logger.error(f"Data Quality Gate error: {e}")
            self.record_result(False)
            return GateResult(
                gate_name=self.name,
                passed=False,
                reason=f"Gate evaluation error: {str(e)}",
                severity="critical"
            )

