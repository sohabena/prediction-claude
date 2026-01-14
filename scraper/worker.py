"""
TITAN Scraper Worker
Individual scraper instance using Playwright with CDP WebSocket interception

Phase 1.2 Enhancement:
- Added stake limit tracking for Whale Shadow strategy
- Added suspension event detection
- Tracks previous state to detect changes
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Optional, Callable
from playwright.async_api import async_playwright, Page, Browser
import redis
import os
from dotenv import load_dotenv
from loguru import logger

# Data flow logging and validation
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.flow_logger import get_flow_logger
from utils.data_validators import ScraperDataValidator
from utils.data_fingerprint import create_fingerprint, track_data, ValidationStatus

load_dotenv()

# Initialize flow logger for this component
flow_log = get_flow_logger("Worker")

# Initialize data validator for Stage 1
scraper_validator = ScraperDataValidator()

class ScraperWorker:
    """Individual scraper worker for Dafabet match pages"""
    
    def __init__(
        self,
        worker_id: int,
        redis_client: redis.Redis,
        on_data_callback: Optional[Callable] = None
    ):
        self.worker_id = worker_id
        self.redis_client = redis_client
        self.on_data_callback = on_data_callback
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.is_running = False
        self.data_count = 0
        self.error_count = 0
        
        # Configuration
        self.base_url = os.getenv('BETTING_SITE_URL', 'https://www.micro999.co/game/4')
        self.headless = os.getenv('SCRAPER_HEADLESS', 'true').lower() == 'true'
        self.timeout = int(os.getenv('SCRAPER_TIMEOUT', 30000))
        
        # Market metadata tracking (Phase 1.2: Whale Shadow support)
        self.market_states: Dict[str, Dict] = {}  # match_id -> {stake_limit, is_suspended}
        
        # Statistics for metadata tracking
        self.metadata_stats = {
            'stake_limit_changes': 0,
            'suspension_events': 0,
            'suspensions_detected': 0,
            'metadata_events_published': 0
        }
        
        logger.info(f"Worker {worker_id} initialized")
        logger.info(f"  📊 Metadata tracking ENABLED for Whale Shadow strategy")
    
    async def start(self, match_url: Optional[str] = None):
        """Start the scraper worker"""
        self.is_running = True
        url = match_url or self.base_url
        
        try:
            async with async_playwright() as p:
                # Launch browser with stealth settings
                self.browser = await p.chromium.launch(
                    headless=self.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-web-security',
                        '--disable-features=IsolateOrigins,site-per-process'
                    ]
                )
                
                # Create context with realistic settings
                context = await self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='en-IN',
                    timezone_id='Asia/Kolkata'
                )
                
                self.page = await context.new_page()
                
                # Set up CDP session for WebSocket interception
                cdp = await context.new_cdp_session(self.page)
                await cdp.send('Network.enable')
                
                # Listen for WebSocket frames
                cdp.on('Network.webSocketFrameReceived', self._handle_websocket_frame)
                cdp.on('Network.webSocketFrameSent', self._handle_websocket_frame)
                
                logger.info(f"Worker {self.worker_id}: Navigating to {url}")
                
                # Navigate to page
                await self.page.goto(url, wait_until='networkidle', timeout=self.timeout)
                
                logger.info(f"Worker {self.worker_id}: Page loaded, monitoring for data...")
                
                # Keep scraping while running
                while self.is_running:
                    try:
                        # Extract visible odds data from page
                        await self._extract_page_data()
                        
                        # Wait before next extraction
                        await asyncio.sleep(3)
                        
                    except Exception as e:
                        logger.error(f"Worker {self.worker_id}: Error in scraping loop: {e}")
                        self.error_count += 1
                        await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"Worker {self.worker_id}: Fatal error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await self.stop()
    
    async def _handle_websocket_frame(self, params: Dict):
        """Handle WebSocket frame from CDP"""
        try:
            if 'response' in params:
                payload = params['response'].get('payloadData', '')
                if payload:
                    # Try to parse as JSON
                    try:
                        data = json.loads(payload)
                        await self._process_websocket_data(data)
                    except json.JSONDecodeError:
                        pass  # Not JSON, ignore
        except Exception as e:
            logger.error(f"Worker {self.worker_id}: Error handling WebSocket frame: {e}")
    
    async def _process_websocket_data(self, data: Dict):
        """Process data from WebSocket"""
        try:
            # Extract relevant betting data
            if isinstance(data, dict):
                # Look for odds updates
                if 'odds' in data or 'markets' in data or 'price' in data:
                    event_data = {
                        'timestamp': datetime.utcnow().isoformat(),
                        'worker_id': self.worker_id,
                        'source': 'websocket',
                        'data': data
                    }
                    
                    await self._publish_data(event_data)
                    self.data_count += 1
                    
        except Exception as e:
            logger.error(f"Worker {self.worker_id}: Error processing WebSocket data: {e}")
    
    async def _extract_page_data(self):
        """Extract odds data directly from page DOM"""
        try:
            if not self.page:
                return
            
            # Execute JavaScript to extract odds data for LIVE matches only
            # Phase 1.2: Also extract stake limit and suspension indicators
            # FIX: Properly associate odds with their matches
            page_data = await self.page.evaluate("""
                () => {
                    const data = {
                        matches: [],
                        timestamp: new Date().toISOString(),
                        text: '',
                        liveMatches: [],
                        totalMatches: 0,
                        marketMetadata: [],  // Phase 1.2: Stake limits and suspensions
                        structuredMatches: [] // NEW: Properly structured match data with odds
                    };
                    
                    // Parse page text to extract matches with their associated odds
                    const allText = document.body.innerText;
                    const lines = allText.split('\\n');
                    
                    for (let i = 0; i < lines.length; i++) {
                        const line = lines[i].trim();
                        
                        // Look for match lines: "DD Mon HH:MM |Team1 v Team2Live Now" or similar
                        if (line.includes(' v ') && line.includes('|')) {
                            data.totalMatches++;
                            
                            // Check if LIVE - must have "Live Now" in the SAME line
                            const isLive = line.includes('Live Now');
                            
                            if (isLive) {
                                // Extract match name (after | and before Live Now)
                                const pipeIndex = line.indexOf('|');
                                let matchPart = line.substring(pipeIndex + 1);
                                matchPart = matchPart.replace('Live Now', '').trim();
                                
                                // Parse team names from "Team1 v Team2"
                                const vMatch = matchPart.match(/(.+?)\\s+v\\s+(.+)/i);
                                if (vMatch) {
                                    const team1 = vMatch[1].trim();
                                    const team2 = vMatch[2].trim();
                                    
                                    // Look for odds in the next few lines (back and lay prices)
                                    let backOdds = null;
                                    let layOdds = null;
                                    
                                    // Search next 5 lines for decimal odds
                                    for (let j = 1; j <= 5 && i + j < lines.length; j++) {
                                        const nextLine = lines[i + j].trim();
                                        // Stop if we hit another match
                                        if (nextLine.includes(' v ') && nextLine.includes('|')) break;
                                        
                                        // Look for decimal odds (e.g., "3.9", "4.2", "1.08")
                                        const oddsMatch = nextLine.match(/^(\\d{1,2}\\.\\d{1,2})$/);
                                        if (oddsMatch) {
                                            const odds = parseFloat(oddsMatch[1]);
                                            if (odds >= 1.01 && odds <= 100) {
                                                if (!backOdds) {
                                                    backOdds = odds;
                                                } else if (!layOdds) {
                                                    layOdds = odds;
                                                    break; // Got both, stop searching
                                                }
                                            }
                                        }
                                    }
                                    
                                    // Only add if we found valid odds
                                    if (backOdds) {
                                        data.structuredMatches.push({
                                            team1: team1,
                                            team2: team2,
                                            matchName: matchPart,
                                            backOdds: backOdds,
                                            layOdds: layOdds || backOdds * 1.02, // Estimate lay if not found
                                            isLive: true,
                                            lineIndex: i
                                        });
                                        data.liveMatches.push(matchPart);
                                        
                                        // Add to matches array for compatibility
                                        data.matches.push({
                                            odds: backOdds.toString(),
                                            element: 'structured-back',
                                            tag: 'STRUCTURED'
                                        });
                                        if (layOdds) {
                                            data.matches.push({
                                                odds: layOdds.toString(),
                                                element: 'structured-lay',
                                                tag: 'STRUCTURED'
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                    
                    // Get page text for match names
                    data.text = document.body.innerText;
                    data.title = document.title;
                    data.url = window.location.href;
                    
                    return data;
                }
            """)
            
            if page_data and page_data.get('matches'):
                # Stage 1 Flow Logging: DOM extraction completed
                odds_count = len(page_data.get('matches', []))
                live_count = len(page_data.get('liveMatches', []))
                structured_count = len(page_data.get('structuredMatches', []))
                
                flow_log.stage1_extracted(
                    odds_count=odds_count,
                    live_count=live_count,
                    matches=page_data.get('liveMatches', [])[:3]
                )
                
                # Log structured matches if available
                if structured_count > 0:
                    for sm in page_data.get('structuredMatches', [])[:2]:
                        logger.debug(f"  [STAGE-1] Structured: {sm.get('team1')} v {sm.get('team2')} @ {sm.get('backOdds')}")
                
                # Stage 1 Data Validation: Verify extraction integrity
                validation_result = scraper_validator.validate(
                    page_data, 
                    context={'raw_text': page_data.get('text', '')}
                )
                
                if not validation_result.is_valid():
                    logger.warning(f"[STAGE-1] Validation: {validation_result}")
                    if validation_result.details.get('issues'):
                        for issue in validation_result.details['issues'][:3]:
                            logger.warning(f"  - {issue}")
                else:
                    logger.debug(f"[STAGE-1] Validation: {validation_result}")
                
                # Track data with fingerprint for lineage
                if structured_count > 0:
                    first_match = page_data['structuredMatches'][0]
                    fingerprint = track_data(
                        stage=1,
                        component="scraper",
                        data={
                            'match_id': f"micro999_{first_match.get('team1', '')}_{first_match.get('team2', '')}".replace(' ', '_'),
                            'back_price': first_match.get('backOdds'),
                            'timestamp': datetime.utcnow().isoformat()
                        },
                        validation_result=validation_result
                    )
                    logger.debug(f"[STAGE-1] Fingerprint: {fingerprint}")
                
                # Phase 1.2: Track market metadata changes
                metadata_events = self._track_market_metadata(page_data.get('marketMetadata', []))
                
                event_data = {
                    'timestamp': datetime.utcnow().isoformat(),
                    'worker_id': self.worker_id,
                    'source': 'dom',
                    'data': page_data,
                    'metadata_events': metadata_events  # Phase 1.2
                }
                
                await self._publish_data(event_data)
                self.data_count += 1
                
        except Exception as e:
            logger.error(f"Worker {self.worker_id}: Error extracting page data: {e}")
    
    def _track_market_metadata(self, metadata_list: list) -> list:
        """
        Phase 1.2: Track stake limit and suspension changes
        
        Returns list of metadata events (changes from previous state)
        """
        events = []
        
        for item in metadata_list:
            match_text = item.get('matchText', '')
            if not match_text:
                continue
            
            # Generate a simple match ID from match text
            match_id = match_text.replace(' ', '_').replace('v', 'vs')
            
            current_state = {
                'stake_limit': item.get('stakeLimit'),
                'is_suspended': item.get('isSuspended', False)
            }
            
            # Get previous state
            prev_state = self.market_states.get(match_id, {
                'stake_limit': None,
                'is_suspended': False
            })
            
            # Detect changes
            stake_limit_changed = False
            is_suspension_event = False
            stake_limit_drop_pct = 0.0
            
            # Stake limit change detection
            if current_state['stake_limit'] is not None and prev_state['stake_limit'] is not None:
                if current_state['stake_limit'] != prev_state['stake_limit']:
                    stake_limit_changed = True
                    self.metadata_stats['stake_limit_changes'] += 1
                    
                    # Calculate drop percentage
                    if prev_state['stake_limit'] > 0:
                        stake_limit_drop_pct = (prev_state['stake_limit'] - current_state['stake_limit']) / prev_state['stake_limit']
                    
                    logger.warning(
                        f"⚠️  STAKE LIMIT CHANGE detected for {match_text}: "
                        f"{prev_state['stake_limit']} -> {current_state['stake_limit']} "
                        f"({stake_limit_drop_pct:.1%} change)"
                    )
            
            # Suspension event detection
            if current_state['is_suspended'] != prev_state['is_suspended']:
                is_suspension_event = True
                self.metadata_stats['suspension_events'] += 1
                
                if current_state['is_suspended']:
                    self.metadata_stats['suspensions_detected'] += 1
                    logger.warning(f"🚫 MARKET SUSPENDED for {match_text}")
                else:
                    logger.info(f"✅ Market RESUMED for {match_text}")
            
            # Update stored state
            self.market_states[match_id] = current_state
            
            # Create event if there were changes
            if stake_limit_changed or is_suspension_event:
                event = {
                    'match_id': match_id,
                    'match_text': match_text,
                    'stake_limit': current_state['stake_limit'],
                    'previous_stake_limit': prev_state['stake_limit'],
                    'stake_limit_changed': stake_limit_changed,
                    'stake_limit_drop_pct': stake_limit_drop_pct,
                    'is_suspended': current_state['is_suspended'],
                    'is_suspension_event': is_suspension_event,
                    'is_limit_change': stake_limit_changed,
                    'timestamp': datetime.utcnow().isoformat()
                }
                events.append(event)
                self.metadata_stats['metadata_events_published'] += 1
                
                # Log clearly for Whale Shadow validation
                logger.warning(
                    f"🐋 WHALE SHADOW METADATA EVENT CREATED:\n"
                    f"   match_id: {match_id}\n"
                    f"   is_suspension_event: {is_suspension_event}\n"
                    f"   is_limit_change: {stake_limit_changed}\n"
                    f"   stake_limit_drop_pct: {stake_limit_drop_pct:.1%}\n"
                    f"   Total metadata events: {self.metadata_stats['metadata_events_published']}"
                )
        
        return events
    
    async def _publish_data(self, event_data: Dict):
        """Publish data to Redis"""
        try:
            # Publish to hot path (real-time)
            # Note: Publishing to match_events is now handled by manager after parsing
            # self.redis_client.publish('match_events', json.dumps(event_data))
            
            # Queue for cold path (TimescaleDB)
            self.redis_client.lpush('timescale_queue', json.dumps(event_data))
            
            # Call callback if provided
            if self.on_data_callback:
                await self.on_data_callback(event_data)
            
            if self.data_count % 10 == 0:
                logger.info(f"Worker {self.worker_id}: Published {self.data_count} events")
                
        except Exception as e:
            logger.error(f"Worker {self.worker_id}: Error publishing data: {e}")
            self.error_count += 1
    
    async def stop(self):
        """Stop the scraper worker"""
        self.is_running = False
        
        if self.page:
            await self.page.close()
        
        if self.browser:
            await self.browser.close()
        
        logger.info(f"Worker {self.worker_id}: Stopped. Stats - Data: {self.data_count}, Errors: {self.error_count}")
    
    def get_stats(self) -> Dict:
        """Get worker statistics"""
        return {
            'worker_id': self.worker_id,
            'is_running': self.is_running,
            'data_count': self.data_count,
            'error_count': self.error_count,
            'metadata_stats': self.metadata_stats,  # Phase 1.2
            'tracked_markets': len(self.market_states)  # Phase 1.2
        }


async def main():
    """Test the scraper worker"""
    from dotenv import load_dotenv
    load_dotenv()
    
    # Connect to Redis
    redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        decode_responses=False
    )
    
    # Create worker
    worker = ScraperWorker(worker_id=1, redis_client=redis_client)
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())

