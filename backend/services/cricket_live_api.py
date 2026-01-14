"""
Cricket Live Data API Integration
Integrates with CricAPI for real-time match data (ball-by-ball, scores, wickets)

The Ghost (Data Engineer): "Real-time match context is the foundation of profitable betting.
Without live scores, our signals are blind to actual game momentum."
"""

import aiohttp
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BallEvent:
    """Individual ball event in a cricket match"""
    over: float
    runs: int
    wicket: bool
    batsman: str
    bowler: str
    extras: int = 0
    boundary: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class LiveMatchState:
    """Real-time match state"""
    match_id: str
    team1: str
    team2: str
    batting_team: str
    bowling_team: str
    
    # Current score
    runs: int
    wickets: int
    overs: float
    
    # Momentum indicators
    run_rate: float
    required_run_rate: Optional[float] = None
    
    # Recent momentum (last 6 balls)
    recent_runs: int = 0
    recent_wickets: int = 0
    
    # Match context
    match_format: str = "T20"  # T20, ODI, TEST
    venue: str = ""
    toss_winner: str = ""
    toss_decision: str = ""
    
    # Time
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def match_phase(self) -> str:
        """Identify current match phase"""
        if self.match_format == "T20":
            if self.overs < 6:
                return "powerplay"
            elif self.overs < 16:
                return "middle"
            else:
                return "death"
        elif self.match_format == "ODI":
            if self.overs < 10:
                return "powerplay_1"
            elif self.overs < 40:
                return "middle"
            else:
                return "death"
        return "unknown"
    
    @property
    def momentum_score(self) -> float:
        """Calculate momentum score (0-100)"""
        # Weighted momentum: recent runs positive, recent wickets negative
        base_momentum = 50.0
        
        # Recent runs boost (0-30 points)
        runs_boost = min(self.recent_runs * 3, 30)
        
        # Recent wickets penalty (-20 points per wicket)
        wickets_penalty = self.recent_wickets * 20
        
        momentum = base_momentum + runs_boost - wickets_penalty
        return max(0, min(100, momentum))
    
    @property
    def is_powerplay(self) -> bool:
        """Check if in powerplay"""
        return "powerplay" in self.match_phase.lower()


