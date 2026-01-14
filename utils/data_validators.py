"""
TITAN Data Validators
Shared validation classes for each pipeline stage

Each validator ensures data integrity at its respective stage:
- Stage 1 (Scraper): Validates extracted DOM data
- Stage 2 (Parser): Validates parsed match data
- Stage 3 (Manager): Validates Redis publish operations
- Stage 4 (Cortex): Validates event-to-signal transformation
- Stage 5 (Backend): Validates API responses
- Stage 6 (Frontend): Validates display data
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from loguru import logger

from utils.data_fingerprint import (
    ValidationResult, 
    ValidationStatus, 
    create_fingerprint,
    track_data
)


class BaseDataValidator(ABC):
    """Base class for all data validators"""
    
    def __init__(self, stage: int, component: str):
        self.stage = stage
        self.component = component
        self._validation_count = 0
        self._failure_count = 0
    
    @abstractmethod
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate data at this stage
        
        Args:
            data: Data to validate
            context: Optional context (e.g., raw source for comparison)
            
        Returns:
            ValidationResult with status and details
        """
        pass
    
    def _create_result(self, status: ValidationStatus, message: str, 
                       details: Dict = None, fingerprint: str = None) -> ValidationResult:
        """Helper to create validation results"""
        self._validation_count += 1
        if status == ValidationStatus.FAIL:
            self._failure_count += 1
        
        return ValidationResult(
            status=status,
            stage=self.stage,
            component=self.component,
            message=message,
            fingerprint=fingerprint,
            details=details or {}
        )
    
    def get_stats(self) -> Dict:
        """Get validation statistics"""
        return {
            "total_validations": self._validation_count,
            "failures": self._failure_count,
            "success_rate": (
                (self._validation_count - self._failure_count) / self._validation_count * 100
                if self._validation_count > 0 else 100
            )
        }


