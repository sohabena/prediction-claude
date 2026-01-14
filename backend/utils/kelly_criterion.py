"""
Kelly Criterion Calculator
Calculates optimal bet sizing based on confidence and odds

The Kelly Criterion is a mathematical formula used to determine the optimal bet size
that maximizes long-term wealth growth while minimizing risk of ruin.

Formula: f* = (bp - q) / b
where:
- f* = fraction of bankroll to bet
- b = odds - 1 (decimal odds minus one)
- p = probability of win (confidence)
- q = probability of loss (1 - p)

Phase 3.2: Added dynamic drawdown brakes
- Cut stakes 50% at -3% session P&L
- Stop betting at -5% session P&L
- Per-bet cap: 1.5% bankroll
- Per-match cap: 8% bankroll
"""

import os
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger


@dataclass
class SessionState:
    """Tracks session P&L for drawdown brakes"""
    started_at: datetime = field(default_factory=datetime.utcnow)
    starting_bankroll: float = 0.0
    current_pnl: float = 0.0
    bets_placed: int = 0
    wins: int = 0
    losses: int = 0
    match_exposure: Dict[str, float] = field(default_factory=dict)
    
    @property
    def drawdown_percentage(self) -> float:
        """Calculate current drawdown as percentage of starting bankroll"""
        if self.starting_bankroll <= 0:
            return 0.0
        return (self.current_pnl / self.starting_bankroll) * 100
    
    @property
    def win_rate(self) -> float:
        """Calculate session win rate"""
        total = self.wins + self.losses
        if total == 0:
            return 0.0
        return self.wins / total


