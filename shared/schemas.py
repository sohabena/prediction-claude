"""
PHOENIX Shared Schemas
Pydantic models used across all services. Single source of truth for data contracts.

All inter-service communication (Redis pub/sub, API requests) uses these models.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# Enums
# ============================================================


class BettingAction(IntEnum):
    """RL agent action space."""

    HOLD = 0
    BACK_HOME_SM = 1  # Back home team, 1% bankroll
    BACK_HOME_LG = 2  # Back home team, 3% bankroll
    BACK_AWAY_SM = 3  # Back away team, 1% bankroll
    BACK_AWAY_LG = 4  # Back away team, 3% bankroll
    LAY_HOME_SM = 5  # Lay home team, 1% bankroll
    LAY_AWAY_SM = 6  # Lay away team, 1% bankroll


class MatchStatus(StrEnum):
    """Match status (raw, no phase heuristics)."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    INNINGS_BREAK = "innings_break"
    COMPLETED = "completed"


class AgentState(StrEnum):
    """RL agent operating mode."""

    TRAINING = "training"
    EVALUATING = "evaluating"
    PAUSED = "paused"
    LIVE = "live"


class BetOutcome(StrEnum):
    """Virtual bet settlement outcome."""

    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"
    VOID = "void"


# ============================================================
# Data Models
# ============================================================


class OddsEvent(BaseModel):
    """Normalized odds event from LotusBook scraper."""

    match_id: str
    timestamp: datetime
    team_home: str
    team_away: str
    competition: str = ""

    # 1X2 odds
    back_home: Optional[float] = Field(None, gt=1.0, description="Back price for home team")
    lay_home: Optional[float] = Field(None, gt=1.0, description="Lay price for home team")
    back_draw: Optional[float] = Field(None, gt=1.0, description="Back price for draw")
    lay_draw: Optional[float] = Field(None, gt=1.0, description="Lay price for draw")
    back_away: Optional[float] = Field(None, gt=1.0, description="Back price for away team")
    lay_away: Optional[float] = Field(None, gt=1.0, description="Lay price for away team")

    # State
    is_live: bool = False
    scheduled_time: Optional[datetime] = None

    # Metadata
    source: str = "lotusbook"
    scrape_method: str = "dom"
    scrape_latency_ms: int = 0


class MatchContext(BaseModel):
    """Cricket match context from Cricbuzz enricher (raw stats only, no heuristics)."""

    match_id: str
    timestamp: datetime
    is_live: bool = False
    score: int = 0
    wickets: int = 0
    overs: float = 0.0
    run_rate: float = 0.0
    required_run_rate: float = 0.0
    innings: int = 1
    balls_remaining: int = 0
    max_overs: float = 20.0  # T20=20, ODI=50
    max_balls: int = 120  # T20=120, ODI=300
    batting_team: str = ""
    bowling_team: str = ""
    status: MatchStatus = MatchStatus.SCHEDULED
    match_format: str = "T20"  # T20, ODI, Test


class VirtualBet(BaseModel):
    """Virtual bet placed by RL agent."""

    id: Optional[str] = None
    match_id: str
    placed_at: datetime
    action: BettingAction
    team: str
    odds: float = Field(gt=1.0)
    stake: float = Field(gt=0)
    confidence: Optional[float] = None

    # Settlement
    settled_at: Optional[datetime] = None
    outcome: BetOutcome = BetOutcome.PENDING
    profit_loss: float = 0.0

    # RL metadata
    agent_version: str = ""
    observation_hash: str = ""
    reward: float = 0.0


class PortfolioState(BaseModel):
    """Current portfolio state for the RL agent."""

    initial_balance: float
    current_balance: float
    open_positions: int = 0
    total_exposure: float = 0.0
    session_pnl: float = 0.0
    daily_pnl: float = 0.0
    total_bets: int = 0
    total_wins: int = 0
    consecutive_streak: int = 0  # Positive = wins, negative = losses
    time_since_last_bet: float = 0.0  # Seconds

    @property
    def win_rate(self) -> float:
        return self.total_wins / max(self.total_bets, 1)

    @property
    def roi(self) -> float:
        return self.session_pnl / max(self.initial_balance, 1)

    @property
    def exposure_pct(self) -> float:
        return self.total_exposure / max(self.current_balance, 1)


class GraduationCriterion(BaseModel):
    """Single graduation criterion with current value and threshold."""

    name: str
    value: float
    threshold: float
    met: bool


class GraduationStatus(BaseModel):
    """Overall graduation status."""

    ready: bool = False
    consecutive_days: int = 0
    required_days: int = 14
    criteria: list[GraduationCriterion] = []


class TrainingMetrics(BaseModel):
    """Training progress metrics for dashboard."""

    timestamp: datetime
    episode: int
    total_timesteps: int
    episode_reward: float
    episode_length: int
    win_rate: float = 0.0
    roi: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    policy_loss: float = 0.0
    value_loss: float = 0.0
    entropy: float = 0.0
    agent_version: str = ""


class HealthStatus(BaseModel):
    """System health check response."""

    status: str = "healthy"  # healthy | degraded | unhealthy
    redis: str = "unknown"
    database: str = "unknown"
    scraper_status: str = "unknown"
    scraper_last_update_seconds: Optional[float] = None
    agent_state: AgentState = AgentState.PAUSED
    agent_version: str = ""
    timestamp: datetime
