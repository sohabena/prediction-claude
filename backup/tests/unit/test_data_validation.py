"""
TITAN Data Validation Unit Tests
Tests for data integrity verification at each pipeline stage

These tests ensure:
1. Validators correctly identify valid/invalid data
2. Fingerprints are consistent across stages
3. Validation catches real-world edge cases
"""

import pytest
import sys
import os
from datetime import datetime, timedelta

# Add parent directories to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.data_fingerprint import (
    DataFingerprint,
    FingerprintTracker,
    ValidationResult,
    ValidationStatus,
    create_fingerprint,
    track_data
)
from utils.data_validators import (
    ScraperDataValidator,
    ParserDataValidator,
    RedisPublishValidator,
    CortexDataValidator,
    APIResponseValidator,
    get_validator
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def valid_scraped_data():
    """Valid data from Stage 1 (Scraper)"""
    return {
        'matches': [
            {'odds': '2.5', 'element': 'structured-back', 'tag': 'STRUCTURED'},
            {'odds': '2.55', 'element': 'structured-lay', 'tag': 'STRUCTURED'}
        ],
        'liveMatches': ['India v South Africa'],
        'totalMatches': 3,
        'structuredMatches': [
            {
                'team1': 'India',
                'team2': 'South Africa',
                'matchName': 'India v South Africa',
                'backOdds': 2.5,
                'layOdds': 2.55,
                'isLive': True,
                'lineIndex': 5
            }
        ],
        'text': 'India v South Africa Live Now 2.5 2.55',
        'title': 'Cricket Betting',
        'url': 'https://micro999.co/game/4'
    }


@pytest.fixture
def valid_parsed_data():
    """Valid data from Stage 2 (Parser)"""
    return {
        'timestamp': datetime.utcnow().isoformat(),
        'match_id': 'micro999_India_South_Africa',
        'market_type': 'match_odds',
        'team': 'India',
        'team1': 'India',
        'team2': 'South Africa',
        'odds': 2.5,
        'back_price': 2.5,
        'lay_price': 2.55,
        'volume': None,
        'score': None,
        'wickets': None,
        'overs': None,
        'is_suspended': False,
        'metadata': {
            'parser_version': '3.0',
            'source': 'micro999',
            'is_live': True
        }
    }


@pytest.fixture
def valid_signal_data():
    """Valid signal data from Stage 4 (Cortex)"""
    return {
        'signal_id': 'simple_odds_change_micro999_India_20251211_123456',
        'strategy': 'simple_odds_change',
        'action': 'BACK',
        'team': 'India',
        'odds': 2.5,
        'confidence': 0.75,
        'stake_recommended': 1500,
        'edge_window_seconds': 45,
        'reasoning': 'Odds increased by 2.5%',
        'timestamp': datetime.utcnow().isoformat()
    }


@pytest.fixture
def valid_api_response():
    """Valid API response from Stage 5 (Backend)"""
    return {
        'status': 'success',
        'count': 1,
        'matches': [
            {
                'match_id': 'micro999_India_South_Africa',
                'match_name': 'India v South Africa',
                'team1': 'India',
                'team2': 'South Africa',
                'status': 'Live Now',
                'current_odds': {'India': 2.5},
                'last_updated': datetime.utcnow().isoformat(),
                'score': None,
                'overs': None,
                'match_phase': 'unknown'
            }
        ]
    }


# ============================================================================
# Stage 1: Scraper Validator Tests
# ============================================================================

class TestScraperDataValidator:
    """Tests for Stage 1 (Scraper) validation"""
    
    def test_valid_extraction_passes(self, valid_scraped_data):
        """Test that valid scraped data passes validation"""
        validator = ScraperDataValidator()
        result = validator.validate(
            valid_scraped_data,
            context={'raw_text': valid_scraped_data['text']}
        )
        
        assert result.is_valid()
        assert result.status in (ValidationStatus.PASS, ValidationStatus.WARNING)
    
    def test_empty_structured_matches_warns(self):
        """Test that empty structuredMatches generates warning"""
        validator = ScraperDataValidator()
        data = {
            'matches': [],
            'liveMatches': [],
            'totalMatches': 5,  # Reported matches but none extracted
            'structuredMatches': []
        }
        
        result = validator.validate(data)
        # Should pass but with warnings about missing structured data
        assert result.is_valid()
    
    def test_invalid_odds_range_fails(self):
        """Test that odds outside valid range are caught"""
        validator = ScraperDataValidator()
        data = {
            'matches': [],
            'liveMatches': ['Test Match'],
            'totalMatches': 1,
            'structuredMatches': [
                {
                    'team1': 'Team A',
                    'team2': 'Team B',
                    'matchName': 'Team A v Team B',
                    'backOdds': 0.5,  # Invalid: below 1.01
                    'isLive': True
                }
            ],
            'text': 'Team A v Team B Live Now 0.5'
        }
        
        result = validator.validate(data, context={'raw_text': data['text']})
        
        assert result.status == ValidationStatus.FAIL
        assert any('out of valid range' in issue for issue in result.details.get('issues', []))
    
    def test_odds_not_in_raw_text_warns(self, valid_scraped_data):
        """Test that odds not found in raw text generate warning"""
        validator = ScraperDataValidator()
        # Remove odds from text
        data = valid_scraped_data.copy()
        data['text'] = 'India v South Africa Live Now'  # No odds in text
        
        result = validator.validate(data, context={'raw_text': data['text']})
        
        # Should still pass but with warnings
        assert result.is_valid()
    
    def test_validate_odds_range_helper(self):
        """Test odds range validation helper"""
        validator = ScraperDataValidator()
        
        assert validator.validate_odds_range(1.5) == True
        assert validator.validate_odds_range(50.0) == True
        assert validator.validate_odds_range(0.5) == False
        assert validator.validate_odds_range(150.0) == False


# ============================================================================
# Stage 2: Parser Validator Tests
# ============================================================================

class TestParserDataValidator:
    """Tests for Stage 2 (Parser) validation"""
    
    def test_valid_parsed_data_passes(self, valid_parsed_data):
        """Test that valid parsed data passes validation"""
        validator = ParserDataValidator()
        result = validator.validate(valid_parsed_data)
        
        assert result.is_valid()
        assert result.fingerprint is not None
    
    def test_missing_required_fields_fails(self):
        """Test that missing required fields fail validation"""
        validator = ParserDataValidator()
        incomplete_data = {
            'timestamp': datetime.utcnow().isoformat(),
            # Missing: match_id, back_price, team1, team2
        }
        
        result = validator.validate(incomplete_data)
        
        assert result.status == ValidationStatus.FAIL
        assert 'Missing required fields' in result.message
    
    def test_stale_timestamp_warns(self):
        """Test that stale timestamps generate warning"""
        validator = ParserDataValidator()
        stale_data = {
            'timestamp': (datetime.utcnow() - timedelta(seconds=60)).isoformat(),
            'match_id': 'test_match',
            'back_price': 2.0,
            'team1': 'Team A',
            'team2': 'Team B'
        }
        
        result = validator.validate(stale_data)
        
        # Should pass but with warning about stale timestamp
        assert result.is_valid()
        assert any('old' in warning for warning in result.details.get('warnings', []))
    
    def test_invalid_match_id_format_warns(self):
        """Test that non-standard match_id format generates warning"""
        validator = ParserDataValidator()
        data = {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': 'other_format_123',  # Doesn't start with micro999_
            'back_price': 2.0,
            'team1': 'Team A',
            'team2': 'Team B'
        }
        
        result = validator.validate(data)
        
        assert result.is_valid()
        assert any('format' in warning.lower() for warning in result.details.get('warnings', []))
    
    def test_lay_price_less_than_back_fails(self):
        """Test that lay < back price fails validation"""
        validator = ParserDataValidator()
        invalid_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': 'micro999_Test_Match',
            'back_price': 3.0,
            'lay_price': 2.5,  # Invalid: lay should be >= back
            'team1': 'Team A',
            'team2': 'Team B'
        }
        
        result = validator.validate(invalid_data)
        
        assert result.status == ValidationStatus.FAIL
        assert any('lay price' in issue.lower() for issue in result.details.get('issues', []))
    
    def test_batch_validation(self):
        """Test batch validation of multiple records"""
        validator = ParserDataValidator()
        records = [
            {
                'timestamp': datetime.utcnow().isoformat(),
                'match_id': 'micro999_Match_1',
                'back_price': 2.0,
                'team1': 'A', 'team2': 'B'
            },
            {
                'timestamp': datetime.utcnow().isoformat(),
                'match_id': 'micro999_Match_2',
                'back_price': 2.5,
                'team1': 'C', 'team2': 'D'
            },
            {
                'timestamp': datetime.utcnow().isoformat(),
                # Missing fields - should fail
            }
        ]
        
        results, summary = validator.validate_batch(records)
        
        assert len(results) == 3
        assert summary['passed'] == 2
        assert summary['failed'] == 1
        assert summary['success_rate'] == pytest.approx(66.67, rel=0.1)


