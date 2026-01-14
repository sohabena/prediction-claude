"""
Match State Management
Maintains in-memory state of live matches for strategy evaluation
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from collections import deque


@dataclass
class MatchState:
    """In-memory state for a live cricket match"""
    
    match_id: str
    team1: str = "Team A"
    team2: str = "Team B"
    batting_team: str = "Team A"
    
    # Current match state
    score: int = 0
    wickets: int = 0
    overs: float = 0.0
    target: Optional[int] = None
    
    # Odds tracking
    current_odds: float = 2.0
    previous_odds: float = 2.0
    odds_history: deque = field(default_factory=lambda: deque(maxlen=50))
    
    # Ball-by-ball data
    last_12_balls: List[int] = field(default_factory=list)
    last_6_balls: List[int] = field(default_factory=list)
    
    # Market metadata
    is_suspended: bool = False
    stake_limit: Optional[int] = None
    previous_stake_limit: Optional[int] = None
    suspension_count: int = 0
    suspension_duration: float = 0.0
    last_suspension_time: Optional[datetime] = None
    
    # Calculated metrics
    run_rate: float = 0.0
    required_run_rate: Optional[float] = None
    
    # Bowler tracking
    current_bowler: Optional[str] = None
    bowler_changed_recently: bool = False
    
    # Match phase
    match_phase: str = "early"  # early, middle, death
    
    # Timestamps
    last_update: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def update_odds(self, new_odds: float, back_price: float = None, lay_price: float = None):
        """Update odds and track history"""
        self.previous_odds = self.current_odds
        self.current_odds = new_odds
        self.odds_history.append({
            'odds': new_odds,
            'back_price': back_price if back_price is not None else new_odds,
            'lay_price': lay_price,
            'timestamp': datetime.utcnow()
        })
        self.last_update = datetime.utcnow()
    
    def odds_velocity(self) -> float:
        """Calculate odds movement velocity (percentage change)"""
        if self.previous_odds == 0:
            return 0.0
        return abs(self.current_odds - self.previous_odds) / self.previous_odds
    
    def add_ball(self, runs: int):
        """Add a ball to the tracking"""
        self.last_6_balls.append(runs)
        if len(self.last_6_balls) > 6:
            self.last_6_balls.pop(0)
        
        self.last_12_balls.append(runs)
        if len(self.last_12_balls) > 12:
            self.last_12_balls.pop(0)
        
        self.score += runs
        self.last_update = datetime.utcnow()
        self._update_run_rate()
        self._update_match_phase()
    
    def add_wicket(self):
        """Record a wicket"""
        self.wickets += 1
        self.last_update = datetime.utcnow()
    
    def update_overs(self, overs: float):
        """Update overs bowled"""
        self.overs = overs
        self._update_run_rate()
        self._update_required_run_rate()
        self._update_match_phase()
        self.last_update = datetime.utcnow()
    
    def update_suspension(self, is_suspended: bool):
        """Update suspension status"""
        if is_suspended and not self.is_suspended:
            # Just suspended
            self.suspension_count += 1
            self.last_suspension_time = datetime.utcnow()
        elif not is_suspended and self.is_suspended:
            # Just resumed
            if self.last_suspension_time:
                duration = (datetime.utcnow() - self.last_suspension_time).total_seconds()
                self.suspension_duration = duration
        
        self.is_suspended = is_suspended
        self.last_update = datetime.utcnow()
    
    def update_stake_limit(self, new_limit: int):
        """Update stake limit"""
        self.previous_stake_limit = self.stake_limit
        self.stake_limit = new_limit
        self.last_update = datetime.utcnow()
    
    def stake_limit_drop_percentage(self) -> float:
        """Calculate stake limit drop percentage"""
        if self.previous_stake_limit and self.stake_limit:
            if self.previous_stake_limit > self.stake_limit:
                return (self.previous_stake_limit - self.stake_limit) / self.previous_stake_limit
        return 0.0
    
    def _update_run_rate(self):
        """Calculate current run rate"""
        if self.overs > 0:
            self.run_rate = round(self.score / self.overs, 2)
        else:
            self.run_rate = 0.0
    
    def _update_required_run_rate(self):
        """Calculate required run rate (if chasing)"""
        if self.target is not None:
            runs_needed = self.target - self.score
            overs_remaining = 20.0 - self.overs  # Assuming T20
            
            if overs_remaining > 0:
                self.required_run_rate = round(runs_needed / overs_remaining, 2)
            else:
                self.required_run_rate = 0.0
        else:
            self.required_run_rate = None
    
    def _update_match_phase(self):
        """Determine match phase based on overs"""
        if self.overs < 6:
            self.match_phase = "powerplay"
        elif self.overs < 15:
            self.match_phase = "middle"
        else:
            self.match_phase = "death"
    
    def get_rolling_run_rate(self, overs: int = 3) -> float:
        """Calculate run rate for last N overs"""
        if not self.last_12_balls:
            return self.run_rate
        
        balls = min(overs * 6, len(self.last_12_balls))
        recent_balls = self.last_12_balls[-balls:]
        total_runs = sum(recent_balls)
        
        if balls > 0:
            return round((total_runs / balls) * 6, 2)  # Convert to per-over rate
        return 0.0
    
    def calculate_std_dev(self) -> float:
        """Calculate standard deviation of recent run rates"""
        if len(self.last_12_balls) < 6:
            return 0.0
        
        # Calculate run rate for each over
        over_rates = []
        for i in range(0, len(self.last_12_balls), 6):
            over_balls = self.last_12_balls[i:i+6]
            if len(over_balls) == 6:
                over_rates.append(sum(over_balls))
        
        if len(over_rates) < 2:
            return 0.0
        
        # Calculate standard deviation
        mean = sum(over_rates) / len(over_rates)
        variance = sum((x - mean) ** 2 for x in over_rates) / len(over_rates)
        return round(variance ** 0.5, 2)
    
    def is_momentum_shift(self) -> bool:
        """Detect if there's a momentum shift"""
        if len(self.last_12_balls) < 12:
            return False
        
        first_6 = sum(self.last_12_balls[:6])
        last_6 = sum(self.last_12_balls[6:])
        
        # Significant change (>50% difference)
        if first_6 > 0:
            change = abs(last_6 - first_6) / first_6
            return change > 0.5
        
        return False
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            'match_id': self.match_id,
            'team1': self.team1,
            'team2': self.team2,
            'batting_team': self.batting_team,
            'score': self.score,
            'wickets': self.wickets,
            'overs': self.overs,
            'target': self.target,
            'current_odds': self.current_odds,
            'run_rate': self.run_rate,
            'required_run_rate': self.required_run_rate,
            'match_phase': self.match_phase,
            'is_suspended': self.is_suspended,
            'stake_limit': self.stake_limit,
            'last_update': self.last_update.isoformat()
        }

