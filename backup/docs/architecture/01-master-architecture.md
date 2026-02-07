# Cricket Betting Intelligence System - Master Architecture Design
## Collaborative Design Session (All Personas)

_This document synthesizes the architectural vision from the entire cross-functional team: Betting Expert, Quant Analyst, Data Engineer, Full Stack Dev, and DevOps._

---

## Document Status
- **Version:** 1.0
- **Last Updated:** December 6, 2025
- **Contributors:** All 5 Personas

---

## Table of Contents
1. Strategic Core (The "Why" & "What")
2. Data Pipeline Architecture (The "How")
3. Processing & Analysis Engine (The "Brain")
4. Application Layer (The "Delivery")
5. Execution Roadmap

---

## 1. Strategic Core (The "Why" & "What")
**Contributors:** *Betting Expert & Quant Analyst*

### A. Core Hypotheses to Automate

The system is designed to validate and alert on specific market inefficiencies.

#### Hypothesis 1: The "Panic Wicket" Rebound
- **Hypothesis:** Bookmakers often over-adjust odds against the batting team immediately after a wicket falls in the middle overs (7-15), especially if the Required Run Rate (RRR) is still manageable (< 8.0).
- **Rationale:** Markets react emotionally to wickets, but statistical analysis shows partnerships often stabilize when RRR remains reasonable.
- **Quant Task:** Measure the average "odds drift" post-wicket vs. the actual win probability shift based on historical data.
- **Expected Edge:** 5-8% value opportunity window lasting 15-30 seconds.

#### Hypothesis 2: The "Death Over" Undervaluation
- **Hypothesis:** In T20s, betting algorithms underestimate the scoring potential of "set" batsmen (30+ runs scored, SR > 140) in the last 4 overs.
- **Rationale:** Bookmaker models use average scoring rates, not player-specific form and momentum.
- **Quant Task:** Correlate "Batsman Strike Rate > 150" + "Overs Remaining < 4" with "Session Runs" outcomes.
- **Expected Edge:** 10-15% on session markets when conditions align.

#### Hypothesis 3: Momentum Shift Detection
- **Hypothesis:** A sudden tight over (maiden or < 3 runs) followed by a boundary often signals a counter-attack pattern that markets misprice.
- **Rationale:** Batsmen shift from defensive to aggressive mode, but markets remain anchored to the tight over.
- **Quant Task:** Identify these micro-patterns in ball-by-ball data to predict "Next Over Runs" volatility.
- **Expected Edge:** 8-12% on over-by-over markets.

### B. Data Requirements

To test these hypotheses, we need comprehensive match and market data:

**Essential Data Points (Priority 1):**
- Match Score (runs/wickets)
- Overs (current over and ball)
- Current Odds (Back/Lay prices with timestamps)
- Market State (active, suspended, closed)

**Advanced Data Points (Priority 2):**
- Batsman individual scores and strike rates
- Bowler economy rates for current spell
- "Lambi" (Session projections) from bookmakers
- Market Volume/Liquidity indicators

**Metadata (Priority 3 - The Alpha):**
- `is_suspended` flags with timestamps
- `max_stake_limit` changes
- `handicap_movement` speed
- Bid-ask spread variations

---

## 2. Data Pipeline Architecture (The "How")
**Contributors:** *Data Engineer & DevOps*

### A. Ingestion Layer ("The Scraper Farm")

**Target Sources:** 
- Primary: Dafabet.com
- Secondary: Micro999.co

**Technology Stack:**
- **Core:** Python 3.11+ with Playwright
- **Why Playwright:** Handles JavaScript-heavy betting sites better than requests/BeautifulSoup, provides CDP access for WebSocket interception
- **Stealth Layer:** Playwright-stealth plugin, custom user-agent rotation, mouse movement simulation

**Architecture Design:**

```
┌─────────────────┐
│ Manager Service │  (Distributes scraping jobs)
└────────┬────────┘
         │
    ┌────┴─────┬──────────┬──────────┐
    │          │          │          │
┌───▼───┐  ┌──▼───┐  ┌──▼───┐  ┌──▼───┐
│Worker │  │Worker│  │Worker│  │Worker│
│  #1   │  │  #2  │  │  #3  │  │  #4  │
└───┬───┘  └──┬───┘  └──┬───┘  └──┬───┘
    │         │         │         │
    └─────────┴─────────┴─────────┘
                  │
            ┌─────▼─────┐
            │ Proxy Pool│ (4G Mobile Proxies)
            └───────────┘
```

