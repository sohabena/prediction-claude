"""
Match Completion Monitor
Background task that checks for completed matches and calculates P&L
"""

import asyncio
from loguru import logger
from backend.db.connection import get_db_context
from backend.models.betting import VirtualBet, MatchResult, BetStatus
from backend.services.cricket_api import is_match_completed, get_match_status
from backend.services.pnl_calculator import close_multiple_bets
from datetime import datetime


class MatchMonitor:
    """
    Background service that monitors active matches
    Detects completion and automatically closes all related bets
    """
    
    def __init__(self, poll_interval: int = 30):
        """
        Args:
            poll_interval: Seconds between each check (default 30s)
        """
        self.poll_interval = poll_interval
        self.is_running = False
        logger.info(f"Match Monitor initialized (poll interval: {poll_interval}s)")
    
    async def start(self):
        """Start the monitoring loop"""
        self.is_running = True
        logger.info("🏏 Match Monitor started")
        
        while self.is_running:
            try:
                await self.check_active_matches()
            except Exception as e:
                logger.error(f"Error in match monitor loop: {e}")
            
            # Wait before next check
            await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Stop the monitoring loop"""
        self.is_running = False
        logger.info("Match Monitor stopped")
    
    async def check_active_matches(self):
        """
        Check all matches with pending bets for completion
        """
        try:
            with get_db_context() as db:
                # Get all unique match IDs with pending bets
                pending_match_ids = db.query(VirtualBet.match_id).filter(
                    VirtualBet.status == BetStatus.PENDING
                ).distinct().all()
                
                match_ids = [m[0] for m in pending_match_ids]
                
                if not match_ids:
                    logger.debug("No matches with pending bets")
                    return
                
                logger.debug(f"Monitoring {len(match_ids)} matches with pending bets")
                
                for match_id in match_ids:
                    await self.check_match_completion(match_id, db)
                    
        except Exception as e:
            logger.error(f"Error checking active matches: {e}")
    
    async def check_match_completion(self, match_id: str, db):
        """
        Check if a specific match is completed and process bets
        """
        try:
            # Check if we already processed this match
            existing_result = db.query(MatchResult).filter_by(match_id=match_id).first()
            
            if existing_result:
                logger.debug(f"Match {match_id} already processed")
                return
            
            # Check match status via Cricket API
            is_completed, winner = is_match_completed(match_id)
            
            if is_completed and winner:
                logger.info(f"🏆 Match {match_id} completed. Winner: {winner}")
                
                # Get full match data
                match_data = get_match_status(match_id)
                
                # Store match result
                match_result = MatchResult(
                    match_id=match_id,
                    team1=match_data.get('team1', ''),
                    team2=match_data.get('team2', ''),
                    match_format=match_data.get('match_type', ''),
                    winner=winner,
                    final_score_team1=match_data.get('score_team1', ''),
                    final_score_team2=match_data.get('score_team2', ''),
                    result_summary=f"{winner} won",
                    match_started_at=None,
                    completed_at=datetime.utcnow(),
                    venue=match_data.get('venue', ''),
                    api_response=str(match_data)
                )
                
                db.add(match_result)
                db.commit()
                
                # Close all bets for this match
                result = close_multiple_bets(match_id, winner, db)
                
                logger.info(f"✅ Match {match_id} processed: "
                          f"{result['bets_closed']} bets closed, "
                          f"P&L: ₹{result['total_profit_loss']:.2f}")
                
                # TODO: Send WebSocket notification to frontend
                # await self.notify_match_completed(match_id, result)
                
        except Exception as e:
            logger.error(f"Error processing match {match_id}: {e}")


# Global instance
match_monitor = MatchMonitor(poll_interval=30)


async def start_match_monitor():
    """Start the match monitor as a background task"""
    await match_monitor.start()


def stop_match_monitor():
    """Stop the match monitor"""
    match_monitor.stop()

