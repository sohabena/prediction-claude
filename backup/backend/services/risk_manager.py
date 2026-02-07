"""
Risk Management System
Comprehensive risk controls to protect bankroll and ensure long-term profitability

The House (Bookmaker): "Risk management is the difference between professionals
and gamblers. Control position sizing, stop losses, and exposure—or go broke."
"""

from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from loguru import logger
import redis
import json
from enum import Enum

from backend.models.betting import BettingBudget, VirtualBet, BetStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func


class RiskLevel(Enum):
    """Risk assessment levels"""
    SAFE = "safe"  # < 30% bankroll at risk
    MODERATE = "moderate"  # 30-50% at risk
    HIGH = "high"  # 50-70% at risk
    CRITICAL = "critical"  # > 70% at risk
    HALT = "halt"  # Trading should stop


@dataclass
class RiskLimits:
    """
    Risk limit configuration
    
    The House (Bookmaker): "These are battle-tested limits. Violate them at your peril."
    """
    # Position sizing limits
    max_bet_percent: float = 5.0  # Max % of bankroll per bet
    max_match_exposure_percent: float = 20.0  # Max % exposed to single match
    max_total_exposure_percent: float = 50.0  # Max % of bankroll at risk
    
    # Loss limits (kill switches)
    max_daily_loss_percent: float = 10.0  # Halt trading if down 10% in a day
    max_weekly_loss_percent: float = 20.0  # Halt if down 20% in a week
    max_drawdown_percent: float = 30.0  # Absolute max drawdown before pause
    
    # Edge requirements
    min_edge_percent: float = 3.0  # Minimum edge to place bet
    min_confidence: float = 0.65  # Minimum confidence threshold
    
    # Betting frequency limits
    max_bets_per_hour: int = 10  # Rate limit to avoid tilt
    max_concurrent_bets: int = 20  # Max open positions
    
    # Recovery mode (after losses)
    recovery_mode_threshold: float = -15.0  # Enter recovery mode at -15%
    recovery_mode_bet_size: float = 0.5  # Reduce bet size by 50% in recovery


@dataclass
class RiskAssessment:
    """Current risk assessment"""
    risk_level: RiskLevel
    current_exposure_percent: float
    current_exposure_amount: float
    match_exposures: Dict[str, float]  # match_id -> exposure amount
    
    # Loss tracking
    daily_pnl: float
    weekly_pnl: float
    max_drawdown: float
    
    # Flags
    is_trading_halted: bool
    is_recovery_mode: bool
    halt_reason: Optional[str]
    
    # Limits
    available_for_betting: float
    max_bet_size: float
    
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            "risk_level": self.risk_level.value,
            "current_exposure_percent": round(self.current_exposure_percent, 2),
            "current_exposure_amount": round(self.current_exposure_amount, 2),
            "match_exposures": {k: round(v, 2) for k, v in self.match_exposures.items()},
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "is_trading_halted": self.is_trading_halted,
            "is_recovery_mode": self.is_recovery_mode,
            "halt_reason": self.halt_reason,
            "available_for_betting": round(self.available_for_betting, 2),
            "max_bet_size": round(self.max_bet_size, 2),
            "timestamp": self.timestamp.isoformat()
        }