**Worker Node Specifications:**
- **Container:** Docker with Playwright pre-installed
- **Resources:** 1 vCPU, 2GB RAM per worker
- **Proxy:** Each worker routes through rotating residential/mobile proxy
- **Isolation:** No shared state between workers to prevent cross-contamination

**Snapshot Strategy:**
- **Frequency:** Every 3-5 seconds during active match
- **Trigger:** WebSocket message received OR timer expires
- **Data Capture:** Full DOM state + intercepted WebSocket frames

**Key Implementation Detail - CDP Interception:**
```python
# Instead of parsing rendered HTML (slow)
# Intercept WebSocket frames directly via Chrome DevTools Protocol

from playwright.sync_api import sync_playwright

def intercept_websocket_data(page):
    def handle_websocket_frame(payload):
        # Extract odds updates from raw WebSocket data
        # Before browser even renders it
        odds_data = parse_frame(payload)
        publish_to_redis(odds_data)
    
    # Attach to CDP network layer
    page.on("websocketframe", handle_websocket_frame)
```

### B. Raw Storage (The "Lake")

**Database:** TimescaleDB (PostgreSQL extension optimized for time-series)

**Why TimescaleDB over InfluxDB:**
- Better SQL support for complex queries
- Easier to join with relational match metadata
- Automatic partitioning and compression

**Schema Design:**
```sql
-- Hypertable for time-series odds data
CREATE TABLE market_ticks (
    time            TIMESTAMPTZ NOT NULL,
    match_id        UUID NOT NULL,
    bookmaker       TEXT NOT NULL,
    market_type     TEXT NOT NULL,  -- 'match_winner', 'session_6_over'
    team            TEXT,
    back_odds       DECIMAL(10,2),
    lay_odds        DECIMAL(10,2),
    is_suspended    BOOLEAN DEFAULT FALSE,
    stake_limit     INTEGER,
    volume_back     BIGINT,
    volume_lay      BIGINT,
    score_snapshot  JSONB,  -- {runs: 150, wickets: 3, overs: 15.2}
    PRIMARY KEY (time, match_id, market_type, bookmaker)
);

-- Convert to hypertable (automatic time-based partitioning)
SELECT create_hypertable('market_ticks', 'time');

-- Indexes for fast queries
CREATE INDEX idx_match_time ON market_ticks (match_id, time DESC);
CREATE INDEX idx_suspended ON market_ticks (is_suspended, time) WHERE is_suspended = TRUE;
```

**Data Retention Policy:**
- High-resolution (3-5 second ticks): 7 days
- Downsampled (1-minute aggregates): 90 days
- Match summaries: Indefinite

---

## 3. Processing & Analysis Engine (The "Brain")
**Contributors:** *Quant Analyst & Data Engineer*

### A. The Dual-Stream Architecture

Critical Design Decision: We split data flow into two independent paths to optimize for different use cases.

#### Path 1: The Hot Path (Live Signal Generation)
**Purpose:** Real-time alert generation with minimal latency

**Technology:**
- **Transport:** Redis Pub/Sub
- **Processing:** Python FastAPI background workers with in-memory state
- **Latency Target:** < 10ms from data ingestion to signal emission

**Flow:**
```
Scraper → Redis Channel "match_events" → Signal Processor → Redis Channel "signals" → WebSocket Server → UI
```