# ============================================================================
# Stage 3: Redis Publish Validator Tests
# ============================================================================

class TestRedisPublishValidator:
    """Tests for Stage 3 (Redis Publish) validation"""
    
    def test_successful_publish_passes(self, valid_parsed_data):
        """Test that successful publish passes validation"""
        validator = RedisPublishValidator()
        result = validator.validate(
            valid_parsed_data,
            context={
                'channel': 'match_events',
                'publish_result': 2,  # 2 subscribers received
                'original_data': valid_parsed_data
            }
        )
        
        assert result.is_valid()
    
    def test_no_subscribers_warns(self, valid_parsed_data):
        """Test that zero subscribers generates warning"""
        validator = RedisPublishValidator()
        result = validator.validate(
            valid_parsed_data,
            context={
                'channel': 'match_events',
                'publish_result': 0,  # No subscribers
            }
        )
        
        assert result.status == ValidationStatus.WARNING
        assert any('No subscribers' in warning for warning in result.details.get('warnings', []))
    
    def test_data_modification_detected(self):
        """Test that data modification during publish is detected"""
        validator = RedisPublishValidator()
        original = {
            'match_id': 'test_match',
            'back_price': 2.0,
            'timestamp': datetime.utcnow().isoformat()
        }
        modified = original.copy()
        modified['back_price'] = 2.5  # Modified during publish
        
        result = validator.validate(
            modified,
            context={
                'channel': 'match_events',
                'publish_result': 1,
                'original_data': original
            }
        )
        
        assert result.status == ValidationStatus.FAIL
        assert any('modified' in issue.lower() for issue in result.details.get('issues', []))