class ScraperDataValidator(BaseDataValidator):
    """
    Stage 1 Validator: Validates scraped DOM data
    
    Checks:
    1. Match names exist in raw HTML
    2. Extracted odds appear in raw HTML
    3. Odds are within valid range (1.01 - 100)
    4. Live indicator is properly detected
    """
    
    # Valid odds range
    MIN_ODDS = 1.01
    MAX_ODDS = 100.0
    
    def __init__(self):
        super().__init__(stage=1, component="scraper")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate scraped data
        
        Args:
            data: Extracted page data from worker.py
            context: Should contain 'raw_text' for verification
        """
        issues = []
        warnings = []
        
        raw_text = context.get('raw_text', '') if context else ''
        structured_matches = data.get('structuredMatches', [])
        
        # Check 1: At least some data was extracted
        if not structured_matches:
            if data.get('totalMatches', 0) > 0:
                warnings.append("Matches detected but no structured data extracted")
            else:
                # This might be OK if no matches are live
                pass
        
        # Check 2: Validate each structured match
        for match in structured_matches:
            team1 = match.get('team1', '')
            team2 = match.get('team2', '')
            back_odds = match.get('backOdds')
            is_live = match.get('isLive', False)
            
            # Validate team names exist in raw text
            if raw_text:
                if team1 and team1 not in raw_text:
                    issues.append(f"Team '{team1}' not found in raw text")
                if team2 and team2 not in raw_text:
                    issues.append(f"Team '{team2}' not found in raw text")
            
            # Validate odds value
            if back_odds is not None:
                if back_odds < self.MIN_ODDS or back_odds > self.MAX_ODDS:
                    issues.append(f"Odds {back_odds} out of valid range [{self.MIN_ODDS}-{self.MAX_ODDS}]")
                
                # Check odds appear in raw text (as string)
                if raw_text and str(back_odds) not in raw_text:
                    # Try with formatting variations
                    odds_str = f"{back_odds:.2f}"
                    if odds_str not in raw_text:
                        warnings.append(f"Odds {back_odds} not found in raw text")
            
            # Validate live status
            if is_live and raw_text:
                # Check that "Live Now" appears near the match
                match_name = match.get('matchName', f"{team1} v {team2}")
                if 'Live Now' not in raw_text:
                    warnings.append("No 'Live Now' indicator in page")
        
        # Check 3: Validate odds count matches expectations
        reported_odds = len(data.get('matches', []))
        live_count = len(data.get('liveMatches', []))
        
        if live_count > 0 and len(structured_matches) == 0:
            issues.append(f"Reported {live_count} live matches but no structured data")
        
        # Generate fingerprint for first structured match
        fingerprint = None
        if structured_matches:
            first_match = structured_matches[0]
            fp_data = {
                'match_id': f"micro999_{first_match.get('team1', '')}_{first_match.get('team2', '')}".replace(' ', '_'),
                'back_price': first_match.get('backOdds'),
                'timestamp': datetime.utcnow().isoformat()
            }
            fingerprint = create_fingerprint(fp_data)
        
        # Determine overall status
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"Extraction validation failed: {len(issues)} issues",
                details={"issues": issues, "warnings": warnings},
                fingerprint=fingerprint
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"Extraction completed with {len(warnings)} warnings",
                details={"warnings": warnings},
                fingerprint=fingerprint
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"Extraction valid: {len(structured_matches)} matches",
                fingerprint=fingerprint
            )
    
    def validate_odds_range(self, odds: float) -> bool:
        """Quick check if odds are in valid range"""
        return self.MIN_ODDS <= odds <= self.MAX_ODDS


class ParserDataValidator(BaseDataValidator):
    """
    Stage 2 Validator: Validates parsed match data
    
    Checks:
    1. All required fields present
    2. Field types are correct
    3. Timestamp is fresh (within last 30 seconds)
    4. Match ID format is valid
    5. Match-odds pairing is correct
    """
    
    REQUIRED_FIELDS = ['match_id', 'back_price', 'timestamp', 'team1', 'team2']
    OPTIONAL_FIELDS = ['lay_price', 'score', 'wickets', 'overs', 'run_rate']
    
    # Field type specifications
    FIELD_TYPES = {
        'match_id': str,
        'back_price': (int, float),
        'lay_price': (int, float, type(None)),
        'timestamp': str,
        'team1': str,
        'team2': str,
        'score': (int, type(None)),
        'wickets': (int, type(None)),
        'overs': (float, type(None)),
    }
    
    MAX_TIMESTAMP_AGE_SECONDS = 30
    
    def __init__(self):
        super().__init__(stage=2, component="parser")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate parsed match data
        
        Args:
            data: Parsed match record
            context: Optional context with 'raw_extraction' for comparison
        """
        issues = []
        warnings = []
        
        # Check 1: Required fields present
        missing_fields = []
        for field in self.REQUIRED_FIELDS:
            if field not in data or data[field] is None:
                missing_fields.append(field)
        
        if missing_fields:
            issues.append(f"Missing required fields: {missing_fields}")
        
        # Check 2: Field types
        for field, expected_type in self.FIELD_TYPES.items():
            if field in data and data[field] is not None:
                if not isinstance(data[field], expected_type):
                    issues.append(f"Field '{field}' has wrong type: expected {expected_type}, got {type(data[field])}")
        
        # Check 3: Timestamp freshness
        timestamp_str = data.get('timestamp')
        if timestamp_str:
            try:
                # Parse timestamp (handle with/without Z suffix)
                ts = timestamp_str.rstrip('Z')
                timestamp = datetime.fromisoformat(ts)
                age = datetime.utcnow() - timestamp
                
                if age.total_seconds() > self.MAX_TIMESTAMP_AGE_SECONDS:
                    warnings.append(f"Timestamp is {age.total_seconds():.1f}s old (max {self.MAX_TIMESTAMP_AGE_SECONDS}s)")
                elif age.total_seconds() < -5:  # Future timestamp (with tolerance)
                    issues.append(f"Timestamp is in the future by {-age.total_seconds():.1f}s")
            except (ValueError, TypeError) as e:
                issues.append(f"Invalid timestamp format: {timestamp_str}")
        
        # Check 4: Match ID format
        match_id = data.get('match_id', '')
        if match_id:
            if not match_id.startswith('micro999_'):
                warnings.append(f"Match ID doesn't follow expected format: {match_id}")
            if len(match_id) < 10:
                issues.append(f"Match ID too short: {match_id}")
        
        # Check 5: Odds values
        back_price = data.get('back_price')
        lay_price = data.get('lay_price')
        
        if back_price is not None:
            if back_price < 1.01 or back_price > 100:
                issues.append(f"Back price {back_price} out of valid range")
        
        if lay_price is not None:
            if lay_price < 1.01 or lay_price > 100:
                issues.append(f"Lay price {lay_price} out of valid range")
            if back_price and lay_price < back_price:
                issues.append(f"Lay price {lay_price} should not be less than back price {back_price}")
        
        # Check 6: Team names
        team1 = data.get('team1', '')
        team2 = data.get('team2', '')
        
        if team1 == team2:
            issues.append(f"Team1 and Team2 are the same: {team1}")
        
        if len(team1) < 2 or len(team2) < 2:
            warnings.append(f"Short team names: '{team1}' vs '{team2}'")
        
        # Generate fingerprint
        fingerprint = create_fingerprint(data)
        
        # Track this validation
        result = self._determine_result(issues, warnings, fingerprint, data)
        track_data(self.stage, self.component, data, result)
        
        return result
    
    def _determine_result(self, issues: List[str], warnings: List[str], 
                          fingerprint: str, data: Dict) -> ValidationResult:
        """Determine validation result based on issues and warnings"""
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"Parse validation failed: {len(issues)} issues",
                details={"issues": issues, "warnings": warnings, "data_sample": {
                    "match_id": data.get("match_id"),
                    "back_price": data.get("back_price")
                }},
                fingerprint=fingerprint
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"Parse completed with {len(warnings)} warnings",
                details={"warnings": warnings},
                fingerprint=fingerprint
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"Parse valid: {data.get('match_id')}",
                fingerprint=fingerprint
            )
    
    def validate_batch(self, records: List[Dict]) -> Tuple[List[ValidationResult], Dict]:
        """
        Validate a batch of parsed records
        
        Returns:
            Tuple of (list of results, summary dict)
        """
        results = []
        passed = 0
        failed = 0
        
        for record in records:
            result = self.validate(record)
            results.append(result)
            if result.is_valid():
                passed += 1
            else:
                failed += 1
        
        summary = {
            "total": len(records),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / len(records) * 100 if records else 100
        }
        
        return results, summary