**Implementation:**
```python
import redis
from dataclasses import dataclass
from typing import Optional

@dataclass
class MatchState:
    """In-memory state for real-time processing"""
    match_id: str
    score: int
    wickets: int
    overs: float
    current_odds: float
    last_12_balls: list[int]  # Recent ball outcomes
    
    def required_run_rate(self) -> float:
        # Calculate RRR logic
        pass
    
    def odds_velocity(self, new_odds: float, time_delta: float) -> float:
        """Calculate rate of odds change"""
        return (new_odds - self.current_odds) / time_delta

class SignalProcessor:
    def __init__(self):
        self.redis = redis.Redis()
        self.match_states = {}  # In-memory state store
    
    async def process_event(self, event: dict):
        match_id = event['match_id']
        
        # Update in-memory state
        state = self.match_states.get(match_id)
        state.update(event)
        
        # Run strategy checks
        signals = []
        signals.extend(self.check_panic_rebound(state, event))
        signals.extend(self.check_mean_reversion(state, event))
        signals.extend(self.check_whale_shadow(state, event))
        
        # Publish signals
        for signal in signals:
            self.redis.publish('signals', signal.to_json())
    
    def check_panic_rebound(self, state: MatchState, event: dict) -> list:
        """Strategy 1: Detect market overreaction"""
        signals = []
        
        odds_drop_pct = ((state.current_odds - event['new_odds']) / state.current_odds) * 100
        time_elapsed = event['timestamp'] - state.last_update_time
        
        if (odds_drop_pct > 15 and 
            time_elapsed < 5 and 
            event.get('event_type') not in ['wicket', 'boundary']):
            
            signals.append(Signal(
                type='panic_rebound',
                action='BACK',
                team=state.batting_team,
                odds=event['new_odds'],
                confidence=self.calculate_confidence(odds_drop_pct),
                reasoning=f'{odds_drop_pct:.1f}% odds drop with no game event'
            ))
        
        return signals
```

#### Path 2: The Cold Path (Historical Analysis)
**Purpose:** Training data for models, backtesting, performance analysis

**Technology:**
- **Transport:** Asynchronous queue (RabbitMQ or Kafka)
- **Storage:** TimescaleDB
- **Processing:** Batch jobs for model training

**Flow:**
```
Scraper → Message Queue → Batch Writer → TimescaleDB → Jupyter Notebooks (Analysis)
```

**Why Separate Paths:**
1. **Performance:** Live signals can't wait for disk writes
2. **Reliability:** If database is slow/down, live system continues
3. **Optimization:** Different tuning for real-time vs. batch workloads

### B. Signal Generator Logic

#### Strategy 1: Panic Rebound (High Frequency)
```python
def panic_rebound_strategy(state: MatchState, new_odds: float, time_delta: float) -> Optional[Signal]:
    """
    Detects when market overreacts to non-events
    Win Rate Target: 58%
    Expected ROI: 10-12%
    """
    velocity = (new_odds - state.current_odds) / time_delta
    
    # Thresholds derived from backtesting
    VELOCITY_THRESHOLD = 0.03  # 3% per second
    MIN_OVERS = 7
    MAX_OVERS = 15
    MAX_RRR = 8.0
    
    if (abs(velocity) > VELOCITY_THRESHOLD and
        MIN_OVERS <= state.overs <= MAX_OVERS and
        state.required_run_rate() < MAX_RRR and
        not state.recent_wicket()):
        
        return Signal(
            strategy='panic_rebound',
            action='BACK' if velocity < 0 else 'LAY',
            odds=new_odds,
            confidence=min(abs(velocity) / VELOCITY_THRESHOLD, 1.0)
        )
    
    return None
```

#### Strategy 2: Middle-Over Mean Reversion
```python
def mean_reversion_strategy(state: MatchState) -> Optional[Signal]:
    """
    Exploits run rate regression in middle overs
    Win Rate Target: 62%
    Expected ROI: 8-10%
    """
    if not (7 <= state.overs <= 15):
        return None
    
    recent_rr = state.calculate_rolling_run_rate(overs=3)
    match_avg_rr = state.match_run_rate
    volatility = state.calculate_rr_std_dev()
    
    if recent_rr > (match_avg_rr + 2 * volatility):
        # Session line likely adjusted up, bet UNDER
        return Signal(
            strategy='mean_reversion',
            action='UNDER',
            market='session_runs',
            confidence=0.75
        )
    
    return None
```

#### Strategy 3: Whale Shadow (Metadata Trading)
```python
def whale_shadow_strategy(state: MatchState, event: dict) -> Optional[Signal]:
    """
    Follows smart money signals from bookmaker behavior
    Win Rate Target: 70%+ (rare but high confidence)
    Expected ROI: 15-20%
    """
    if event.get('stake_limit_change'):
        old_limit = state.stake_limit
        new_limit = event['stake_limit']
        drop_pct = ((old_limit - new_limit) / old_limit) * 100
        
        if drop_pct > 50:
            # Bookmaker reducing exposure - smart money detected
            return Signal(
                strategy='whale_shadow',
                action='WAIT_AND_FOLLOW',  # Special signal
                confidence=0.90,
                reasoning=f'Stake limit dropped {drop_pct:.0f}%'
            )
    
    if event.get('unexplained_suspension'):
        # Market suspended without obvious reason
        return Signal(
            strategy='whale_shadow',
            action='CAUTION',
            confidence=0.95,
            reasoning='Unexplained market suspension detected'
        )
    
    return None
```

