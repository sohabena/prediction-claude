# PHOENIX Admin & Operations Guide

## Overview

This guide provides system administrators and quant engineers with full operational documentation for running, monitoring, tuning, and maintaining the PHOENIX cricket prediction and active two-way rate trading platform.

---

## 1. System Architecture & Topology

```mermaid
flowchart TD
    subgraph "External Feeds"
        A[LotusBook Live: lotusbook.cc/cricket]
        B[Official Cricsheet Archives: 19k+ Matches]
        C[Weekly Cricsheet Delta Sync: recently_added_7_json.zip]
    end

    subgraph "Core Daemons"
        D[Scraper Manager<br/>python -m scraper.manager]
        E[RL Orchestrator<br/>python -m rl.orchestrator]
        F[FastAPI Backend<br/>port 8001]
        G[Next.js Dashboard<br/>port 3000]
    end

    subgraph "Infrastructure"
        H[(TimescaleDB :5432<br/>Database: phoenix_betting)]
        I[(Redis :6379<br/>Broker & Shared State)]
    end

    subgraph "12-Specialist Model Zoo"
        J[Format & Gender Router<br/>rl/model_zoo.py]
        K[12 PPO Specialists<br/>models/specialists/*.zip]
    end

    A --> D
    B -->|scripts/batch_cricsheet_historical_importer.py| H
    C -->|scripts/weekly_cricsheet_sync_and_retrain.py| K
    D --> I
    D --> H
    I --> E
    H --> E
    E --> J
    J --> K
    F --> H
    F --> I
    F --> J
    G --> F
```

---

## 2. Infrastructure & Credentials

| Component | Default Host & Port | Credentials / Target | Notes |
|---|---|---|---|
| **TimescaleDB** | `localhost:5432` | `user=phoenix`<br/>`password=phoenix_secure_2026`<br/>`database=phoenix_betting` | Hypertable partitioned time-series storage. |
| **Redis** | `localhost:6379` | `db=0` (no auth by default) | Pub/Sub message broker & real-time state. |
| **Backend API** | `localhost:8001` | — | FastAPI application gateway (`backend.main:app`). |
| **Frontend UI** | `localhost:3000` | — | Next.js 14 reactive dashboard. |
| **Target Bookmaker** | Web | `https://lotusbook.cc/cricket` | CDP WebSocket interception + DOM polling. |
| **Model Zoo Storage** | Filesystem | `models/specialists/` | 12 dedicated PPO checkpoints for format/gender. |

---

## 3. PowerShell CLI (`phoenix.ps1`) Reference

All operational tasks can be managed using the root PowerShell harness:

| Command | Description |
|---|---|
| `.\phoenix.ps1 start` | Start all infrastructure and application daemons. |
| `.\phoenix.ps1 start -InfraOnly` | Start only TimescaleDB and Redis containers. |
| `.\phoenix.ps1 start -Dev` | Start services with developer UI tools (pgAdmin, Redis Commander). |
| `.\phoenix.ps1 stop` | Gracefully shut down all background processes and containers. |
| `.\phoenix.ps1 stop -KeepInfra` | Stop application processes while keeping TimescaleDB and Redis running. |
| `.\phoenix.ps1 status` | Health check all services, ports, and background tasks. |
| `.\phoenix.ps1 logs` | Aggregate tail of recent logs across all running components. |
| `.\phoenix.ps1 logs -Service scraper` | Tail logs specifically for the Scraper Manager. |
| `.\phoenix.ps1 orch` | Print current RL Orchestrator lifecycle state from Redis. |
| `.\phoenix.ps1 sync` | Download latest Cricsheet matches and fine-tune specialist models. |
| `.\phoenix.ps1 db-init` | Run database schema initialization and migrations. |
| `.\phoenix.ps1 backup` | Dump `phoenix_betting` database to a timestamped `.sql` archive. |
| `.\phoenix.ps1 restore` | Restore database from the most recent backup archive. |

---

## 4. Key Configuration Parameters (`shared/config.py` & `.env`)

| Parameter | Environment Variable | Default Value | Description |
|---|---|---|---|
| **Per-Match Budget** | `PER_MATCH_BUDGET` | `100_000` | Hard capital commitment per match ($100k / ₹1,00,000). |
| **Max Bets Per Match** | `MAX_BETS_PER_MATCH` | `30` | Maximum trades executed per match (target: 20–30). |
| **Max Bets Per Hour** | `MAX_BETS_PER_HOUR` | `30` | Rate-limiting ceiling to prevent high-frequency churn. |
| **Small Stake %** | `SMALL_STAKE_PERCENT`| `0.02` | Small bet stake: 2% of budget ($2,000). |
| **Large Stake %** | `LARGE_STAKE_PERCENT`| `0.04` | Large bet stake: 4% of budget ($4,000). |
| **Observation Size** | `OBSERVATION_SIZE` | `50` | Exact feature vector dimension. |
| **Scraper Target URL**| `SCRAPER_BETTING_SITE_URL` | `https://lotusbook.cc/cricket` | Live exchange scraping target. |
| **Auto-Approve Matches**| `SCRAPER_AUTO_APPROVE_MATCHES` | `false` | Controls whether Tier-1 matches auto-approve (`false` = manual approval). |
| **International Only** | `SCRAPER_INTERNATIONAL_ONLY` | `true` | Restricts discovery strictly to verified international & Tier-1 matches. |
| **Min Quality Score** | `DATA_QUALITY_MIN_SCORE` | `0.70` | Threshold for approving matches into training. |
| **Exchange Commission**| `EXCHANGE_COMMISSION`| `0.02` | 2.0% Betfair commission on gross winnings. |
| **Max Lay Odds** | `MAX_LAY_ODDS` | `2.10` | Hard cap on Lay execution to eliminate catastrophic tail risk. |

