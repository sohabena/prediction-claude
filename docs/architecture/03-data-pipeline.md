# PHOENIX Data Pipeline Architecture

## Overview

The data pipeline is the foundation of PHOENIX. Every decision the RL agent makes depends on the quality, freshness, and richness of the data flowing through the system.

---

## Pipeline Stages

```
Stage 1: Collection       Stage 2: Normalization    Stage 3: Storage
┌─────────────────┐      ┌──────────────────┐      ┌─────────────┐
│ LotusBook Site  │ ───► │ LotusBookParser  │ ───► │ TimescaleDB │
│ (Playwright)    │      │ (normalize data) │      │ (odds_ticks)│
└─────────────────┘      └──────────────────┘      └─────────────┘
                                 │
┌─────────────────┐              │                  ┌─────────────┐
│ Cricbuzz API    │ ─────────────┤                  │    Redis    │
│ (REST scraping) │              │                  │ (pub/sub)   │
└─────────────────┘              │                  └─────────────┘
                                 ▼
Stage 4: Enrichment       Stage 5: Features         Stage 6: Consumption
┌──────────────────┐     ┌──────────────────┐      ┌─────────────┐
│ DataEnricher     │ ──► │ FeaturePipeline  │ ───► │ RL Agent    │
│ (merge odds +    │     │ (compute obs     │      │ (Gymnasium  │
│  cricket stats)  │     │  vector)         │      │  env)       │
└──────────────────┘     └──────────────────┘      └─────────────┘
```

---

## Stage 1: Collection

### LotusBook Scraper Architecture

```python
class ScraperManager:
    """
    Orchestrates LotusBook scraping.
    
    Responsibilities:
    - Manage Playwright browser instances
    - Coordinate scraper workers per match
    - Handle session/login management
    - Publish events to Redis
    - Write to TimescaleDB
    """

class ScraperWorker:
    """
    Individual match scraper using Playwright + CDP.
    
    Two data extraction modes:
    1. WebSocket interception (preferred - lowest latency)
    2. DOM polling fallback (every 3 seconds)
    """
```

### LotusBook Page Structure

Based on observed site structure at `https://lotusbook.site/cricket`:

```
Page Layout:
├── Header (Login/Navigation)
├── Sports Navigation (Cricket selected)
├── In Play Section
│   ├── Match Card
│   │   ├── Team Names (Home vs Away)
│   │   ├── Competition Badge (e.g., "ICC Men's T20 World Cup")
│   │   ├── 1X2 Odds Grid
│   │   │   ├── Column 1 (Home): Back Price | Lay Price
│   │   │   ├── Column X (Draw): Back Price | Lay Price
│   │   │   └── Column 2 (Away): Back Price | Lay Price
│   │   └── "LIVE" indicator
│   └── ... more match cards
├── Upcoming Events Section
│   └── Same structure but with scheduled time
└── Bet Slip (sidebar)
```

### Normalized Event Schema

```python
@dataclass
class OddsEvent:
    """Normalized event from LotusBook scraper."""
    
    # Identity
    match_id: str              # Unique match identifier
    source: str                # "lotusbook"
    timestamp: datetime        # UTC timestamp
    
    # Teams
    team_home: str             # Home team name
    team_away: str             # Away team name
    competition: str           # Tournament/league name
    
    # Odds (1X2 format)
    back_home: Optional[float]
    lay_home: Optional[float]
    back_draw: Optional[float]
    lay_draw: Optional[float]
    back_away: Optional[float]
    lay_away: Optional[float]
    
    # Match state
    is_live: bool
    scheduled_time: Optional[datetime]
    
    # Metadata
    scrape_method: str         # "websocket" or "dom"
    scrape_latency_ms: int     # Time to extract data
```

---

## Stage 2: Normalization

### LotusBook Parser

```python
class LotusBookParser:
    """
    Parse raw scraper data into normalized OddsEvent format.
    
    Handles:
    - DOM data (HTML elements → structured data)
    - WebSocket frames (JSON → structured data)
    - Data validation (required fields, valid ranges)
    - Match ID generation and caching
    - Odds format normalization (ensure decimal)
    """
    
    VALID_ODDS_RANGE = (1.01, 1000.0)  # Sanity check
    
    def parse_dom_data(self, raw: Dict) -> List[OddsEvent]: ...
    def parse_websocket_data(self, frame: Dict) -> List[OddsEvent]: ...
    def validate_event(self, event: OddsEvent) -> bool: ...
```

### Validation Rules

