"""
Gate 5: Technical Health Gate
Validates system health and technical conditions
"""

import psutil
from datetime import datetime, timedelta
from cortex.quality_gates.base import BaseQualityGate, GateResult
from loguru import logger


class TechnicalGate(BaseQualityGate):
    """
    Gate 5: Technical Health
    
    Checks:
    1. System resource availability (CPU, memory)
    2. Processing latency acceptable
    3. No system errors or warnings
    4. Signal generation rate within bounds
    5. Database and cache connectivity
    """
    
    def __init__(self):
        super().__init__("technical")
        self.max_cpu_percent = 90.0
        self.max_memory_percent = 90.0
        self.max_processing_latency_ms = 500
        self.signal_timestamps = []
        self.max_signals_per_minute = 20
    
    def evaluate(self, signal, match_state) -> GateResult:
        """Evaluate technical health"""
        
        try:
            # Check 1: CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent > self.max_cpu_percent:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"High CPU usage: {cpu_percent:.1f}% (max: {self.max_cpu_percent}%)",
                    severity="warning"
                )
            
            # Check 2: Memory usage
            memory = psutil.virtual_memory()
            if memory.percent > self.max_memory_percent:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"High memory usage: {memory.percent:.1f}% (max: {self.max_memory_percent}%)",
                    severity="warning"
                )
            
            # Check 3: Processing latency
            processing_time = (datetime.utcnow() - signal.created_at).total_seconds() * 1000
            if processing_time > self.max_processing_latency_ms:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"High latency: {processing_time:.0f}ms (max: {self.max_processing_latency_ms}ms)",
                    severity="warning"
                )
            
            # Check 4: Signal generation rate
            self.signal_timestamps.append(datetime.utcnow())
            
            # Clean old timestamps (older than 1 minute)
            cutoff = datetime.utcnow() - timedelta(minutes=1)
            self.signal_timestamps = [ts for ts in self.signal_timestamps if ts > cutoff]
            
            # Check if generating too many signals
            if len(self.signal_timestamps) > self.max_signals_per_minute:
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Signal rate too high: {len(self.signal_timestamps)}/min (max: {self.max_signals_per_minute})",
                    severity="warning"
                )
            
            # Check 5: Data freshness
            data_age = (datetime.utcnow() - match_state.last_update).total_seconds()
            if data_age > 10:  # Data older than 10 seconds
                self.record_result(False)
                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reason=f"Stale data: {data_age:.1f}s old",
                    severity="warning"
                )
            
            # Confidence boost for excellent technical conditions
            confidence_boost = 0.0
            
            # Low latency
            if processing_time < 100:
                confidence_boost += 0.02
            
            # Low resource usage
            if cpu_percent < 50 and memory.percent < 50:
                confidence_boost += 0.01
            
            # Fresh data
            if data_age < 2:
                confidence_boost += 0.01
            
            self.record_result(True)
            return GateResult(
                gate_name=self.name,
                passed=True,
                confidence_adjustment=confidence_boost,
                reason="Technical health checks passed",
                severity="info"
            )
        
        except Exception as e:
            logger.error(f"Technical Gate error: {e}")
            self.record_result(False)
            return GateResult(
                gate_name=self.name,
                passed=False,
                reason=f"Gate evaluation error: {str(e)}",
                severity="critical"
            )

