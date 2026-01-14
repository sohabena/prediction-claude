"""
Base Quality Gate
Abstract base class for all quality gates
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class GateResult:
    """Result from a quality gate evaluation"""
    
    gate_name: str
    passed: bool
    confidence_adjustment: float = 0.0  # Positive = boost, negative = penalty
    reason: str = ""
    severity: str = "info"  # info, warning, critical
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class BaseQualityGate(ABC):
    """Abstract base class for quality gates"""
    
    def __init__(self, name: str):
        self.name = name
        self.signals_evaluated = 0
        self.signals_passed = 0
        self.signals_suppressed = 0
    
    @abstractmethod
    def evaluate(self, signal, match_state) -> GateResult:
        """
        Evaluate a signal against this quality gate
        
        Args:
            signal: The signal to evaluate
            match_state: Current match state
            
        Returns:
            GateResult with pass/fail and reasoning
        """
        pass
    
    def record_result(self, passed: bool):
        """Record gate evaluation result"""
        self.signals_evaluated += 1
        if passed:
            self.signals_passed += 1
        else:
            self.signals_suppressed += 1
    
    def get_stats(self):
        """Get gate statistics"""
        suppression_rate = 0.0
        if self.signals_evaluated > 0:
            suppression_rate = self.signals_suppressed / self.signals_evaluated
        
        return {
            'gate_name': self.name,
            'signals_evaluated': self.signals_evaluated,
            'signals_passed': self.signals_passed,
            'signals_suppressed': self.signals_suppressed,
            'suppression_rate': suppression_rate
        }

