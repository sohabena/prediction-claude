"""
Cricket Score API Integration
Fetches live match data and detects match completion

For production, integrate with:
- CricAPI (https://www.cricapi.com/) - free tier available
- Cricbuzz API 
- ESPN Cricinfo API

This is a mock implementation with fallback to manual entry
"""

import requests
from typing import Optional, Dict
from loguru import logger
from datetime import datetime
import os


class CricketAPIClient:
    """
    Cricket API Client
    
    Note: This is a mock implementation. In production, replace with actual API integration.
    """
    
    def __init__(self):
        self.api_key = os.getenv('CRICKET_API_KEY', '')
        self.base_url = os.getenv('CRICKET_API_URL', 'https://api.cricapi.com/v1')
        self.enabled = bool(self.api_key)
        
        if not self.enabled:
            logger.warning("Cricket API key not found. Using mock data.")
    
    def get_match_status(self, match_id: str) -> Optional[Dict]:
        """
        Get current status of a match
        
        Returns:
            dict: {
                'match_id': str,
                'status': str,  # 'live', 'completed', 'upcoming'
                'team1': str,
                'team2': str,
                'winner': Optional[str],
                'score_team1': Optional[str],
                'score_team2': Optional[str],
                'match_type': str,  # 'T20', 'ODI', 'TEST'
            }
        """
        if not self.enabled:
            logger.debug(f"Mock API: Getting status for match {match_id}")
            return self._mock_match_status(match_id)
        
        try:
            # Real API call (example with CricAPI)
            endpoint = f"{self.base_url}/match_info"
            params = {
                'apikey': self.api_key,
                'id': match_id
            }
            
            response = requests.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Parse and normalize response
            return self._parse_match_data(data)
            
        except Exception as e:
            logger.error(f"Error fetching match status: {e}")
            return None
    
    def is_match_completed(self, match_id: str) -> tuple[bool, Optional[str]]:
        """
        Check if a match is completed and get winner
        
        Returns:
            tuple: (is_completed: bool, winner: Optional[str])
        """
        match_data = self.get_match_status(match_id)
        
        if not match_data:
            return False, None
        
        is_completed = match_data['status'] == 'completed'
        winner = match_data.get('winner')
        
        return is_completed, winner
    
    def get_live_matches(self) -> list[Dict]:
        """
        Get all currently live matches
        
        Returns:
            list: List of live match dictionaries
        """
        if not self.enabled:
            logger.debug("Mock API: Getting live matches")
            return []
        
        try:
            endpoint = f"{self.base_url}/currentMatches"
            params = {'apikey': self.api_key}
            
            response = requests.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Filter for live matches only
            live_matches = [m for m in data.get('data', []) if m.get('matchStarted') and not m.get('matchEnded')]
            
            return [self._parse_match_data(m) for m in live_matches]
            
        except Exception as e:
            logger.error(f"Error fetching live matches: {e}")
            return []
    
    def _parse_match_data(self, raw_data: Dict) -> Dict:
        """
        Parse and normalize match data from API response
        
        This method handles different API response formats
        """
        # This is API-specific parsing logic
        # Adjust based on actual API being used
        
        return {
            'match_id': raw_data.get('id', raw_data.get('match_id', 'unknown')),
            'status': self._determine_status(raw_data),
            'team1': raw_data.get('team1', raw_data.get('teams', ['', ''])[0]),
            'team2': raw_data.get('team2', raw_data.get('teams', ['', ''])[1]),
            'winner': raw_data.get('winner', raw_data.get('matchWinner')),
            'score_team1': raw_data.get('score1', raw_data.get('teamInfo', [{}])[0].get('score')),
            'score_team2': raw_data.get('score2', raw_data.get('teamInfo', [{}])[1].get('score')),
            'match_type': raw_data.get('matchType', raw_data.get('type', 'T20')),
            'venue': raw_data.get('venue', ''),
            'match_date': raw_data.get('dateTimeGMT', datetime.utcnow().isoformat())
        }
    
    def _determine_status(self, raw_data: Dict) -> str:
        """Determine match status from raw API data"""
        if raw_data.get('matchEnded') or raw_data.get('status') == 'completed':
            return 'completed'
        elif raw_data.get('matchStarted') or raw_data.get('status') == 'live':
            return 'live'
        else:
            return 'upcoming'
    
    def _mock_match_status(self, match_id: str) -> Dict:
        """
        Mock match data for testing when API is not available
        
        In production, this would be removed
        """
        return {
            'match_id': match_id,
            'status': 'live',  # Mock as live
            'team1': 'India',
            'team2': 'Australia',
            'winner': None,  # Not completed yet
            'score_team1': '180/5 (18.2 ov)',
            'score_team2': None,  # Batting second not started
            'match_type': 'T20',
            'venue': 'Wankhede Stadium',
            'match_date': datetime.utcnow().isoformat()
        }


# Singleton instance
cricket_api = CricketAPIClient()


# Helper functions for easy access
def get_match_status(match_id: str) -> Optional[Dict]:
    """Get match status"""
    return cricket_api.get_match_status(match_id)


def is_match_completed(match_id: str) -> tuple[bool, Optional[str]]:
    """Check if match is completed"""
    return cricket_api.is_match_completed(match_id)


def get_live_matches() -> list[Dict]:
    """Get all live matches"""
    return cricket_api.get_live_matches()


# Manual match completion endpoint (fallback)
def manually_complete_match(
    match_id: str,
    winner: str,
    score_team1: str,
    score_team2: str
) -> Dict:
    """
    Manually record match completion (for when API is unavailable)
    
    This allows users to manually input match results
    """
    return {
        'match_id': match_id,
        'status': 'completed',
        'winner': winner,
        'score_team1': score_team1,
        'score_team2': score_team2,
        'completed_at': datetime.utcnow().isoformat(),
        'manually_entered': True
    }

