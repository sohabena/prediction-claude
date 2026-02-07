"""
Cricbuzz Web Scraper
Scrapes live cricket match data without API keys

The Ghost (Data Engineer): "Web scraping is more reliable than free APIs.
We control the data flow and have no rate limits."
"""

import asyncio
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, asdict


@dataclass
class LiveMatchData:
    """Live match data from Cricbuzz"""
    match_id: str
    match_name: str
    team1: str
    team2: str
    
    # Current score
    batting_team: str
    runs: int
    wickets: int
    overs: float
    
    # Match details
    match_format: str  # T20, ODI, TEST
    venue: str
    status: str  # Live, Completed, Upcoming
    
    # Momentum indicators
    run_rate: float
    recent_runs: int = 0
    recent_wickets: int = 0
    
    # Timestamp
    scraped_at: datetime = None
    
    def __post_init__(self):
        if self.scraped_at is None:
            self.scraped_at = datetime.utcnow()
    
    @property
    def match_phase(self) -> str:
        """Determine match phase based on overs"""
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
        """Calculate momentum (0-100)"""
        base = 50.0
        runs_boost = min(self.recent_runs * 3, 30)
        wickets_penalty = self.recent_wickets * 20
        return max(0, min(100, base + runs_boost - wickets_penalty))


class CricbuzzScraper:
    """
    Scrapes live cricket data from Cricbuzz.com
    
    No API key required!
    
    Features:
    - Live match scores
    - Ball-by-ball updates
    - Match details (venue, format, teams)
    - Player statistics
    
    The Guardian (UI Tester): "Always handle scraping errors gracefully.
    Websites can change HTML structure anytime."
    """
    
    BASE_URL = "https://www.cricbuzz.com"
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get_live_matches(self) -> List[LiveMatchData]:
        """
        Scrape all currently live matches
        
        Returns:
            List of LiveMatchData objects
        """
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)
        
        try:
            url = f"{self.BASE_URL}/cricket-match/live-scores"
            logger.info(f"🏏 Scraping live matches from Cricbuzz...")
            
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    logger.error(f"❌ Failed to fetch live matches: {response.status}")
                    return []
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                matches = []
                
                # Find all live match cards
                match_cards = soup.find_all('div', class_='cb-mtch-lst')
                
                for card in match_cards:
                    try:
                        match_data = self._parse_match_card(card)
                        if match_data and match_data.status == "Live":
                            matches.append(match_data)
                    except Exception as e:
                        logger.error(f"❌ Error parsing match card: {e}")
                        continue
                
                logger.success(f"✅ Found {len(matches)} live matches")
                return matches
        
        except asyncio.TimeoutError:
            logger.error("❌ Timeout while scraping Cricbuzz")
            return []
        except Exception as e:
            logger.error(f"❌ Error scraping live matches: {e}")
            return []
    
    def _parse_match_card(self, card) -> Optional[LiveMatchData]:
        """Parse a single match card from HTML"""
        try:
            # Extract match link for ID
            link = card.find('a', href=True)
            if not link:
                return None
            
            match_id = link['href'].split('/')[-1]
            
            # Extract teams
            teams = card.find_all('div', class_='cb-hmscg-tm-nm')
            if len(teams) < 2:
                return None
            
            team1 = teams[0].get_text(strip=True)
            team2 = teams[1].get_text(strip=True)
            
            # Extract scores
            scores = card.find_all('div', class_='cb-hmscg-bat-txt')
            if not scores:
                return None
            
            # Parse first score (current batting team)
            score_text = scores[0].get_text(strip=True)
            runs, wickets, overs = self._parse_score(score_text)
            
            # Extract match format and venue
            match_desc = card.find('div', class_='cb-mtch-crd-hdr')
            match_info = match_desc.get_text(strip=True) if match_desc else ""
            
            match_format = "T20"
            if "ODI" in match_info:
                match_format = "ODI"
            elif "Test" in match_info:
                match_format = "TEST"
            
            # Extract venue (if available)
            venue_elem = card.find('div', class_='cb-mtch-crd-venue')
            venue = venue_elem.get_text(strip=True) if venue_elem else "Unknown"
            
            # Check status
            status_elem = card.find('div', class_='cb-text-live')
            status = "Live" if status_elem else "Upcoming"
            
            # Calculate run rate
            run_rate = runs / overs if overs > 0 else 0
            
            match_data = LiveMatchData(
                match_id=match_id,
                match_name=f"{team1} vs {team2}",
                team1=team1,
                team2=team2,
                batting_team=team1,  # Assume team1 is batting
                runs=runs,
                wickets=wickets,
                overs=overs,
                match_format=match_format,
                venue=venue,
                status=status,
                run_rate=run_rate
            )
            
            return match_data
        
        except Exception as e:
            logger.error(f"❌ Error parsing match card: {e}")
            return None
    
    def _parse_score(self, score_text: str) -> tuple:
        """
        Parse score text like "150/4 (15.3)" into runs, wickets, overs
        
        Returns:
            (runs, wickets, overs)
        """
        try:
            # Remove extra spaces
            score_text = score_text.strip()
            
            # Extract runs/wickets and overs
            # Format: "150/4 (15.3)" or "150-4 in 15.3 ov"
            
            if '/' in score_text or '-' in score_text:
                # Split by space or parenthesis
                parts = score_text.replace('(', ' ').replace(')', ' ').split()
                
                # Get runs/wickets
                score_part = parts[0]
                if '/' in score_part:
                    runs_str, wickets_str = score_part.split('/')
                elif '-' in score_part:
                    runs_str, wickets_str = score_part.split('-')
                else:
                    runs_str = score_part
                    wickets_str = "0"
                
                runs = int(runs_str)
                wickets = int(wickets_str)
                
                # Get overs
                overs = 0.0
                for part in parts[1:]:
                    if any(char.isdigit() for char in part):
                        # Clean and convert to float
                        overs_str = part.replace('ov', '').replace('overs', '').strip()
                        overs = float(overs_str)
                        break
                
                return runs, wickets, overs
            
            return 0, 0, 0.0
        
        except Exception as e:
            logger.error(f"❌ Error parsing score '{score_text}': {e}")
            return 0, 0, 0.0
    
    async def get_match_details(self, match_id: str) -> Optional[Dict]:
        """
        Get detailed match information including ball-by-ball
        
        Args:
            match_id: Cricbuzz match ID
        
        Returns:
            Detailed match data dictionary
        """
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)
        
        try:
            url = f"{self.BASE_URL}/live-cricket-scores/{match_id}"
            
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Extract detailed information
                details = {
                    'match_id': match_id,
                    'commentary': [],
                    'scorecard': {}
                }
                
                # Get commentary (last 6 balls for recent momentum)
                commentary_items = soup.find_all('div', class_='cb-col cb-col-90 cb-cmnt-itm')
                for item in commentary_items[:6]:  # Last 6 balls
                    text = item.get_text(strip=True)
                    details['commentary'].append(text)
                
                return details
        
        except Exception as e:
            logger.error(f"❌ Error fetching match details: {e}")
            return None


# Example usage and testing
if __name__ == "__main__":
    async def test_scraper():
        """Test the Cricbuzz scraper"""
        print("\n🏏 Testing Cricbuzz Scraper...")
        print("=" * 60)
        
        async with CricbuzzScraper() as scraper:
            # Get live matches
            matches = await scraper.get_live_matches()
            
            if matches:
                print(f"\n✅ Found {len(matches)} live matches:\n")
                
                for i, match in enumerate(matches, 1):
                    print(f"{i}. {match.match_name}")
                    print(f"   Score: {match.runs}/{match.wickets} ({match.overs} overs)")
                    print(f"   Format: {match.match_format}")
                    print(f"   Venue: {match.venue}")
                    print(f"   Phase: {match.match_phase}")
                    print(f"   Run Rate: {match.run_rate:.2f}")
                    print(f"   Momentum: {match.momentum_score:.0f}/100")
                    print()
            else:
                print("\n⚠️  No live matches currently")
                print("   (This is normal if no matches are being played)")
        
        print("=" * 60)
    
    # Run test
    asyncio.run(test_scraper())