# ============================================================================
# Stage 4: Cortex Validator Tests
# ============================================================================

class TestCortexDataValidator:
    """Tests for Stage 4 (Cortex) validation"""
    
    def test_valid_signal_passes(self, valid_signal_data, valid_parsed_data):
        """Test that valid signal passes validation"""
        validator = CortexDataValidator()
        result = validator.validate(
            valid_signal_data,
            context={'event': valid_parsed_data}
        )
        
        assert result.is_valid()
    
    def test_invalid_confidence_fails(self, valid_signal_data, valid_parsed_data):
        """Test that confidence outside 0-1 fails"""
        validator = CortexDataValidator()
        invalid_signal = valid_signal_data.copy()
        invalid_signal['confidence'] = 1.5  # Invalid: > 1
        
        result = validator.validate(
            invalid_signal,
            context={'event': valid_parsed_data}
        )
        
        assert result.status == ValidationStatus.FAIL
        assert any('Invalid confidence' in issue for issue in result.details.get('issues', []))
    
    def test_invalid_action_fails(self, valid_signal_data, valid_parsed_data):
        """Test that invalid action fails"""
        validator = CortexDataValidator()
        invalid_signal = valid_signal_data.copy()
        invalid_signal['action'] = 'BUY'  # Invalid: should be BACK or LAY
        
        result = validator.validate(
            invalid_signal,
            context={'event': valid_parsed_data}
        )
        
        assert result.status == ValidationStatus.FAIL
    
    def test_odds_mismatch_detected(self, valid_signal_data, valid_parsed_data):
        """Test that signal odds not matching event odds is detected"""
        validator = CortexDataValidator()
        invalid_signal = valid_signal_data.copy()
        invalid_signal['odds'] = 3.0  # Different from event back_price of 2.5
        
        result = validator.validate(
            invalid_signal,
            context={'event': valid_parsed_data}
        )
        
        assert result.status == ValidationStatus.FAIL
        assert any('don\'t match' in issue.lower() for issue in result.details.get('issues', []))


# ============================================================================
# Stage 5: API Response Validator Tests
# ============================================================================

class TestAPIResponseValidator:
    """Tests for Stage 5 (Backend API) validation"""
    
    def test_valid_response_passes(self, valid_api_response):
        """Test that valid API response passes validation"""
        validator = APIResponseValidator()
        result = validator.validate(valid_api_response)
        
        assert result.is_valid()
    
    def test_count_mismatch_fails(self, valid_api_response):
        """Test that count mismatch fails validation"""
        validator = APIResponseValidator()
        invalid_response = valid_api_response.copy()
        invalid_response['count'] = 5  # Claims 5 but only 1 match
        
        result = validator.validate(invalid_response)
        
        assert result.status == ValidationStatus.FAIL
        assert any('count mismatch' in issue.lower() for issue in result.details.get('issues', []))
    
    def test_invalid_timestamp_format_fails(self, valid_api_response):
        """Test that invalid timestamp format is caught"""
        validator = APIResponseValidator()
        invalid_response = valid_api_response.copy()
        invalid_response['matches'][0]['last_updated'] = 'not-a-timestamp'
        
        result = validator.validate(invalid_response)
        
        assert result.status == ValidationStatus.FAIL
        assert any('timestamp' in issue.lower() for issue in result.details.get('issues', []))


# ============================================================================
# Fingerprint Tests
# ============================================================================

