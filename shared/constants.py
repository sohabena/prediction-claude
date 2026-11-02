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
KEY_WATCHED_MATCH_ID = "demo:watched_match_id"  # User-selected match for watch mode
KEY_AGENT_STATE = "agent:state"                # Current agent mode
KEY_AGENT_VERSION = "agent:version"            # Current model version
KEY_GRADUATION_STATUS = "graduation:status"    # Current graduation progress
KEY_FEATURE_CACHE = "feature_cache:{match_id}" # Cached feature vector per match
KEY_MATCH_CONTEXT = "match_context:{match_id}" # Latest match context per match (TTL 60s)

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

OBSERVATION_SIZE = 50           # Feature vector dimension (lean expert trading set)
                                # Groups: odds(7) + momentum(6) + market(5) +
                                #   match_stats(7) + portfolio(5) + position(4) +
                                #   volume(4) + bookmaker(4) + format(4) + timing(2) + match_momentum(2)
ACTION_SPACE_SIZE = 9           # Number of discrete actions (HOLD + 4 BACK + 4 LAY)
SMALL_STAKE_PERCENT = 0.02     # 2% of bankroll ($2,000 on 100k budget)
LARGE_STAKE_PERCENT = 0.04     # 4% of bankroll ($4,000 on 100k budget)

# ============================================================
# Graduation Criteria
# ============================================================