class DynamicKellyManager:
    """
    Phase 3.2: Dynamic Kelly with Drawdown Brakes
    
    Risk management features:
    - Session drawdown tracking
    - Automatic stake reduction at -3% drawdown
    - Trading halt at -5% drawdown
    - Per-bet maximum (1.5% bankroll)
    - Per-match exposure limit (8% bankroll)
    """
    
    # Configuration thresholds
    DRAWDOWN_WARNING = float(os.getenv('DRAWDOWN_WARNING_PCT', '-3.0'))  # -3%
    DRAWDOWN_HALT = float(os.getenv('DRAWDOWN_HALT_PCT', '-5.0'))  # -5%
    STAKE_REDUCTION_FACTOR = float(os.getenv('STAKE_REDUCTION_FACTOR', '0.50'))  # 50%
    MAX_PER_BET_PCT = float(os.getenv('MAX_PER_BET_PCT', '1.5'))  # 1.5%
    MAX_PER_MATCH_PCT = float(os.getenv('MAX_PER_MATCH_PCT', '8.0'))  # 8%
    SESSION_HOURS = int(os.getenv('SESSION_HOURS', '24'))  # Session length
    
    def __init__(self, starting_bankroll: float):
        self.session = SessionState(starting_bankroll=starting_bankroll)
        self.is_halted = False
        self.stake_multiplier = 1.0
        
        logger.info(
            f"💰 Dynamic Kelly Manager initialized:\n"
            f"   Starting bankroll: ₹{starting_bankroll:,.2f}\n"
            f"   Warning at: {self.DRAWDOWN_WARNING}%\n"
            f"   Halt at: {self.DRAWDOWN_HALT}%\n"
            f"   Per-bet max: {self.MAX_PER_BET_PCT}%\n"
            f"   Per-match max: {self.MAX_PER_MATCH_PCT}%"
        )
    
    def record_outcome(self, stake: float, profit_loss: float, match_id: str = None):
        """
        Record bet outcome and update session state
        
        Args:
            stake: Amount bet
            profit_loss: Profit (positive) or loss (negative)
            match_id: Match identifier for exposure tracking
        """
        self.session.current_pnl += profit_loss
        self.session.bets_placed += 1
        
        if profit_loss >= 0:
            self.session.wins += 1
        else:
            self.session.losses += 1
        
        # Update match exposure
        if match_id:
            if match_id in self.session.match_exposure:
                # Reduce exposure after bet settles
                self.session.match_exposure[match_id] -= stake
                if self.session.match_exposure[match_id] <= 0:
                    del self.session.match_exposure[match_id]
        
        # Check drawdown thresholds
        self._check_drawdown_brakes()
        
        logger.info(
            f"📊 Outcome recorded: P&L={profit_loss:+.2f}, "
            f"Session: {self.session.current_pnl:+.2f} ({self.session.drawdown_percentage:+.2f}%)"
        )
    
    def _check_drawdown_brakes(self):
        """Check and apply drawdown brakes"""
        drawdown = self.session.drawdown_percentage
        
        if drawdown <= self.DRAWDOWN_HALT:
            # HALT: Stop all betting
            if not self.is_halted:
                self.is_halted = True
                self.stake_multiplier = 0.0
                logger.error(
                    f"🛑 TRADING HALTED! Drawdown {drawdown:.2f}% exceeded {self.DRAWDOWN_HALT}% threshold.\n"
                    f"   Session P&L: ₹{self.session.current_pnl:,.2f}\n"
                    f"   Bets: {self.session.bets_placed} ({self.session.win_rate:.1%} win rate)"
                )
        
        elif drawdown <= self.DRAWDOWN_WARNING:
            # WARNING: Reduce stakes by 50%
            self.stake_multiplier = self.STAKE_REDUCTION_FACTOR
            logger.warning(
                f"⚠️ Drawdown warning! Reducing stakes by {(1-self.STAKE_REDUCTION_FACTOR)*100:.0f}%.\n"
                f"   Current drawdown: {drawdown:.2f}%"
            )
        else:
            # Normal operation
            self.stake_multiplier = 1.0
            self.is_halted = False
    
    def calculate_adjusted_stake(
        self,
        base_stake: float,
        match_id: str = None
    ) -> Dict:
        """
        Calculate adjusted stake with drawdown brakes
        
        Args:
            base_stake: Original Kelly-calculated stake
            match_id: Match identifier for exposure check
            
        Returns:
            dict with adjusted stake and risk info
        """
        # Check if trading is halted
        if self.is_halted:
            return {
                'adjusted_stake': 0.0,
                'original_stake': base_stake,
                'stake_multiplier': 0.0,
                'status': 'HALTED',
                'reason': f'Session drawdown exceeded {self.DRAWDOWN_HALT}%'
            }
        
        # Apply stake multiplier (reduced during warning)
        adjusted = base_stake * self.stake_multiplier
        
        # Apply per-bet maximum
        max_per_bet = self.session.starting_bankroll * (self.MAX_PER_BET_PCT / 100)
        if adjusted > max_per_bet:
            adjusted = max_per_bet
            logger.debug(f"Stake capped by per-bet max: ₹{max_per_bet:.2f}")
        
        # Check per-match exposure
        if match_id:
            current_exposure = self.session.match_exposure.get(match_id, 0)
            max_match_exposure = self.session.starting_bankroll * (self.MAX_PER_MATCH_PCT / 100)
            remaining_exposure = max_match_exposure - current_exposure
            
            if remaining_exposure <= 0:
                return {
                    'adjusted_stake': 0.0,
                    'original_stake': base_stake,
                    'stake_multiplier': self.stake_multiplier,
                    'status': 'MATCH_LIMIT_REACHED',
                    'reason': f'Per-match exposure limit ({self.MAX_PER_MATCH_PCT}%) reached'
                }
            
            if adjusted > remaining_exposure:
                adjusted = remaining_exposure
                logger.debug(f"Stake capped by match exposure: ₹{remaining_exposure:.2f}")
            
            # Update match exposure
            self.session.match_exposure[match_id] = current_exposure + adjusted
        
        status = 'NORMAL'
        reason = ''
        
        if self.stake_multiplier < 1.0:
            status = 'REDUCED'
            reason = f'Drawdown brake active ({self.session.drawdown_percentage:.2f}%)'
        
        return {
            'adjusted_stake': round(adjusted, 2),
            'original_stake': base_stake,
            'stake_multiplier': self.stake_multiplier,
            'status': status,
            'reason': reason,
            'session_drawdown': self.session.drawdown_percentage,
            'match_exposure': self.session.match_exposure.get(match_id, 0) if match_id else 0
        }
    
    def reset_session(self, new_bankroll: float = None):
        """Reset session state (e.g., start of new trading day)"""
        bankroll = new_bankroll or self.session.starting_bankroll
        self.session = SessionState(starting_bankroll=bankroll)
        self.is_halted = False
        self.stake_multiplier = 1.0
        
        logger.info(f"🔄 Session reset. New bankroll: ₹{bankroll:,.2f}")
    
    def get_status(self) -> Dict:
        """Get current risk management status"""
        return {
            'is_halted': self.is_halted,
            'stake_multiplier': self.stake_multiplier,
            'session': {
                'started_at': self.session.started_at.isoformat(),
                'starting_bankroll': self.session.starting_bankroll,
                'current_pnl': self.session.current_pnl,
                'drawdown_percentage': round(self.session.drawdown_percentage, 2),
                'bets_placed': self.session.bets_placed,
                'win_rate': round(self.session.win_rate, 4),
                'match_exposure_count': len(self.session.match_exposure)
            },
            'thresholds': {
                'warning': self.DRAWDOWN_WARNING,
                'halt': self.DRAWDOWN_HALT,
                'max_per_bet_pct': self.MAX_PER_BET_PCT,
                'max_per_match_pct': self.MAX_PER_MATCH_PCT
            }
        }


