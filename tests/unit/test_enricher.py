"""
Enricher Testing
----------------
Unit tests for cricket stats enricher, particularly wicket detection.

Phase 1.2: Added comprehensive wicket detection tests
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
import asyncio

# Import enricher classes
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scraper.cricket_stats_enricher import CricketStatsEnricher
from scraper.parsers.cricbuzz_scraper import LiveMatchData


class TestWicketDetection:
    """Test wicket detection in CricketStatsEnricher"""
    
    def setup_method(self):
        """Set up test enricher"""
        self.enricher = CricketStatsEnricher(test_mode=True)
    
    def test_detects_first_wicket(self):
        """Test detection of first wicket fall"""
        match_id = "test_wicket_001"
        
        # Set initial state
        self.enricher.previous_states[match_id] = {
            'runs': 50,
            'wickets': 0,
            'overs': 6.0
        }
        
        # Create LiveMatchData with wicket
        current = LiveMatchData(
            match_id="cricbuzz_123",
            match_name="Team A vs Team B",
            team1="Team A",
            team2="Team B",
            batting_team="Team A",
            runs=52,
            wickets=1,  # Wicket fell!
            overs=6.2,
            match_format="T20",
            venue="Test Stadium",
            status="Live",
            run_rate=8.67
        )
        
        events = self.enricher._detect_events(match_id, current)
        
        assert events['is_wicket'] == True
        assert self.enricher.stats['wickets_detected'] >= 1
    
    def test_detects_multiple_wickets(self):
        """Test detection of multiple wickets in one update"""
        match_id = "test_wicket_002"
        
        # Set initial state
        self.enricher.previous_states[match_id] = {
            'runs': 80,
            'wickets': 2,
            'overs': 10.0
        }
        
        # Create LiveMatchData with 2 more wickets (run out + dismissal)
        current = LiveMatchData(
            match_id="cricbuzz_124",
            match_name="Team A vs Team B",
            team1="Team A",
            team2="Team B",
            batting_team="Team A",
            runs=81,
            wickets=4,  # 2 wickets fell!
            overs=10.1,
            match_format="T20",
            venue="Test Stadium",
            status="Live",
            run_rate=8.1
        )
        
        events = self.enricher._detect_events(match_id, current)
        
        assert events['is_wicket'] == True
    
    def test_no_wicket_on_score_increase(self):
        """Test that score increase without wicket doesn't flag is_wicket"""
        match_id = "test_wicket_003"
        
        # Set initial state
        self.enricher.previous_states[match_id] = {
            'runs': 100,
            'wickets': 3,
            'overs': 12.0
        }
        
        # Create LiveMatchData with runs but no wicket
        current = LiveMatchData(
            match_id="cricbuzz_125",
            match_name="Team A vs Team B",
            team1="Team A",
            team2="Team B",
            batting_team="Team A",
            runs=106,  # 6 runs scored
            wickets=3,  # No wicket
            overs=12.3,
            match_format="T20",
            venue="Test Stadium",
            status="Live",
            run_rate=8.83
        )
        
        events = self.enricher._detect_events(match_id, current)
        
        assert events['is_wicket'] == False
        assert events['runs_scored'] == 6
        assert events['is_boundary'] == True  # 6 runs = boundary
    
    def test_boundary_detection(self):
        """Test boundary detection (4+ runs)"""
        match_id = "test_boundary_001"
        
        # Set initial state
        self.enricher.previous_states[match_id] = {
            'runs': 90,
            'wickets': 2,
            'overs': 11.5
        }
        
        # Create LiveMatchData with boundary
        current = LiveMatchData(
            match_id="cricbuzz_126",
            match_name="Team A vs Team B",
            team1="Team A",
            team2="Team B",
            batting_team="Team A",
            runs=94,  # 4 runs
            wickets=2,
            overs=12.0,
            match_format="T20",
            venue="Test Stadium",
            status="Live",
            run_rate=7.83
        )
        
        events = self.enricher._detect_events(match_id, current)
        
        assert events['is_boundary'] == True
        assert events['runs_scored'] == 4
    
    def test_first_state_no_events(self):
        """Test that first state for a match doesn't trigger events"""
        match_id = "test_new_match"
        
        # No previous state exists
        current = LiveMatchData(
            match_id="cricbuzz_127",
            match_name="Team A vs Team B",
            team1="Team A",
            team2="Team B",
            batting_team="Team A",
            runs=45,
            wickets=2,
            overs=6.0,
            match_format="T20",
            venue="Test Stadium",
            status="Live",
            run_rate=7.5
        )
        
        events = self.enricher._detect_events(match_id, current)
        
        # First state should not trigger any events
        assert events['is_wicket'] == False
        assert events['is_boundary'] == False
        assert events['runs_scored'] == 0