1. **Odds must be > 1.0** (decimal format)
2. **Back price <= Lay price** (arbitrage check)
3. **Team names non-empty** and consistent
4. **Timestamp within 5 seconds** of current time
5. **No duplicate events** (fingerprint check)

---

## Stage 3: Storage

### TimescaleDB Schema

```sql
-- Main odds time series table
CREATE TABLE odds_ticks (
    time        TIMESTAMPTZ NOT NULL,
    match_id    TEXT NOT NULL,
    team_home   TEXT NOT NULL,
    team_away   TEXT NOT NULL,
    competition TEXT,
    
    -- 1X2 Odds
    back_home   DOUBLE PRECISION,
    lay_home    DOUBLE PRECISION,
    back_draw   DOUBLE PRECISION,
    lay_draw    DOUBLE PRECISION,
    back_away   DOUBLE PRECISION,
    lay_away    DOUBLE PRECISION,
    
    -- Derived
    implied_prob_home  DOUBLE PRECISION,
    implied_prob_away  DOUBLE PRECISION,
    overround          DOUBLE PRECISION,
    
    -- Match state
    is_live     BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    source      TEXT DEFAULT 'lotusbook',
    
    UNIQUE (time, match_id)
);

-- Convert to hypertable
SELECT create_hypertable('odds_ticks', 'time', chunk_time_interval => INTERVAL '1 hour');

-- Indexes for fast RL queries
CREATE INDEX idx_odds_match_time ON odds_ticks (match_id, time DESC);
CREATE INDEX idx_odds_live ON odds_ticks (is_live, time DESC);

-- Match context from Cricbuzz
CREATE TABLE match_context (
    time        TIMESTAMPTZ NOT NULL,
    match_id    TEXT NOT NULL,
    score       INTEGER,
    wickets     INTEGER,
    overs       DOUBLE PRECISION,
    run_rate    DOUBLE PRECISION,
    req_run_rate DOUBLE PRECISION,
    innings     INTEGER,
    batting_team TEXT,
    bowling_team TEXT,
    last_event  TEXT,  -- 'wicket', 'boundary', 'dot', etc.
    
    UNIQUE (time, match_id)
);

SELECT create_hypertable('match_context', 'time', chunk_time_interval => INTERVAL '1 hour');

-- Virtual bets
CREATE TABLE virtual_bets (
    id          SERIAL PRIMARY KEY,
    placed_at   TIMESTAMPTZ NOT NULL,
    match_id    TEXT NOT NULL,
    action      TEXT NOT NULL,  -- 'BACK_HOME', 'LAY_AWAY', etc.
    team        TEXT NOT NULL,
    odds        DOUBLE PRECISION NOT NULL,
    stake       DOUBLE PRECISION NOT NULL,
    confidence  DOUBLE PRECISION,
    
    -- Settlement
    settled_at  TIMESTAMPTZ,
    outcome     TEXT,  -- 'win', 'loss', 'void'
    profit_loss DOUBLE PRECISION,
    
    -- RL metadata
    agent_version   TEXT,
    observation_hash TEXT,
    reward          DOUBLE PRECISION
);

SELECT create_hypertable('virtual_bets', 'placed_at');

-- RL training metrics
CREATE TABLE training_metrics (
    time            TIMESTAMPTZ NOT NULL,
    episode         INTEGER,
    total_timesteps BIGINT,
    episode_reward  DOUBLE PRECISION,
    episode_length  INTEGER,
    win_rate        DOUBLE PRECISION,
    roi             DOUBLE PRECISION,
    sharpe_ratio    DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    policy_loss     DOUBLE PRECISION,
    value_loss      DOUBLE PRECISION,
    entropy         DOUBLE PRECISION,
    agent_version   TEXT
);

SELECT create_hypertable('training_metrics', 'time');

-- Graduation tracking
CREATE TABLE graduation_snapshots (
    time                TIMESTAMPTZ NOT NULL,
    win_rate            DOUBLE PRECISION,
    roi                 DOUBLE PRECISION,
    sharpe_ratio        DOUBLE PRECISION,
    max_drawdown        DOUBLE PRECISION,
    profitable_days     INTEGER,
    total_bets          INTEGER,
    all_criteria_met    BOOLEAN,
    consecutive_days    INTEGER
);

SELECT create_hypertable('graduation_snapshots', 'time');

-- Data retention policies
SELECT add_retention_policy('odds_ticks', INTERVAL '90 days');
SELECT add_compression_policy('odds_ticks', INTERVAL '7 days');
```

### Redis Channels & Keys