### C. Normalization Service

Before data reaches the signal processor, it must be cleaned and standardized:

```python
class DataNormalizer:
    def normalize_score_string(self, score_str: str) -> dict:
        """
        Parse: "143/3 (15.2)" → {runs: 143, wickets: 3, overs: 15.2}
        """
        import re
        pattern = r'(\d+)/(\d+)\s*\((\d+)\.(\d+)\)'
        match = re.match(pattern, score_str)
        
        if match:
            return {
                'runs': int(match.group(1)),
                'wickets': int(match.group(2)),
                'overs': float(f"{match.group(3)}.{match.group(4)}")
            }
        return None
    
    def standardize_team_name(self, team: str) -> str:
        """
        Mumbai Indians, MI, Mumbai → MUM
        """
        team_map = {
            'mumbai indians': 'MUM',
            'mi': 'MUM',
            'chennai super kings': 'CSK',
            # ... complete mapping
        }
        return team_map.get(team.lower(), team)
```

---

## 4. Application Layer (The "Delivery")
**Contributors:** *Full Stack Dev & DevOps*

### A. Backend API Server

**Framework:** FastAPI (Python)
**Why FastAPI:** 
- Native async support for WebSockets
- Automatic API documentation (OpenAPI)
- High performance (comparable to Node.js)
- Python ecosystem integration with ML models

**Core Endpoints:**

```python
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="TITAN Betting Intelligence API")

# REST Endpoints
@app.get("/api/matches/live")
async def get_live_matches():
    """Returns list of currently tracked matches"""
    return {
        "matches": [
            {
                "id": "IND_vs_AUS_2024_T20_1",
                "teams": ["India", "Australia"],
                "status": "live",
                "current_over": 12.3,
                "tracking_since": "2024-12-06T14:30:00Z"
            }
        ]
    }

@app.get("/api/signals/history")
async def get_signal_history(match_id: str = None, limit: int = 50):
    """Returns past signals with outcomes"""
    # Query TimescaleDB for historical signals
    pass

@app.get("/api/performance/stats")
async def get_performance_stats():
    """Returns system performance metrics"""
    return {
        "today": {
            "signals_generated": 45,
            "win_rate": 0.58,
            "roi": 0.11,
            "pnl": 4250
        },
        "session": {
            "active_matches": 3,
            "signals_generated": 12,
            "win_rate": 0.67
        }
    }

# WebSocket for real-time signals
@app.websocket("/ws/signals")
async def websocket_signals(websocket: WebSocket):
    await websocket.accept()
    
    # Subscribe to Redis signal channel
    pubsub = redis_client.pubsub()
    pubsub.subscribe('signals')
    
    try:
        while True:
            message = pubsub.get_message()
            if message and message['type'] == 'message':
                await websocket.send_json(message['data'])
            
            # Keep-alive ping
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pubsub.unsubscribe('signals')
```

### B. Frontend Dashboard (The "HUD")

**Framework:** Next.js 14 with React 18
**Styling:** Tailwind CSS
**Real-Time:** Socket.io client

**UI Architecture - Vertical Sidebar Design:**

```
User's Screen Layout:
┌──────────────────────────────────────────┬───────────┐
│                                          │  TITAN    │
│   Betting Site (Dafabet/Micro999)       │   HUD     │
│   (80% width)                            │ (20% w)   │
│                                          │           │
│   User places bets here manually        │ ◉ LIVE    │
│                                          │           │
│                                          │ SIGNAL    │
│                                          │ ┌───────┐ │
│                                          │ │ BACK  │ │
│                                          │ │ India │ │
│                                          │ │ @2.10 │ │
│                                          │ │ 87%   │ │
│                                          │ └───────┘ │
│                                          │           │
│                                          │ [Chart]   │
│                                          │           │
│                                          │ P&L       │
│                                          │ +₹4,250   │
└──────────────────────────────────────────┴───────────┘
```

**Key Components:**