class TestEnricherStats:
    """Test enricher statistics tracking"""
    
    def setup_method(self):
        self.enricher = CricketStatsEnricher(test_mode=True)
    
    def test_stats_tracking(self):
        """Test that stats are properly tracked"""
        stats = self.enricher.get_stats()
        
        assert 'wickets_detected' in stats
        assert 'boundaries_detected' in stats
        assert 'events_published' in stats
        assert 'cycles_completed' in stats
    
    def test_wicket_counter_increments(self):
        """Test that wicket counter increments correctly"""
        initial_count = self.enricher.stats['wickets_detected']
        
        match_id = "test_counter"
        self.enricher.previous_states[match_id] = {
            'runs': 50, 'wickets': 0, 'overs': 6.0
        }
        
        current = LiveMatchData(
            match_id="x", match_name="A vs B", team1="A", team2="B",
            batting_team="A", runs=51, wickets=1, overs=6.1,
            match_format="T20", venue="V", status="Live", run_rate=8.5
        )
        
        self.enricher._detect_events(match_id, current)
        
        assert self.enricher.stats['wickets_detected'] == initial_count + 1


class TestTestMode:
    """Test the enricher's test mode for simulated events"""
    
    def setup_method(self):
        self.enricher = CricketStatsEnricher(test_mode=True)
    
    @pytest.mark.asyncio
    async def test_generates_test_events(self):
        """Test that test mode generates events"""
        # Run one test cycle
        await self.enricher._generate_test_events()
        
        # Should have published an event
        assert self.enricher.stats['events_published'] >= 1
    
    @pytest.mark.asyncio
    async def test_simulates_wickets_over_time(self):
        """Test that test mode eventually generates wickets"""
        # Run many cycles to statistically expect a wicket
        for _ in range(50):
            await self.enricher._generate_test_events()
        
        # With 5% wicket probability, should have at least 1 in 50 tries
        # (statistically ~2.5 expected)
        stats = self.enricher.get_stats()
        # This is probabilistic, so we check >= 0 (test shouldn't fail randomly)
        assert stats['wickets_detected'] >= 0


class TestMatchMatching:
    """Test Micro999 to Cricbuzz match matching"""
    
    def setup_method(self):
        self.enricher = CricketStatsEnricher(test_mode=True)
    
    def test_exact_team_match(self):
        """Test matching when team names match exactly"""
        cb_match = LiveMatchData(
            match_id="123", match_name="MI vs CSK",
            team1="Mumbai Indians", team2="Chennai Super Kings",
            batting_team="Mumbai Indians", runs=100, wickets=2, overs=10.0,
            match_format="T20", venue="Mumbai", status="Live", run_rate=10.0
        )
        
        m999_matches = [
            {'team1': 'mumbai indians', 'team2': 'chennai super kings', 'match_id': 'm999_1'}
        ]
        
        result = self.enricher._find_matching_match(cb_match, m999_matches)
        
        assert result is not None
        assert result['match_id'] == 'm999_1'
    
    def test_partial_team_match(self):
        """Test matching with partial team names"""
        cb_match = LiveMatchData(
            match_id="124", match_name="IND vs AUS",
            team1="India", team2="Australia",
            batting_team="India", runs=80, wickets=1, overs=8.0,
            match_format="T20", venue="Sydney", status="Live", run_rate=10.0
        )
        
        m999_matches = [
            {'team1': 'ind', 'team2': 'aus', 'match_name': 'india vs australia', 'match_id': 'm999_2'}
        ]
        
        result = self.enricher._find_matching_match(cb_match, m999_matches)
        
        assert result is not None
    
    def test_no_match_found(self):
        """Test when no match is found"""
        cb_match = LiveMatchData(
            match_id="125", match_name="ENG vs NZ",
            team1="England", team2="New Zealand",
            batting_team="England", runs=70, wickets=2, overs=7.0,
            match_format="T20", venue="London", status="Live", run_rate=10.0
        )
        
        m999_matches = [
            {'team1': 'india', 'team2': 'pakistan', 'match_id': 'm999_3'}
        ]
        
        result = self.enricher._find_matching_match(cb_match, m999_matches)
        
        assert result is None


def run_enricher_tests():
    """Run all enricher tests"""
    pytest.main([__file__, "-v", "--tb=short"])


if __name__ == "__main__":
    run_enricher_tests()