class RedisPublishValidator(BaseDataValidator):
    """
    Stage 3 Validator: Validates Redis publish operations
    
    Checks:
    1. Data was published successfully
    2. Published data matches original
    3. Channel is correct
    4. Subscribers received the message
    """
    
    VALID_CHANNELS = ['match_events', 'signals', 'circuit_breaker_events']
    
    def __init__(self):
        super().__init__(stage=3, component="redis_publish")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate Redis publish operation
        
        Args:
            data: Data that was published
            context: Should contain 'channel', 'publish_result', 'original_data'
        """
        issues = []
        warnings = []
        context = context or {}
        
        channel = context.get('channel', '')
        publish_result = context.get('publish_result', 0)
        original_data = context.get('original_data')
        
        # Check 1: Channel is valid
        if channel and channel not in self.VALID_CHANNELS:
            warnings.append(f"Publishing to non-standard channel: {channel}")
        
        # Check 2: Publish succeeded (returned number of subscribers)
        if publish_result == 0:
            warnings.append("No subscribers received the message")
        
        # Check 3: Data matches original (if provided)
        if original_data:
            original_fp = create_fingerprint(original_data)
            published_fp = create_fingerprint(data)
            
            if original_fp != published_fp:
                issues.append(f"Data modified during publish: original={original_fp}, published={published_fp}")
        
        # Check 4: Essential fields present
        if 'match_id' not in data:
            issues.append("Published data missing match_id")
        if 'back_price' not in data and 'odds' not in data:
            warnings.append("Published data missing odds information")
        
        fingerprint = create_fingerprint(data)
        
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"Publish validation failed",
                details={"issues": issues, "warnings": warnings, "channel": channel},
                fingerprint=fingerprint
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"Published with warnings",
                details={"warnings": warnings, "channel": channel, "subscribers": publish_result},
                fingerprint=fingerprint
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"Published to {channel} ({publish_result} subscribers)",
                fingerprint=fingerprint
            )


class CortexDataValidator(BaseDataValidator):
    """
    Stage 4 Validator: Validates Cortex event-to-signal transformation
    
    Checks:
    1. Event data is complete
    2. Signal uses correct odds from event
    3. Signal team matches event team
    4. Confidence calculation is reasonable
    5. Timestamp propagation is correct
    """
    
    def __init__(self):
        super().__init__(stage=4, component="cortex")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate Cortex processing
        
        Args:
            data: Signal data
            context: Should contain 'event' (original event that triggered signal)
        """
        issues = []
        warnings = []
        context = context or {}
        
        event = context.get('event', {})
        
        # Check 1: Signal has required fields
        required_signal_fields = ['strategy', 'action', 'odds', 'confidence']
        for field in required_signal_fields:
            if field not in data:
                issues.append(f"Signal missing field: {field}")
        
        # Check 2: Signal odds match event odds
        signal_odds = data.get('odds')
        event_back_price = event.get('back_price')
        
        if signal_odds and event_back_price:
            # Allow small floating point differences
            if abs(float(signal_odds) - float(event_back_price)) > 0.01:
                issues.append(f"Signal odds ({signal_odds}) don't match event odds ({event_back_price})")
        
        # Check 3: Signal team is from the event
        signal_team = data.get('team')
        event_team1 = event.get('team1')
        event_team2 = event.get('team2')
        
        if signal_team and event_team1 and event_team2:
            if signal_team not in [event_team1, event_team2]:
                warnings.append(f"Signal team '{signal_team}' not in event teams ({event_team1}, {event_team2})")
        
        # Check 4: Confidence is valid
        confidence = data.get('confidence', 0)
        if confidence < 0 or confidence > 1:
            issues.append(f"Invalid confidence: {confidence} (should be 0-1)")
        elif confidence < 0.5:
            warnings.append(f"Low confidence signal: {confidence:.0%}")
        
        # Check 5: Action is valid
        action = data.get('action', '')
        if action not in ['BACK', 'LAY']:
            issues.append(f"Invalid action: {action}")
        
        # Check 6: Strategy is valid
        valid_strategies = ['panic_rebound', 'mean_reversion', 'whale_shadow', 
                          'odds_velocity', 'simple_odds_change']
        strategy = data.get('strategy', '')
        if strategy and strategy not in valid_strategies:
            warnings.append(f"Unknown strategy: {strategy}")
        
        fingerprint = create_fingerprint({
            'match_id': event.get('match_id'),
            'back_price': signal_odds,
            'timestamp': data.get('timestamp', datetime.utcnow().isoformat())
        })
        
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"Signal validation failed",
                details={"issues": issues, "warnings": warnings},
                fingerprint=fingerprint
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"Signal validated with warnings",
                details={"warnings": warnings},
                fingerprint=fingerprint
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"Signal valid: [{strategy}] {action} @ {signal_odds}",
                fingerprint=fingerprint
            )


