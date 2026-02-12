---
description: Master rule for PHOENIX project. Always applied. Ground truth for architecture, file paths, data flows, and engineering standards.
globs: ["**/*"]
---
# PHOENIX — Master Project Rule

## Goal
Build an RL-based cricket betting system that scrapes live odds from LotusBook, trains a PPO agent through virtual betting, and graduates to live execution only after proven profitability.

**Data-only philosophy:** features are raw data or mathematical transformations. No cricket heuristics. The agent discovers all patterns from data.

## Lifecycle States
The system progresses through these states automatically:
```
ACCUMULATING → OFFLINE_TRAINING → ONLINE_TRAINING → VIRTUAL_TRADING → GRADUATED
```
Current state is stored in Redis key `orchestrator:state`. The orchestrator (`rl/orchestrator.py`) manages all transitions.

## Actual Project Structure (verified)
```
shared/
├── schemas.py          # OddsEvent, MatchContext, VirtualBet, MatchResult, etc.
├── config.py           # Pydantic Settings (get_settings())
├── constants.py        # Redis channels/keys, risk limits, graduation criteria
├── redis_client.py     # ReliableRedis with retry/dead letter
├── db.py               # Async SQLAlchemy engine + session factory
└── logging.py          # structlog JSON logging

scraper/
├── manager.py          # Orchestrates scraping: discovery → approval → live-only collection
├── worker.py           # Playwright DOM polling + WebSocket interception
├── match_filter.py     # MatchClassifier (international cricket filter)
├── live_match_tracker.py  # Derives MatchContext from LotusBook data inline
├── lotus_result_detector.py  # Detects match results from LotusBook odds data
└── parsers/
    └── lotusbook_parser.py  # Raw DOM → OddsEvent normalization

features/
├── pipeline.py         # 74-dim observation vector (float32)
├── data_quality.py     # TickValidator + EpisodeQualityGate
├── normalizer.py       # Online z-score normalization
├── store.py            # Feature caching (Redis)
└── extractors/         # odds, momentum, market, match_stats, temporal, portfolio, statistical, category

rl/
├── environment.py      # CricketBettingEnv (Gymnasium, 74-obs, 9-action)
├── agent.py            # PhoenixAgent (PPO wrapper)
├── reward.py           # 6-component reward function
├── trainer.py          # Offline + online training, saves to models/best_model.zip
├── orchestrator.py     # Autonomous lifecycle manager (1070 lines)
├── graduation.py       # GraduationEvaluator (14 consecutive days)
├── curriculum.py       # 4-stage curriculum
├── data_loader.py      # MatchDataLoader (TimescaleDB → episodes)
└── callbacks.py        # Training callbacks

virtual_trading/
├── engine.py           # VirtualBetEngine (slippage, rejection, cooldown)
├── portfolio.py        # PortfolioManager (balance, exposure, risk)
├── settlement.py       # SettlementEngine (result-based, CLV)
├── risk_manager.py     # RiskManager (per-bet, exposure, drawdown limits)
├── shadow_trader.py    # Post-graduation shadow validation + drift detection
└── live_trading_loop.py # Live virtual trading on Redis match events

backend/
├── main.py             # FastAPI app (CORS, routers, lifespan)
├── db_init.py          # Schema creation + migrations
├── models/             # SQLAlchemy ORM: odds, match, match_status, bets, results, training, graduation
└── routers/            # health, matches, training, agent, graduation, advisor, demo, websocket_router

frontend/
├── app/
│   ├── page.tsx              # Dashboard (lifecycle state, accumulation, stats)
│   ├── training/page.tsx     # Training metrics + charts
│   ├── trading/page.tsx      # Virtual bet history + performance
│   ├── graduation/page.tsx   # Graduation criteria progress
│   ├── matches/page.tsx      # Match discovery + scrape/training approval
│   ├── matches/[id]/watch/   # Single-match bet watcher
│   └── advisor/page.tsx      # Graduated agent signals + shadow trading + drift
├── components/         # StatCard, HealthStatus, Sidebar, TrainingChart, etc.
├── hooks/useApi.ts     # Polling data fetcher
└── lib/api.ts          # API_BASE constant
```

## Critical Data Flows

### Match Lifecycle
```
LotusBook page → worker.py extracts DOM → parser normalizes → manager receives OddsEvent[]
  ├── Discovery: new match_id → DB insert (scrape_status=discovered)
  ├── User approves match on /matches page → scrape_status=scrape_approved
  ├── LIVE GATE: ticks only stored when is_live=true AND approved AND not completed
  ├── Stored: odds_ticks table (TimescaleDB) + Redis publish (match_events channel)
  ├── Match ends: result_collector finds result → match_results table → _completed_match_ids
  └── Training: user approves for RL → training_status=approved → data_loader loads episodes
```

### is_live Detection (LotusBook-specific)
The scraper JavaScript walks up the DOM to find the section header:
- Match under "In Play" section + score pattern (e.g. "45/2") → `is_live = true`
- Match under "Upcoming Events" section OR has date pattern → `is_live = false`
- Matches not seen in current scrape batch → `is_live = false` (staleness cleanup)
- Matches not seen for 2+ hours → removed from active_matches entirely

### Redis Keys & Channels
| Key/Channel | Purpose | Producer | Consumer |
|---|---|---|---|
| `match_events` (channel) | Live odds ticks | scraper | orchestrator, live_trading_loop |
| `match_results` (channel) | Completed match results | result_collector | orchestrator, scraper |
| `active_matches` (key) | Current match list + is_live | scraper | backend API → frontend |
| `orchestrator:state` (key) | Current lifecycle state | orchestrator | backend API → frontend |
| `training:progress` (key) | Training step progress | trainer | backend API → frontend |

## Tech Stack
- **Python 3.11+**, FastAPI, SQLAlchemy async, **Pydantic v2** (use `pattern=` not `regex=`)
- **RL:** Stable-Baselines3, Gymnasium, PyTorch
- **Scraping:** Playwright (Chromium, DOM polling + CDP WebSocket)
- **DB:** TimescaleDB (time-series), Redis 7 (pub/sub + JSON cache)
- **Frontend:** Next.js 14, Tailwind CSS, Recharts, TypeScript
- **Logging:** structlog (JSON), never print()

## Engineering Standards

### Imports & Shared Code
- All schemas: `from shared.schemas import OddsEvent, VirtualBet, ...`
- All config: `from shared.config import get_settings`
- All constants: `from shared.constants import CHANNEL_MATCH_EVENTS, ...`
- All DB: `from shared.db import get_session`
- All Redis: `from shared.redis_client import get_redis`
- **Never** duplicate schemas, constants, or config across services.

### Database Contracts
- Every column referenced in Python must exist in the SQLAlchemy model AND the DB.
- When adding columns: update the ORM model, add a migration in `db_init.py`, update any queries.
- Use `on_conflict_do_update` for upserts. Always specify `index_elements`.

### Frontend-Backend Contract
- Every field the frontend reads must be returned by the API endpoint.
- Every API endpoint must handle the case where data is empty/null gracefully.
- The frontend must never show stale or misleading state (see UI rules below).

### RL Standards
- Observation: fixed 74 floats, dtype=float32, no NaN.
- Actions: 9 discrete (HOLD + 4 BACK + 4 LAY). HOLD should be ~90%.
- Reward: 6 components (P&L, patience, overtrading, risk, drawdown, Sharpe). No cricket heuristics.
- Curriculum: 4 stages. Graduation: all criteria met for 14 consecutive days.
- Model saved to `models/best_model.zip` after every training run.
