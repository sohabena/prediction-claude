"""
Integrated Auto-Bet Engine
Fully integrated with Risk Manager for safe automated betting

The House (Bookmaker): "Automation is powerful but dangerous. Every auto-bet
must pass through risk validation—no exceptions."

The Architect (Full-Stack): "This engine subscribes to Redis signals, validates
through risk manager, and executes bets automatically. Set it and forget it."
"""

import asyncio
import redis
import json
from typing import Optional, Dict
from datetime import datetime
from loguru import logger
from dataclasses import dataclass

from backend.db.connection import get_db_context
from backend.services.risk_manager import get_risk_manager, RiskManager
from backend.routers.bets import create_bet_internal
from backend.utils.kelly_criterion import calculate_kelly_stake


@dataclass
class AutoBetConfig:
    """Configuration for auto-bet engine"""
    enabled: bool = False
    min_confidence: float = 0.75  # Only bet on signals >= 75% confidence
    min_edge: float = 0.05  # Require 5% edge minimum
    kelly_multiplier: float = 0.25  # Quarter Kelly (conservative)
    max_bets_per_hour: int = 10
    strategies_enabled: list = None  # None = all strategies
    
    def __post_init__(self):
        if self.strategies_enabled is None:
            self.strategies_enabled = ["panic_rebound", "mean_reversion", "whale_shadow"]