class TestDataFingerprint:
    """Tests for data fingerprinting"""
    
    def test_same_data_same_fingerprint(self):
        """Test that identical data produces same fingerprint"""
        fp = DataFingerprint()
        data1 = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        data2 = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        
        assert fp.create(data1) == fp.create(data2)
    
    def test_different_data_different_fingerprint(self):
        """Test that different data produces different fingerprint"""
        fp = DataFingerprint()
        data1 = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        data2 = {'match_id': 'test', 'back_price': 2.5, 'timestamp': '2025-01-01T00:00:00'}
        
        assert fp.create(data1) != fp.create(data2)
    
    def test_fingerprint_length(self):
        """Test that fingerprint has expected length"""
        fp = DataFingerprint(fingerprint_length=12)
        data = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        
        assert len(fp.create(data)) == 12
    
    def test_compare_function(self):
        """Test fingerprint comparison function"""
        fp = DataFingerprint()
        data1 = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        data2 = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        data3 = {'match_id': 'test', 'back_price': 2.5, 'timestamp': '2025-01-01T00:00:00'}
        
        assert fp.compare(data1, data2) == True
        assert fp.compare(data1, data3) == False


class TestFingerprintTracker:
    """Tests for fingerprint tracking"""
    
    def test_record_and_retrieve(self):
        """Test recording and retrieving fingerprint lineage"""
        tracker = FingerprintTracker()
        tracker.clear()
        
        data = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
        
        fp = tracker.record(stage=1, component='scraper', data=data)
        tracker.record(stage=2, component='parser', data=data)
        tracker.record(stage=3, component='redis', data=data)
        
        lineage = tracker.get_lineage(fp)
        
        assert len(lineage) == 3
        assert lineage[0]['stage'] == 1
        assert lineage[2]['stage'] == 3
    
    def test_validation_summary(self):
        """Test validation summary generation"""
        tracker = FingerprintTracker()
        tracker.clear()
        
        data = {'match_id': 'test', 'back_price': 2.0, 'timestamp': '2025-01-01T00:00:00'}
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
        
        tracker.record(stage=1, component='scraper', data=data, validation_result=pass_result)
        tracker.record(stage=2, component='parser', data=data, validation_result=fail_result)
        
        summary = tracker.get_validation_summary()
        
        assert summary['by_stage'][1]['pass'] == 1
        assert summary['by_stage'][2]['fail'] == 1
        assert len(summary['failures']) == 1


# ============================================================================
# Helper Function Tests
# ============================================================================

class TestGetValidator:
    """Tests for validator factory function"""
    
    def test_get_validator_returns_correct_type(self):
        """Test that get_validator returns correct validator types"""
        assert isinstance(get_validator(1), ScraperDataValidator)
        assert isinstance(get_validator(2), ParserDataValidator)
        assert isinstance(get_validator(3), RedisPublishValidator)
        assert isinstance(get_validator(4), CortexDataValidator)
        assert isinstance(get_validator(5), APIResponseValidator)
    
    def test_get_validator_invalid_stage_raises(self):
        """Test that invalid stage raises ValueError"""
        with pytest.raises(ValueError):
            get_validator(99)


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""
    
    def test_empty_data_validation(self):
        """Test validation with empty data"""
        validator = ParserDataValidator()
        result = validator.validate({})
        
        assert result.status == ValidationStatus.FAIL
    
    def test_none_values_handled(self):
        """Test that None values are handled gracefully"""
        validator = ParserDataValidator()
        data = {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': 'micro999_Test',
            'back_price': 2.0,
            'team1': 'A',
            'team2': 'B',
            'lay_price': None,  # None value
            'score': None,
            'overs': None
        }
        
        result = validator.validate(data)
        
        # Should pass - None optional fields are OK
        assert result.is_valid()
    
    def test_boundary_odds_values(self):
        """Test boundary values for odds"""
        validator = ScraperDataValidator()
        
        # Test lower boundary
        assert validator.validate_odds_range(1.01) == True
        assert validator.validate_odds_range(1.00) == False
        
        # Test upper boundary
        assert validator.validate_odds_range(100.0) == True
        assert validator.validate_odds_range(100.01) == False
    
    def test_future_timestamp_fails(self):
        """Test that future timestamps are caught"""
        validator = ParserDataValidator()
        future_data = {
            'timestamp': (datetime.utcnow() + timedelta(hours=1)).isoformat(),
            'match_id': 'micro999_Test',
            'back_price': 2.0,
            'team1': 'A',
            'team2': 'B'
        }
        
        result = validator.validate(future_data)
        
        assert result.status == ValidationStatus.FAIL
        assert any('future' in issue.lower() for issue in result.details.get('issues', []))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
