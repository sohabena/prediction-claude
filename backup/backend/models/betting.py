"""
TITAN Betting Models
SQLAlchemy models for virtual betting system
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, ForeignKey, Enum as SQLEnum, JSON, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()


class MarketTicks(Base):
    """
    Market ticks - real-time odds data from exchanges
    Time-series data for odds movements and market analysis
    """
    __tablename__ = 'market_ticks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    time = Column(DateTime, nullable=False, index=True)
    match_id = Column(String(200), nullable=False, index=True)
    market_type = Column(String(50), nullable=False)
    team = Column(String(100))
    
    # Odds data
    odds = Column(Float)
    back_price = Column(Float)
    lay_price = Column(Float)
    volume = Column(Float)
    
    # Cricket stats
    score = Column(String(20))
    wickets = Column(Integer)
    overs = Column(Float)
    run_rate = Column(Float)
    required_run_rate = Column(Float)
    
    # Market status
    is_suspended = Column(Boolean, default=False)
    stake_limit = Column(Float)
    
    # Metadata (using meta_data to avoid SQLAlchemy reserved name)
    meta_data = Column(JSON)
    
    # Unique constraint to prevent duplicate ticks
    __table_args__ = (
        UniqueConstraint('time', 'match_id', 'market_type', name='uq_market_tick'),
    )
    
    def __repr__(self):
        return f"<MarketTicks(match={self.match_id}, odds={self.odds}, time={self.time})>"


class BetStatus(enum.Enum):
    """Bet status enumeration"""
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"


class BettingBudget(Base):
    """
    User betting budget tracking
    Supports both master budget and match-specific budgets
    
    - Master budget: match_id = None (overall funds)
    - Match budget: match_id = specific match ID (allocated for that match)
    """
    __tablename__ = 'betting_budgets'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, default='default_user')
    match_id = Column(String(100), nullable=True)  # NULL = master budget, else match-specific
    match_name = Column(String(200), nullable=True)  # e.g., "India vs Australia"
    
    current_balance = Column(Float, nullable=False, default=50000.0)
    initial_amount = Column(Float, nullable=False, default=50000.0)
    total_profit_loss = Column(Float, default=0.0)
    total_bets_placed = Column(Integer, default=0)
    total_bets_won = Column(Integer, default=0)
    total_bets_lost = Column(Integer, default=0)
    
    is_active = Column(Boolean, default=True)  # Can be deactivated after match ends
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship
    bets = relationship("VirtualBet", back_populates="budget")
    
    def __repr__(self):
        return f"<BettingBudget(user={self.user_id}, balance={self.current_balance}, roi={self.roi:.2f}%)>"
    
    @property
    def roi(self) -> float:
        """Calculate Return on Investment percentage"""
        if self.initial_amount == 0:
            return 0.0
        return ((self.current_balance - self.initial_amount) / self.initial_amount) * 100
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage"""
        total = self.total_bets_won + self.total_bets_lost
        if total == 0:
            return 0.0
        return (self.total_bets_won / total) * 100


class VirtualBet(Base):
    """
    Individual virtual bet records
    Stores all bet details and outcomes
    """
    __tablename__ = 'virtual_bets'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey('betting_budgets.id'), nullable=False)
    
    # Signal details
    signal_id = Column(String(100), nullable=False)
    match_id = Column(String(100), nullable=False)
    match_info = Column(Text)  # JSON string with match details
    
    # Bet details
    strategy = Column(String(50), nullable=False)  # panic_rebound, mean_reversion, whale_shadow
    market = Column(String(50), nullable=False)  # match_odds, over_under, innings_runs
    action = Column(String(20), nullable=False)  # BACK or LAY
    team = Column(String(100))
    
    # Financial details
    stake = Column(Float, nullable=False)
    odds = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)  # 0.0 to 1.0
    potential_profit = Column(Float, nullable=False)
    
    # Outcome
    status = Column(SQLEnum(BetStatus), default=BetStatus.PENDING, nullable=False)
    profit_loss = Column(Float, default=0.0)
    closed_at = Column(DateTime)
    
    # Metadata
    placed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    reasoning = Column(Text)
    auto_placed = Column(Boolean, default=False)
    
    # Relationship
    budget = relationship("BettingBudget", back_populates="bets")
    
    def __repr__(self):
        return f"<VirtualBet(id={self.id}, strategy={self.strategy}, stake={self.stake}, status={self.status.value})>"
    
    def close_bet(self, won: bool):
        """Close the bet and calculate profit/loss"""
        self.closed_at = datetime.utcnow()
        
        if won:
            self.status = BetStatus.WON
            self.profit_loss = self.stake * (self.odds - 1)  # Win: stake * (odds - 1)
        else:
            self.status = BetStatus.LOST
            self.profit_loss = -self.stake  # Loss: -stake
        
        return self.profit_loss


class MatchResult(Base):
    """
    Match outcomes for P&L calculation
    Stores final match results from Cricket API
    """
    __tablename__ = 'match_results'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Match details
    team1 = Column(String(100), nullable=False)
    team2 = Column(String(100), nullable=False)
    match_format = Column(String(20))  # T20, ODI, TEST
    
    # Result
    winner = Column(String(100))
    final_score_team1 = Column(String(50))
    final_score_team2 = Column(String(50))
    result_summary = Column(Text)
    
    # Timing
    match_started_at = Column(DateTime)
    completed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Metadata
    venue = Column(String(200))
    api_response = Column(Text)  # Raw API response for debugging
    
    def __repr__(self):
        return f"<MatchResult(match_id={self.match_id}, winner={self.winner})>"


class BettingHistory(Base):
    """
    Aggregated betting performance metrics
    Daily/weekly summaries for efficient querying
    """
    __tablename__ = 'betting_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey('betting_budgets.id'), nullable=False)
    
    # Time period
    date = Column(DateTime, nullable=False, index=True)
    period_type = Column(String(20), nullable=False)  # daily, weekly, monthly
    
    # Metrics
    total_bets = Column(Integer, default=0)
    bets_won = Column(Integer, default=0)
    bets_lost = Column(Integer, default=0)
    total_staked = Column(Float, default=0.0)
    total_profit_loss = Column(Float, default=0.0)
    
    # Strategy breakdown (JSON string)
    strategy_performance = Column(Text)  # JSON: {strategy: {bets, profit_loss, win_rate}}
    
    # Best performing
    best_strategy = Column(String(50))
    worst_strategy = Column(String(50))
    
    # Balance at end of period
    ending_balance = Column(Float, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<BettingHistory(date={self.date}, period={self.period_type}, p&l={self.total_profit_loss})>"
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate for this period"""
        total = self.bets_won + self.bets_lost
        if total == 0:
            return 0.0
        return (self.bets_won / total) * 100
    
    @property
    def roi(self) -> float:
        """Calculate ROI for this period"""
        if self.total_staked == 0:
            return 0.0
        return (self.total_profit_loss / self.total_staked) * 100