1. **Signal Card Component:**
```tsx
// components/SignalCard.tsx
interface Signal {
  id: string;
  strategy: 'panic_rebound' | 'mean_reversion' | 'whale_shadow';
  action: string;
  team: string;
  odds: number;
  confidence: number;
  reasoning: string;
  timestamp: number;
}

export function SignalCard({ signal }: { signal: Signal }) {
  const confidenceColor = signal.confidence > 0.8 ? 'bg-green-500' : 'bg-yellow-500';
  
  return (
    <div className="border border-gray-700 rounded-lg p-4 mb-3 bg-gray-900">
      <div className="flex justify-between items-center">
        <span className="text-2xl font-bold text-white">
          {signal.action} {signal.team}
        </span>
        <span className={`${confidenceColor} text-white px-2 py-1 rounded`}>
          {(signal.confidence * 100).toFixed(0)}%
        </span>
      </div>
      
      <div className="text-3xl font-mono text-green-400 my-2">
        @ {signal.odds.toFixed(2)}
      </div>
      
      <div className="text-sm text-gray-400">
        {signal.reasoning}
      </div>
      
      <button 
        onClick={() => navigator.clipboard.writeText(`${signal.action} ${signal.team} @ ${signal.odds}`)}
        className="mt-2 w-full bg-blue-600 hover:bg-blue-700 text-white py-1 rounded"
      >
        Copy to Clipboard
      </button>
    </div>
  );
}
```

2. **Audio Alert System:**
```tsx
// hooks/useAudioAlerts.ts
import { useEffect, useRef } from 'react';

export function useAudioAlerts(enabled: boolean) {
  const synthRef = useRef(window.speechSynthesis);
  
  const announceSignal = (signal: Signal) => {
    if (!enabled) return;
    
    const text = `${signal.action} ${signal.team} at ${signal.odds}`;
    const utterance = new SpeechSynthesisUtterance(text);
    
    // Adjust speech parameters
    utterance.rate = 1.2;  // Slightly faster
    utterance.pitch = signal.confidence > 0.8 ? 1.1 : 0.9;
    utterance.volume = 0.8;
    
    synthRef.current.speak(utterance);
  };
  
  return { announceSignal };
}
```

3. **Momentum Gauge:**
```tsx
// components/MomentumGauge.tsx
export function MomentumGauge({ value }: { value: number }) {
  // value ranges from -100 to +100
  const rotation = (value / 100) * 90;  // -90 to +90 degrees
  
  return (
    <div className="relative w-full h-32">
      <svg viewBox="0 0 200 100">
        {/* Gauge arc */}
        <path
          d="M 10 90 A 80 80 0 0 1 190 90"
          fill="none"
          stroke="#374151"
          strokeWidth="20"
        />
        
        {/* Needle */}
        <line
          x1="100"
          y1="90"
          x2="100"
          y2="20"
          stroke="#10B981"
          strokeWidth="3"
          transform={`rotate(${rotation} 100 90)`}
        />
      </svg>
      
      <div className="text-center text-sm text-gray-400">
        Market Pressure
      </div>
    </div>
  );
}
```

### C. Infrastructure (The "Fortress")

**Deployment Architecture:**

```
Production Environment:

┌─────────────────────────────────────────────┐
│ Cloudflare (DDoS + CDN)                     │
└────────────────┬────────────────────────────┘
                 │
┌────────────────▼────────────────────────────┐
│ AWS Application Load Balancer               │
└────────────────┬────────────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
┌───────▼────────┐  ┌────▼──────────┐
│ Frontend       │  │ Backend API   │
│ (Next.js)      │  │ (FastAPI)     │
│ 2 instances    │  │ 3 instances   │
└────────────────┘  └───────┬───────┘
                            │
                    ┌───────┴────────┐
                    │                │
            ┌───────▼────┐   ┌──────▼────────┐
            │ Redis      │   │ TimescaleDB   │
            │ Cluster    │   │ Cluster       │
            │ (3 nodes)  │   │ (Primary+2    │
            └────────────┘   │  Replicas)    │
                             └───────────────┘

┌─────────────────────────────────────────────┐
│ Scraper Farm (Isolated Network)             │
│ ┌────────┐ ┌────────┐ ┌────────┐          │
│ │Worker 1│ │Worker 2│ │Worker 3│ ...      │
│ └────────┘ └────────┘ └────────┘          │
│ Each with 4G Mobile Proxy                   │
└─────────────────────────────────────────────┘
```

