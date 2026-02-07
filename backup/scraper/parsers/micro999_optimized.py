"""
Micro999 Optimized Parser
Specifically designed for micro999.co/game/4 cricket betting page structure

Phase 1.2 Enhancement:
- Added stake_limit extraction
- Added is_suspended detection
- Added is_suspension_event flag for Whale Shadow strategy

Phase 1.3 Enhancement:
- Added data validation at parse stage
- Added fingerprint tracking for data lineage
"""

import re
import os
import sys
from datetime import datetime
from typing import Dict, Optional, List
from loguru import logger

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils.flow_logger import get_flow_logger
from utils.data_validators import ParserDataValidator
from utils.data_fingerprint import create_fingerprint, track_data

# Initialize flow logger and validator for Stage 2
flow_log = get_flow_logger("Parser")
parser_validator = ParserDataValidator()


class Micro999OptimizedParser:
    """
    Optimized parser for micro999.co cricket betting page
    
    Page structure observed:
    - Cricket matches listed with team names
    - Odds displayed in blue/pink boxes (decimal format)
    - Columns: 1 (Team 1), x (Draw), 2 (Team 2)
    - Live Now indicators for active matches
    - BM (Bookmaker) and F (Fancy) betting options
    
    Phase 1.2: Now tracks stake_limit and suspension events
    """
    
    def __init__(self):
        self.match_id_counter = 1000
        self.match_cache = {}
        self.metadata_cache = {}  # Phase 1.2: Track metadata by match
    
    def parse(self, raw_data: Dict) -> Optional[List[Dict]]:
        """
        Parse raw scraper data into normalized format
        
        Returns list of parsed match data (multiple matches on page)
        """
        try:
            source = raw_data.get('source', 'unknown')
            data = raw_data.get('data', {})
            timestamp = raw_data.get('timestamp', datetime.utcnow().isoformat())
            
            # Phase 1.2: Extract metadata events
            metadata_events = raw_data.get('metadata_events', [])
            self._update_metadata_cache(metadata_events)
            
            # Debug: Log what we're parsing
            if not hasattr(self, '_parse_count'):
                self._parse_count = 0
            self._parse_count += 1
            if self._parse_count <= 3:
                logger.info(f"🔍 Parser called #{self._parse_count}: source={source}, data keys={list(data.keys()) if isinstance(data, dict) else 'not_dict'}")
            
            if source == 'dom':
                return self._parse_dom_data(data, timestamp, metadata_events)
            elif source == 'websocket':
                return self._parse_websocket_data(data, timestamp)
            
            return None
        
        except Exception as e:
            logger.error(f"Error parsing data: {e}")
            return None
    
    def _update_metadata_cache(self, metadata_events: List[Dict]):
        """Phase 1.2: Update metadata cache from events"""
        for event in metadata_events:
            match_id = event.get('match_id', '')
            if match_id:
                self.metadata_cache[match_id] = {
                    'stake_limit': event.get('stake_limit'),
                    'previous_stake_limit': event.get('previous_stake_limit'),
                    'stake_limit_changed': event.get('stake_limit_changed', False),
                    'stake_limit_drop_pct': event.get('stake_limit_drop_pct', 0.0),
                    'is_suspended': event.get('is_suspended', False),
                    'is_suspension_event': event.get('is_suspension_event', False),
                    'timestamp': event.get('timestamp')
                }
    
    def _parse_dom_data(self, data: Dict, timestamp: str, metadata_events: List[Dict] = None) -> Optional[List[Dict]]:
        """
        Parse DOM-extracted data from micro999.co
        
        NEW: Uses structuredMatches for accurate odds-to-match association
        """
        if metadata_events is None:
            metadata_events = []
        try:
            # NEW: Use structured matches if available (preferred method)
            structured_matches = data.get('structuredMatches', [])
            
            if structured_matches:
                parsed_matches = []
                
                for match in structured_matches:
                    if not match.get('isLive', False):
                        continue  # Skip non-live matches
                    
                    team1 = match.get('team1', '')
                    team2 = match.get('team2', '')
                    back_odds = match.get('backOdds')
                    lay_odds = match.get('layOdds')
                    
                    if not team1 or not back_odds:
                        continue
                    
                    match_id = f"micro999_{team1}_{team2}".replace(' ', '_')
                    
                    match_record = {
                        'timestamp': datetime.utcnow().isoformat(),
                        'match_id': match_id,
                        'market_type': 'match_odds',
                        'team': team1,
                        'team1': team1,
                        'team2': team2,
                        'odds': back_odds,
                        'back_price': back_odds,
                        'lay_price': lay_odds,
                        'volume': None,
                        'score': None,
                        'wickets': None,
                        'overs': None,
                        'run_rate': None,
                        'required_run_rate': None,
                        'is_suspended': False,
                        'is_suspension_event': False,
                        'stake_limit': None,
                        'previous_stake_limit': None,
                        'stake_limit_changed': False,
                        'stake_limit_drop_pct': 0.0,
                        'metadata': {
                            'parser_version': '3.0',  # New structured parser
                            'source': 'micro999',
                            'site': 'micro999.co',
                            'url': data.get('url', ''),
                            'match_name': match.get('matchName', f'{team1} v {team2}'),
                            'is_live': True,
                            'live_indicator': 'Live Now'
                        }
                    }
                    parsed_matches.append(match_record)
                
                live_count = len(parsed_matches)
                total_count = data.get('totalMatches', 0)
                logger.info(f"Parsed {live_count} LIVE matches from {total_count} total (structured parser v3.0)")
                
                # Stage 2 Data Validation: Validate each parsed record
                valid_matches = []
                validation_failures = 0
                
                for match_record in parsed_matches:
                    validation_result = parser_validator.validate(match_record)
                    
                    if validation_result.is_valid():
                        valid_matches.append(match_record)
                        
                        # Track data with fingerprint
                        fingerprint = track_data(
                            stage=2,
                            component="parser",
                            data=match_record,
                            validation_result=validation_result
                        )
                        logger.debug(f"[STAGE-2] Validated: {match_record.get('match_id')} [FP:{fingerprint}]")
                    else:
                        validation_failures += 1
                        logger.warning(f"[STAGE-2] Validation failed: {validation_result}")
                        if validation_result.details.get('issues'):
                            for issue in validation_result.details['issues'][:2]:
                                logger.warning(f"  - {issue}")
                
                if validation_failures > 0:
                    logger.warning(f"[STAGE-2] {validation_failures}/{len(parsed_matches)} matches failed validation")
                
                # Log flow summary
                if valid_matches:
                    odds_summary = ", ".join([
                        f"{m.get('team1', '?')[:10]}:{m.get('back_price', 'N/A')}"
                        for m in valid_matches[:3]
                    ])
                    flow_log.stage2_parsed(
                        match_count=len(valid_matches),
                        match_ids=[m.get('match_id') for m in valid_matches],
                        odds_summary=odds_summary
                    )
                
                return valid_matches if valid_matches else None
            
            # FALLBACK: Old method for backward compatibility
            matches_data = data.get('matches', [])
            if not matches_data:
                logger.debug("No match data found in DOM")
                return None
            
            # Extract all odds from the page
            all_odds = []
            for match in matches_data:
                odds_str = match.get('odds', '')
                odds = self._extract_decimal_odds(odds_str)
                if odds:
                    all_odds.append({
                        'odds': odds,
                        'element': match.get('element', ''),
                        'tag': match.get('tag', ''),
                        'html': match.get('html', '')
                    })
            
            if not all_odds:
                logger.debug("No valid odds extracted")
                return None
            
            # Log live match filtering statistics
            live_matches_count = len(data.get('liveMatches', []))
            total_matches_count = data.get('totalMatches', 0)
            logger.info(f"Extracted {len(all_odds)} odds from {live_matches_count} live matches (filtered {total_matches_count - live_matches_count} non-live matches)")
            
            parsed_matches = []
            page_text = data.get('text', '')
            match_names = self.extract_match_names(page_text, live_only=True)
            
            for i in range(0, len(all_odds), 2):
                if i + 1 < len(all_odds):
                    match_idx = i // 2
                    
                    if match_idx < len(match_names):
                        match_info = match_names[match_idx]
                        
                        if not match_info.get('is_live', False):
                            continue
                        
                        match_id = f"micro999_{match_info['team1']}_{match_info['team2']}".replace(' ', '_')
                        team = match_info['team1']
                        
                        match_metadata = self._get_match_metadata(match_id, match_info)
                        
                        match_record = {
                            'timestamp': datetime.utcnow().isoformat(),
                            'match_id': match_id,
                            'market_type': 'match_odds',
                            'team': team,
                            'team1': match_info['team1'],
                            'team2': match_info['team2'],
                            'odds': all_odds[i]['odds'],
                            'back_price': all_odds[i]['odds'],
                            'lay_price': all_odds[i+1]['odds'] if i+1 < len(all_odds) else None,
                            'volume': None,
                            'score': None,
                            'wickets': None,
                            'overs': None,
                            'run_rate': None,
                            'required_run_rate': None,
                            'is_suspended': match_metadata.get('is_suspended', False),
                            'is_suspension_event': match_metadata.get('is_suspension_event', False),
                            'stake_limit': match_metadata.get('stake_limit'),
                            'previous_stake_limit': match_metadata.get('previous_stake_limit'),
                            'stake_limit_changed': match_metadata.get('stake_limit_changed', False),
                            'stake_limit_drop_pct': match_metadata.get('stake_limit_drop_pct', 0.0),
                            'metadata': {
                                'raw_data': {
                                    'back': all_odds[i],
                                    'lay': all_odds[i+1] if i+1 < len(all_odds) else None
                                },
                                'parser_version': '2.3',
                                'source': 'micro999',
                                'site': 'micro999.co',
                                'url': data.get('url', ''),
                                'match_name': match_info['match_string'],
                                'is_live': True,
                                'live_indicator': match_info.get('live_indicator', 'Live Now')
                            }
                        }
                        parsed_matches.append(match_record)
            
            logger.info(f"Parsed {len(parsed_matches)} match records (fallback parser)")
            
            # Stage 2 Data Validation: Validate fallback-parsed records
            valid_matches = []
            validation_failures = 0
            
            for match_record in parsed_matches:
                validation_result = parser_validator.validate(match_record)
                
                if validation_result.is_valid():
                    valid_matches.append(match_record)
                    
                    # Track data with fingerprint
                    fingerprint = track_data(
                        stage=2,
                        component="parser_fallback",
                        data=match_record,
                        validation_result=validation_result
                    )
                    logger.debug(f"[STAGE-2] Validated (fallback): {match_record.get('match_id')} [FP:{fingerprint}]")
                else:
                    validation_failures += 1
                    logger.warning(f"[STAGE-2] Validation failed (fallback): {validation_result}")
            
            if validation_failures > 0:
                logger.warning(f"[STAGE-2] Fallback: {validation_failures}/{len(parsed_matches)} matches failed validation")
            
            # Log flow summary
            if valid_matches:
                odds_summary = ", ".join([
                    f"{m.get('team1', '?')[:10]}:{m.get('back_price', 'N/A')}"
                    for m in valid_matches[:3]
                ])
                flow_log.stage2_parsed(
                    match_count=len(valid_matches),
                    match_ids=[m.get('match_id') for m in valid_matches],
                    odds_summary=odds_summary
                )
            
            return valid_matches if valid_matches else None
        
        except Exception as e:
            logger.error(f"Error parsing DOM data: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_match_metadata(self, match_id: str, match_info: Dict) -> Dict:
        """
        Phase 1.2: Get metadata for a match from cache or DOM data
        
        Returns metadata dict with stake_limit, is_suspended, etc.
        """
        # First check cache
        if match_id in self.metadata_cache:
            return self.metadata_cache[match_id]
        
        # Check if match_info has metadata
        return {
            'stake_limit': match_info.get('stake_limit'),
            'previous_stake_limit': None,
            'stake_limit_changed': False,
            'stake_limit_drop_pct': 0.0,
            'is_suspended': match_info.get('is_suspended', False),
            'is_suspension_event': False
        }
    
    def _parse_websocket_data(self, data: Dict, timestamp: str) -> Optional[List[Dict]]:
        """Parse WebSocket data from micro999.co"""
        try:
            # WebSocket format to be determined based on actual traffic
            # For now, return None and log
            logger.debug("WebSocket parsing not yet implemented for micro999.co")
            return None
        
        except Exception as e:
            logger.error(f"Error parsing WebSocket data: {e}")
            return None
    
    def _extract_decimal_odds(self, value) -> Optional[float]:
        """
        Extract decimal odds from various formats
        
        Validates odds are in reasonable cricket betting range (1.01 to 100)
        """
        try:
            if isinstance(value, (int, float)):
                odds = float(value)
            elif isinstance(value, str):
                # Remove non-numeric characters except decimal point
                cleaned = re.sub(r'[^\d.]', '', value)
                if not cleaned:
                    return None
                odds = float(cleaned)
            else:
                return None
            
            # Validate odds are reasonable for cricket betting
            if 1.01 <= odds <= 100:
                return odds
            else:
                logger.debug(f"Odds {odds} outside valid range (1.01-100)")
                return None
        
        except Exception as e:
            logger.debug(f"Failed to extract odds from {value}: {e}")
            return None
    
    def extract_match_names(self, page_text: str, live_only: bool = True) -> List[Dict]:
        """
        Extract match names from page text, optionally filtering for live matches only
        
        Expected format: "India v South Africa", "Railways v Vidarbha", etc.
        
        Args:
            page_text: Raw page text containing match information
            live_only: If True, only return matches marked as live (default: True)
        
        Returns:
            List of match dictionaries with team names and live status
        """
        matches = []
        lines = page_text.split('\n')
        
        for i, line in enumerate(lines):
            # Pattern to match cricket match names
            pattern = r'(\w+(?:\s+\w+)*)\s+v\s+(\w+(?:\s+\w+)*)'
            match = re.search(pattern, line)
            
            if match:
                team1 = match.group(1).strip()
                team2 = match.group(2).strip()
                
                # Check if match is live
                # Method 1: Check for "Live Now" indicator
                is_live_now = 'Live Now' in line or \
                             (i > 0 and 'Live Now' in lines[i-1]) or \
                             (i < len(lines)-1 and 'Live Now' in lines[i+1])
                
                # Method 2: Check for score indicators (format: 123/4 or similar)
                has_score = False
                if i < len(lines)-1:
                    has_score = bool(re.search(r'\d+/\d+', lines[i+1]))
                if not has_score and i < len(lines)-2:
                    has_score = bool(re.search(r'\d+/\d+', lines[i+2]))
                
                is_live = is_live_now or has_score
                
                # Only add match if it's live (when live_only is True)
                if not live_only or is_live:
                    matches.append({
                        'team1': team1,
                        'team2': team2,
                        'match_string': f"{team1} v {team2}",
                        'is_live': is_live,
                        'live_indicator': 'Live Now' if is_live_now else ('Score' if has_score else None)
                    })
        
        return matches

