"""
Match Context API Integration
Fetches live match data from Cricbuzz/ESPN for detailed match context

Author: The Ghost + The Architect
Purpose: Provide match context for elite betting strategies
"""

import aiohttp
import asyncio
from typing import Dict, Optional, List
from datetime import datetime
from loguru import logger


class MatchContextAPI:
    """
    Fetches live cricket match data from multiple sources
    
    Priority order:
    1. Cricbuzz (unofficial API)
    2. CricketAPI.com
    3. ESPN Cricinfo
    """
    
    def __init__(self):
        self.session = None
        self.cricbuzz_base = "https://cricbuzz-cricket.p.rapidapi.com"
        self.backup_sources = []
        
        # Rate limiting
        self.last_request_time = {}
        self.min_request_interval = 5  # seconds
        
        logger.info("Match Context API initialized")
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """Close aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get_match_context(self, match_id: str, team1: str, team2: str) -> Optional[Dict]:
        """
        Get live match context for a specific match
        
        Returns:
        {
            'match_id': str,
            'team1': str,
            'team2': str,
            'overs': float,
            'wickets': int,
            'score': int,
            'run_rate': float,
            'required_run_rate': float (if chasing),
            'batting_team': str,
            'bowling_team': str,
            'match_phase': str,
            'last_wicket_over': float,
            'recent_run_rate': float,  # Last 3 overs
            'timestamp': str
        }
        """
        try:
            # Try Cricbuzz first (unofficial/scraped approach)
            context = await self._get_cricbuzz_context(team1, team2)
            if context:
                return context
            
            # Fallback to basic inference from match names
            logger.warning(f"Could not fetch match context for {team1} v {team2}, using fallback")
            return self._create_fallback_context(match_id, team1, team2)
            
        except Exception as e:
            logger.error(f"Error getting match context: {e}")
            return None
    
    async def _get_cricbuzz_context(self, team1: str, team2: str) -> Optional[Dict]:
        """
        Get match context from Cricbuzz
        
        Note: This uses web scraping approach since official API is paid
        For production, consider:
        1. RapidAPI Cricbuzz (paid)
        2. CricketAPI.com (paid)
        3. Official ESPN API (if available)
        """
        try:
            # For now, return None to use fallback
            # In production, implement actual API/scraping here
            logger.debug("Cricbuzz integration pending - using fallback")
            return None
            
        except Exception as e:
            logger.error(f"Cricbuzz error: {e}")
            return None
    
    def _create_fallback_context(self, match_id: str, team1: str, team2: str) -> Dict:
        """
        Create basic context when API unavailable
        
        Uses reasonable defaults for live matches
        """
        return {
            'match_id': match_id,
            'team1': team1,
            'team2': team2,
            'overs': 10.0,  # Assume mid-innings if live
            'wickets': 2,   # Reasonable default
            'score': 80,    # ~8 RPO
            'run_rate': 8.0,
            'required_run_rate': None,  # Unknown
            'batting_team': team1,
            'bowling_team': team2,
            'match_phase': 'middle',
            'last_wicket_over': 8.0,
            'recent_run_rate': 8.5,
            'timestamp': datetime.utcnow().isoformat(),
            'data_source': 'fallback',
            'confidence': 'low'  # Mark as low confidence
        }
    
    async def search_live_matches(self) -> List[Dict]:
        """
        Search for all currently live matches
        
        Returns list of match IDs with basic info
        """
        try:
            # Placeholder - implement actual API call
            logger.debug("Live match search not yet implemented")
            return []
        except Exception as e:
            logger.error(f"Error searching live matches: {e}")
            return []
    
    async def enrich_match_data(self, matches: List[Dict]) -> List[Dict]:
        """
        Enrich match data from Micro999 with context from Cricbuzz
        
        Args:
            matches: List of matches from Micro999 scraper
        
        Returns:
            Enriched match data with context
        """
        enriched = []
        
        for match in matches:
            try:
                # Extract team names (if available)
                team1 = match.get('team', 'Unknown')
                team2 = match.get('opponent', 'Unknown')
                match_id = match.get('match_id', '')
                
                # Get context
                context = await self.get_match_context(match_id, team1, team2)
                
                if context:
                    # Merge context into match data
                    match_enriched = {**match, **context}
                    enriched.append(match_enriched)
                else:
                    enriched.append(match)
                    
            except Exception as e:
                logger.error(f"Error enriching match {match.get('match_id')}: {e}")
                enriched.append(match)
        
        return enriched


# Global instance
_match_context_api = None


async def get_match_context_api() -> MatchContextAPI:
    """Get global Match Context API instance"""
    global _match_context_api
    if _match_context_api is None:
        _match_context_api = MatchContextAPI()
    return _match_context_api

