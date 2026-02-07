# PHOENIX Admin Guide

> A comprehensive guide for system administrators managing PHOENIX via both the dashboard UI and Cursor IDE commands.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Quick Start](#quick-start)
3. [CLI Reference (phoenix.ps1)](#cli-reference)
4. [Cursor Commands Reference](#cursor-commands-reference)
5. [Service Management](#service-management)
6. [Infrastructure](#infrastructure)
7. [Database Administration](#database-administration)
8. [Configuration Reference](#configuration-reference)
9. [Monitoring & Health Checks](#monitoring--health-checks)
10. [API Reference](#api-reference)
11. [Match Filtering](#match-filtering)
12. [RL Training Pipeline](#rl-training-pipeline)
13. [Risk Management](#risk-management)
14. [Docker Deployment](#docker-deployment)
15. [Testing](#testing)
16. [Troubleshooting](#troubleshooting)
17. [Backup & Recovery](#backup--recovery)
18. [Security](#security)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (3000)                       │
│                  Next.js 14 + Tailwind CSS                   │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP + WebSocket
┌────────────────────────┴────────────────────────────────────┐
│                     Backend API (8000)                        │
│               FastAPI + WebSocket Streaming                   │
└───────┬──────────────────┬──────────────────┬───────────────┘
        │                  │                  │
┌───────┴───────┐  ┌───────┴──────┐  ┌───────┴───────┐
│ TimescaleDB   │  │    Redis     │  │  Orchestrator │
│   (5432)      │  │   (6379)     │  │  (background) │
│  Time-series  │  │  Pub/Sub +   │  │  Training     │
│  storage      │  │  Cache       │  │  lifecycle    │
└───────────────┘  └───────┬──────┘  └───────────────┘
                           │
                   ┌───────┴──────┐
                   │   Scraper    │
                   │  (Playwright)│
                   │  LotusBook   │
                   └──────────────┘
```

### Services Overview

| Service | Port | Technology | Purpose |
|---------|------|-----------|---------|
| Frontend | 3000 | Next.js 14, TypeScript, Tailwind | Dashboard UI |
| Backend | 8000 | FastAPI, Python | REST API + WebSocket |
| TimescaleDB | 5432 | PostgreSQL + TimescaleDB | Time-series data storage |
| Redis | 6379 | Redis 7 | Message broker + cache |
| Scraper | -- | Playwright, Python | LotusBook odds collection |
| Orchestrator | -- | Python | Autonomous training loop |
| PgAdmin | 5050 | pgAdmin 4 | Database GUI (dev) |
| Redis Commander | 8081 | redis-commander | Redis GUI (dev) |
| Prometheus | 9090 | Prometheus | Metrics collection |
| Grafana | 3001 | Grafana | Dashboards & alerts |

---

## Quick Start

### First-time Setup

```powershell
# 1. Start infrastructure
.\phoenix.ps1 start -InfraOnly

# 2. Initialize database schema
.\phoenix.ps1 db-init

# 3. Start all services
.\phoenix.ps1 start

# 4. Verify everything is healthy
.\phoenix.ps1 status

# 5. Open the dashboard
.\phoenix.ps1 open
```

### Daily Operations

```powershell
# Check status
.\phoenix.ps1 status

# View logs
.\phoenix.ps1 logs

# Check orchestrator progress
.\phoenix.ps1 orch

# Restart a misbehaving service
.\phoenix.ps1 restart -Service scraper

# Stop everything at end of day (optional -- it's designed to run 24/7)
.\phoenix.ps1 stop
```

---

## CLI Reference

The `phoenix.ps1` script is the primary management tool. Run from the project root.

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `start` | Start services | `.\phoenix.ps1 start` |
| `stop` | Stop services | `.\phoenix.ps1 stop` |
| `status` | Health check all services | `.\phoenix.ps1 status` |
| `logs` | View service logs | `.\phoenix.ps1 logs` |
| `restart` | Restart services | `.\phoenix.ps1 restart` |
| `test` | Run test suites | `.\phoenix.ps1 test` |
| `db-init` | Initialize database | `.\phoenix.ps1 db-init` |
| `open` | Open dashboard page | `.\phoenix.ps1 open` |
| `orch` | Orchestrator status | `.\phoenix.ps1 orch` |

### Start Flags

| Flag | Effect |
|------|--------|
| `-InfraOnly` | Only start Redis + TimescaleDB |
| `-Dev` | Also start PgAdmin (5050) + Redis Commander (8081) |
| `-Monitor` | Also start Prometheus (9090) + Grafana (3001) |
| `-All` | Start everything including dev tools and monitoring |

### Stop Flags

| Flag | Effect |
|------|--------|
| `-KeepInfra` | Stop app services but keep Redis + TimescaleDB running |

### Service-specific Flags

| Flag | Values | Used with |
|------|--------|-----------|
| `-Service <name>` | `backend`, `scraper`, `orchestrator`, `frontend`, `redis`, `timescaledb` | `logs`, `restart` |
| `-Suite <name>` | `unit`, `rl`, `e2e`, `integration` | `test` |
| `-Page <name>` | `dashboard`, `training`, `trading`, `graduation`, `matches`, `advisor`, `api`, `health`, `pgadmin`, `redis`, `prometheus`, `grafana` | `open` |

### Examples

```powershell
# Start with dev tools
.\phoenix.ps1 start -Dev

# View only scraper logs
.\phoenix.ps1 logs -Service scraper

# Restart just the backend
.\phoenix.ps1 restart -Service backend

# Run only unit tests
.\phoenix.ps1 test -Suite unit

# Open the advisor page
.\phoenix.ps1 open -Page advisor

# Stop everything except database
.\phoenix.ps1 stop -KeepInfra
```

---

## Cursor Commands Reference

In Cursor IDE, type `/` in the chat to access these commands:

| Command | Purpose |
|---------|---------|
| `/phoenix-start` | Start all services |
| `/phoenix-stop` | Stop all services |
| `/phoenix-status` | Check service health |
| `/phoenix-open` | Open a dashboard page in browser |
| `/phoenix-logs` | View service logs |
| `/phoenix-restart` | Restart a service |
| `/phoenix-test` | Run test suites |
| `/phoenix-db-init` | Initialize database |
| `/phoenix-orch` | Check orchestrator state |
| `/phoenix-health` | Quick API health check |

### Using Cursor Commands

1. Open the Cursor chat panel.
2. Type `/phoenix-` to see all available commands.
3. Select the command. The AI will execute the appropriate `phoenix.ps1` command or API call.
4. For browser verification, the AI can use the built-in browser tools to navigate and inspect dashboard pages.

### Natural Language Shortcuts

You can also just tell the AI what you want in plain language:

- "start the app" -- runs `.\phoenix.ps1 start`
- "check status" -- runs `.\phoenix.ps1 status`
- "open the advisor page" -- navigates browser to `http://localhost:3000/advisor`
- "show me the scraper logs" -- runs `.\phoenix.ps1 logs -Service scraper`
- "restart the backend" -- runs `.\phoenix.ps1 restart -Service backend`
- "run the tests" -- runs `.\phoenix.ps1 test`

---

## Service Management

### Starting Services

The recommended startup order (handled automatically by `phoenix.ps1 start`):

1. **Infrastructure** -- Redis + TimescaleDB (Docker containers)
2. **Database schema** -- Tables, hypertables, indexes, retention policies
3. **Backend API** -- FastAPI on port 8000 with uvicorn
4. **Scraper** -- Playwright browser scraping LotusBook
5. **Orchestrator** -- Autonomous training lifecycle manager
6. **Frontend** -- Next.js dev server on port 3000

### Stopping Services

```powershell
# Graceful stop (recommended)
.\phoenix.ps1 stop

# Keep database running (faster restart)
.\phoenix.ps1 stop -KeepInfra
```

### Restarting Individual Services

```powershell
.\phoenix.ps1 restart -Service backend       # Restart FastAPI
.\phoenix.ps1 restart -Service scraper       # Restart odds scraper
.\phoenix.ps1 restart -Service orchestrator  # Restart training loop
.\phoenix.ps1 restart -Service frontend      # Restart Next.js
```

### Process Management

Services are managed as PowerShell background jobs. Process IDs are saved to `.phoenix_pids.json` in the project root. If this file is lost, use `.\phoenix.ps1 status` to check what's running, then restart.

---

## Infrastructure

### Redis

```powershell
# Check health
docker exec phoenix-redis redis-cli ping
# Expected: PONG

# Check memory usage
docker exec phoenix-redis redis-cli info memory

# Monitor real-time commands
docker exec -it phoenix-redis redis-cli monitor

# List all keys
docker exec phoenix-redis redis-cli keys "*"
```

**Key Redis keys:**
| Key | Contents |
|-----|----------|
| `active_matches` | JSON list of currently tracked matches |
| `agent:state` | Current agent mode (training/evaluating/paused/live) |
| `agent:version` | Current model version metadata |
| `graduation:status` | Latest graduation evaluation result |
| `orchestrator:state` | Orchestrator lifecycle state |
| `orchestrator:stats` | Training runs, data accumulation stats |
| `advisor:signals` | Current bet suggestions |
| `shadow:performance` | Shadow trading performance (post-graduation) |
| `shadow:drift` | Drift detection status and violations |
| `feature_cache:{match_id}` | Cached feature vectors per match |

**Redis pub/sub channels:**
| Channel | Publisher | Subscriber |
|---------|-----------|------------|
| `match_events` | Scraper | Feature pipeline, WebSocket |
| `match_context` | Enricher | Feature pipeline |
| `rl_actions` | RL Agent | Virtual trading |
| `virtual_outcomes` | Virtual trading | RL Agent |
| `training_progress` | Trainer | Dashboard |
| `advisor_signals` | Orchestrator | Dashboard |

### TimescaleDB

```powershell
# Check health
docker exec phoenix-timescaledb pg_isready -U phoenix

# Connect via psql
docker exec -it phoenix-timescaledb psql -U phoenix -d phoenix_betting

# Check hypertable info
docker exec phoenix-timescaledb psql -U phoenix -d phoenix_betting -c "SELECT * FROM timescaledb_information.hypertables;"

# Check data counts
docker exec phoenix-timescaledb psql -U phoenix -d phoenix_betting -c "
  SELECT 'odds_ticks' as t, count(*) FROM odds_ticks
  UNION ALL SELECT 'match_context', count(*) FROM match_context
  UNION ALL SELECT 'virtual_bets', count(*) FROM virtual_bets
  UNION ALL SELECT 'training_metrics', count(*) FROM training_metrics
  UNION ALL SELECT 'graduation_snapshots', count(*) FROM graduation_snapshots;
"
```

**Database tables (hypertables):**

| Table | Partitioned By | Purpose |
|-------|---------------|---------|
| `odds_ticks` | `time` (1-hour chunks) | Raw odds data from scraper |
| `match_context` | `time` (1-hour chunks) | Match stats from enricher |
| `virtual_bets` | `placed_at` | Virtual bet records |
| `training_metrics` | `time` | RL training progress |
| `graduation_snapshots` | `time` | Daily graduation evaluations |

**Retention policies:**
- `odds_ticks`: 90-day retention (older data automatically removed)

---

## Database Administration

### Initialize Schema

```powershell
.\phoenix.ps1 db-init
```

This creates all tables, converts them to TimescaleDB hypertables, creates indexes, and sets retention policies.

### Manual SQL Access

```powershell
# Via Docker
docker exec -it phoenix-timescaledb psql -U phoenix -d phoenix_betting

# Via PgAdmin (start with -Dev flag)
.\phoenix.ps1 start -Dev
.\phoenix.ps1 open -Page pgadmin
# Login: admin@phoenix.local / admin
```

### Useful Queries

```sql
-- Check how many matches have been collected
SELECT COUNT(DISTINCT match_id) FROM odds_ticks;

-- Check qualifying matches (50+ ticks)
SELECT match_id, COUNT(*) as ticks
FROM odds_ticks
GROUP BY match_id
HAVING COUNT(*) >= 50
ORDER BY ticks DESC;

-- Recent virtual bet performance
SELECT outcome, COUNT(*), SUM(profit_loss)
FROM virtual_bets
WHERE placed_at > NOW() - INTERVAL '7 days'
GROUP BY outcome;

-- Daily P&L
SELECT DATE(placed_at) as day, SUM(profit_loss) as pnl
FROM virtual_bets
GROUP BY day
ORDER BY day DESC
LIMIT 14;

-- Chunk info (TimescaleDB)
SELECT * FROM timescaledb_information.chunks
ORDER BY range_start DESC
LIMIT 10;
```

---

## Configuration Reference

All configuration is in `shared/config.py` with environment variable overrides.

### Database (`POSTGRES_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DB` | `phoenix_betting` | Database name |
| `POSTGRES_USER` | `phoenix` | Database user |
| `POSTGRES_PASSWORD` | `phoenix_secure_2026` | Database password |

### Redis (`REDIS_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_DB` | `0` | Redis database number |

### Scraper (`SCRAPER_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `SCRAPER_BETTING_SITE_URL` | `https://lotusbook.site/cricket` | Target betting site |
| `SCRAPER_HEADLESS` | `true` | Run browser headless |
| `SCRAPER_POLL_INTERVAL` | `3` | Seconds between DOM polls |
| `SCRAPER_MAX_RETRIES` | `3` | Retry attempts for failed scrapes |
| `SCRAPER_SESSION_TIMEOUT` | `30000` | Browser session timeout (ms) |
| `SCRAPER_INTERNATIONAL_ONLY` | `true` | Filter for international matches |

### RL Agent (`RL_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `RL_TRAINING_MODE` | `offline` | Training mode (offline/online/eval) |
| `RL_MODEL_PATH` | `models/best_model.zip` | Path to save/load model |
| `RL_STARTING_BANKROLL` | `100000` | Virtual starting bankroll |
| `RL_GRADUATION_ENABLED` | `true` | Enable graduation checks |
| `RL_LEARNING_RATE` | `0.0003` | PPO learning rate |
| `RL_TOTAL_TIMESTEPS` | `500000` | Total training steps |
| `RL_OBSERVATION_SIZE` | `66` | Feature vector dimension |
| `RL_N_STEPS` | `2048` | PPO steps per update |
| `RL_BATCH_SIZE` | `64` | PPO mini-batch size |
| `RL_MIN_MATCHES_TO_TRAIN` | `30` | Min matches before training starts |
| `RL_NIGHTLY_RETRAIN_STEPS` | `50000` | Incremental steps per nightly run |
| `RL_AUTO_ADVANCE_CURRICULUM` | `true` | Auto-advance curriculum stages |
| `RL_DATA_LOOKBACK_DAYS` | `90` | Days of data to load for training |
| `RL_MIN_TICKS_PER_MATCH` | `50` | Min ticks per match for training |

### API (`API_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8000` | Backend API port |
| `API_CORS_ORIGINS` | `http://localhost:3000,...` | Allowed CORS origins |
| `API_AUTH_ENABLED` | `false` | Enable API key auth |
| `API_API_KEY` | `change_me_in_production` | API key (when auth enabled) |

### Logging (`LOG_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `LOG_FORMAT` | `json` | Log format (json/console) |

### Overriding Configuration

Set environment variables before starting services:

```powershell
# Via environment variable
$env:RL_MIN_MATCHES_TO_TRAIN = 10
$env:SCRAPER_POLL_INTERVAL = 5

# Via .env file (edit project root .env)
RL_MIN_MATCHES_TO_TRAIN=10
SCRAPER_POLL_INTERVAL=5
```

---

## Monitoring & Health Checks

### Quick Health Check

```powershell
.\phoenix.ps1 status
```

### API Health Endpoint

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

Returns:
```json
{
  "status": "healthy",
  "redis": "connected",
  "database": "connected",
  "scraper_status": "active",
  "scraper_last_update_seconds": 3.2,
  "agent_state": "training",
  "agent_version": "ppo_v1_500k",
  "timestamp": "2026-02-07T..."
}
```

**Status values:**
- `healthy` -- Everything working.
- `degraded` -- One component has issues.
- `unhealthy` -- Multiple components down.

### Prometheus Metrics (with -Monitor flag)

```powershell
.\phoenix.ps1 start -Monitor
.\phoenix.ps1 open -Page prometheus   # http://localhost:9090
.\phoenix.ps1 open -Page grafana      # http://localhost:3001 (admin/phoenixadmin)
```

### Orchestrator Status

```powershell
.\phoenix.ps1 orch
```

Shows lifecycle state, model version, curriculum stage, accumulation progress, and graduation status.

---

## API Reference

### All Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info |
| GET | `/api/health` | System health check |
| GET | `/api/matches/active` | Active matches list |
| GET | `/api/matches/odds/{match_id}?limit=100` | Odds history for a match |
| GET | `/api/matches/context/{match_id}?limit=100` | Match context history |
| GET | `/api/training/metrics?limit=200` | Training metrics |
| GET | `/api/training/summary` | Training summary stats |
| GET | `/api/agent/state` | Agent state and version |
| GET | `/api/agent/bets?limit=50` | Virtual bet history |
| GET | `/api/agent/performance` | Performance summary |
| GET | `/api/graduation/status` | Graduation criteria status |
| GET | `/api/graduation/history?limit=30` | Graduation snapshots |
| GET | `/api/advisor/state` | Orchestrator lifecycle state |
| GET | `/api/advisor/signals` | Current bet suggestions |
| GET | `/api/advisor/stats` | Orchestrator statistics |
| GET | `/api/advisor/shadow/performance` | Shadow trading performance metrics |
| GET | `/api/advisor/shadow/drift` | Drift detection status |
| POST | `/api/advisor/demote` | Manually demote agent to virtual trading |
| WS | `/ws` | WebSocket real-time stream |
| GET | `/docs` | Swagger API documentation |

### Testing Endpoints

```powershell
# With Python
python -c "import httpx; print(httpx.get('http://localhost:8000/api/health', timeout=5).json())"

# With PowerShell
Invoke-RestMethod http://localhost:8000/api/health

# With curl
curl http://localhost:8000/api/health
```

---

## Match Filtering

The `MatchClassifier` in `scraper/match_filter.py` ensures only relevant matches are collected.

### Layer 1: Competition Name

**Whitelisted patterns:**
- ICC events: `ICC`, `World Cup`, `Champions Trophy`, `WTC`
- International formats: `T20I`, `ODI`, `Test`
- Franchise leagues: `IPL`, `BBL`, `PSL`, `CPL`, `The Hundred`, `SA20`, `MLC`, `ILT20`, `BPL`, `LPL`
- Domestic first-class: `Ranji Trophy`, `Sheffield Shield`, `County Championship`

**Blacklisted patterns (always rejected):**
- `SRL`, `Simulated`, `Virtual`, `Esports`, `Cyber`, `Fantasy`, `Practice`, `Warm-up`

### Layer 2: Team Name Database

If the competition name doesn't match a whitelist pattern, the classifier checks both team names against a database of ~200 known teams:

- **ICC Full Members (12):** India, Australia, England, Pakistan, South Africa, New Zealand, West Indies, Sri Lanka, Bangladesh, Afghanistan, Ireland, Zimbabwe
- **ICC Associate Members:** Nepal, USA, Netherlands, Scotland, Namibia, Oman, UAE, Canada, Hong Kong, Papua New Guinea, etc.
- **Franchise teams:** All teams from IPL, BBL, PSL, CPL, The Hundred, SA20, MLC

### Toggling the Filter

```powershell
# Disable filtering (collect all matches)
$env:SCRAPER_INTERNATIONAL_ONLY = "false"

# Re-enable
$env:SCRAPER_INTERNATIONAL_ONLY = "true"
```

---

## RL Training Pipeline

### Observation Space (66 features)

| Group | Count | Features |
|-------|-------|----------|
| Raw Odds | 12 | Back/lay prices, implied probs, overround, spreads |
| Momentum | 16 | Velocity, acceleration, volatility, EMA crossover, max swing |
| Market Microstructure | 8 | Spread width/velocity, efficiency, price levels, staleness |
| Raw Match Stats | 8 | is_live, overs, wickets, score, run_rate, req_rr, innings, balls |
| Temporal | 6 | Cyclical hour/day, match_minutes, tick_interval |
| Portfolio State | 8 | Bankroll, exposure, streak, win_rate, P&L |
| Statistical Patterns | 8 | Z-scores, percentiles, autocorrelation, trend strength |

### Action Space (7 actions)

| Action | ID | Stake |
|--------|----|-------|
| HOLD | 0 | -- |
| BACK_HOME_SM | 1 | 1% bankroll |
| BACK_HOME_LG | 2 | 3% bankroll |
| BACK_AWAY_SM | 3 | 1% bankroll |
| BACK_AWAY_LG | 4 | 3% bankroll |
| LAY_HOME_SM | 5 | 1% bankroll |
| LAY_AWAY_SM | 6 | 1% bankroll |

### Curriculum Stages

| Stage | Name | Allowed Actions | Graduation Criteria |
|-------|------|----------------|-------------------|
| 1 | Pattern Recognition | HOLD, BACK_HOME_SM | Win rate > 52%, 100+ episodes |
| 2 | Full Actions | All 7 | ROI > 5%, Win rate > 53%, 200+ episodes |
| 3 | Live Simulation | All 7 | ROI > 8%, Win rate > 55%, Sharpe > 1.0, 200+ episodes |
| 4 | Adversarial | All 7 | ROI > 5%, Win rate > 52%, 100+ episodes |

### Graduation Criteria

All must be met for **14 consecutive days:**

| Criterion | Threshold | Window |
|-----------|-----------|--------|
| Win Rate | > 55% | Last 200 bets |
| ROI | > 8% | Last 200 bets |
| Sharpe Ratio | > 1.5 | Last 30 days |
| Max Drawdown | < 15% | Last 30 days |
| Profitable Days | > 10 | Last 14 days |
| Bet Volume | > 100 bets | Last 30 days |

### Post-Graduation Lifecycle

After graduation, the agent enters a continuous loop:

```
GRADUATED
  |
  +-- Generate advisor signals (every 60s)
  +-- Place shadow bets on each signal
  +-- Settle shadow bets when matches complete
  +-- Publish shadow performance to Redis
  +-- Check drift detection (daily)
  |     |
  |     +-- If drift detected for 5 days -> Auto-demote to VIRTUAL_TRADING
  |
  +-- Nightly retraining continues (01:30 UTC)
  +-- Nightly evaluation continues (02:00 UTC)
```

The agent never stops learning. Nightly retraining keeps the model fresh with the latest match data, and shadow trading ensures the agent's real-world performance is continuously validated.

---

## Shadow Trading & Drift Detection

### What is Shadow Trading?

After the agent graduates, it doesn't just generate signals -- it also places virtual "shadow bets" on every recommendation. This creates a live parallel P&L that validates the agent's post-graduation performance.

**Key Redis keys:**

| Key | Contents |
|-----|----------|
| `shadow:performance` | Full shadow portfolio metrics, calibration, daily history |
| `shadow:drift` | Drift detection status, violations, demotion flag |

### Drift Detection

The system checks shadow performance daily against floor thresholds:

| Metric | Floor | Description |
|--------|-------|-------------|
| Rolling win rate | > 50% | Over last 100 shadow bets |
| Rolling ROI | > -2% | Over last 100 shadow bets |
| Sharpe ratio | > 0.50 | From daily shadow P&L |
| Drawdown | < 20% | From shadow peak balance |

If **any** threshold is breached, a drift warning fires. If drift persists for **5 consecutive days**, the agent is **automatically demoted** back to virtual trading.

### Auto-Demotion

When auto-demotion triggers:
1. Orchestrator state reverts to `virtual_trading`.
2. Agent state is set to `virtual_trading` with reason `performance_drift`.
3. Advisor signals are cleared.
4. The agent must re-graduate (14 consecutive days meeting all criteria).
5. Nightly retraining continues, helping the agent adapt to changed conditions.
6. The `demotions` counter in orchestrator stats increments.

### Manual Demotion

Admins can manually demote the agent at any time:

```powershell
# Via API
Invoke-RestMethod -Method POST http://localhost:8000/api/advisor/demote

# Via dashboard
# Navigate to Advisor page -> Admin Controls -> "Demote to Virtual Trading"
```

### Monitoring Shadow Performance

```powershell
# Check shadow performance
Invoke-RestMethod http://localhost:8000/api/advisor/shadow/performance

# Check drift status
Invoke-RestMethod http://localhost:8000/api/advisor/shadow/drift
```

### Confidence Calibration

The shadow trader tracks how well the agent's confidence scores predict actual outcomes. The calibration data is available at `/api/advisor/shadow/performance` in the `calibration` field, showing actual win rate per confidence bucket (30-40%, 40-50%, ..., 80-100%).

A well-calibrated agent should have higher actual win rates for higher confidence buckets. If calibration is off, it indicates the agent is overconfident or underconfident in certain ranges.

### Configuring Drift Thresholds

Drift constants are in `shared/constants.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `DRIFT_WIN_RATE_FLOOR` | 0.50 | Minimum rolling win rate |
| `DRIFT_ROI_FLOOR` | -0.02 | Minimum rolling ROI |
| `DRIFT_LOOKBACK_BETS` | 100 | Rolling window size (bets) |
| `DRIFT_LOOKBACK_DAYS` | 7 | Rolling window size (days) |
| `DRIFT_DEMOTION_CONSECUTIVE_DAYS` | 5 | Days of drift before auto-demotion |

---

## Risk Management

The risk manager enforces limits that protect the virtual (and eventually real) bankroll.

### Limits

| Rule | Limit | What Happens |
|------|-------|-------------|
| Per-bet size | Max 5% of bankroll | Bet rejected if exceeds |
| Match exposure | Max 20% per match | No more bets on that match |
| Total exposure | Max 50% of bankroll | All new bets blocked |
| Daily loss | Max 10% daily loss | Trading halted for the day |
| Weekly loss | Max 20% weekly loss | Trading halted for the week |
| Max drawdown | Max 15% from peak | Graduation fails |
| Bet frequency | Max 15 bets/hour | Additional bets delayed |
| Circuit breaker | 5 consecutive losses | Trading halted until reset |

---

## Docker Deployment

### Docker Compose Profiles

```powershell
# Core infrastructure only
docker compose up -d redis timescaledb

# App services (includes infra)
docker compose --profile dev up -d

# With monitoring
docker compose --profile dev --profile monitor up -d
```

### Container Names

| Container | Image | Notes |
|-----------|-------|-------|
| `phoenix-redis` | redis:7-alpine | 512MB max memory |
| `phoenix-timescaledb` | timescale/timescaledb:latest-pg15 | Persistent volume |
| `phoenix-backend` | Custom (Dockerfile.backend) | FastAPI |
| `phoenix-scraper` | Custom (Dockerfile.scraper) | Playwright |
| `phoenix-rl-trainer` | Custom (Dockerfile.rl) | Manual training |
| `phoenix-orchestrator` | Custom (Dockerfile.rl) | Autonomous lifecycle |
| `phoenix-frontend` | Custom (frontend/Dockerfile) | Next.js |
| `phoenix-pgadmin` | dpage/pgadmin4 | Dev profile only |
| `phoenix-redis-commander` | rediscommander/redis-commander | Dev profile only |
| `phoenix-prometheus` | prom/prometheus:v2.50.0 | Monitor profile |
| `phoenix-grafana` | grafana/grafana:10.3.0 | Monitor profile |

### Networks

| Network | Services | Purpose |
|---------|----------|---------|
| `phoenix-data` | Redis, TimescaleDB, Scraper, PgAdmin | Data layer |
| `phoenix-app` | Backend, Frontend, Redis, TimescaleDB | Application layer |
| `phoenix-monitor` | Prometheus, Grafana, Backend | Monitoring |

### Resource Allocation (recommended)

| Service | RAM | CPU |
|---------|-----|-----|
| TimescaleDB | 2 GB | 2 cores |
| Redis | 512 MB | 1 core |
| Backend API | 1 GB | 1 core |
| Scraper | 1 GB | 1 core |
| Orchestrator/Trainer | 4 GB | 2+ cores |
| Frontend | 512 MB | 1 core |
| **Total** | **~10 GB** | **8+ cores** |

---

## Testing

### Running Tests

```powershell
.\phoenix.ps1 test                # All tests
.\phoenix.ps1 test -Suite unit    # Unit tests only
.\phoenix.ps1 test -Suite rl      # RL environment tests
.\phoenix.ps1 test -Suite e2e     # End-to-end browser tests
.\phoenix.ps1 test -Suite integration  # Integration tests
```

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   ├── test_parser.py       # LotusBook parser tests
│   ├── test_features.py     # Feature extraction tests
│   ├── test_reward.py       # Reward function tests
│   └── test_portfolio.py    # Portfolio management tests
├── rl/
│   └── test_environment.py  # RL environment sanity tests
├── e2e/
│   └── test_lotusbook_scraper.py  # Browser-based tests
└── integration/             # Integration tests
```

### What Each Suite Tests

| Suite | Tests | Requires |
|-------|-------|----------|
| `unit` | Parser, features, reward, portfolio | Python packages only |
| `rl` | Environment reset/step, action/observation spaces | Gymnasium, SB3 |
| `e2e` | LotusBook site loads, scraper extracts data, API endpoints | Backend running |
| `integration` | Cross-service communication | All services |

---

## Troubleshooting

### Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| Backend won't start | Port 8000 in use | `Get-NetTCPConnection -LocalPort 8000` and kill process |
| DB connection failed | Wrong password | Check `POSTGRES_PASSWORD` env var matches docker-compose |
| Redis timeout | Redis not running | `docker exec phoenix-redis redis-cli ping` |
| Frontend compile error | Missing npm packages | `cd frontend && npm install` |
| Scraper fails | Site structure changed | Check `scraper/parsers/lotusbook_parser.py` |
| Orchestrator stuck | Corrupt state in Redis | `docker exec phoenix-redis redis-cli DEL orchestrator:state` |
| Agent wrongly demoted | Drift detection too sensitive | Increase `DRIFT_DEMOTION_CONSECUTIVE_DAYS` or lower `DRIFT_WIN_RATE_FLOOR` |
| Shadow perf not updating | Shadow trader not running | Check orchestrator is in `graduated` state |
| Tests fail after model change | Stale schema | Drop tables and re-run `.\phoenix.ps1 db-init` |
| No matches appearing | Filter too strict | Set `SCRAPER_INTERNATIONAL_ONLY=false` temporarily |

### Resetting the System

```powershell
# Full reset (preserves odds data)
.\phoenix.ps1 stop
docker exec phoenix-redis redis-cli FLUSHALL
.\phoenix.ps1 start

# Nuclear reset (destroys all data)
.\phoenix.ps1 stop
docker compose down -v    # Removes volumes
.\phoenix.ps1 start -InfraOnly
.\phoenix.ps1 db-init
.\phoenix.ps1 start
```

### Checking Logs

```powershell
# All recent logs
.\phoenix.ps1 logs

# Specific service
.\phoenix.ps1 logs -Service scraper
.\phoenix.ps1 logs -Service backend
.\phoenix.ps1 logs -Service orchestrator

# Docker container logs
docker logs phoenix-redis --tail 50
docker logs phoenix-timescaledb --tail 50
```

---

## Backup & Recovery

### Database Backup

```powershell
# Backup TimescaleDB
docker exec phoenix-timescaledb pg_dump -U phoenix phoenix_betting > backup_$(Get-Date -Format "yyyy-MM-dd").sql

# Restore
docker exec -i phoenix-timescaledb psql -U phoenix phoenix_betting < backup_2026-02-07.sql
```

### Model Checkpoints

Model files are saved to `models/` directory. The best model is at `models/best_model.zip`. Checkpoints are saved during training to `models/checkpoints/`.

```powershell
# Back up current best model
Copy-Item models/best_model.zip models/backup_best_model.zip
```

### Redis Backup

Redis is configured with append-only file (AOF) persistence. Data survives container restarts via the `redis_data` Docker volume.

---

## Security

### Checklist

- [ ] Change `POSTGRES_PASSWORD` from default in `.env`
- [ ] Change `API_KEY` from default if enabling auth
- [ ] Never commit `.env` to git (already in `.gitignore`)
- [ ] Enable `API_AUTH_ENABLED=true` in production
- [ ] Restrict `API_CORS_ORIGINS` to specific domains
- [ ] Model files should not be committed to git
- [ ] Use strong, unique passwords for PgAdmin and Grafana

### Anti-Detection (Scraper)

The scraper includes these anti-detection measures:
- Random delays (5-15 seconds) before bet placement
- Stake noise (+/-20%) on every bet
- Win rate capped at 58% (higher triggers bookmaker detection)
- Randomized viewport sizes and user agents
- Never bets immediately after an odds change

### Network Isolation

Docker networks provide isolation:
- `phoenix-data`: Only data services can access the database and Redis directly.
- `phoenix-app`: Application services communicate here.
- `phoenix-monitor`: Monitoring tools are separated.