**Docker Compose (Development):**

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
  
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - db_data:/var/lib/postgresql/data
  
  api:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://postgres:${DB_PASSWORD}@timescaledb:5432/betting
    depends_on:
      - redis
      - timescaledb
  
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    depends_on:
      - api
  
  scraper:
    build: ./scraper
    environment:
      REDIS_URL: redis://redis:6379
      PROXY_LIST: ${PROXY_LIST}
    depends_on:
      - redis
    deploy:
      replicas: 3

volumes:
  redis_data:
  db_data:
```

---

## 5. Execution Roadmap

### Phase 1: Data Foundation (Week 1)
**Goal:** Establish reliable data pipeline

**Tasks:**
1. ✅ **Day 1-2:** Project setup
   - Initialize Python/Node.js projects
   - Set up Docker development environment
   - Configure TimescaleDB and Redis

2. ✅ **Day 3-5:** Build Scraper
   - Implement Playwright-based scraper for Dafabet
   - Add CDP WebSocket interception
   - Test proxy rotation and anti-detection measures

3. ✅ **Day 6-7:** Data Storage
   - Create TimescaleDB schema
   - Implement data normalization service
   - Set up Redis Pub/Sub channels

**Success Criteria:**
- Scraper runs for 1 full match without being blocked
- Data stored in database with <2% loss rate
- Real-time data flow through Redis verified

### Phase 2: Intelligence Layer (Week 2)
**Goal:** Implement signal generation algorithms

**Tasks:**
1. **Day 8-10:** Signal Processor
   - Implement in-memory match state management
   - Code the 3 core strategies (Panic Rebound, Mean Reversion, Whale Shadow)
   - Add confidence scoring logic

2. **Day 11-12:** Backtesting Framework
   - Build tool to replay historical data through strategies
   - Test on 20-30 completed matches
   - Tune thresholds based on results

3. **Day 13-14:** Integration
   - Connect scraper → processor → signal output
   - Implement circuit breaker logic
   - Add monitoring and logging

**Success Criteria:**
- Generate at least 5 signals per test match
- Backtested win rate >55% on historical data
- Processing latency <10ms (p95)

### Phase 3: User Interface (Week 3)
**Goal:** Build the HUD dashboard

**Tasks:**
1. **Day 15-17:** Frontend Development
   - Create Next.js project structure
   - Build core UI components (Signal Card, Gauge, Chart)
   - Implement WebSocket client connection

2. **Day 18-19:** Audio & UX
   - Add text-to-speech alerts
   - Implement clipboard copy functionality
   - Add connection status indicators

3. **Day 20-21:** Backend API
   - Build FastAPI endpoints
   - Implement WebSocket server
   - Add authentication (JWT)

**Success Criteria:**
- Signal appears on UI <100ms after backend emission
- Audio alerts work reliably
- UI remains responsive with 50+ signals

### Phase 4: War Games (Week 4)
**Goal:** Live testing with paper trading

**Tasks:**
1. **Day 22-25:** Live Testing
   - Run system on 10-15 live matches
   - Track all signals and outcomes
   - Calculate actual ROI vs theoretical

2. **Day 26-27:** Calibration
   - Adjust confidence thresholds based on real results
   - Fine-tune circuit breaker parameters
   - Optimize for false positive reduction

3. **Day 28:** Production Prep
   - Set up production infrastructure
   - Configure monitoring and alerting
   - Write operational runbooks

**Success Criteria:**
- Win rate on live matches ≥52% (accounting for slippage)
- ROI >5% over 50+ signals
- Zero critical system failures during match hours
- Circuit breaker activates correctly during losing streak

### Post-Launch: Continuous Improvement
- **Week 5-8:** Add support for more betting sites
- **Week 9-12:** Implement ML models for confidence scoring
- **Week 13+:** Expand to ODI and other formats

---

## Document Approvals

**Approved By:**
- ✅ Data Engineer (The Ghost)
- ✅ Quantitative Analyst (The Math)
- ✅ Full Stack Developer (The Architect)
- ✅ DevOps Engineer (The General)
- ✅ Betting Expert (The Oracle)

**Next Steps:**
1. Begin Phase 1: Initialize project structure
2. Set up development environment
3. Start building the scraper prototype

---

_End of Master Architecture Document_


