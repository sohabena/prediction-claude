"""
TITAN Data Flow Integration Tests
End-to-end tests for data integrity across the entire pipeline

These tests verify:
1. Data flows correctly from Stage 1 (Scraper) to Stage 5 (Backend)
2. Fingerprints are consistent across stages
3. Data is not corrupted during transformation
4. Timestamps are properly propagated
"""

import pytest
import asyncio
import json
import sys
import os
import redis
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

# Add parent directories to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.data_fingerprint import (
    DataFingerprint,
    FingerprintTracker,
    ValidationResult,
    ValidationStatus,
    create_fingerprint,
    track_data,
    get_tracker,
    get_validation_summary
)
from utils.data_validators import (
    ScraperDataValidator,
    ParserDataValidator,
    RedisPublishValidator,
    CortexDataValidator,
    APIResponseValidator
)


# ============================================================================
# Test Configuration
# ============================================================================

@pytest.fixture(scope="module")
def redis_client():
    """Create Redis client for tests"""
    try:
        client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        client.ping()
        return client
    except redis.ConnectionError:
        pytest.skip("Redis not available for integration tests")


@pytest.fixture
def fresh_tracker():
    """Get a fresh fingerprint tracker"""
    tracker = get_tracker()
    tracker.clear()
    return tracker


# ============================================================================
# Test Fixtures - Simulated Pipeline Data
# ============================================================================

@pytest.fixture
def stage1_scraped_data():
    """Simulated data from Stage 1 (Scraper DOM extraction)"""
    return {
        'matches': [
            {'odds': '2.50', 'element': 'structured-back', 'tag': 'STRUCTURED'},
            {'odds': '2.55', 'element': 'structured-lay', 'tag': 'STRUCTURED'}
        ],
        'liveMatches': ['Test Team A v Test Team B'],
        'totalMatches': 1,
        'structuredMatches': [
            {
                'team1': 'Test Team A',
                'team2': 'Test Team B',
                'matchName': 'Test Team A v Test Team B',
                'backOdds': 2.50,
                'layOdds': 2.55,
                'isLive': True,
                'lineIndex': 5
            }
        ],
        'text': 'Test Team A v Test Team B Live Now 2.50 2.55',
        'title': 'Cricket Betting',
        'url': 'https://micro999.co/game/4',
        'timestamp': datetime.utcnow().isoformat()
    }


@pytest.fixture
def stage2_parsed_data():
    """Simulated data from Stage 2 (Parser)"""
    return {
        'timestamp': datetime.utcnow().isoformat(),
        'match_id': 'micro999_Test_Team_A_Test_Team_B',
        'market_type': 'match_odds',
        'team': 'Test Team A',
        'team1': 'Test Team A',
        'team2': 'Test Team B',
        'odds': 2.50,
        'back_price': 2.50,
        'lay_price': 2.55,
        'volume': None,
        'score': None,
        'wickets': None,
        'overs': None,
        'is_suspended': False,
        'stake_limit': None,
        'metadata': {
            'parser_version': '3.0',
            'source': 'micro999',
            'is_live': True
        }
    }


@pytest.fixture
def stage4_signal_data():
    """Simulated signal from Stage 4 (Cortex)"""
    return {
        'signal_id': 'simple_odds_change_micro999_Test_Team_20251211_123456',
        'strategy': 'simple_odds_change',
        'action': 'BACK',
        'team': 'Test Team A',
        'odds': 2.50,
        'confidence': 0.75,
        'stake_recommended': 1500,
        'edge_window_seconds': 45,
        'reasoning': 'Odds increased by 2.5%',
        'timestamp': datetime.utcnow().isoformat()
    }