# Global instance
_kelly_manager: Optional[DynamicKellyManager] = None


def get_kelly_manager(starting_bankroll: float = None) -> DynamicKellyManager:
    """Get or create global Kelly manager"""
    global _kelly_manager
    if _kelly_manager is None:
        bankroll = starting_bankroll or float(os.getenv('STARTING_BANKROLL', '100000'))
        _kelly_manager = DynamicKellyManager(bankroll)
    return _kelly_manager


def calculate_kelly_stake(
    confidence: float,
    odds: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    min_stake: float = 10.0,
    max_stake: float = 5000.0
) -> dict:
    """
    Calculate optimal stake using fractional Kelly Criterion
    
    Args:
        confidence: Probability of winning (0.0 to 1.0, e.g., 0.85 for 85% confidence)
        odds: Decimal odds (e.g., 2.5 for 2.5x payout)
        bankroll: Current available balance
        kelly_fraction: Fraction of Kelly to use (0.25 = 25% Kelly, more conservative)
        min_stake: Minimum allowed stake
        max_stake: Maximum allowed stake
    
    Returns:
        dict: {
            'recommended_stake': float,
            'kelly_fraction_used': float,
            'full_kelly_stake': float,
            'edge': float,
            'risk_assessment': str
        }
    
    Example:
        >>> calculate_kelly_stake(confidence=0.80, odds=2.0, bankroll=10000)
        {
            'recommended_stake': 750.0,
            'kelly_fraction_used': 0.25,
            'full_kelly_stake': 3000.0,
            'edge': 0.60,
            'risk_assessment': 'MODERATE'
        }
    """
    
    # Validation
    if confidence <= 0 or confidence >= 1:
        logger.warning(f"Invalid confidence: {confidence}. Using 0.75")
        confidence = 0.75
    
    if odds <= 1.0:
        logger.warning(f"Invalid odds: {odds}. Minimum odds is 1.01")
        odds = 1.01
    
    if bankroll <= 0:
        logger.error(f"Invalid bankroll: {bankroll}")
        return {
            'recommended_stake': 0.0,
            'kelly_fraction_used': 0.0,
            'full_kelly_stake': 0.0,
            'edge': 0.0,
            'risk_assessment': 'NO_FUNDS'
        }
    
    # Kelly Criterion calculation
    b = odds - 1  # Net odds (profit per unit staked)
    p = confidence  # Probability of win
    q = 1 - confidence  # Probability of loss
    
    # Calculate edge (expected value)
    edge = (b * p) - q
    
    # Full Kelly fraction
    if edge <= 0:
        # Negative edge = don't bet
        logger.warning(f"Negative edge detected: {edge:.4f}. Not recommended to bet.")
        return {
            'recommended_stake': 0.0,
            'kelly_fraction_used': 0.0,
            'full_kelly_stake': 0.0,
            'edge': edge,
            'risk_assessment': 'NEGATIVE_EDGE'
        }
    
    kelly_percentage = edge / b  # Fraction of bankroll to bet
    full_kelly_stake = bankroll * kelly_percentage
    
    # Apply fractional Kelly for risk management
    # Fractional Kelly (typically 0.25 to 0.5) reduces variance and protects against overconfidence
    fractional_kelly_stake = full_kelly_stake * kelly_fraction
    
    # Apply min/max constraints
    recommended_stake = max(min_stake, min(fractional_kelly_stake, max_stake))
    
    # Ensure we don't bet more than available bankroll
    recommended_stake = min(recommended_stake, bankroll * 0.2)  # Never bet more than 20% of bankroll
    
    # Risk assessment based on stake size relative to bankroll
    stake_percentage = (recommended_stake / bankroll) * 100
    
    if stake_percentage < 1:
        risk_level = 'VERY_LOW'
    elif stake_percentage < 3:
        risk_level = 'LOW'
    elif stake_percentage < 5:
        risk_level = 'MODERATE'
    elif stake_percentage < 10:
        risk_level = 'HIGH'
    else:
        risk_level = 'VERY_HIGH'
    
    result = {
        'recommended_stake': round(recommended_stake, 2),
        'kelly_fraction_used': kelly_fraction,
        'full_kelly_stake': round(full_kelly_stake, 2),
        'edge': round(edge, 4),
        'risk_assessment': risk_level,
        'stake_percentage': round(stake_percentage, 2),
        'expected_value': round(recommended_stake * edge, 2)
    }
    
    logger.debug(f"Kelly calculation: confidence={confidence}, odds={odds}, "
                f"stake={result['recommended_stake']}, edge={result['edge']}")
    
    return result