class AutoBetEngine:
    """
    Automated betting engine with risk integration
    
    The Guardian (UI Tester): "Automated systems need extra validation.
    Test every edge case before enabling auto-bet in production."
    
    Safety Features:
    1. Risk manager validation (8-point check)
    2. Minimum confidence threshold
    3. Minimum edge requirement
    4. Kelly Criterion sizing
    5. Frequency limits
    6. Emergency stop flag
    7. Audit logging
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        risk_manager: RiskManager,
        config: AutoBetConfig = None
    ):
        self.redis = redis_client
        self.risk_manager = risk_manager
        self.config = config or AutoBetConfig()
        
        self.running = False
        self.bets_placed_last_hour = 0
        self.total_bets_placed = 0
        self.total_bets_rejected = 0
        
        # Emergency stop flag (set in Redis to stop immediately)
        self.EMERGENCY_STOP_KEY = "autobet:emergency_stop"
        
        logger.info(f"🤖 Auto-Bet Engine initialized (enabled: {self.config.enabled})")
    
    async def start(self):
        """
        Start the auto-bet engine
        
        The Sentinel (DevOps): "This must be stoppable instantly. Check
        emergency stop flag every loop iteration."
        """
        if not self.config.enabled:
            logger.warning("⚠️  Auto-Bet Engine is DISABLED. Enable in config to start.")
            return
        
        self.running = True
        logger.info("🤖 Auto-Bet Engine STARTED - Monitoring signals...")
        
        # Subscribe to signals channel
        pubsub = self.redis.pubsub()
        pubsub.subscribe('signals')
        
        try:
            while self.running:
                # Check emergency stop
                if self._check_emergency_stop():
                    logger.error("🚨 EMERGENCY STOP ACTIVATED - Auto-betting halted!")
                    break
                
                # Check for new signals
                message = pubsub.get_message()
                
                if message and message['type'] == 'message':
                    try:
                        signal_data = json.loads(message['data'])
                        await self._process_signal(signal_data)
                    except Exception as e:
                        logger.error(f"❌ Error processing signal: {e}")
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.1)
        
        except Exception as e:
            logger.error(f"❌ Auto-Bet Engine error: {e}")
        
        finally:
            pubsub.unsubscribe('signals')
            self.running = False
            logger.info("🛑 Auto-Bet Engine stopped")
    
    def stop(self):
        """Stop the auto-bet engine"""
        self.running = False
        logger.warning("🛑 Auto-Bet Engine stopping...")
    
    def emergency_stop(self):
        """
        Emergency stop - sets Redis flag to stop all instances immediately
        
        The House (Bookmaker): "When things go wrong, you need an instant kill switch.
        One bad algorithm can drain your bankroll in minutes."
        """
        self.redis.set(self.EMERGENCY_STOP_KEY, "1")
        self.stop()
        logger.error("🚨 EMERGENCY STOP ACTIVATED!")
    
    def resume(self):
        """Clear emergency stop flag"""
        self.redis.delete(self.EMERGENCY_STOP_KEY)
        logger.info("✅ Emergency stop cleared - engine can restart")
    
    def _check_emergency_stop(self) -> bool:
        """Check if emergency stop is active"""
        return self.redis.exists(self.EMERGENCY_STOP_KEY)
    
    async def _process_signal(self, signal_data: Dict):
        """
        Process a single signal and potentially place bet
        
        The Math (Quant Analyst): "Every decision must be quantitatively justified.
        Confidence, edge, Kelly sizing—all must align before betting."
        """
        signal_id = signal_data.get('signal_id', 'unknown')
        strategy = signal_data.get('strategy', '')
        confidence = signal_data.get('confidence', 0)
        team = signal_data.get('team', '')
        odds = signal_data.get('odds', 0)
        match_id = signal_data.get('match_id', '')
        
        logger.debug(f"📥 Processing signal: {strategy} - {team} @ {odds} (confidence: {confidence:.0%})")
        
        # ========================================
        # VALIDATION 1: Strategy Filter
        # ========================================
        if strategy not in self.config.strategies_enabled:
            logger.debug(f"⏭️  Strategy '{strategy}' not enabled for auto-betting")
            self.total_bets_rejected += 1
            return
        
        # ========================================
        # VALIDATION 2: Confidence Threshold
        # ========================================
        if confidence < self.config.min_confidence:
            logger.debug(f"⏭️  Confidence {confidence:.0%} below minimum {self.config.min_confidence:.0%}")
            self.total_bets_rejected += 1
            return
        
        # ========================================
        # VALIDATION 3: Calculate Edge
        # ========================================
        implied_probability = 1 / odds if odds > 0 else 0
        edge = confidence - implied_probability
        
        if edge < self.config.min_edge:
            logger.debug(f"⏭️  Edge {edge:.2%} below minimum {self.config.min_edge:.2%}")
            self.total_bets_rejected += 1
            return
        
        # ========================================
        # VALIDATION 4: Frequency Limit
        # ========================================
        if self.bets_placed_last_hour >= self.config.max_bets_per_hour:
            logger.warning(f"⏭️  Hourly bet limit reached ({self.bets_placed_last_hour}/{self.config.max_bets_per_hour})")
            self.total_bets_rejected += 1
            return
        
        # ========================================
        # CALCULATE KELLY STAKE
        # ========================================
        async with get_db_context() as db:
            # Get current budget for stake calculation
            from backend.models.betting import BettingBudget
            from sqlalchemy.future import select
            
            budget_query = select(BettingBudget).where(
                BettingBudget.user_id == "default_user",
                BettingBudget.match_id.is_(None)  # Master budget
            )
            result = await db.execute(budget_query)
            budget = result.scalar_one_or_none()
            
            if not budget:
                logger.error("❌ No budget found - cannot calculate stake")
                self.total_bets_rejected += 1
                return
            
            # Calculate Kelly stake
            kelly_stake = calculate_kelly_stake(
                bankroll=budget.current_balance,
                odds=odds,
                win_probability=confidence,
                kelly_fraction=self.config.kelly_multiplier
            )
            
            logger.info(f"💰 Kelly stake calculated: ${kelly_stake:.2f}")
            
            # ========================================
            # VALIDATION 5: Risk Manager (8-Point Check)
            # ========================================
            is_valid, rejection_reason = await self.risk_manager.validate_bet(
                bet_amount=kelly_stake,
                match_id=match_id,
                confidence=confidence,
                edge=edge,
                db=db,
                user_id="default_user"
            )
            
            if not is_valid:
                logger.warning(f"⛔ Bet rejected by risk manager: {rejection_reason}")
                self.total_bets_rejected += 1
                return
            
            # ========================================
            # PLACE BET (All validations passed!)
            # ========================================
            try:
                bet_request = {
                    "signal_id": signal_id,
                    "match_id": match_id,
                    "strategy": strategy,
                    "market": "match_odds",  # TODO: Make dynamic
                    "action": signal_data.get('action', 'BACK'),
                    "team": team,
                    "stake": kelly_stake,
                    "odds": odds,
                    "confidence": confidence,
                    "reasoning": signal_data.get('reasoning', ''),
                    "auto_placed": True
                }
                
                # Create bet
                bet = await create_bet_internal(bet_request, db, user_id="default_user")
                
                # Update counters
                self.total_bets_placed += 1
                self.bets_placed_last_hour += 1
                self.risk_manager.record_bet_placed(kelly_stake)
                
                # Log audit trail
                self._log_bet_audit(signal_data, bet.id, kelly_stake, edge)
                
                logger.info(f"✅ AUTO-BET PLACED: ${kelly_stake:.2f} on {team} @ {odds} (Bet ID: {bet.id})")
                logger.info(f"   📊 Stats: {self.total_bets_placed} placed, {self.total_bets_rejected} rejected")
            
            except Exception as e:
                logger.error(f"❌ Failed to place bet: {e}")
                self.total_bets_rejected += 1
    
    def _log_bet_audit(self, signal_data: Dict, bet_id: int, stake: float, edge: float):
        """
        Log bet to audit trail (for compliance and review)
        
        The Sentinel (DevOps): "Audit logs are critical. Every automated
        decision must be traceable for debugging and compliance."
        """
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "bet_id": bet_id,
            "signal_id": signal_data.get('signal_id'),
            "strategy": signal_data.get('strategy'),
            "team": signal_data.get('team'),
            "odds": signal_data.get('odds'),
            "stake": stake,
            "confidence": signal_data.get('confidence'),
            "edge": edge,
            "auto_placed": True
        }
        
        # Store in Redis (last 1000 bets)
        self.redis.lpush("autobet:audit_log", json.dumps(audit_entry))
        self.redis.ltrim("autobet:audit_log", 0, 999)
        
        # Also log to file for permanent record
        logger.info(f"📝 AUDIT: {json.dumps(audit_entry)}")
    
    def get_stats(self) -> Dict:
        """Get auto-bet engine statistics"""
        return {
            "enabled": self.config.enabled,
            "running": self.running,
            "total_bets_placed": self.total_bets_placed,
            "total_bets_rejected": self.total_bets_rejected,
            "bets_last_hour": self.bets_placed_last_hour,
            "config": {
                "min_confidence": self.config.min_confidence,
                "min_edge": self.config.min_edge,
                "kelly_multiplier": self.config.kelly_multiplier,
                "strategies_enabled": self.config.strategies_enabled
            }
        }


# Global auto-bet engine instance
_auto_bet_engine: Optional[AutoBetEngine] = None

def get_auto_bet_engine(
    redis_client: redis.Redis = None,
    config: AutoBetConfig = None
) -> AutoBetEngine:
    """Get or create global auto-bet engine"""
    global _auto_bet_engine
    if _auto_bet_engine is None:
        if redis_client is None:
            import redis as redis_lib
            redis_client = redis_lib.Redis(host='localhost', port=6379, decode_responses=True)
        
        risk_manager = get_risk_manager(redis_client)
        _auto_bet_engine = AutoBetEngine(redis_client, risk_manager, config)
    
    return _auto_bet_engine


# CLI for manual testing
if __name__ == "__main__":
    import signal as sys_signal
    
    # Initialize Redis
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    # Create config
    config = AutoBetConfig(
        enabled=True,
        min_confidence=0.70,
        min_edge=0.03,
        kelly_multiplier=0.25
    )
    
    # Create engine
    engine = get_auto_bet_engine(redis_client, config)
    
    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info("🛑 Shutdown signal received")
        engine.stop()
    
    sys_signal.signal(sys_signal.SIGINT, shutdown_handler)
    sys_signal.signal(sys_signal.SIGTERM, shutdown_handler)
    
    # Run engine
    logger.info("🤖 Starting Auto-Bet Engine...")
    asyncio.run(engine.start())

