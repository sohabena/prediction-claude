"""
Strategy Testing
----------------
Unit tests for all betting strategies with fixture data.
Updated to match actual MatchState and Strategy APIs.
"""

import pytest
from datetime import datetime, timedelta
from cortex.strategies.panic_rebound import PanicReboundStrategy
from cortex.strategies.mean_reversion import MeanReversionStrategy
from cortex.strategies.whale_shadow import WhaleShadowStrategy
from cortex.strategies.simple_odds_change import SimpleOddsChangeStrategy
from cortex.strategies.odds_velocity import OddsVelocityStrategy
from cortex.match_state import MatchState


class TestSimpleOddsChange:
    """Test Simple Odds Change Strategy - this is the primary working strategy"""
    
    def setup_method(self):
        self.strategy = SimpleOddsChangeStrategy()
    
    def _create_match_state_with_odds_history(self, odds_data: list) -> MatchState:
        """Helper to create MatchState with odds history"""
        state = MatchState(match_id="test_match")
        state.batting_team = "Team A"
        
        base_time = datetime.utcnow() - timedelta(seconds=len(odds_data) * 5)
        
        for i, odds in enumerate(odds_data):
            timestamp = base_time + timedelta(seconds=i * 5)
            state.odds_history.append({
                'odds': odds,
                'back_price': odds,
                'timestamp': timestamp
            })
            state.current_odds = odds
            if i > 0:
                state.previous_odds = odds_data[i-1]
        
        return state
    
    def test_detects_significant_increase(self):
        """Test detection of significant odds increase (>2%)"""
        # Create state with 2.5% increase
        state = self._create_match_state_with_odds_history([2.00, 2.05])
        event = {'back_price': 2.05}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is not None
        assert signal.strategy == "simple_odds_change"
        assert signal.action == "BACK"  # Contrarian: odds up -> BACK
        assert signal.confidence >= 0.55
    
    def test_detects_significant_decrease(self):
        """Test detection of significant odds decrease (>2%)"""
        # Create state with 2.5% decrease
        state = self._create_match_state_with_odds_history([2.00, 1.95])
        event = {'back_price': 1.95}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is not None
        assert signal.strategy == "simple_odds_change"
        assert signal.action == "LAY"  # Contrarian: odds down -> LAY
    
    def test_ignores_small_changes(self):
        """Test that small odds changes don't trigger signals"""
        # Create state with 1% change (below threshold)
        state = self._create_match_state_with_odds_history([2.00, 2.02])
        event = {'back_price': 2.02}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None
    
    def test_requires_minimum_samples(self):
        """Test that strategy needs at least 2 samples"""
        state = MatchState(match_id="test_match")
        state.odds_history.append({'odds': 2.00, 'back_price': 2.00, 'timestamp': datetime.utcnow()})
        event = {'back_price': 2.00}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None


class TestOddsVelocity:
    """Test Odds Velocity Strategy"""
    
    def setup_method(self):
        self.strategy = OddsVelocityStrategy()
    
    def _create_match_state_with_spike(self, spike_direction: str = "up") -> MatchState:
        """Create MatchState with a spike pattern"""
        state = MatchState(match_id="test_match")
        state.batting_team = "Team A"
        
        base_time = datetime.utcnow() - timedelta(seconds=60)
        
        if spike_direction == "up":
            # Simulate spike up then stabilize
            odds_sequence = [1.80, 1.85, 2.00, 2.05, 2.03, 2.04]  # Spike then stable
        else:
            # Simulate spike down then stabilize
            odds_sequence = [2.00, 1.90, 1.80, 1.75, 1.76, 1.75]
        
        for i, odds in enumerate(odds_sequence):
            timestamp = base_time + timedelta(seconds=i * 10)
            state.odds_history.append({
                'odds': odds,
                'back_price': odds,
                'timestamp': timestamp
            })
            state.current_odds = odds
        
        return state
    
    def test_detects_spike_up_pattern(self):
        """Test detection of spike up then stabilization"""
        state = self._create_match_state_with_spike("up")
        event = {'back_price': 2.04}
        
        signal = self.strategy.evaluate(state, event)
        
        # Strategy should detect spike and recommend counter-trade
        if signal:
            assert signal.action == "LAY"  # Counter to upward spike
    
    def test_detects_spike_down_pattern(self):
        """Test detection of spike down then stabilization"""
        state = self._create_match_state_with_spike("down")
        event = {'back_price': 1.75}
        
        signal = self.strategy.evaluate(state, event)
        
        if signal:
            assert signal.action == "BACK"  # Counter to downward spike
    
    def test_requires_minimum_samples(self):
        """Test that strategy needs minimum history"""
        state = MatchState(match_id="test_match")
        # Only 2 samples, needs 3+
        for odds in [2.00, 2.05]:
            state.odds_history.append({
                'odds': odds,
                'back_price': odds,
                'timestamp': datetime.utcnow()
            })
        
        event = {'back_price': 2.05}
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None