class CricketLiveAPI:
    """
    CricAPI Integration for live cricket data
    
    Features:
    - Real-time ball-by-ball updates
    - Match state tracking
    - Player performance data
    - Historical match data
    
    Rate Limiting: 100 requests/hour (free tier) or 1000 requests/hour (paid)
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('CRICAPI_KEY', '')
        self.base_url = "https://api.cricapi.com/v1"
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Cache to avoid redundant API calls
        self.cache: Dict[str, Any] = {}
        self.cache_ttl = 5  # seconds
        
        if not self.api_key:
            logger.warning("⚠️  CRICAPI_KEY not set. Live match data will be unavailable.")
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """
        Make API request with error handling and rate limiting
        
        The Sentinel (DevOps): "Implement exponential backoff and circuit breaker
        to prevent API ban during high-frequency requests."
        """
        if not self.api_key:
            raise ValueError("CRICAPI_KEY not configured")
        
        url = f"{self.base_url}/{endpoint}"
        params = params or {}
        params['apikey'] = self.api_key
        
        # Check cache
        cache_key = f"{endpoint}:{str(params)}"
        if cache_key in self.cache:
            cached_data, cached_time = self.cache[cache_key]
            if (datetime.utcnow() - cached_time).total_seconds() < self.cache_ttl:
                logger.debug(f"Cache hit for {endpoint}")
                return cached_data
        
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Cache result
                    self.cache[cache_key] = (data, datetime.utcnow())
                    
                    return data
                elif response.status == 429:
                    logger.error(f"⚠️  Rate limit exceeded for CricAPI")
                    raise Exception("Rate limit exceeded")
                else:
                    logger.error(f"❌ CricAPI error: {response.status}")
                    return {}
        
        except asyncio.TimeoutError:
            logger.error(f"❌ CricAPI timeout for {endpoint}")
            return {}
        except Exception as e:
            logger.error(f"❌ CricAPI request failed: {e}")
            return {}
    
    async def get_current_matches(self) -> List[Dict]:
        """
        Get list of currently live matches
        
        Returns:
            List of live match summaries with match_id, teams, scores
        """
        data = await self._make_request("currentMatches")
        
        if data.get('status') == 'success':
            matches = data.get('data', [])
            
            # Filter for live matches only
            live_matches = [
                m for m in matches 
                if m.get('matchStarted') and not m.get('matchEnded')
            ]
            
            logger.info(f"📡 Found {len(live_matches)} live matches")
            return live_matches
        
        return []
    
    async def get_match_info(self, match_id: str) -> Optional[Dict]:
        """
        Get detailed match information
        
        Args:
            match_id: CricAPI match ID
        
        Returns:
            Detailed match data including teams, venue, toss, etc.
        """
        data = await self._make_request("match_info", {"id": match_id})
        
        if data.get('status') == 'success':
            return data.get('data', {})
        
        return None
    
    async def get_live_score(self, match_id: str) -> Optional[LiveMatchState]:
        """
        Get live score and match state
        
        Args:
            match_id: CricAPI match ID
        
        Returns:
            LiveMatchState with current score, momentum, phase
        """
        data = await self._make_request("match_scorecard", {"id": match_id})
        
        if data.get('status') != 'success':
            return None
        
        match_data = data.get('data', {})
        
        # Parse score data
        score = match_data.get('score', [{}])[0]  # Current innings
        
        try:
            match_state = LiveMatchState(
                match_id=match_id,
                team1=match_data.get('team1', ''),
                team2=match_data.get('team2', ''),
                batting_team=score.get('inning', ''),
                bowling_team=match_data.get('team2', '') if score.get('inning') == match_data.get('team1', '') else match_data.get('team1', ''),
                runs=score.get('r', 0),
                wickets=score.get('w', 0),
                overs=score.get('o', 0.0),
                run_rate=score.get('r', 0) / max(score.get('o', 1), 1),
                match_format=match_data.get('matchType', 'T20'),
                venue=match_data.get('venue', ''),
                toss_winner=match_data.get('tossWinner', ''),
                toss_decision=match_data.get('tossChoice', '')
            )
            
            logger.info(f"📊 {match_state.batting_team}: {match_state.runs}/{match_state.wickets} ({match_state.overs} overs)")
            return match_state
        
        except Exception as e:
            logger.error(f"❌ Failed to parse match state: {e}")
            return None
    
    async def get_ball_by_ball(self, match_id: str) -> List[BallEvent]:
        """
        Get ball-by-ball commentary (for premium API tier)
        
        The Math (Quant Analyst): "Ball-by-ball data is gold for momentum modeling.
        Each ball event updates our probability distribution in real-time."
        """
        # Note: This requires premium CricAPI subscription
        data = await self._make_request("match_commentary", {"id": match_id})
        
        if data.get('status') != 'success':
            logger.warning(f"⚠️  Ball-by-ball data unavailable (requires premium API)")
            return []
        
        # Parse ball events
        ball_events = []
        commentary = data.get('data', {}).get('commentary', [])
        
        for ball in commentary:
            try:
                event = BallEvent(
                    over=ball.get('over', 0.0),
                    runs=ball.get('runs', 0),
                    wicket=ball.get('isWicket', False),
                    batsman=ball.get('batsman', ''),
                    bowler=ball.get('bowler', ''),
                    extras=ball.get('extras', 0),
                    boundary=ball.get('runs', 0) in [4, 6]
                )
                ball_events.append(event)
            except Exception as e:
                logger.error(f"❌ Failed to parse ball event: {e}")
        
        return ball_events
    
    async def get_player_stats(self, player_name: str) -> Optional[Dict]:
        """
        Get player statistics (career, recent form)
        
        The Oracle (Betting Expert): "Player form is critical. A batsman in red-hot
        form should shift odds, especially if they're currently at the crease."
        """
        # Note: This is a placeholder - actual endpoint varies by API provider
        data = await self._make_request("player_stats", {"name": player_name})
        
        if data.get('status') == 'success':
            return data.get('data', {})
        
        return None


class MatchMonitor:
    """
    Continuous monitoring of live matches with state updates
    
    The Architect (Full-Stack): "This is the nervous system of TITAN. Every betting
    signal depends on accurate, real-time match state."
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api = CricketLiveAPI(api_key)
        self.active_matches: Dict[str, LiveMatchState] = {}
        self.running = False
    
    async def start_monitoring(self, poll_interval: int = 10):
        """
        Start continuous monitoring of live matches
        
        Args:
            poll_interval: Seconds between API polls
        """
        self.running = True
        logger.info(f"🚀 Starting match monitor (polling every {poll_interval}s)")
        
        async with self.api:
            while self.running:
                try:
                    # Get current live matches
                    live_matches = await self.api.get_current_matches()
                    
                    # Update state for each match
                    for match in live_matches:
                        match_id = match.get('id')
                        if match_id:
                            state = await self.api.get_live_score(match_id)
                            if state:
                                self.active_matches[match_id] = state
                                
                                # Log significant events
                                if state.momentum_score > 75:
                                    logger.info(f"🔥 HIGH MOMENTUM: {state.batting_team} - {state.momentum_score:.0f}")
                                elif state.recent_wickets > 0:
                                    logger.info(f"🎯 WICKET ALERT: {state.batting_team} - {state.wickets}W")
                    
                    # Remove ended matches
                    current_ids = {m.get('id') for m in live_matches}
                    ended_matches = [mid for mid in self.active_matches.keys() if mid not in current_ids]
                    for mid in ended_matches:
                        logger.info(f"✅ Match ended: {mid}")
                        del self.active_matches[mid]
                    
                    # Wait before next poll
                    await asyncio.sleep(poll_interval)
                
                except Exception as e:
                    logger.error(f"❌ Match monitor error: {e}")
                    await asyncio.sleep(poll_interval)
    
    def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.running = False
        logger.info("🛑 Match monitor stopped")
    
    def get_match_state(self, match_id: str) -> Optional[LiveMatchState]:
        """Get current state for a specific match"""
        return self.active_matches.get(match_id)
    
    def get_all_active_matches(self) -> List[LiveMatchState]:
        """Get all currently active matches"""
        return list(self.active_matches.values())


# Global monitor instance
_monitor: Optional[MatchMonitor] = None

def get_match_monitor() -> MatchMonitor:
    """Get or create global match monitor"""
    global _monitor
    if _monitor is None:
        _monitor = MatchMonitor()
    return _monitor


# Example usage
if __name__ == "__main__":
    async def test_cricket_api():
        """Test the Cricket API integration"""
        api = CricketLiveAPI()
        
        async with api:
            # Get current matches
            print("\n📡 Fetching live matches...")
            matches = await api.get_current_matches()
            print(f"Found {len(matches)} live matches\n")
            
            for match in matches[:3]:  # Test first 3 matches
                match_id = match.get('id')
                print(f"\n🏏 Match: {match.get('name')}")
                print(f"   ID: {match_id}")
                
                # Get live score
                state = await api.get_live_score(match_id)
                if state:
                    print(f"   Score: {state.batting_team} - {state.runs}/{state.wickets} ({state.overs} overs)")
                    print(f"   Phase: {state.match_phase}")
                    print(f"   Momentum: {state.momentum_score:.0f}/100")
                    print(f"   Run Rate: {state.run_rate:.2f}")
    
    # Run test
    asyncio.run(test_cricket_api())

