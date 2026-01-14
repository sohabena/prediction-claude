"""
Quality Gate Testing
--------------------
Tests for the 5-gate quality system.
"""

import pytest
from datetime import datetime, timedelta
from cortex.quality_gates.data_quality import DataQualityGate
from cortex.quality_gates.statistical import StatisticalGate
from cortex.quality_gates.context import ContextGate
from cortex.quality_gates.bookmaker import BookmakerGate
from cortex.quality_gates.technical import TechnicalGate
from cortex.match_state import MatchState


class TestDataQualityGate:
    """Test Gate 1: Data Quality"""
    
    def setup_method(self):
        self.gate = DataQualityGate()
    
    def test_passes_fresh_data(self):
        """Test that fresh data passes the gate"""
        signal = {
            "timestamp": datetime.now().isoformat(),
            "odds": 2.0,
            "confidence": 0.8
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.0, "away": 2.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.0}],
            volume_data={"home": 1000}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is True
        assert result.score > 0.7
    
    def test_rejects_stale_data(self):
        """Test that stale data is rejected"""
        old_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        signal = {
            "timestamp": old_time,
            "odds": 2.0,
            "confidence": 0.8
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.0, "away": 2.0},
            odds_history=[{"timestamp": old_time, "home": 2.0}],
            volume_data={}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is False


class TestStatisticalGate:
    """Test Gate 2: Statistical Confidence"""
    
    def setup_method(self):
        self.gate = StatisticalGate()
    
    def test_passes_high_confidence_signal(self):
        """Test that high confidence signals pass"""
        signal = {
            "confidence": 0.85,
            "edge": 0.12,
            "odds": 2.5
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.5, "away": 1.6},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.0}] * 30,
            volume_data={"home": 5000}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is True
        assert result.score > 0.7
    
    def test_rejects_low_confidence_signal(self):
        """Test that low confidence signals are rejected"""
        signal = {
            "confidence": 0.45,
            "edge": 0.02,
            "odds": 1.5
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 1.5, "away": 3.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 1.5}] * 5,
            volume_data={}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is False


class TestContextGate:
    """Test Gate 3: Context Validation"""
    
    def setup_method(self):
        self.gate = ContextGate()
    
    def test_passes_appropriate_match_phase(self):
        """Test that signals in appropriate match phases pass"""
        signal = {
            "context": {
                "match_phase": "mid_game",
                "score_pressure": "moderate"
            }
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.0, "away": 2.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.0}] * 20,
            volume_data={"home": 3000}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is True
    
    def test_rejects_inappropriate_context(self):
        """Test that signals in risky contexts are rejected"""
        signal = {
            "context": {
                "match_phase": "first_ball",  # Too early
                "score_pressure": "unknown"
            }
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 1.5, "away": 3.0},
            odds_history=[],  # No history
            volume_data={}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is False


class TestBookmakerGate:
    """Test Gate 4: Bookmaker Reality Check"""
    
    def setup_method(self):
        self.gate = BookmakerGate()
    
    def test_passes_realistic_edge(self):
        """Test that realistic edges pass"""
        signal = {
            "odds": 2.5,
            "edge": 0.08,  # 8% edge is realistic
            "market": "match_odds"
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.5, "away": 1.6},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.4}] * 15,
            volume_data={"home": 10000}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is True
    
    def test_rejects_unrealistic_edge(self):
        """Test that unrealistic edges are rejected"""
        signal = {
            "odds": 1.5,
            "edge": 0.25,  # 25% edge is too good to be true
            "market": "match_odds"
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 1.5, "away": 3.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 1.5}] * 3,
            volume_data={}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is False


class TestTechnicalGate:
    """Test Gate 5: Technical Health"""
    
    def setup_method(self):
        self.gate = TechnicalGate()
    
    def test_passes_healthy_system(self):
        """Test that signals from healthy system pass"""
        signal = {
            "latency_ms": 50,
            "data_completeness": 0.98
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.0, "away": 2.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.0}] * 50,
            volume_data={"home": 5000}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is True
    
    def test_rejects_degraded_system(self):
        """Test that signals from degraded system are rejected"""
        signal = {
            "latency_ms": 2000,  # High latency
            "data_completeness": 0.65  # Low completeness
        }
        match_state = MatchState(
            match_id="test",
            match_name="Test",
            current_odds={"home": 2.0, "away": 2.0},
            odds_history=[{"timestamp": datetime.now().isoformat(), "home": 2.0}] * 5,
            volume_data={}
        )
        
        result = self.gate.evaluate(signal, match_state)
        assert result.passed is False


def run_all_gate_tests():
    """Run all quality gate tests"""
    pytest.main([__file__, "-v", "--tb=short"])


if __name__ == "__main__":
    run_all_gate_tests()

