"""
TITAN Scraper Manager
Orchestrates multiple scraper workers and manages data pipeline
"""

import asyncio
import redis
import psycopg2
import json
import os
import sys
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
from loguru import logger
from scraper.worker import ScraperWorker
from scraper.parsers.micro999_optimized import Micro999OptimizedParser

# Data flow logging and validation
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.flow_logger import get_flow_logger
from utils.data_validators import RedisPublishValidator
from utils.data_fingerprint import create_fingerprint, track_data

load_dotenv()

# Initialize flow logger and validator for this component
flow_log = get_flow_logger("Manager")
redis_publish_validator = RedisPublishValidator()

class ScraperManager:
    """Manages multiple scraper workers and data pipeline"""
    
    def __init__(self, num_workers: int = 5):
        self.num_workers = num_workers
        self.workers: List[ScraperWorker] = []
        self.is_running = False
        
        # Redis connection
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=False
        )
        
        # Database connection config
        self.db_config = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'database': os.getenv('POSTGRES_DB', 'titan_betting'),
            'user': os.getenv('POSTGRES_USER', 'titan'),
            'password': os.getenv('POSTGRES_PASSWORD', 'titan_secure_2025')
        }
        
        # Parser
        self.parser = Micro999OptimizedParser()
        
        # Statistics
        self.total_events = 0
        self.total_errors = 0
        
        logger.info(f"ScraperManager initialized with {num_workers} workers")
    
    async def start(self, match_urls: List[str] = None):
        """Start all scraper workers"""
        self.is_running = True
        
        # Start database writer task
        db_writer_task = asyncio.create_task(self._database_writer())
        
        # Create and start workers
        worker_tasks = []
        for i in range(self.num_workers):
            worker = ScraperWorker(
                worker_id=i + 1,
                redis_client=self.redis_client,
                on_data_callback=self._on_data_received
            )
            self.workers.append(worker)
            
            # Assign match URL if provided
            match_url = match_urls[i] if match_urls and i < len(match_urls) else None
            worker_task = asyncio.create_task(worker.start(match_url))
            worker_tasks.append(worker_task)
        
        logger.info(f"Started {len(self.workers)} scraper workers")
        
        try:
            # Wait for all tasks
            await asyncio.gather(db_writer_task, *worker_tasks)
        except KeyboardInterrupt:
            logger.info("Received stop signal")
            await self.stop()
    
    async def _on_data_received(self, event_data: Dict):
        """Callback when data is received from worker"""
        try:
            # Parse the data
            parsed_data = self.parser.parse(event_data)
            
            if parsed_data:
                self.total_events += 1
                
                if self.total_events % 50 == 0:
                    logger.info(f"Total events processed: {self.total_events}")
        
        except Exception as e:
            logger.error(f"Error in data callback: {e}")
            self.total_errors += 1
    
    async def _database_writer(self):
        """Background task to write data from Redis queue to TimescaleDB"""
        logger.info("Database writer started")
        
        while self.is_running:
            try:
                # Get batch of events from Redis queue
                batch = []
                for _ in range(100):  # Process up to 100 events at a time
                    event_json = self.redis_client.rpop('timescale_queue')
                    if event_json:
                        batch.append(json.loads(event_json))
                    else:
                        break
                
                if batch:
                    await self._write_to_database(batch)
                else:
                    # No data, wait a bit
                    await asyncio.sleep(1)
            
            except Exception as e:
                logger.error(f"Error in database writer: {e}")
                await asyncio.sleep(5)
        
        logger.info("Database writer stopped")
    
    def _update_active_matches_list(self, parsed_list: List[Dict]):
        """Update active matches list in Redis for dashboard display"""
        try:
            # Extract unique matches from parsed data
            matches_dict = {}
            
            for parsed in parsed_list:
                match_id = parsed.get('match_id')
                if not match_id or match_id == 'unknown':
                    continue
                
                # Extract match name from match_id (e.g., "micro999_India_South_AfricaLive_Now")
                match_name = match_id.replace('micro999_', '').replace('_', ' ').replace('Live Now', '').strip()
                
                # Try to extract team names
                team_parts = match_name.split(' ')
                team1 = team_parts[0] if len(team_parts) > 0 else "Team 1"
                team2 = team_parts[-1] if len(team_parts) > 1 else "Team 2"
                
                # Aggregate match data
                if match_id not in matches_dict:
                    matches_dict[match_id] = {
                        'match_id': match_id,
                        'match_name': match_name,
                        'team1': team1,
                        'team2': team2,
                        'status': 'Live Now',
                        # Accept null values for cricket stats (Micro999 doesn't provide them)
                        'score': f"{parsed.get('score')}/{parsed.get('wickets')}" if parsed.get('score') is not None else None,
                        'overs': parsed.get('overs'),  # Can be None
                        'run_rate': parsed.get('run_rate'),  # Can be None
                        'current_odds': {},
                        'last_updated': datetime.utcnow().isoformat(),  # Always use current time
                        'venue': '',  # Not available in current data
                        'format': 'T20'  # Default assumption
                    }
                
                # Update odds for this team
                team = parsed.get('team', '')
                if team and parsed.get('back_price'):
                    matches_dict[match_id]['current_odds'][team] = parsed.get('back_price')
            
            # Convert to list and store in Redis
            if matches_dict:
                matches_list = list(matches_dict.values())
                self.redis_client.set(
                    'active_matches',
                    json.dumps(matches_list),
                    ex=600  # Expire after 10 minutes
                )
                
                # Stage 2 Flow Logging: Updated active_matches
                if not hasattr(self, '_matches_update_count'):
                    self._matches_update_count = 0
                
                self._matches_update_count += 1
                
                # Log every 10th update to avoid spam
                if self._matches_update_count % 10 == 1:
                    flow_log.stage2_active_matches(
                        count=len(matches_list),
                        matches=matches_list
                    )
        
        except Exception as e:
            logger.error(f"Error updating active matches list: {e}")
    
    async def _write_to_database(self, events: List[Dict]):
        """Write events to TimescaleDB"""
        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor()
            
            # Debug counter
            if not hasattr(self, '_db_write_count'):
                self._db_write_count = 0
            
            for event in events:
                try:
                    # Parse event data
                    self._db_write_count += 1
                    if self._db_write_count <= 3:
                        logger.info(f"🔍 Parsing event #{self._db_write_count}, keys: {list(event.keys())}")
                    
                    parsed_list = self.parser.parse(event)
                    
                    if self._db_write_count <= 3:
                        logger.info(f"📦 Parser returned: {type(parsed_list)}, length: {len(parsed_list) if parsed_list else 0}")
                    
                    if not parsed_list:
                        continue
                    
                    # Parser returns a list of match records
                    if not isinstance(parsed_list, list):
                        parsed_list = [parsed_list]
                    
                    # Stage 2 Flow Logging: Parsing completed
                    if len(parsed_list) > 0:
                        odds_summary = ", ".join([
                            f"{p.get('match_id', '?').split('_')[1][:10]}:{p.get('back_price', 'N/A')}" 
                            for p in parsed_list[:3]
                        ])
                        flow_log.stage2_parsed(
                            match_count=len(parsed_list),
                            match_ids=[p.get('match_id') for p in parsed_list],
                            odds_summary=odds_summary
                        )
                    
                    # Process each parsed match record
                    for parsed in parsed_list:
                        # Publish to Cortex processor
                        publish_result = self.redis_client.publish('match_events', json.dumps(parsed))
                        
                        # Stage 3 Data Validation: Verify Redis publish
                        validation_result = redis_publish_validator.validate(
                            parsed,
                            context={
                                'channel': 'match_events',
                                'publish_result': publish_result,
                                'original_data': parsed
                            }
                        )
                        
                        if not validation_result.is_valid():
                            logger.warning(f"[STAGE-3] Publish validation failed: {validation_result}")
                        else:
                            # Track successful publish with fingerprint
                            fingerprint = track_data(
                                stage=3,
                                component="redis_publish",
                                data=parsed,
                                validation_result=validation_result
                            )
                            logger.debug(f"[STAGE-3] Published to match_events [FP:{fingerprint}] ({publish_result} subscribers)")
                        
                        # Stage 2 Flow Logging: Published to match_events
                        flow_log.stage2_published(
                            channel='match_events',
                            match_id=parsed.get('match_id', 'unknown'),
                            back_price=parsed.get('back_price')
                        )
                    
                    # Update active matches list in Redis
                    self._update_active_matches_list(parsed_list)
                    
                    # Insert first record into database (they're all similar)
                    parsed = parsed_list[0]
                    
                    # Insert into market_ticks
                    cursor.execute("""
                        INSERT INTO market_ticks 
                        (time, match_id, market_type, team, odds, back_price, lay_price, 
                         volume, score, wickets, overs, run_rate, required_run_rate, 
                         is_suspended, stake_limit, meta_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (time, match_id, market_type) DO UPDATE SET
                            odds = EXCLUDED.odds,
                            back_price = EXCLUDED.back_price,
                            lay_price = EXCLUDED.lay_price,
                            volume = EXCLUDED.volume,
                            score = EXCLUDED.score,
                            wickets = EXCLUDED.wickets,
                            overs = EXCLUDED.overs,
                            run_rate = EXCLUDED.run_rate,
                            required_run_rate = EXCLUDED.required_run_rate,
                            is_suspended = EXCLUDED.is_suspended,
                            stake_limit = EXCLUDED.stake_limit,
                            meta_data = EXCLUDED.meta_data
                    """, (
                        parsed.get('timestamp'),
                        parsed.get('match_id', 'unknown'),
                        parsed.get('market_type', 'match_odds'),
                        parsed.get('team'),
                        parsed.get('odds'),
                        parsed.get('back_price'),
                        parsed.get('lay_price'),
                        parsed.get('volume'),
                        parsed.get('score'),
                        parsed.get('wickets'),
                        parsed.get('overs'),
                        parsed.get('run_rate'),
                        parsed.get('required_run_rate'),
                        parsed.get('is_suspended', False),
                        parsed.get('stake_limit'),
                        json.dumps(parsed.get('metadata', {}))
                    ))
                
                except Exception as e:
                    logger.error(f"Error inserting event: {e}")
                    continue
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug(f"Wrote {len(events)} events to database")
        
        except Exception as e:
            logger.error(f"Database write error: {e}")
    
    async def stop(self):
        """Stop all workers and cleanup"""
        self.is_running = False
        
        logger.info("Stopping all workers...")
        for worker in self.workers:
            await worker.stop()
        
        # Get final stats
        stats = self.get_stats()
        logger.info(f"Final stats: {stats}")
    
    def get_stats(self) -> Dict:
        """Get manager statistics"""
        worker_stats = [w.get_stats() for w in self.workers]
        
        return {
            'total_events': self.total_events,
            'total_errors': self.total_errors,
            'num_workers': len(self.workers),
            'workers': worker_stats
        }


async def main():
    """Main entry point"""
    logger.info("🚀 TITAN Scraper Manager Starting...")
    
    num_workers = int(os.getenv('SCRAPER_WORKERS', 5))
    manager = ScraperManager(num_workers=num_workers)
    
    try:
        await manager.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())

