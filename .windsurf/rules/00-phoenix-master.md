---
description: Master rule for PHOENIX project. Always applied. Ground truth for architecture, file paths, data flows, and engineering standards.
globs: ["**/*"]
trigger: model_decision
---
# PHOENIX — Master Project Rule

## Goal
Build an RL-based cricket betting intelligence and two-way rate trading system that scrapes live odds from LotusBook, trains a portfolio of format-, league-, and gender-specialized PPO agents on institutional historical exchange microstructure, and suggests high-EV bets post-graduation with continuous shadow validation.

**Data-only philosophy:** features are raw exchange microstructure or mathematical transformations. No cricket heuristics. The agent discovers all patterns from data.

## Lifecycle States
The system progresses through these states automatically:
```
ACCUMULATING → OFFLINE_TRAINING → ONLINE_TRAINING → VIRTUAL_TRADING → GRADUATED
```
Current state is stored in Redis key `orchestrator:state`. The orchestrator (`rl/orchestrator.py`) manages all transitions and triggers weekly automated synchronization with Cricsheet every Monday at 03:00 UTC.

## Format Specialist Model Zoo (12 Models)
Rather than a single monolithic model, PHOENIX utilizes a dynamic 12-model portfolio partitioned by format, league, and gender:
1. `ipl`: Indian Premier League (Men)
2. `bbl_men`: Big Bash League (Men)
3. `wbbl_women`: Women's Big Bash League
4. `t20i_men`: Men's T20 International
5. `t20i_women`: Women's T20 International
6. `odi_men`: Men's One-Day International
7. `odi_women`: Women's One-Day International
8. `test_men`: Men's Test Matches
9. `t20_blast`: T20 Blast & Domestic T20
10. `franchise_t20`: Global Franchise T20 (CPL, PSL, BPL, etc.)
11. `first_class`: First-Class Multi-Day (County Championship)
12. `list_a`: List A Domestic 50-Over (Royal London, Vijay Hazare)