class RiskManager:
    """
    Comprehensive risk management system
    
    Responsibilities:
    1. Enforce position sizing limits
    2. Monitor exposure across matches
    3. Implement kill switches (stop trading after losses)
    4. Enforce minimum edge requirements
    5. Track drawdowns and recovery
    6. Prevent over-betting (frequency limits)
    
    The House (Bookmaker): "This is your survival mechanism. When emotions run high,
    risk management keeps you alive."
    """
    
    def __init__(self, redis_client: redis.Redis, limits: RiskLimits = None):
        self.redis = redis_client
        self.limits = limits or RiskLimits()
        
        # Redis keys
        self.RISK_STATE_KEY = "risk:state"
        self.DAILY_PNL_KEY = "risk:daily_pnl:{date}"
        self.WEEKLY_PNL_KEY = "risk:weekly_pnl:{week}"
        self.HALT_FLAG_KEY = "risk:trading_halted"
        self.BET_HISTORY_KEY = "risk:bet_history_hour"
        
        logger.info(f"🛡️  Risk Manager initialized with limits: max_bet={self.limits.max_bet_percent}%, max_daily_loss={self.limits.max_daily_loss_percent}%")
    
    async def assess_risk(self, db: AsyncSession, user_id: str = "default_user") -> RiskAssessment:
        """
        Comprehensive risk assessment
        
        Returns:
            Current risk state with all metrics
        """
        # Get master budget
        budget_query = select(BettingBudget).where(
            BettingBudget.user_id == user_id,
            BettingBudget.match_id.is_(None)  # Master budget
        )
        result = await db.execute(budget_query)
        budget = result.scalar_one_or_none()
        
        if not budget:
            logger.warning(f"⚠️  No master budget found for {user_id}")
            return self._default_risk_assessment()
        
        # Calculate current exposure (all pending bets)
        exposure_query = select(func.sum(VirtualBet.stake)).where(
            VirtualBet.status == BetStatus.PENDING,
            VirtualBet.budget.has(user_id=user_id)
        )
        result = await db.execute(exposure_query)
        total_exposure = result.scalar() or 0.0
        
        # Calculate exposure by match
        match_exposure_query = select(
            VirtualBet.match_id,
            func.sum(VirtualBet.stake)
        ).where(
            VirtualBet.status == BetStatus.PENDING,
            VirtualBet.budget.has(user_id=user_id)
        ).group_by(VirtualBet.match_id)
        
        result = await db.execute(match_exposure_query)
        match_exposures = {row[0]: row[1] for row in result.fetchall()}
        
        # Calculate P&L
        daily_pnl = await self._calculate_daily_pnl(db, user_id)
        weekly_pnl = await self._calculate_weekly_pnl(db, user_id)
        max_drawdown = await self._calculate_max_drawdown(db, user_id)
        
        # Calculate risk metrics
        current_balance = budget.current_balance
        initial_balance = budget.initial_amount
        
        exposure_percent = (total_exposure / current_balance * 100) if current_balance > 0 else 0
        daily_loss_percent = (daily_pnl / initial_balance * 100) if initial_balance > 0 else 0
        weekly_loss_percent = (weekly_pnl / initial_balance * 100) if initial_balance > 0 else 0
        drawdown_percent = (max_drawdown / initial_balance * 100) if initial_balance > 0 else 0
        
        # Determine risk level
        risk_level = self._calculate_risk_level(exposure_percent)
        
        # Check halt conditions
        is_halted, halt_reason = self._check_halt_conditions(
            daily_loss_percent,
            weekly_loss_percent,
            drawdown_percent
        )
        
        # Check recovery mode
        is_recovery = daily_loss_percent < self.limits.recovery_mode_threshold
        
        # Calculate available amounts
        available_for_betting = current_balance - total_exposure
        
        # Max bet size (lesser of: percentage limit, available funds)
        max_bet_from_percent = current_balance * (self.limits.max_bet_percent / 100)
        max_bet_size = min(max_bet_from_percent, available_for_betting)
        
        # Reduce in recovery mode
        if is_recovery:
            max_bet_size *= self.limits.recovery_mode_bet_size
        
        assessment = RiskAssessment(
            risk_level=risk_level,
            current_exposure_percent=exposure_percent,
            current_exposure_amount=total_exposure,
            match_exposures=match_exposures,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            max_drawdown=max_drawdown,
            is_trading_halted=is_halted,
            is_recovery_mode=is_recovery,
            halt_reason=halt_reason,
            available_for_betting=available_for_betting,
            max_bet_size=max_bet_size,
            timestamp=datetime.utcnow()
        )
        
        # Cache in Redis
        self.redis.setex(self.RISK_STATE_KEY, 60, json.dumps(assessment.to_dict()))
        
        # Set halt flag if needed
        if is_halted:
            self.redis.setex(self.HALT_FLAG_KEY, 3600, halt_reason)  # 1 hour halt
            logger.error(f"🚨 TRADING HALTED: {halt_reason}")
        
        return assessment
    
    async def validate_bet(
        self,
        bet_amount: float,
        match_id: str,
        confidence: float,
        edge: float,
        db: AsyncSession,
        user_id: str = "default_user"
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate if a bet meets risk requirements
        
        Args:
            bet_amount: Proposed bet size
            match_id: Match identifier
            confidence: Signal confidence (0-1)
            edge: Estimated edge in decimal (0.03 = 3% edge)
            db: Database session
            user_id: User identifier
        
        Returns:
            (is_valid, reason_if_invalid)
        
        The House (Bookmaker): "Every bet must pass 7 risk checks. One failure = no bet."
        """
        # Get current risk assessment
        assessment = await self.assess_risk(db, user_id)
        
        # Check 1: Trading halt
        if assessment.is_trading_halted:
            return False, f"Trading halted: {assessment.halt_reason}"
        
        # Check 2: Minimum confidence
        if confidence < self.limits.min_confidence:
            return False, f"Confidence {confidence:.0%} below minimum {self.limits.min_confidence:.0%}"
        
        # Check 3: Minimum edge
        edge_percent = edge * 100
        if edge_percent < self.limits.min_edge_percent:
            return False, f"Edge {edge_percent:.1f}% below minimum {self.limits.min_edge_percent:.1f}%"
        
        # Check 4: Bet size limit
        if bet_amount > assessment.max_bet_size:
            return False, f"Bet ${bet_amount:.2f} exceeds max ${assessment.max_bet_size:.2f}"
        
        # Check 5: Match exposure limit
        current_match_exposure = assessment.match_exposures.get(match_id, 0)
        new_match_exposure = current_match_exposure + bet_amount
        
        # Get master budget for limit calculation
        budget_query = select(BettingBudget).where(
            BettingBudget.user_id == user_id,
            BettingBudget.match_id.is_(None)
        )
        result = await db.execute(budget_query)
        budget = result.scalar_one_or_none()
        
        if budget:
            max_match_exposure = budget.current_balance * (self.limits.max_match_exposure_percent / 100)
            if new_match_exposure > max_match_exposure:
                return False, f"Match exposure ${new_match_exposure:.2f} exceeds max ${max_match_exposure:.2f}"
        
        # Check 6: Total exposure limit
        new_total_exposure = assessment.current_exposure_amount + bet_amount
        max_total_exposure = budget.current_balance * (self.limits.max_total_exposure_percent / 100) if budget else 0
        
        if new_total_exposure > max_total_exposure:
            return False, f"Total exposure ${new_total_exposure:.2f} exceeds max ${max_total_exposure:.2f}"
        
        # Check 7: Frequency limit (bets per hour)
        bets_last_hour = self._count_recent_bets(60)
        if bets_last_hour >= self.limits.max_bets_per_hour:
            return False, f"Bet frequency limit: {bets_last_hour}/{self.limits.max_bets_per_hour} bets in last hour"
        
        # Check 8: Concurrent bets limit
        concurrent_bets = await self._count_concurrent_bets(db, user_id)
        if concurrent_bets >= self.limits.max_concurrent_bets:
            return False, f"Concurrent bets limit: {concurrent_bets}/{self.limits.max_concurrent_bets} open positions"
        
        # All checks passed
        logger.info(f"✅ Bet validated: ${bet_amount:.2f} on {match_id} (confidence: {confidence:.0%}, edge: {edge_percent:.1f}%)")
        return True, None
    
    def record_bet_placed(self, bet_amount: float):
        """Record a bet for frequency tracking"""
        timestamp = datetime.utcnow().isoformat()
        self.redis.lpush(self.BET_HISTORY_KEY, f"{timestamp}:{bet_amount}")
        self.redis.ltrim(self.BET_HISTORY_KEY, 0, 99)  # Keep last 100
        self.redis.expire(self.BET_HISTORY_KEY, 3600)  # 1 hour TTL
    
    def _count_recent_bets(self, minutes: int) -> int:
        """Count bets placed in last N minutes"""
        bet_history = self.redis.lrange(self.BET_HISTORY_KEY, 0, -1)
        
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
        recent_count = 0
        
        for entry in bet_history:
            try:
                timestamp_str = entry.split(':')[0]
                bet_time = datetime.fromisoformat(timestamp_str)
                if bet_time > cutoff_time:
                    recent_count += 1
            except:
                pass
        
        return recent_count
    
    async def _count_concurrent_bets(self, db: AsyncSession, user_id: str) -> int:
        """Count currently open bets"""
        query = select(func.count(VirtualBet.id)).where(
            VirtualBet.status == BetStatus.PENDING,
            VirtualBet.budget.has(user_id=user_id)
        )
        result = await db.execute(query)
        return result.scalar() or 0
    
    async def _calculate_daily_pnl(self, db: AsyncSession, user_id: str) -> float:
        """Calculate P&L for today"""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        query = select(func.sum(VirtualBet.profit_loss)).where(
            VirtualBet.closed_at >= today_start,
            VirtualBet.budget.has(user_id=user_id)
        )
        result = await db.execute(query)
        return result.scalar() or 0.0
    
    async def _calculate_weekly_pnl(self, db: AsyncSession, user_id: str) -> float:
        """Calculate P&L for this week"""
        week_start = datetime.utcnow() - timedelta(days=7)
        
        query = select(func.sum(VirtualBet.profit_loss)).where(
            VirtualBet.closed_at >= week_start,
            VirtualBet.budget.has(user_id=user_id)
        )
        result = await db.execute(query)
        return result.scalar() or 0.0
    
    async def _calculate_max_drawdown(self, db: AsyncSession, user_id: str) -> float:
        """
        Calculate maximum drawdown from peak
        
        The Math (Quant Analyst): "Max drawdown is the worst peak-to-trough decline.
        It measures pain and capital preservation."
        """
        # Get budget to find current drawdown
        budget_query = select(BettingBudget).where(
            BettingBudget.user_id == user_id,
            BettingBudget.match_id.is_(None)
        )
        result = await db.execute(budget_query)
        budget = result.scalar_one_or_none()
        
        if not budget:
            return 0.0
        
        # Simple drawdown: initial - current (if negative)
        drawdown = min(0, budget.current_balance - budget.initial_amount)
        return abs(drawdown)
    
    def _calculate_risk_level(self, exposure_percent: float) -> RiskLevel:
        """Determine risk level based on exposure"""
        if exposure_percent >= 70:
            return RiskLevel.CRITICAL
        elif exposure_percent >= 50:
            return RiskLevel.HIGH
        elif exposure_percent >= 30:
            return RiskLevel.MODERATE
        else:
            return RiskLevel.SAFE
    
    def _check_halt_conditions(
        self,
        daily_loss_percent: float,
        weekly_loss_percent: float,
        drawdown_percent: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if trading should be halted
        
        Returns:
            (should_halt, reason)
        """
        # Check daily loss limit
        if daily_loss_percent < -self.limits.max_daily_loss_percent:
            return True, f"Daily loss limit breached: {daily_loss_percent:.1f}%"
        
        # Check weekly loss limit
        if weekly_loss_percent < -self.limits.max_weekly_loss_percent:
            return True, f"Weekly loss limit breached: {weekly_loss_percent:.1f}%"
        
        # Check max drawdown
        if drawdown_percent > self.limits.max_drawdown_percent:
            return True, f"Max drawdown breached: {drawdown_percent:.1f}%"
        
        return False, None
    
    def _default_risk_assessment(self) -> RiskAssessment:
        """Return default risk assessment (when no budget exists)"""
        return RiskAssessment(
            risk_level=RiskLevel.HALT,
            current_exposure_percent=0.0,
            current_exposure_amount=0.0,
            match_exposures={},
            daily_pnl=0.0,
            weekly_pnl=0.0,
            max_drawdown=0.0,
            is_trading_halted=True,
            is_recovery_mode=False,
            halt_reason="No budget initialized",
            available_for_betting=0.0,
            max_bet_size=0.0,
            timestamp=datetime.utcnow()
        )
    
    async def manual_resume_trading(self):
        """
        Manually resume trading after halt
        
        The House (Bookmaker): "Use this cautiously. Halts exist for a reason.
        Only resume after reviewing what went wrong."
        """
        self.redis.delete(self.HALT_FLAG_KEY)
        logger.warning("⚠️  Trading manually resumed (halt flag cleared)")
    
    def get_risk_report(self, assessment: RiskAssessment) -> str:
        """
        Generate human-readable risk report
        
        Returns:
            Formatted risk report
        """
        report = f"""
╔══════════════════════════════════════╗
║        RISK MANAGEMENT REPORT        ║
╚══════════════════════════════════════╝

Risk Level: {assessment.risk_level.value.upper()}
Trading Status: {"🚨 HALTED" if assessment.is_trading_halted else "✅ ACTIVE"}
Recovery Mode: {"⚠️  YES" if assessment.is_recovery_mode else "NO"}

📊 Exposure:
   Current: ${assessment.current_exposure_amount:.2f} ({assessment.current_exposure_percent:.1f}%)
   Available: ${assessment.available_for_betting:.2f}
   Max Bet Size: ${assessment.max_bet_size:.2f}

💰 P&L:
   Daily: ${assessment.daily_pnl:+.2f}
   Weekly: ${assessment.weekly_pnl:+.2f}
   Max Drawdown: ${assessment.max_drawdown:.2f}

🎯 Match Exposures:
"""
        for match_id, exposure in assessment.match_exposures.items():
            report += f"   {match_id}: ${exposure:.2f}\n"
        
        if assessment.is_trading_halted:
            report += f"\n🚨 HALT REASON: {assessment.halt_reason}\n"
        
        return report


# Global risk manager instance
_risk_manager: Optional[RiskManager] = None

def get_risk_manager(redis_client: redis.Redis = None) -> RiskManager:
    """Get or create global risk manager"""
    global _risk_manager
    if _risk_manager is None:
        if redis_client is None:
            import redis as redis_lib
            redis_client = redis_lib.Redis(host='localhost', port=6379, decode_responses=True)
        _risk_manager = RiskManager(redis_client)
    return _risk_manager