@pytest.fixture
def stage5_api_response(stage2_parsed_data):
    """Simulated API response from Stage 5 (Backend)"""
    return {
        'status': 'success',
        'count': 1,
        'matches': [
            {
                'match_id': stage2_parsed_data['match_id'],
                'match_name': 'Test Team A v Test Team B',
                'team1': stage2_parsed_data['team1'],
                'team2': stage2_parsed_data['team2'],
                'status': 'Live Now',
                'current_odds': {stage2_parsed_data['team1']: stage2_parsed_data['back_price']},
                'last_updated': stage2_parsed_data['timestamp'],
                'score': None,
                'overs': None,
                'match_phase': 'unknown'
            }
        ]
    }


# ============================================================================
# Full Pipeline Tests
# ============================================================================

class TestFullPipelineDataFlow:
    """Test data integrity across the full pipeline"""
    
    def test_fingerprint_consistency_across_stages(
        self, 
        fresh_tracker,
        stage1_scraped_data,
        stage2_parsed_data,
        stage4_signal_data,
        stage5_api_response
    ):
        """Test that fingerprints remain consistent as data flows through pipeline"""
        
        # Stage 1: Scraper validation
        scraper_validator = ScraperDataValidator()
        scraper_result = scraper_validator.validate(
            stage1_scraped_data, 
            context={'raw_text': stage1_scraped_data['text']}
        )
        
        # Track Stage 1
        fp1 = track_data(
            stage=1, 
            component='scraper', 
            data={
                'match_id': 'micro999_Test_Team_A_Test_Team_B',
                'back_price': 2.50,
                'timestamp': stage1_scraped_data['timestamp']
            },
            validation_result=scraper_result
        )
        
        # Stage 2: Parser validation
        parser_validator = ParserDataValidator()
        parser_result = parser_validator.validate(stage2_parsed_data)
        
        # Track Stage 2
        fp2 = track_data(
            stage=2, 
            component='parser', 
            data={
                'match_id': stage2_parsed_data['match_id'],
                'back_price': stage2_parsed_data['back_price'],
                'timestamp': stage2_parsed_data['timestamp']
            },
            validation_result=parser_result
        )
        
        # Stage 3: Redis publish validation
        redis_validator = RedisPublishValidator()
        redis_result = redis_validator.validate(
            stage2_parsed_data,
            context={
                'channel': 'match_events',
                'publish_result': 1,
                'original_data': stage2_parsed_data
            }
        )
        
        # Track Stage 3
        fp3 = track_data(
            stage=3,
            component='redis_publish',
            data={
                'match_id': stage2_parsed_data['match_id'],
                'back_price': stage2_parsed_data['back_price'],
                'timestamp': stage2_parsed_data['timestamp']
            },
            validation_result=redis_result
        )
        
        # Verify all stages passed
        assert scraper_result.is_valid(), f"Stage 1 failed: {scraper_result}"
        assert parser_result.is_valid(), f"Stage 2 failed: {parser_result}"
        assert redis_result.is_valid(), f"Stage 3 failed: {redis_result}"
        
        # Verify fingerprints are consistent (same data = same fingerprint)
        assert fp1 == fp2, f"Fingerprint changed from Stage 1 ({fp1}) to Stage 2 ({fp2})"
        assert fp2 == fp3, f"Fingerprint changed from Stage 2 ({fp2}) to Stage 3 ({fp3})"
    
    def test_data_transformation_preserves_critical_fields(
        self,
        stage1_scraped_data,
        stage2_parsed_data
    ):
        """Test that critical fields are preserved during transformation"""
        
        structured_match = stage1_scraped_data['structuredMatches'][0]
        
        # Verify odds are preserved
        assert structured_match['backOdds'] == stage2_parsed_data['back_price'], \
            "Back price not preserved from scraper to parser"
        
        # Verify team names are preserved
        assert structured_match['team1'] == stage2_parsed_data['team1'], \
            "Team 1 not preserved"
        assert structured_match['team2'] == stage2_parsed_data['team2'], \
            "Team 2 not preserved"
    
    def test_validation_summary_accumulates_correctly(self, fresh_tracker):
        """Test that validation summary correctly accumulates results"""
        
        # Simulate multiple validations
        valid_data = {
            'match_id': 'test_match',
            'back_price': 2.0,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        pass_result = ValidationResult(
            status=ValidationStatus.PASS,
            stage=1,
            component='test',
            message='OK'
        )
        
        fail_result = ValidationResult(
            status=ValidationStatus.FAIL,
            stage=2,
            component='test',
            message='Failed'
        )
        
        # Record 3 passes and 2 failures
        track_data(1, 'test', valid_data, pass_result)
        track_data(1, 'test', valid_data, pass_result)
        track_data(1, 'test', valid_data, pass_result)
        track_data(2, 'test', valid_data, fail_result)
        track_data(2, 'test', valid_data, fail_result)
        
        summary = get_validation_summary()
        
        assert summary['total_records'] == 5
        assert summary['by_stage'][1]['pass'] == 3
        assert summary['by_stage'][2]['fail'] == 2


# ============================================================================
# Stage-to-Stage Transition Tests
# ============================================================================

class TestStageTransitions:
    """Test data integrity during stage transitions"""
    
    def test_stage1_to_stage2_transition(self, stage1_scraped_data):
        """Test Scraper -> Parser transition"""
        
        # Validate Stage 1 output
        scraper_validator = ScraperDataValidator()
        scraper_result = scraper_validator.validate(
            stage1_scraped_data,
            context={'raw_text': stage1_scraped_data['text']}
        )
        
        assert scraper_result.is_valid()
        
        # Simulate Stage 2 parsing
        structured = stage1_scraped_data['structuredMatches'][0]
        parsed = {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': f"micro999_{structured['team1']}_{structured['team2']}".replace(' ', '_'),
            'back_price': structured['backOdds'],
            'lay_price': structured['layOdds'],
            'team1': structured['team1'],
            'team2': structured['team2']
        }
        
        # Validate Stage 2 output
        parser_validator = ParserDataValidator()
        parser_result = parser_validator.validate(parsed)
        
        assert parser_result.is_valid()
    
    def test_stage2_to_stage3_transition(self, stage2_parsed_data):
        """Test Parser -> Redis Publish transition"""
        
        # Validate Stage 2 output
        parser_validator = ParserDataValidator()
        parser_result = parser_validator.validate(stage2_parsed_data)
        
        assert parser_result.is_valid()
        
        # Validate Stage 3 publish
        redis_validator = RedisPublishValidator()
        redis_result = redis_validator.validate(
            stage2_parsed_data,
            context={
                'channel': 'match_events',
                'publish_result': 1,
                'original_data': stage2_parsed_data
            }
        )
        
        assert redis_result.is_valid()
    
    def test_stage3_to_stage4_transition(self, stage2_parsed_data, stage4_signal_data):
        """Test Redis -> Cortex transition"""
        
        # Validate signal
        cortex_validator = CortexDataValidator()
        cortex_result = cortex_validator.validate(
            stage4_signal_data,
            context={'event': stage2_parsed_data}
        )
        
        assert cortex_result.is_valid()
    
    def test_stage4_to_stage5_transition(self, stage5_api_response):
        """Test Cortex -> Backend API transition"""
        
        # Validate API response
        api_validator = APIResponseValidator()
        api_result = api_validator.validate(stage5_api_response)
        
        assert api_result.is_valid()


# ============================================================================
# Error Propagation Tests
# ============================================================================

class TestErrorPropagation:
    """Test that errors are properly detected and logged at each stage"""
    
    def test_invalid_stage1_data_detected(self):
        """Test that invalid Stage 1 data is caught"""
        
        invalid_data = {
            'matches': [],
            'structuredMatches': [
                {
                    'team1': 'A',
                    'team2': 'B',
                    'backOdds': 0.5,  # Invalid odds
                    'isLive': True
                }
            ],
            'text': 'A v B Live Now 0.5'
        }
        
        validator = ScraperDataValidator()
        result = validator.validate(invalid_data, context={'raw_text': invalid_data['text']})
        
        assert result.status == ValidationStatus.FAIL
    
    def test_invalid_stage2_data_detected(self):
        """Test that invalid Stage 2 data is caught"""
        
        invalid_data = {
            'timestamp': datetime.utcnow().isoformat(),
            # Missing required fields
        }
        
        validator = ParserDataValidator()
        result = validator.validate(invalid_data)
        
        assert result.status == ValidationStatus.FAIL
    
    def test_data_modification_detected(self):
        """Test that data modification between stages is detected"""
        
        original = {
            'match_id': 'test',
            'back_price': 2.0,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        modified = original.copy()
        modified['back_price'] = 2.5  # Modified
        
        validator = RedisPublishValidator()
        result = validator.validate(
            modified,
            context={
                'channel': 'match_events',
                'publish_result': 1,
                'original_data': original
            }
        )
        
        assert result.status == ValidationStatus.FAIL
        assert 'modified' in str(result.details).lower()


# ============================================================================
# Live System Tests (requires running services)
# ============================================================================

@pytest.mark.integration
class TestLiveSystem:
    """Tests that require running TITAN services"""
    
    @pytest.mark.skipif(
        os.getenv('RUN_LIVE_TESTS', 'false').lower() != 'true',
        reason="Live tests disabled. Set RUN_LIVE_TESTS=true to enable."
    )
    def test_live_redis_data_flow(self, redis_client):
        """Test data flow through live Redis"""
        
        # Get current active matches
        matches_json = redis_client.get('active_matches')
        
        if matches_json:
            matches = json.loads(matches_json)
            
            # Validate each match
            api_validator = APIResponseValidator()
            response_data = {
                'status': 'success',
                'count': len(matches),
                'matches': matches
            }
            
            result = api_validator.validate(response_data)
            
            assert result.is_valid(), f"Live data validation failed: {result}"
    
    @pytest.mark.skipif(
        os.getenv('RUN_LIVE_TESTS', 'false').lower() != 'true',
        reason="Live tests disabled. Set RUN_LIVE_TESTS=true to enable."
    )
    def test_live_signal_history(self, redis_client):
        """Test signal history contains valid signals"""
        
        # Get recent signals
        signals_json = redis_client.lrange('signal_history', 0, 9)
        
        if signals_json:
            cortex_validator = CortexDataValidator()
            
            for signal_str in signals_json:
                signal = json.loads(signal_str)
                
                result = cortex_validator.validate(
                    signal,
                    context={'event': {}}  # No event context for historical
                )
                
                # Historical signals should still have valid structure
                assert 'strategy' in signal
                assert 'action' in signal
                assert 'odds' in signal
                assert 'confidence' in signal


# ============================================================================
# Performance Tests
# ============================================================================

class TestValidationPerformance:
    """Test validation performance is acceptable"""
    
    def test_parser_validation_speed(self, stage2_parsed_data):
        """Test that parser validation completes quickly"""
        
        validator = ParserDataValidator()
        
        start = time.time()
        for _ in range(1000):
            validator.validate(stage2_parsed_data)
        elapsed = time.time() - start
        
        # Should complete 1000 validations in under 1 second
        assert elapsed < 1.0, f"Parser validation too slow: {elapsed:.2f}s for 1000 validations"
    
    def test_fingerprint_creation_speed(self, stage2_parsed_data):
        """Test that fingerprint creation is fast"""
        
        start = time.time()
        for _ in range(10000):
            create_fingerprint(stage2_parsed_data)
        elapsed = time.time() - start
        
        # Should complete 10000 fingerprints in under 1 second
        assert elapsed < 1.0, f"Fingerprint creation too slow: {elapsed:.2f}s for 10000 creations"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])