```
# Pub/Sub Channels
match_events        → Real-time odds from scraper
match_context       → Cricket stats from enricher
rl_actions          → Agent decisions
virtual_outcomes    → Bet settlements
training_progress   → Training metrics for dashboard

# Key-Value
active_matches              → JSON list of live matches
agent:state                 → Current agent mode (training/eval/live)
agent:version               → Current model version
feature_cache:{match_id}    → Cached feature vector
graduation:status           → Current graduation progress
graduation:history          → Last 30 days of snapshots
```

---

## Stage 4: Enrichment

### Cricket Stats Enricher

```python
class CricbuzzEnricher:
    """
    Enriches odds data with live cricket match context.
    
    Data source: Cricbuzz (public cricket scores)
    Poll interval: 10 seconds
    
    Provides:
    - Live score, wickets, overs
    - Run rate, required run rate
    - Match phase (powerplay/middle/death)
    - Recent events (wickets, boundaries)
    """
```

### Match ID Mapping

Challenge: LotusBook and Cricbuzz use different match identifiers.

Solution:
```python
class MatchMapper:
    """
    Maps LotusBook match IDs to Cricbuzz match IDs.
    
    Strategy:
    1. Fuzzy match team names (e.g., "India" ↔ "IND")
    2. Match scheduled time (±30 minutes)
    3. Cache mapping for duration of match
    """
```

---

## Stage 5: Feature Computation

### Feature Pipeline

```python
class FeaturePipeline:
    """
    Computes RL observation vector from raw data. DATA-ONLY approach.
    
    Input: match_id + latest OddsEvent + MatchContext
    Output: np.ndarray of shape (66,)
    
    Feature groups (66 total, all data-backed):
    1. Raw odds (12) -- prices, implied probs, overround, spreads
    2. Odds momentum (16) -- velocity, acceleration, volatility (math on prices)
    3. Market microstructure (8) -- spread dynamics, efficiency, staleness
    4. Raw match statistics (8) -- is_live, overs, wickets, score, run_rate, innings
    5. Temporal (6) -- cyclical time encoding
    6. Portfolio state (8) -- bankroll, exposure, streak, win_rate
    7. Statistical patterns (8) -- z-scores, trend strength, autocorrelation
    """
    
    def compute(self, match_id: str, event: OddsEvent, 
                context: Optional[MatchContext] = None,
                portfolio: PortfolioState = None) -> np.ndarray:
        
        features = []
        
        # Fetch recent history for momentum computation
        history = self.fetch_recent_ticks(match_id, lookback_seconds=120)
        
        features.extend(self.compute_odds_features(event))
        features.extend(self.compute_momentum_features(history))
        features.extend(self.compute_microstructure_features(event, history))
        features.extend(self.compute_context_features(context))
        features.extend(self.compute_temporal_features(event))
        features.extend(self.compute_portfolio_features(portfolio))
        features.extend(self.compute_historical_features(match_id, event))
        
        # Normalize
        obs = np.array(features, dtype=np.float32)
        obs = self.normalizer.transform(obs)
        
        return obs
```

### Feature Normalization

- Z-score normalization for continuous features
- Min-max scaling for bounded features (probabilities)
- Cyclical encoding for time features (sin/cos)
- Running statistics updated online (no look-ahead bias)

---

## Stage 6: Consumption

### RL Agent Consumption

The RL agent receives observations and returns actions:

```python
# In the training loop
obs = feature_pipeline.compute(match_id, event, context, portfolio)
action, _ = agent.predict(obs, deterministic=False)  # Stochastic during training

# In evaluation/live
action, _ = agent.predict(obs, deterministic=True)   # Deterministic in production
```

### Dashboard Consumption

The frontend receives data via:
1. **WebSocket** - Real-time odds, signals, training progress
2. **REST API** - Historical data, analytics, graduation status
3. **Redis subscription** - Agent state changes

---

## Data Quality Monitoring

### Metrics to Track

| Metric | Threshold | Alert |
|--------|-----------|-------|
| Scraper uptime | > 99% during matches | Scraper down > 30s |
| Data freshness | < 5 seconds | Stale data > 10s |
| Parse success rate | > 95% | Error rate > 10% |
| Feature completeness | 100% required fields | Missing critical field |
| Odds range validity | 1.01 - 1000.0 | Out of range |
| Enrichment match rate | > 80% | Low match rate |

### Data Lineage

Every piece of data is traceable:
```
OddsEvent → fingerprint → TimescaleDB (with scrape metadata)
                ↓
         FeatureVector → observation_hash → RL action → virtual_bet
                                                            ↓
                                                     outcome → reward
```
