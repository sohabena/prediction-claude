"""
Cricket Stats Enricher
Enriches Micro999 odds data with cricket statistics from Cricbuzz

This service runs alongside the main scraper to provide:
- Score updates
- Wicket events (is_wicket flag for PanicRebound strategy)
- Ball-by-ball data
- Match phase information

The Ghost (Data Engineer): "We merge betting odds with cricket stats 
to give our strategies the full context they need."

Phase 1.1 Implementation:
- Added detailed logging for wicket detection
- Added enrichment success rate tracking
- Added test mode for simulating wicket events
"""

import asyncio
import redis
import json
import os
import random
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv
from loguru import logger

from scraper.parsers.cricbuzz_scraper import CricbuzzScraper, LiveMatchData

load_dotenv()


class CricketStatsEnricher:
    """
    Enriches match data with cricket statistics from Cricbuzz
    
    Flow:
    1. Periodically fetch live matches from Cricbuzz
    2. Match them with active matches from Micro999
    3. Publish enriched events to Redis for Cortex
    
    Key outputs for strategies:
    - is_wicket: True when wicket detected (PanicRebound needs this)
    - score, wickets, overs: Match state (PanicRebound, MeanReversion need these)
    - run_rate: Calculated metric
    - is_boundary: True when 4+ runs scored
    """
    
    def __init__(self, test_mode: bool = False):
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        self.scraper = CricbuzzScraper()
        self.is_running = False
        self.test_mode = test_mode
        
        # Track previous state for event detection
        self.previous_states: Dict[str, Dict] = {}
        
        # Polling interval (seconds)
        self.poll_interval = int(os.getenv('CRICBUZZ_POLL_INTERVAL', 10))
        
        # Statistics tracking
        self.stats = {
            'cycles_completed': 0,
            'events_published': 0,
            'wickets_detected': 0,
            'boundaries_detected': 0,
            'matches_enriched': 0,
            'matches_basic': 0,
            'cricbuzz_errors': 0,
            'micro999_matches_found': 0,
            'started_at': None
        }
        
        logger.info(f"Cricket Stats Enricher initialized (test_mode={test_mode})")
    
    async def start(self):
        """Start the enricher service"""
        self.is_running = True
        self.stats['started_at'] = datetime.utcnow().isoformat()
        logger.info("🏏 Cricket Stats Enricher starting...")
        logger.info(f"   Poll interval: {self.poll_interval}s")
        logger.info(f"   Test mode: {self.test_mode}")
        
        async with self.scraper:
            while self.is_running:
                try:
                    await self._enrich_cycle()
                    self.stats['cycles_completed'] += 1
                    
                    # Log stats every 10 cycles
                    if self.stats['cycles_completed'] % 10 == 0:
                        self._log_stats()
                        
                except Exception as e:
                    logger.error(f"Error in enrichment cycle: {e}")
                    self.stats['cricbuzz_errors'] += 1
                
                await asyncio.sleep(self.poll_interval)
        
        logger.info("Cricket Stats Enricher stopped")
        self._log_stats()
    
    def _log_stats(self):
        """Log current statistics"""
        logger.info(
            f"📊 Enricher Stats: "
            f"cycles={self.stats['cycles_completed']}, "
            f"events={self.stats['events_published']}, "
            f"wickets={self.stats['wickets_detected']}, "
            f"boundaries={self.stats['boundaries_detected']}, "
            f"enriched={self.stats['matches_enriched']}, "
            f"errors={self.stats['cricbuzz_errors']}"
        )
    
    async def _enrich_cycle(self):
        """Run one cycle of enrichment"""
        # Test mode: generate simulated events
        if self.test_mode:
            await self._generate_test_events()
            return
        
        # Fetch live matches from Cricbuzz
        cricbuzz_matches = await self.scraper.get_live_matches()
        
        if not cricbuzz_matches:
            logger.debug("No live matches from Cricbuzz")
            return
        
        logger.info(f"🏏 Found {len(cricbuzz_matches)} live matches from Cricbuzz")
        
        # Get active matches from Micro999 (stored in Redis by scraper)
        micro999_matches = self._get_micro999_matches()
        self.stats['micro999_matches_found'] = len(micro999_matches)
        
        if micro999_matches:
            logger.info(f"📊 Found {len(micro999_matches)} active matches from Micro999")
        
        # Match and enrich
        for cb_match in cricbuzz_matches:
            # Try to find matching Micro999 match
            matched_match = self._find_matching_match(cb_match, micro999_matches)
            
            if matched_match:
                # Enrich and publish
                enriched_event = self._create_enriched_event(cb_match, matched_match)
                await self._publish_enriched_event(enriched_event)
                self.stats['matches_enriched'] += 1
            else:
                # Even without odds, publish cricket stats for context
                basic_event = self._create_basic_event(cb_match)
                await self._publish_enriched_event(basic_event)
                self.stats['matches_basic'] += 1
    
    async def _generate_test_events(self):
        """Generate test events for development/testing"""
        test_match_id = "test_match_001"
        
        # Get current state or initialize
        prev = self.previous_states.get(test_match_id, {
            'runs': 100,
            'wickets': 2,
            'overs': 12.0
        })
        
        # Simulate match progress
        runs_scored = random.choice([0, 1, 1, 2, 2, 4, 6, 0, 0, 1])
        wicket_fell = random.random() < 0.05  # 5% chance of wicket
        
        current_runs = prev['runs'] + runs_scored
        current_wickets = prev['wickets'] + (1 if wicket_fell else 0)
        current_overs = prev['overs'] + 0.1
        if int(current_overs * 10) % 10 == 6:
            current_overs = int(current_overs) + 1.0
        
        # Detect events
        is_wicket = wicket_fell
        is_boundary = runs_scored >= 4
        
        # Create test event
        test_event = {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': test_match_id,
            'source': 'test_enricher',
            'score': current_runs,
            'wickets': current_wickets,
            'overs': round(current_overs, 1),
            'run_rate': round(current_runs / max(current_overs, 1), 2),
            'batting_team': 'Test Team A',
            'match_format': 'T20',
            'match_phase': 'middle' if current_overs >= 6 else 'powerplay',
            'momentum': 50 + (runs_scored * 5) - (10 if wicket_fell else 0),
            'odds': None,
            'back_price': 2.0 + (random.random() - 0.5) * 0.2,
            'lay_price': None,
            'is_wicket': is_wicket,
            'runs_scored': runs_scored,
            'is_boundary': is_boundary,
            'team1': 'Test Team A',
            'team2': 'Test Team B',
            'venue': 'Test Stadium',
            'enriched': True,
            'test_mode': True
        }
        
        # Update state
        self.previous_states[test_match_id] = {
            'runs': current_runs,
            'wickets': current_wickets,
            'overs': current_overs
        }
        
        # Log events
        if is_wicket:
            logger.warning(f"🎯 TEST WICKET: {current_wickets} wickets down!")
            self.stats['wickets_detected'] += 1
        if is_boundary:
            logger.info(f"🏏 TEST BOUNDARY: {runs_scored} runs!")
            self.stats['boundaries_detected'] += 1
        
        await self._publish_enriched_event(test_event)
    
    def _get_micro999_matches(self) -> List[Dict]:
        """Get active matches from Redis (populated by Micro999 scraper)"""
        try:
            matches_json = self.redis_client.get('active_matches')
            if matches_json:
                return json.loads(matches_json)
            return []
        except Exception as e:
            logger.error(f"Error fetching Micro999 matches: {e}")
            return []
    
    def _find_matching_match(self, cb_match: LiveMatchData, micro999_matches: List[Dict]) -> Optional[Dict]:
        """
        Find Micro999 match that corresponds to Cricbuzz match
        
        Uses fuzzy team name matching
        """
        cb_teams = {cb_match.team1.lower(), cb_match.team2.lower()}
        
        for m999_match in micro999_matches:
            # Extract team names from Micro999 match
            m999_team1 = m999_match.get('team1', '').lower()
            m999_team2 = m999_match.get('team2', '').lower()
            match_name = m999_match.get('match_name', '').lower()
            
            # Check for team name overlap
            for cb_team in cb_teams:
                cb_team_parts = cb_team.split()
                
                # Check if any part of team name matches
                for part in cb_team_parts:
                    if len(part) > 2:  # Skip short words
                        if part in m999_team1 or part in m999_team2 or part in match_name:
                            return m999_match
        
        return None
    
    def _create_enriched_event(self, cb_match: LiveMatchData, m999_match: Dict) -> Dict:
        """Create enriched event combining Cricbuzz stats with Micro999 odds"""
        match_id = m999_match.get('match_id', f"cricbuzz_{cb_match.match_id}")
        
        # Detect events by comparing with previous state
        events = self._detect_events(match_id, cb_match)
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': match_id,
            'source': 'enriched',
            
            # Cricket stats from Cricbuzz
            'score': cb_match.runs,
            'wickets': cb_match.wickets,
            'overs': cb_match.overs,
            'run_rate': round(cb_match.run_rate, 2),
            'batting_team': cb_match.batting_team,
            'match_format': cb_match.match_format,
            'match_phase': cb_match.match_phase,
            'momentum': cb_match.momentum_score,
            
            # Odds from Micro999
            'odds': m999_match.get('current_odds', {}),
            'back_price': self._extract_back_price(m999_match),
            'lay_price': None,  # Could be extracted if available
            
            # Event flags
            'is_wicket': events.get('is_wicket', False),
            'runs_scored': events.get('runs_scored', 0),
            'is_boundary': events.get('is_boundary', False),
            
            # Match details
            'team1': cb_match.team1,
            'team2': cb_match.team2,
            'venue': cb_match.venue,
            
            # Metadata
            'enriched': True,
            'cricbuzz_match_id': cb_match.match_id,
            'micro999_match_id': m999_match.get('match_id')
        }
    
    def _create_basic_event(self, cb_match: LiveMatchData) -> Dict:
        """Create basic event from Cricbuzz data only (no odds)"""
        match_id = f"cricbuzz_{cb_match.match_id}"
        
        events = self._detect_events(match_id, cb_match)
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'match_id': match_id,
            'source': 'cricbuzz',
            
            # Cricket stats
            'score': cb_match.runs,
            'wickets': cb_match.wickets,
            'overs': cb_match.overs,
            'run_rate': round(cb_match.run_rate, 2),
            'batting_team': cb_match.batting_team,
            'match_format': cb_match.match_format,
            'match_phase': cb_match.match_phase,
            'momentum': cb_match.momentum_score,
            
            # No odds available
            'odds': None,
            'back_price': None,
            'lay_price': None,
            
            # Event flags
            'is_wicket': events.get('is_wicket', False),
            'runs_scored': events.get('runs_scored', 0),
            'is_boundary': events.get('is_boundary', False),
            
            # Match details
            'team1': cb_match.team1,
            'team2': cb_match.team2,
            'venue': cb_match.venue,
            
            'enriched': False,
            'cricbuzz_match_id': cb_match.match_id
        }
    
    def _detect_events(self, match_id: str, current: LiveMatchData) -> Dict:
        """
        Detect events by comparing current state with previous state
        
        Returns dict with:
        - is_wicket: True if wicket fell since last update (PanicRebound needs this!)
        - runs_scored: Runs scored since last update
        - is_boundary: True if boundary scored
        
        CRITICAL: is_wicket=True triggers PanicRebound strategy
        """
        events = {
            'is_wicket': False,
            'runs_scored': 0,
            'is_boundary': False
        }
        
        prev = self.previous_states.get(match_id)
        
        if prev:
            prev_wickets = prev.get('wickets', 0)
            prev_runs = prev.get('runs', 0)
            
            # Check for wicket - CRITICAL for PanicRebound
            if current.wickets > prev_wickets:
                events['is_wicket'] = True
                self.stats['wickets_detected'] += 1
                logger.warning(
                    f"🎯 WICKET DETECTED in {match_id}! "
                    f"Wickets: {prev_wickets} -> {current.wickets} "
                    f"(Score: {current.runs}/{current.wickets} @ {current.overs} overs)"
                )
            
            # Calculate runs scored
            runs_diff = current.runs - prev_runs
            if runs_diff > 0:
                events['runs_scored'] = runs_diff
                
                # Check for boundary (4 or 6 runs in one update)
                if runs_diff >= 4:
                    events['is_boundary'] = True
                    self.stats['boundaries_detected'] += 1
                    logger.info(f"🏏 BOUNDARY detected in {match_id}: {runs_diff} runs")
        else:
            logger.debug(f"First state for {match_id}: {current.runs}/{current.wickets}")
        
        # Update previous state
        self.previous_states[match_id] = {
            'runs': current.runs,
            'wickets': current.wickets,
            'overs': current.overs
        }
        
        return events
    
    def _extract_back_price(self, m999_match: Dict) -> Optional[float]:
        """Extract back price from Micro999 match data"""
        odds = m999_match.get('current_odds', {})
        
        if isinstance(odds, dict):
            # Get first available odds
            for team, price in odds.items():
                if price:
                    return float(price)
        
        return None
    
    async def _publish_enriched_event(self, event: Dict):
        """Publish enriched event to Redis for Cortex"""
        try:
            # Publish to match_events channel (same as Micro999 scraper)
            self.redis_client.publish('match_events', json.dumps(event))
            self.stats['events_published'] += 1
            
            # Also queue for database storage
            self.redis_client.lpush('timescale_queue', json.dumps({
                'timestamp': event['timestamp'],
                'source': 'cricbuzz_enricher',
                'data': event
            }))
            
            # Log important events
            if event.get('is_wicket'):
                logger.warning(
                    f"📡 WICKET EVENT PUBLISHED to match_events channel! "
                    f"match_id={event.get('match_id')}, "
                    f"score={event.get('score')}/{event.get('wickets')}"
                )
            elif event.get('is_boundary'):
                logger.info(f"📡 Published BOUNDARY event for {event.get('match_id')}")
            else:
                logger.debug(f"📡 Published event for {event.get('match_id')}")
            
        except Exception as e:
            logger.error(f"Error publishing enriched event: {e}")
            self.stats['cricbuzz_errors'] += 1
    
    def stop(self):
        """Stop the enricher"""
        self.is_running = False
    
    def get_stats(self) -> Dict:
        """Get enricher statistics"""
        return {
            **self.stats,
            'is_running': self.is_running,
            'poll_interval': self.poll_interval,
            'test_mode': self.test_mode,
            'tracked_matches': len(self.previous_states)
        }


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Cricket Stats Enricher')
    parser.add_argument('--test', action='store_true', help='Run in test mode with simulated events')
    args = parser.parse_args()
    
    test_mode = args.test or os.getenv('ENRICHER_TEST_MODE', 'false').lower() == 'true'
    
    logger.info("🏏 Starting Cricket Stats Enricher...")
    if test_mode:
        logger.warning("⚠️  TEST MODE ENABLED - Generating simulated events")
    
    enricher = CricketStatsEnricher(test_mode=test_mode)
    
    try:
        await enricher.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        enricher.stop()


async def test_wicket_detection():
    """Test wicket detection logic"""
    logger.info("🧪 Testing wicket detection...")
    
    enricher = CricketStatsEnricher(test_mode=True)
    
    # Simulate 20 events (should get ~1 wicket with 5% probability)
    for i in range(20):
        await enricher._generate_test_events()
        await asyncio.sleep(0.5)
    
    stats = enricher.get_stats()
    logger.info(f"Test completed: {stats}")
    
    if stats['wickets_detected'] > 0:
        logger.success(f"✅ Wicket detection working! Detected {stats['wickets_detected']} wickets")
    else:
        logger.warning("⚠️  No wickets detected in test (may need more iterations)")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--test-wicket':
        asyncio.run(test_wicket_detection())
    else:
        asyncio.run(main())