Each specialist operates on calibrated par curves (Women's T20: 136 rpo vs Men's: 168 rpo vs IPL: 188 rpo) and wicket sensitivity decays (1.35x decay in Women's cricket).

## Scraper Architecture & Ingestion Governance
- **Two-Phase Gating:**
  1. *Scrape Gate* (`scrape_status`): `discovered` (default, zero tick writes) → `scrape_approved` (user or auto) → `scrape_rejected`.
  2. *Live Gate*: Only collect ticks while `is_live=True AND scrape_approved AND not completed`.
  3. *Schedule Gate*: Wait until 10 minutes before match scheduled start time.
  4. *Training Gate* (`training_status`): Post-match quality score $\ge 0.70$ before ingesting to RL corpus.
- **Layer 0 Blacklist:** `\bsrl\b`, `simulated`, `cyber`, `virtual`, `esport` simultaneously checked against competition, home team, and away team. Simulated Reality League matches can NEVER be auto-approved.
- **Strict Verification:** `MatchClassifier.is_international_or_major()` guards auto-approval; non-international/SRL fixtures always evaluate to `False`.
- **Runtime Dynamic Controls:** Redis keys `config:scraper:auto_approve` (`false` by default, manual mode) and `config:scraper:international_only` (`true` by default) control dynamic filtering without service restarts.
- **Stale Clean-up:** Inactive matches dropped after 120s; worker auto-reloads page every 100 polls or 5 consecutive zero polls; cache refreshes every 5s.

## Actual Project Structure (verified)
```
shared/
├── schemas.py          # OddsEvent, MatchContext, VirtualBet, MatchResult, BettingAction
├── config.py           # Pydantic Settings (get_settings())
├── constants.py        # Redis channels/keys, risk limits, graduation criteria (OBSERVATION_SIZE=50)
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
├── pipeline.py         # 50-dim observation vector (float32 lean expert trading set)
├── data_quality.py     # TickValidator + EpisodeQualityGate
├── normalizer.py       # Online z-score normalization (Welford)
├── store.py            # Feature caching (Redis)
└── extractors/         # odds(7), momentum(6), market(5), match_stats(7), portfolio(5), position(4), volume(4), bookmaker(4), format(4), timing(2), match_momentum(2)

rl/
├── model_zoo.py        # ModelZoo registry & dynamic format/gender router
├── environment.py      # CricketBettingEnv (Gymnasium, 50-obs, 9-action, real settlement)
├── agent.py            # PhoenixAgent (PPO wrapper)
├── reward.py           # 6-component reward function (EV, green-up, drawdown penalty)
├── trainer.py          # Offline + online training, saves to models/best_model.zip
├── orchestrator.py     # Autonomous lifecycle coordinator with weekly Cricsheet sync
├── graduation.py       # GraduationEvaluator (14 consecutive days)
├── curriculum.py       # 4-stage curriculum
├── data_loader.py      # MatchDataLoader (TimescaleDB → episodes)
└── lifecycle/          # Extracted domain handlers: advisor, drift, graduation, training

virtual_trading/
├── engine.py           # VirtualBetEngine (slippage, rejection, cooldown)
├── portfolio.py        # PortfolioManager (balance, exposure, risk, greening-up)
├── settlement.py       # SettlementEngine (result-based real settlement, CLV)
├── risk_manager.py     # RiskManager (per-bet 2-4%, max match 20%, max DD 15%)
├── shadow_trader.py    # Post-graduation shadow validation + drift detection
└── live_trading_loop.py # Live virtual trading on Redis match events

scripts/
├── prepare_specialist_datasets.py   # Extracts 19,494 matches into 12 specialist archives
├── train_format_specialists.py      # Trains & holdout tests 12 specialists (zero lookahead)
└── weekly_cricsheet_sync_and_retrain.py # Automated weekly delta sync & PPO fine-tuning

backend/
├── main.py             # FastAPI app (CORS, routers, lifespan)
├── db_init.py          # Schema creation + migrations
├── models/             # SQLAlchemy ORM: odds, match, match_status, bets, results, training, graduation
└── routers/            # health, matches, training, agent, graduation, advisor, demo, websocket_router

frontend/
├── app/
│   ├── page.tsx              # Dashboard (lifecycle state, accumulation, stats)
│   ├── training/page.tsx     # Training metrics + weekly Cricsheet sync panel
│   ├── trading/page.tsx      # Virtual bet history + performance
│   ├── graduation/page.tsx   # Graduation criteria progress
│   ├── matches/page.tsx      # Match discovery + scrape/training approval
│   ├── matches/[id]/watch/   # Single-match bet watcher
│   └── advisor/page.tsx      # 12-Specialist Model Zoo portfolio cards + shadow trading
├── components/         # StatCard, HealthStatus, Sidebar, TrainingChart, SignalCard
├── hooks/useApi.ts     # Polling data fetcher
└── lib/api.ts          # API_BASE constant
```

## Critical Data Flows

### Match Lifecycle
```
LotusBook / Cricsheet → parser normalizes → OddsEvent[] / Ticks
  ├── Live Gate: ticks stored when is_live=true AND approved AND not completed
  ├── Storage: odds_ticks table (TimescaleDB) + Redis publish (match_events)
  ├── Model Routing: ModelZoo maps match to specialist key (ipl, t20i_men, t20i_women, etc.)
  ├── Trading Engine: 20-30 bets/match, $100k budget, exchange liability limit <= 2.10 lay
  └── Settlement: Real settlement upon result discovery, computing CLV against closing line
```

### Strictly Causal Zero-Lookahead Policy
Trading decisions MUST evaluate strictly against $(innings, overs, score, wickets, target)$ at observation time $t$. No forward access to match outcome (`winner`, `_meta`) is permitted.