> [!NOTE]
> Scraper auto-approval and international filtering can be dynamically toggled at runtime via Redis keys `config:scraper:auto_approve` and `config:scraper:international_only`, or via `GET/POST /api/matches/settings` without restarting daemons.

---

## 5. REST API Endpoint Reference (Backend Gateway `:8001`)

### Health & System Status
- `GET /api/health` — Checks database, Redis, and worker connectivity.
- `GET /api/advisor/state` — Returns current RL Orchestrator lifecycle state.
- `GET /api/advisor/stats` — Returns match accumulation and training statistics.

### 12-Specialist Model Zoo & Advisor
- `GET /api/advisor/specialists` — Returns detailed catalog of all 12 specialist models, including active status, holdout P&L, win rate, Sharpe ratio, and training date.
- `GET /api/advisor/signals` — Broadcasts real-time trading recommendations tagged with the specific routed specialist.
- `POST /api/advisor/demote` — Manually demotes an agent back to `VIRTUAL_TRADING` for retraining.

### Automated Weekly Cricsheet Synchronization
- `GET /api/training/cricsheet-sync/status` — Returns synchronization status (last sync timestamp, matches processed, specialists updated, status: `idle` or `running`).
- `POST /api/training/cricsheet-sync/trigger` — Manually triggers background download of `recently_added_7_json.zip` and incremental specialist fine-tuning.

### Matches & Data Validation
- `GET /api/matches/settings` — Returns runtime scraper governance settings (`auto_approve`, `international_only`).
- `POST /api/matches/settings` — Updates runtime scraper settings with instant propagation across scraper workers.
- `GET /api/matches/active` — Lists all currently live cricket matches, enriched with real-time Back/Lay odds, scorecard text, and tick counts.
- `GET /api/matches/training-status` — Lists all matches with two-phase scrape/training status.
- `PATCH /api/matches/{id}/approve-scrape` — Approves a discovered match for live odds tick collection.
- `PATCH /api/matches/{id}/reject-scrape` — Skips/rejects a match from live scraping.
- `GET /api/matches/{id}/validate` — Executes `EpisodeQualityGate` validation and returns a diagnostic report.
- `PATCH /api/matches/{id}/approve` — Approves a match for RL training (validates automatically).
- `PATCH /api/matches/{id}/reject` — Rejects a match from training.
- `DELETE /api/matches/{id}` — Deletes a rejected match and its associated data.
- `POST /api/matches/{id}/result` — Manually submits a match result (winner, margin, result type).
- `GET /api/matches/{id}/closing_odds` — Retrieves closing odds and calculates Closing Line Value (CLV).

### Virtual Trading & RL Performance
- `GET /api/agent/bets` — Retrieves historical virtual trades from `virtual_bets`.
- `GET /api/agent/performance` — Returns aggregate P&L, win rate, Sharpe ratio, and profit factor.
- `GET /api/training/metrics` — Returns episodic training curves (reward, loss, entropy).
- `GET /api/graduation/status` — Returns rolling 14-day graduation evaluation status.

---

## 6. Model Training & Harmonization Runbook

### 1. Bulk Historical Harmonization (19,494 Matches)
To re-process raw Cricsheet JSON archives into the 12 specialist datasets in `data/specialists/`:
```powershell
$env:PYTHONPATH = "c:\Users\sohai\Downloads\prediction-claude"
$env:PYTHONHOME = "C:\Users\sohai\AppData\Local\Programs\Python\Python313"
& "C:\Python313\python.exe" scripts/batch_cricsheet_historical_importer.py
```

### 2. Training the 12 Specialist Models
To train or re-evaluate all 12 specialist models with holdout validation:
```powershell
$env:PYTHONPATH = "c:\Users\sohai\Downloads\prediction-claude"
$env:PYTHONHOME = "C:\Users\sohai\AppData\Local\Programs\Python\Python313"
& "C:\Python313\python.exe" scripts/train_and_evaluate_all_specialists.py
```

### 3. Executing Weekly Cricsheet Sync & Retraining
To manually pull recent matches from Cricsheet and incrementally update models:
```powershell
.\phoenix.ps1 sync
```
Or execute directly via Python:
```powershell
$env:PYTHONPATH = "c:\Users\sohai\Downloads\prediction-claude"
$env:PYTHONHOME = "C:\Users\sohai\AppData\Local\Programs\Python\Python313"
& "C:\Python313\python.exe" scripts/weekly_cricsheet_sync_and_retrain.py
```

### 4. Background Scheduled Automation
The RL Orchestrator daemon (`rl/orchestrator.py`) includes a weekly cron schedule that runs `weekly_cricsheet_sync_and_retrain.py` every Monday at 03:00 UTC. When new matches are detected, it:
1. Deduplicates against previously seen match signatures (`team1_vs_team2_date`).
2. Appends new matches to the relevant specialist archive in `data/specialists/`.
3. Performs 4,000 steps of incremental PPO fine-tuning on the affected specialist.
4. Updates `models/specialists/catalog.json` and notifies the running Model Zoo.