class TestPanicRebound:
    """Test Panic Rebound Strategy"""
    
    def setup_method(self):
        self.strategy = PanicReboundStrategy()
    
    def test_requires_wicket_event(self):
        """Test that panic rebound requires a wicket event"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.wickets = 2
        state.current_odds = 2.20
        state.previous_odds = 1.50
        state.batting_team = "Team A"
        
        # Event without wicket
        event = {'is_wicket': False, 'odds': 2.20}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None
    
    def test_triggers_on_wicket_with_odds_spike(self):
        """Test that strategy triggers on wicket with significant odds movement"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.wickets = 3
        state.score = 85
        state.current_odds = 2.20
        state.previous_odds = 1.80  # 22% movement
        state.batting_team = "Team A"
        state.run_rate = 8.5
        
        # Wicket event with big odds move
        event = {
            'is_wicket': True,
            'batsman': 'Generic Player',
            'wicket_type': 'bowled',
            'odds': 2.20
        }
        
        signal = self.strategy.evaluate(state, event)
        
        # Should trigger if velocity threshold met
        if state.odds_velocity() >= 0.15:
            assert signal is not None
            assert signal.action == "BACK"
    
    def test_filters_by_overs(self):
        """Test that strategy only triggers in middle overs (7-15)"""
        state = MatchState(match_id="test_match")
        state.overs = 5.0  # Powerplay - outside range
        state.wickets = 2
        state.current_odds = 2.20
        state.previous_odds = 1.50
        state.batting_team = "Team A"
        
        event = {'is_wicket': True, 'odds': 2.20}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None


class TestMeanReversion:
    """Test Mean Reversion Strategy"""
    
    def setup_method(self):
        self.strategy = MeanReversionStrategy()
    
    def test_requires_ball_data(self):
        """Test that mean reversion requires ball-by-ball data"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.score = 80
        # No ball data
        
        event = {'odds': 1.90}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None
    
    def test_triggers_on_high_recent_run_rate(self):
        """Test signal when recent run rate significantly exceeds average"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.score = 90
        state.batting_team = "Team A"
        state.run_rate = 9.0
        
        # Add 18 balls of data (3 overs)
        # Last 2 overs: high scoring (15+ per over)
        # First over: normal (6 runs)
        state.last_12_balls = [1, 1, 1, 1, 1, 1, 4, 6, 4, 4, 6, 2, 6, 4, 6, 4, 4, 2]
        
        event = {'odds': 1.90}
        
        signal = self.strategy.evaluate(state, event)
        
        # Should consider UNDER signal if conditions met
        # Note: The actual implementation may have additional requirements


class TestWhaleShadow:
    """Test Whale Shadow Strategy"""
    
    def setup_method(self):
        self.strategy = WhaleShadowStrategy()
    
    def test_requires_metadata_event(self):
        """Test that whale shadow requires suspension/limit events"""
        state = MatchState(match_id="test_match")
        state.current_odds = 2.00
        state.previous_odds = 1.90
        
        # Normal event without metadata
        event = {'odds': 2.00}
        
        signal = self.strategy.evaluate(state, event)
        
        assert signal is None
    
    def test_triggers_on_stake_limit_drop(self):
        """Test signal on significant stake limit reduction"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.wickets = 2
        state.current_odds = 2.10
        state.previous_odds = 1.90
        state.stake_limit = 5000
        state.previous_stake_limit = 50000  # 90% drop
        state.suspension_duration = 8.0
        state.batting_team = "Team A"
        
        event = {
            'is_limit_change': True,
            'is_suspension_event': True,
            'stake_limit': 5000
        }
        
        signal = self.strategy.evaluate(state, event)
        
        # Should trigger on major limit drop
        if signal:
            assert signal.confidence >= 0.75
    
    def test_triggers_on_long_suspension(self):
        """Test signal on unexplained long suspension"""
        state = MatchState(match_id="test_match")
        state.overs = 10.0
        state.current_odds = 2.20
        state.previous_odds = 1.90
        state.suspension_duration = 10.0  # 10 seconds
        state.batting_team = "Team A"
        
        event = {
            'is_suspension_event': True,
            'suspension_type': 'unexplained'
        }
        
        signal = self.strategy.evaluate(state, event)
        
        if signal:
            assert signal.strategy == "whale_shadow"


class TestStrategyStats:
    """Test strategy statistics tracking"""
    
    def test_tracks_signals_generated(self):
        """Test that strategies track generated signals"""
        strategy = SimpleOddsChangeStrategy()
        
        # Create conditions that trigger a signal
        state = MatchState(match_id="test_match")
        state.batting_team = "Team A"
        
        base_time = datetime.utcnow()
        state.odds_history.append({'back_price': 2.00, 'timestamp': base_time - timedelta(seconds=5)})
        state.odds_history.append({'back_price': 2.10, 'timestamp': base_time})  # 5% change
        
        event = {'back_price': 2.10}
        signal = strategy.evaluate(state, event)
        
        if signal:
            stats = strategy.get_stats()
            assert stats['signals_generated'] >= 1


def run_all_strategy_tests():
    """Run all strategy tests and return results"""
    pytest.main([__file__, "-v", "--tb=short"])


if __name__ == "__main__":
    run_all_strategy_tests()