# "op" is the comparison of metric vs threshold: ge (>=), gt (>), le (<=).
# win_rate is deliberately absent: it is meaningless for a back/lay trading book.
# sharpe_ratio is a NON-annualised per-calendar-day Sharpe (zero days included).
# The 0.10 Sharpe threshold is a PLACEHOLDER: calibrate from the S18 report before real money.
GRADUATION_CRITERIA = {
    "roi_on_turnover": {"threshold": 0.02, "op": "ge", "window_days": 30},
    "sharpe_ratio": {"threshold": 0.10, "op": "ge", "window_days": 30},
    "max_drawdown": {"threshold": 0.15, "op": "le", "window_days": 30},
    "profitable_days": {"threshold": 10, "op": "ge", "window_days": 14},
    "bet_volume": {"threshold": 100, "op": "ge", "window_days": 30},
    "min_matches": {"threshold": 30, "op": "ge", "window_days": 30},
    "avg_clv": {"threshold": 0.0, "op": "gt", "window_days": 30},  # mean CLV (prob points) > 0
    "clv_ci_lower": {"threshold": 0.0, "op": "gt", "window_days": 30},  # bootstrap 95% CI lower bound > 0
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
# Match Results
# ============================================================

CHANNEL_MATCH_RESULTS = "match_results"  # Result collector -> Orchestrator
KEY_MATCH_RESULTS = "match:results"      # Latest match results cache

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

# ============================================================
# Anti-Detection (Bookmaker Avoidance)
# ============================================================

BET_DELAY_MIN_SECONDS = 5      # Min random delay before bet placement
BET_DELAY_MAX_SECONDS = 15     # Max random delay before bet placement
STAKE_NOISE_PERCENT = 0.20     # +/-20% random noise on stake (avoids robotic pattern)

# Bet volume constraints & 20% opportunity tolerance buffer
MAX_BETS_PER_MATCH = 25        # Base target: 20 to 25 bets per match
BETS_TOLERANCE_PERCENT = 0.20  # +20% opportunity tolerance buffer (+5 bets)
MAX_BETS_WITH_TOLERANCE = int(MAX_BETS_PER_MATCH * (1.0 + BETS_TOLERANCE_PERCENT))  # 30 bets ceiling
MIN_BET_INTERVAL_SECONDS = 15  # Baseline cooldown (shortened during high-volatility events)
STAKE_ROUND_BUCKETS = [50, 100, 200, 500, 1000, 2000, 5000]  # Human-like round amounts

# Capital commitment & 20% opportunity tolerance buffer.
# The per-match budget is a percentage of the CURRENT bankroll (not an absolute amount) and
# is measured in worst-case LIABILITY. The tolerance buffer is usable only by bets that
# reduce the match's worst-case loss (hedges / green-ups).
PER_MATCH_BUDGET_PCT = 0.10    # Per-match budget = 10% of current bankroll
BUDGET_TOLERANCE_PERCENT = 0.20  # +20% tolerance, liability-reducing bets only
SMALL_STAKE_BUDGET_FRACTION = 0.15  # SM action targets 15% of match budget as liability
LARGE_STAKE_BUDGET_FRACTION = 0.30  # LG action targets 30% of match budget as liability

# Liability-based risk limits (fractions of current bankroll)
MAX_BET_LIABILITY_PCT = 0.03    # Max liability of any single bet
MAX_MATCH_LIABILITY_PCT = 0.10  # Max worst-case loss on one match
MAX_TOTAL_LIABILITY_PCT = 0.30  # Max worst-case loss summed over all open matches
MAX_TEAM_LIABILITY_PCT = 0.12   # Max summed worst-case loss over matches involving one team
KELLY_FRACTION = 0.25           # Fractional-Kelly multiplier (used only with a calibrated p)
CIRCUIT_BREAKER_COOLDOWN_MINUTES = 60  # Breaker clears this long after the last loss
RESERVATION_TTL_SECONDS = 120   # Un-committed liability reservations expire after this
PAYOUT_HEADROOM_FACTOR = 1.5   # Allow up to 150% of budget if potential payouts justify it

# ============================================================
# Team Name Databases (shared across scraper + feature extractors)
# ============================================================

# ICC Full Member national teams + common aliases
INTERNATIONAL_TEAMS: set[str] = {
    # Full Members (12)
    "india", "ind", "team india",
    "australia", "aus",
    "england", "eng",
    "pakistan", "pak",
    "south africa", "sa", "rsa", "proteas",
    "new zealand", "nz", "black caps", "blackcaps",
    "west indies", "wi", "windies",
    "sri lanka", "sl",
    "bangladesh", "ban", "bd",
    "afghanistan", "afg",
    "ireland", "ire",
    "zimbabwe", "zim",
    # Associate Members (commonly seen on betting sites)
    "nepal", "nep",
    "usa", "united states",
    "netherlands", "ned",
    "scotland", "sco",
    "namibia", "nam",
    "oman", "oma",
    "uae", "united arab emirates",
    "canada", "can",
    "hong kong", "hk",
    "papua new guinea", "png",
    "jersey", "jer",
    "uganda", "uga",
    "kenya", "ken",
    "bermuda",
}

# Major franchise team names (IPL, BBL, PSL, CPL, Hundred, SA20, etc.)
FRANCHISE_TEAMS: dict[str, set[str]] = {
    "IPL": {
        "mumbai indians", "mi",
        "chennai super kings", "csk",
        "royal challengers", "rcb", "royal challengers bengaluru",
        "kolkata knight riders", "kkr",
        "sunrisers hyderabad", "srh",
        "rajasthan royals", "rr",
        "delhi capitals", "dc",
        "punjab kings", "pbks",
        "lucknow super giants", "lsg",
        "gujarat titans", "gt",
    },
    "BBL": {
        "sydney sixers", "sixers",
        "sydney thunder", "thunder",
        "melbourne stars", "stars",
        "melbourne renegades", "renegades",
        "brisbane heat", "heat",
        "perth scorchers", "scorchers",
        "hobart hurricanes", "hurricanes",
        "adelaide strikers", "strikers",
    },
    "PSL": {
        "karachi kings",
        "lahore qalandars", "qalandars",
        "islamabad united",
        "peshawar zalmi", "zalmi",
        "quetta gladiators", "gladiators",
        "multan sultans", "sultans",
    },
    "CPL": {
        "trinbago knight riders", "tkr",
        "guyana amazon warriors", "amazon warriors",
        "jamaica tallawahs", "tallawahs",
        "barbados royals", "royals",
        "st kitts and nevis patriots", "patriots",
        "st lucia kings",
    },
    "Hundred": {
        "oval invincibles", "invincibles",
        "trent rockets", "rockets",
        "southern brave", "brave",
        "birmingham phoenix", "phoenix",
        "manchester originals", "originals",
        "london spirit", "spirit",
        "northern superchargers", "superchargers",
        "welsh fire", "fire",
    },
    "SA20": {
        "sunrisers eastern cape",
        "mi cape town",
        "joburg super kings",
        "paarl royals",
        "durban super giants",
        "pretoria capitals",
    },
    "MLC": {
        "los angeles knight riders", "la knight riders",
        "mi new york",
        "san francisco unicorns",
        "seattle orcas",
        "texas super kings",
        "washington freedom",
    },
}