def validate_bet_sizing(
    stake: float,
    bankroll: float,
    max_risk_percentage: float = 20.0
) -> tuple[bool, str]:
    """
    Validate if a proposed stake is within acceptable risk parameters
    
    Args:
        stake: Proposed bet amount
        bankroll: Current balance
        max_risk_percentage: Maximum % of bankroll allowed per bet
    
    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if stake <= 0:
        return False, "Stake must be greater than 0"
    
    if stake > bankroll:
        return False, f"Insufficient funds. Stake (₹{stake}) exceeds bankroll (₹{bankroll})"
    
    risk_percentage = (stake / bankroll) * 100
    
    if risk_percentage > max_risk_percentage:
        return False, f"Stake too large. {risk_percentage:.1f}% of bankroll exceeds {max_risk_percentage}% limit"
    
    return True, "Bet size validated successfully"


def calculate_expected_profit(stake: float, odds: float, confidence: float) -> dict:
    """
    Calculate expected profit/loss for a bet
    
    Args:
        stake: Amount to bet
        odds: Decimal odds
        confidence: Win probability (0-1)
    
    Returns:
        dict: {
            'expected_profit': float,
            'potential_win': float,
            'potential_loss': float,
            'expected_roi': float
        }
    """
    potential_win = stake * (odds - 1)  # Profit if win
    potential_loss = -stake  # Loss if lose
    
    # Expected value calculation
    expected_profit = (potential_win * confidence) + (potential_loss * (1 - confidence))
    expected_roi = (expected_profit / stake) * 100 if stake > 0 else 0
    
    return {
        'expected_profit': round(expected_profit, 2),
        'potential_win': round(potential_win, 2),
        'potential_loss': round(potential_loss, 2),
        'expected_roi': round(expected_roi, 2)
    }


# Example usage and testing
if __name__ == "__main__":
    print("Kelly Criterion Calculator - Test Cases")
    print("=" * 60)
    
    test_cases = [
        {"confidence": 0.80, "odds": 2.0, "bankroll": 10000, "desc": "High confidence, even odds"},
        {"confidence": 0.90, "odds": 1.5, "bankroll": 10000, "desc": "Very high confidence, low odds"},
        {"confidence": 0.70, "odds": 3.0, "bankroll": 10000, "desc": "Moderate confidence, high odds"},
        {"confidence": 0.60, "odds": 2.5, "bankroll": 5000, "desc": "Lower confidence, lower bankroll"},
    ]
    
    for i, case in enumerate(test_cases, 1):
        print(f"\nTest Case {i}: {case['desc']}")
        print(f"  Confidence: {case['confidence']*100}% | Odds: {case['odds']} | Bankroll: ₹{case['bankroll']}")
        
        result = calculate_kelly_stake(case['confidence'], case['odds'], case['bankroll'])
        
        print(f"  Recommended Stake: ₹{result['recommended_stake']} "
              f"({result['stake_percentage']:.2f}% of bankroll)")
        print(f"  Full Kelly: ₹{result['full_kelly_stake']} "
              f"(using {result['kelly_fraction_used']*100}% fraction)")
        print(f"  Edge: {result['edge']:.4f} | Risk: {result['risk_assessment']}")
        
        # Expected profit calculation
        exp = calculate_expected_profit(result['recommended_stake'], case['odds'], case['confidence'])
        print(f"  Expected Profit: ₹{exp['expected_profit']} (ROI: {exp['expected_roi']:.2f}%)")