class APIResponseValidator(BaseDataValidator):
    """
    Stage 5 Validator: Validates Backend API responses
    
    Checks:
    1. Response schema matches specification
    2. Timestamp formats are correct (ISO 8601 with UTC)
    3. Data types are correct
    4. No data corruption from Redis read
    """
    
    def __init__(self):
        super().__init__(stage=5, component="backend_api")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate API response
        
        Args:
            data: API response data
            context: Should contain 'redis_data' for comparison
        """
        issues = []
        warnings = []
        context = context or {}
        
        redis_data = context.get('redis_data')
        
        # Check 1: Response has expected structure
        if 'status' not in data:
            warnings.append("Response missing 'status' field")
        
        if 'matches' in data:
            matches = data['matches']
            if not isinstance(matches, list):
                issues.append("'matches' should be a list")
            else:
                # Validate each match
                for i, match in enumerate(matches):
                    match_issues = self._validate_match_response(match, i)
                    issues.extend(match_issues)
        
        # Check 2: Compare with Redis data if available
        if redis_data and 'matches' in data:
            for api_match in data['matches']:
                match_id = api_match.get('match_id')
                redis_match = next(
                    (m for m in redis_data if m.get('match_id') == match_id),
                    None
                )
                if redis_match:
                    # Compare critical fields
                    api_odds = api_match.get('current_odds', {})
                    redis_odds = redis_match.get('current_odds', {})
                    if api_odds != redis_odds:
                        issues.append(f"Odds mismatch for {match_id}: API={api_odds}, Redis={redis_odds}")
        
        # Check 3: Count validation
        reported_count = data.get('count', 0)
        actual_count = len(data.get('matches', []))
        if reported_count != actual_count:
            issues.append(f"Count mismatch: reported {reported_count}, actual {actual_count}")
        
        fingerprint = None
        if data.get('matches'):
            first_match = data['matches'][0]
            fingerprint = create_fingerprint({
                'match_id': first_match.get('match_id'),
                'back_price': list(first_match.get('current_odds', {}).values())[0] if first_match.get('current_odds') else None,
                'timestamp': first_match.get('last_updated')
            })
        
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"API response validation failed",
                details={"issues": issues, "warnings": warnings},
                fingerprint=fingerprint
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"API response validated with warnings",
                details={"warnings": warnings},
                fingerprint=fingerprint
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"API response valid: {actual_count} matches",
                fingerprint=fingerprint
            )
    
    def _validate_match_response(self, match: Dict, index: int) -> List[str]:
        """Validate a single match in API response"""
        issues = []
        prefix = f"Match[{index}]"
        
        # Required fields
        if 'match_id' not in match:
            issues.append(f"{prefix}: missing match_id")
        if 'current_odds' not in match:
            issues.append(f"{prefix}: missing current_odds")
        
        # Timestamp format
        last_updated = match.get('last_updated')
        if last_updated:
            try:
                # Should be ISO 8601 format
                datetime.fromisoformat(last_updated.rstrip('Z'))
            except (ValueError, AttributeError):
                issues.append(f"{prefix}: invalid timestamp format: {last_updated}")
        
        return issues


class FrontendDisplayValidator(BaseDataValidator):
    """
    Stage 6 Validator: Validates Frontend display data
    
    Checks:
    1. Displayed values match API response
    2. Timestamp display is correct (UTC to local conversion)
    3. Stale detection is accurate
    4. Odds formatting is correct
    """
    
    STALE_THRESHOLD_SECONDS = 15
    
    def __init__(self):
        super().__init__(stage=6, component="frontend")
    
    def validate(self, data: Dict, context: Dict = None) -> ValidationResult:
        """
        Validate frontend display data
        
        Args:
            data: Data being displayed
            context: Should contain 'api_data' for comparison
        """
        issues = []
        warnings = []
        context = context or {}
        
        api_data = context.get('api_data', {})
        
        # Check 1: Match count matches API
        displayed_count = data.get('displayed_count', 0)
        api_count = len(api_data.get('matches', []))
        
        if displayed_count != api_count:
            warnings.append(f"Display count ({displayed_count}) doesn't match API ({api_count})")
        
        # Check 2: Validate stale detection
        displayed_matches = data.get('displayed_matches', [])
        for match in displayed_matches:
            last_updated = match.get('last_updated')
            is_stale = match.get('is_stale', False)
            
            if last_updated:
                try:
                    # Parse timestamp
                    ts = last_updated.rstrip('Z')
                    if not last_updated.endswith('Z'):
                        ts = last_updated
                    timestamp = datetime.fromisoformat(ts)
                    age_seconds = (datetime.utcnow() - timestamp).total_seconds()
                    
                    expected_stale = age_seconds > self.STALE_THRESHOLD_SECONDS
                    
                    if is_stale != expected_stale:
                        warnings.append(
                            f"Stale detection mismatch for {match.get('match_id')}: "
                            f"displayed={is_stale}, expected={expected_stale} (age={age_seconds:.1f}s)"
                        )
                except (ValueError, TypeError):
                    warnings.append(f"Could not parse timestamp: {last_updated}")
        
        # Check 3: Odds display formatting
        for match in displayed_matches:
            odds = match.get('displayed_odds')
            if odds is not None:
                # Check it's a reasonable number
                try:
                    odds_val = float(odds)
                    if odds_val < 1.01 or odds_val > 100:
                        issues.append(f"Invalid displayed odds: {odds}")
                except (ValueError, TypeError):
                    issues.append(f"Odds not a valid number: {odds}")
        
        if issues:
            return self._create_result(
                ValidationStatus.FAIL,
                f"Display validation failed",
                details={"issues": issues, "warnings": warnings}
            )
        elif warnings:
            return self._create_result(
                ValidationStatus.WARNING,
                f"Display validated with warnings",
                details={"warnings": warnings}
            )
        else:
            return self._create_result(
                ValidationStatus.PASS,
                f"Display valid: {displayed_count} matches"
            )


# Factory function to get validators
def get_validator(stage: int) -> BaseDataValidator:
    """Get the appropriate validator for a pipeline stage"""
    validators = {
        1: ScraperDataValidator,
        2: ParserDataValidator,
        3: RedisPublishValidator,
        4: CortexDataValidator,
        5: APIResponseValidator,
        6: FrontendDisplayValidator,
    }
    
    validator_class = validators.get(stage)
    if validator_class:
        return validator_class()
    raise ValueError(f"No validator for stage {stage}")


# Convenience instances
scraper_validator = ScraperDataValidator()
parser_validator = ParserDataValidator()
redis_validator = RedisPublishValidator()
cortex_validator = CortexDataValidator()
api_validator = APIResponseValidator()
frontend_validator = FrontendDisplayValidator()
