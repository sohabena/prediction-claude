"""
PHOENIX Shared Constants
Central definition for Redis channels, keys, and system-wide constants.
"""

# ============================================================
# Redis Pub/Sub Channels
# ============================================================

CHANNEL_MATCH_EVENTS = "match_events"          # Scraper -> Feature Pipeline, Cortex
CHANNEL_MATCH_CONTEXT = "match_context"        # Enricher -> Feature Pipeline
CHANNEL_RL_ACTIONS = "rl_actions"              # RL Agent -> Virtual Trading
CHANNEL_VIRTUAL_OUTCOMES = "virtual_outcomes"  # Virtual Trading -> RL Agent
CHANNEL_TRAINING_PROGRESS = "training_progress"  # RL Trainer -> Dashboard

# ============================================================
# Redis Keys
# ============================================================

KEY_ACTIVE_MATCHES = "active_matches"          # JSON list of live matches
KEY_AGENT_STATE = "agent:state"                # Current agent mode
KEY_AGENT_VERSION = "agent:version"            # Current model version
KEY_GRADUATION_STATUS = "graduation:status"    # Current graduation progress
KEY_FEATURE_CACHE = "feature_cache:{match_id}" # Cached feature vector per match

# ============================================================
# Risk Management Limits
# ============================================================

MAX_BET_PERCENT = 0.05          # Max 5% of bankroll per bet
MAX_MATCH_EXPOSURE_PERCENT = 0.20  # Max 20% exposed to single match
MAX_TOTAL_EXPOSURE_PERCENT = 0.50  # Max 50% of bankroll at risk
MAX_DAILY_LOSS_PERCENT = 0.10   # Halt if down 10% in a day
MAX_WEEKLY_LOSS_PERCENT = 0.20  # Halt if down 20% in a week
MAX_DRAWDOWN_PERCENT = 0.15     # Max drawdown before graduation fails
MAX_BETS_PER_HOUR = 15          # Rate limit
CIRCUIT_BREAKER_CONSECUTIVE = 5 # Consecutive losses to trigger halt

# ============================================================
# RL Agent Configuration
# ============================================================

OBSERVATION_SIZE = 66           # Feature vector dimension (data-only, no heuristics)
ACTION_SPACE_SIZE = 7           # Number of discrete actions
SMALL_STAKE_PERCENT = 0.01     # 1% of bankroll
LARGE_STAKE_PERCENT = 0.03     # 3% of bankroll

# ============================================================
# Graduation Criteria
# ============================================================

GRADUATION_CRITERIA = {
    "win_rate": {"threshold": 0.55, "window": 200},
    "roi": {"threshold": 0.08, "window": 200},
    "sharpe_ratio": {"threshold": 1.5, "window_days": 30},
    "max_drawdown": {"threshold": 0.15, "window_days": 30},
    "profitable_days": {"threshold": 10, "window_days": 14},
    "bet_volume": {"threshold": 100, "window_days": 30},
}
GRADUATION_REQUIRED_DAYS = 14  # All criteria met for N consecutive days

# ============================================================
# Orchestrator States
# ============================================================

ORCHESTRATOR_STATE_ACCUMULATING = "accumulating"
ORCHESTRATOR_STATE_OFFLINE_TRAINING = "offline_training"
ORCHESTRATOR_STATE_ONLINE_TRAINING = "online_training"
ORCHESTRATOR_STATE_VIRTUAL_TRADING = "virtual_trading"
ORCHESTRATOR_STATE_GRADUATED = "graduated"

KEY_ORCHESTRATOR_STATE = "orchestrator:state"     # Current orchestrator lifecycle state
KEY_ORCHESTRATOR_STATS = "orchestrator:stats"     # Accumulation / training stats
KEY_ADVISOR_SIGNALS = "advisor:signals"           # Current bet suggestions
KEY_SHADOW_PERFORMANCE = "shadow:performance"     # Shadow trader post-graduation performance
KEY_DRIFT_STATUS = "shadow:drift"                 # Drift detection status
CHANNEL_ADVISOR_SIGNALS = "advisor_signals"       # Real-time advisor signals

# ============================================================
# Drift Detection Thresholds (Post-Graduation)
# ============================================================

DRIFT_WIN_RATE_FLOOR = 0.50         # Drift if rolling win rate drops below 50%
DRIFT_ROI_FLOOR = -0.02             # Drift if rolling ROI drops below -2%
DRIFT_LOOKBACK_BETS = 100           # Rolling window for drift checks (bets)
DRIFT_LOOKBACK_DAYS = 7             # Rolling window for drift checks (days)
DRIFT_DEMOTION_CONSECUTIVE_DAYS = 5 # Auto-demote after N consecutive drift days

# ============================================================
# Data Validation
# ============================================================

VALID_ODDS_MIN = 1.01
VALID_ODDS_MAX = 1000.0
MAX_DATA_AGE_SECONDS = 5       # Reject stale data
SCRAPER_TARGET_LATENCY_MS = 500  # p95 target
