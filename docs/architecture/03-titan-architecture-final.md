# The "TITAN" Architecture: Final Technical Specification
## World-Class Cricket Betting Intelligence System

**Status:** APPROVED | **Version:** 2.0 | **Codename:** TITAN

**Objective:** To build the world's most profitable cricket prediction engine by exploiting market overreactions ("Counter-Punching") and scraping bookmaker behavior ("Metadata").

---

## Table of Contents
1. System Overview (High Level)
2. Component 1: The Omni-Scraper (Data Ingestion)
3. Component 2: The Cortex (Processing Engine)
4. Component 3: The HUD (User Interface)
5. Component 4: Infrastructure & Security (The Fortress)
6. Execution Roadmap

---

## 1. System Overview (High Level)

### The Core Philosophy

The Titan system is **not** a traditional data pipeline that predicts match outcomes. It is a **Low-Latency Market Inefficiency Detection Engine**.

**Key Insight:** 
We bypass the impossible task of "beating" the official scout feed. Instead, we detect when the *betting market* makes errors in its reaction to events and alert the user to exploit those moments.

### The Three Pillars

```
┌─────────────────────────────────────────────────┐
│         TITAN SYSTEM ARCHITECTURE               │
├─────────────────────────────────────────────────┤
│                                                 │
│  Pillar 1: SPEED (Sub-500ms latency)          │
│  Data→Signal→User in <500ms                    │
│                                                 │
│  Pillar 2: INSIGHT (Metadata scraping)         │
│  Scrape the bookmaker, not just the cricket    │
│                                                 │
│  Pillar 3: DELIVERY (Audio-first HUD)          │
│  Non-intrusive alerts for fast execution       │
│                                                 │
└─────────────────────────────────────────────────┘
```

### System Topology

```
┌──────────────────────────────────────────────────────────┐
│                    BETTING SITES                          │
│         (Dafabet.com, Micro999.co)                       │
└────────────────────┬─────────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │ Omni-Scraper│ (Python + Playwright + CDP)
              │  (5 Workers)│
              └──────┬──────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   ┌────▼────┐              ┌────▼─────┐
   │ Redis   │ (Hot Path)   │TimescaleDB│ (Cold Path)
   │ Pub/Sub │              │           │
   └────┬────┘              └───────────┘
        │
   ┌────▼────────┐
   │  The Cortex │ (Signal Processor)
   │ (In-Memory) │
   └────┬────────┘
        │
   ┌────▼─────────┐
   │ FastAPI      │ (WebSocket Server)
   │ Backend      │
   └────┬─────────┘
        │
   ┌────▼─────────┐
   │   Next.js    │ (The HUD)
   │   Frontend   │
   └──────────────┘
        │
   ┌────▼─────────┐
   │    USER      │ (Places bets manually)
   └──────────────┘
```

---

## 2. Component 1: The Omni-Scraper (Data Ingestion)
**"The Eyes of the System"**

### A. Technology Stack

**Core Engine:** Python 3.11+ with Playwright
**Network Interception:** Chrome DevTools Protocol (CDP)
**Infrastructure:** Docker containers on AWS t3.medium instances
**Anonymity:** 4G Mobile Proxy rotation (residential IPs)

**Why This Stack:**

1. **Playwright > Selenium:** 
   - 30% faster page load
   - Better WebSocket handling
   - Built-in stealth features

2. **CDP Interception > DOM Scraping:**
   - Access raw WebSocket frames before rendering
   - 200-400ms latency reduction
   - Less resource intensive (no full page render needed)

3. **4G Mobile Proxies > Datacenter Proxies:**
   - Look like real mobile users
   - Harder to fingerprint and block
   - Residential IP reputation

### B. What We Scrape (The "Alpha")

#### Tier 1: Match Data (Standard)
```python
{
    "match_id": "IND_vs_AUS_2024_T20_1",
    "timestamp": "2024-12-06T14:35:22.156Z",
    "score": {
        "runs": 143,
        "wickets": 3,
        "overs": 15.2,
        "run_rate": 9.34
    },
    "batting_team": "India",
    "bowling_team": "Australia"
}
```

#### Tier 2: Odds Data (Standard)
```python
{
    "market_type": "match_winner",
    "team": "India",
    "back_odds": 2.10,
    "lay_odds": 2.12,
    "timestamp": "2024-12-06T14:35:22.156Z"
}
```

#### Tier 3: Metadata (THE ALPHA - Unique to TITAN)
```python
{
    "is_suspended": true,
    "suspension_duration_ms": 8500,
    "max_stake_limit": 5000,  # Changed from 50000
    "stake_limit_change_pct": -90,
    "bid_ask_spread": 0.02,  # lay - back
    "market_depth": {
        "back_volume_visible": 125000,
        "lay_volume_visible": 45000
    },
    "odds_update_frequency_hz": 0.33  # Updates per second
}
```

**Why Metadata Matters:**

Normal apps see: "India odds are 2.10"
TITAN sees: "India odds DROPPED to 2.10, market SUSPENDED for 8 seconds, stake limits CUT by 90%, reopened with high back volume"

This tells us: **Smart money or insider information hit the market**.

### C. Implementation Architecture

**Manager Service:**
```python
# scraper/manager.py
import asyncio
from redis import Redis

class ScraperManager:
    def __init__(self):
        self.redis = Redis()
        self.active_matches = []
    
    async def distribute_jobs(self):
        """Assign matches to worker nodes"""
        matches = self.get_live_matches()  # From API or schedule
        
        for i, match in enumerate(matches):
            worker_id = i % NUM_WORKERS
            self.redis.lpush(f'scraper_queue:{worker_id}', match.id)
    
    async def monitor_health(self):
        """Check if workers are alive"""
        while True:
            for worker_id in range(NUM_WORKERS):
                last_heartbeat = self.redis.get(f'worker:{worker_id}:heartbeat')
                if time.time() - float(last_heartbeat) > 30:
                    self.restart_worker(worker_id)
            await asyncio.sleep(10)
```

**Worker Node:**
```python
# scraper/worker.py
from playwright.async_api import async_playwright
import json

class ScraperWorker:
    def __init__(self, worker_id, proxy_url):
        self.worker_id = worker_id
        self.proxy = proxy_url
        self.redis = Redis()
    
    async def run(self):
        async with async_playwright() as p:
            # Launch browser with proxy
            browser = await p.chromium.launch(
                proxy={"server": self.proxy},
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    f'--window-size=1920,1080'
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=self.get_random_user_agent()
            )
            
            page = await context.new_page()
            
            # Attach CDP listener for WebSocket frames
            client = await page.context.new_cdp_session(page)
            await client.send('Network.enable')
            
            client.on('Network.webSocketFrameReceived', 
                     lambda params: self.handle_websocket_frame(params))
            
            # Navigate to match page
            await page.goto(f'https://dafabet.com/match/{match_id}')
            
            # Keep page alive and monitor
            while True:
                await asyncio.sleep(1)
                self.send_heartbeat()
    
    def handle_websocket_frame(self, params):
        """Process incoming WebSocket data"""
        try:
            payload = json.loads(params['response']['payloadData'])
            
            if 'odds' in payload:
                # Extract odds update
                odds_data = self.parse_odds(payload)
                self.publish_to_redis('match_events', odds_data)
            
            if 'suspended' in payload:
                # Market suspension event
                suspension_data = {
                    'type': 'suspension',
                    'timestamp': time.time(),
                    'market': payload.get('market_id')
                }
                self.publish_to_redis('match_events', suspension_data)
                
        except Exception as e:
            logger.error(f"Error parsing WebSocket frame: {e}")
    
    def publish_to_redis(self, channel, data):
        """Push data to Redis for processing"""
        self.redis.publish(channel, json.dumps(data))
        # Also write to TimescaleDB asynchronously
        self.redis.lpush('timescale_queue', json.dumps(data))
```

### D. Anti-Detection Measures

**1. Human-Like Behavior:**
```python
async def human_mouse_movement(page):
    """Simulate realistic mouse movements"""
    import numpy as np
    
    # Generate Bézier curve for natural movement
    start = (100, 100)
    end = (800, 600)
    control1 = (300, 200)
    control2 = (600, 400)
    
    for t in np.linspace(0, 1, 50):
        x = (1-t)**3*start[0] + 3*(1-t)**2*t*control1[0] + \
            3*(1-t)*t**2*control2[0] + t**3*end[0]
        y = (1-t)**3*start[1] + 3*(1-t)**2*t*control1[1] + \
            3*(1-t)*t**2*control2[1] + t**3*end[1]
        
        await page.mouse.move(x, y)
        await asyncio.sleep(0.01)
```

**2. Fingerprint Randomization:**
```python
async def setup_stealth_context(playwright):
    """Configure browser to avoid detection"""
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=f'/tmp/profile_{random.randint(1000,9999)}',
        proxy={'server': PROXY_URL},
        viewport={
            'width': random.choice([1366, 1920, 1440]),
            'height': random.choice([768, 1080, 900])
        },
        user_agent=get_random_mobile_ua(),
        locale=random.choice(['en-US', 'en-GB', 'en-IN']),
        timezone_id='Asia/Kolkata',
        permissions=['geolocation'],
        geolocation={'latitude': 19.0760 + random.uniform(-1, 1), 
                     'longitude': 72.8777 + random.uniform(-1, 1)}
    )
    
    # Inject scripts to mask automation
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
    """)
    
    return context
```

**3. Request Timing Randomization:**
```python
async def random_delay():
    """Human-like delays between actions"""
    # Exponential distribution (most humans act quickly, some pause)
    delay = np.random.exponential(scale=2.0)
    delay = min(delay, 8.0)  # Cap at 8 seconds
    await asyncio.sleep(delay)
```

### E. Error Handling & Recovery

```python
class ScraperWithRecovery:
    def __init__(self):
        self.max_retries = 3
        self.backoff_base = 2
    
    async def scrape_with_retry(self, match_id):
        for attempt in range(self.max_retries):
            try:
                return await self.scrape_match(match_id)
            
            except ProxyBlocked:
                logger.warning(f"Proxy blocked, rotating...")
                self.rotate_proxy()
                await asyncio.sleep(self.backoff_base ** attempt)
            
            except PageLayoutChanged:
                logger.error(f"Page layout changed! Manual review needed.")
                self.save_html_snapshot()
                self.alert_admin("Layout change detected")
                raise
            
            except TimeoutError:
                logger.warning(f"Timeout on attempt {attempt + 1}")
                await asyncio.sleep(self.backoff_base ** attempt)
            
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                self.save_debug_info()
                raise
        
        # All retries failed
        self.mark_match_as_failed(match_id)
        return None
```

---

## 3. Component 2: The Cortex (Processing Engine)
**"The Brain of the System"**

### A. The Dual-Stream Architecture

Critical design decision validated through profitability analysis:

**Problem:** Traditional architectures write to database, then query for processing. This adds 50-200ms latency.

**Solution:** Split into two independent data paths.

#### Hot Path (Real-Time Signals)

```
Scraper → Redis Pub/Sub → In-Memory Processor → WebSocket → User
Latency: <10ms processing time
```

**Implementation:**
```python
# cortex/signal_processor.py
import redis
import json
from dataclasses import dataclass, field
from typing import List, Optional
import time

@dataclass
class MatchState:
    """In-memory representation of live match"""
    match_id: str
    timestamp: float
    score: int
    wickets: int
    overs: float
    current_odds: float
    last_update: float
    last_12_balls: List[int] = field(default_factory=list)
    recent_events: List[dict] = field(default_factory=list)
    
    # Metadata tracking
    is_suspended: bool = False
    stake_limit: int = 50000
    stake_limit_history: List[tuple] = field(default_factory=list)
    
    def add_ball(self, runs: int):
        """Track recent deliveries"""
        self.last_12_balls.append(runs)
        if len(self.last_12_balls) > 12:
            self.last_12_balls.pop(0)
    
    def required_run_rate(self, target: int, overs_remaining: float) -> float:
        """Calculate RRR"""
        balls_remaining = overs_remaining * 6
        runs_needed = target - self.score
        return (runs_needed / balls_remaining) * 6 if balls_remaining > 0 else 0
    
    def calculate_run_rate(self) -> float:
        """Current run rate"""
        return (self.score / self.overs) if self.overs > 0 else 0
    
    def odds_velocity(self, new_odds: float) -> float:
        """Rate of odds change per second"""
        time_delta = time.time() - self.last_update
        if time_delta == 0:
            return 0
        return (new_odds - self.current_odds) / time_delta

class CortexProcessor:
    def __init__(self):
        self.redis = redis.Redis(decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.match_states = {}  # In-memory state store
        self.strategies = [
            PanicReboundStrategy(),
            MeanReversionStrategy(),
            WhaleShadowStrategy()
        ]
    
    async def start(self):
        """Main processing loop"""
        self.pubsub.subscribe('match_events')
        
        print("Cortex online. Listening for events...")
        
        for message in self.pubsub.listen():
            if message['type'] == 'message':
                event = json.loads(message['data'])
                await self.process_event(event)
    
    async def process_event(self, event: dict):
        """Process incoming match event"""
        match_id = event['match_id']
        
        # Get or create match state
        if match_id not in self.match_states:
            self.match_states[match_id] = MatchState(
                match_id=match_id,
                timestamp=time.time(),
                score=0,
                wickets=0,
                overs=0.0,
                current_odds=1.0,
                last_update=time.time()
            )
        
        state = self.match_states[match_id]
        
        # Update state
        self.update_state(state, event)
        
        # Run all strategies
        signals = []
        for strategy in self.strategies:
            signal = strategy.evaluate(state, event)
            if signal:
                signals.append(signal)
        
        # Publish signals
        for signal in signals:
            self.publish_signal(signal)
            print(f"📡 Signal Generated: {signal}")
    
    def update_state(self, state: MatchState, event: dict):
        """Update in-memory match state"""
        if 'score' in event:
            state.score = event['score']['runs']
            state.wickets = event['score']['wickets']
            state.overs = event['score']['overs']
        
        if 'odds' in event:
            state.current_odds = event['odds']
        
        if 'is_suspended' in event:
            state.is_suspended = event['is_suspended']
        
        if 'stake_limit' in event:
            old_limit = state.stake_limit
            new_limit = event['stake_limit']
            if old_limit != new_limit:
                state.stake_limit_history.append((time.time(), new_limit))
                state.stake_limit = new_limit
        
        state.last_update = time.time()
        state.recent_events.append(event)
        
        # Keep only last 20 events
        if len(state.recent_events) > 20:
            state.recent_events.pop(0)
    
    def publish_signal(self, signal: dict):
        """Publish signal to Redis for WebSocket delivery"""
        signal['timestamp'] = time.time()
        self.redis.publish('signals', json.dumps(signal))
        
        # Also log to TimescaleDB asynchronously
        self.redis.lpush('signal_history', json.dumps(signal))
```

#### Cold Path (Historical Storage)

```
Scraper → RabbitMQ Queue → Batch Writer → TimescaleDB → Analysis
Latency: Not critical (async)
```

**Benefits of Separation:**
1. Live system never waits for disk I/O
2. Database issues don't affect real-time signals
3. Can optimize each path independently

### B. The Three Core Strategies

#### Strategy 1: Panic Rebound
```python
# cortex/strategies/panic_rebound.py
class PanicReboundStrategy:
    """
    Detects market overreaction to events
    Target Win Rate: 58%
    Target ROI: 10-12%
    """
    
    def __init__(self):
        self.name = "panic_rebound"
        self.velocity_threshold = 0.03  # 3% per second
        self.min_overs = 7
        self.max_overs = 15
        self.max_rrr = 8.0
    
    def evaluate(self, state: MatchState, event: dict) -> Optional[dict]:
        # Only active in middle overs
        if not (self.min_overs <= state.overs <= self.max_overs):
            return None
        
        # Check if odds changed significantly
        if 'odds' not in event:
            return None
        
        new_odds = event['odds']
        velocity = state.odds_velocity(new_odds)
        
        # Detect rapid movement
        if abs(velocity) < self.velocity_threshold:
            return None
        
        # Check if there's a game event justifying the move
        recent_wicket = any(e.get('event_type') == 'wicket' 
                           for e in state.recent_events[-3:])
        recent_boundary = any(e.get('event_type') in ['four', 'six'] 
                             for e in state.recent_events[-3:])
        
        # If big odds move but NO event = overreaction
        if not recent_wicket and not recent_boundary:
            # Calculate confidence
            confidence = min(abs(velocity) / self.velocity_threshold, 1.0)
            confidence = confidence * 0.85  # Cap at 85%
            
            # Determine action
            action = 'BACK' if velocity < 0 else 'LAY'
            
            return {
                'id': f"sig_{int(time.time() * 1000)}",
                'strategy': self.name,
                'match_id': state.match_id,
                'action': action,
                'market': 'match_winner',
                'team': 'batting_team',  # Would need actual team name
                'odds': new_odds,
                'confidence': confidence,
                'reasoning': f'{abs(velocity)*100:.1f}% odds movement with no game event'
            }
        
        return None
```

#### Strategy 2: Mean Reversion (Middle Overs)
```python
# cortex/strategies/mean_reversion.py
class MeanReversionStrategy:
    """
    Exploits run rate regression in middle overs
    Target Win Rate: 62%
    Target ROI: 8-10%
    """
    
    def __init__(self):
        self.name = "mean_reversion"
        self.min_overs = 7
        self.max_overs = 15
        self.std_threshold = 2.0
    
    def evaluate(self, state: MatchState, event: dict) -> Optional[dict]:
        if not (self.min_overs <= state.overs <= self.max_overs):
            return None
        
        # Need at least 6 overs of data
        if state.overs < 6:
            return None
        
        # Calculate recent run rate (last 3 overs)
        recent_balls = state.last_12_balls[-18:] if len(state.last_12_balls) >= 18 else []
        if len(recent_balls) < 12:
            return None
        
        recent_runs = sum(recent_balls)
        recent_rr = (recent_runs / len(recent_balls)) * 6
        
        # Calculate match average run rate
        match_rr = state.calculate_run_rate()
        
        # Simple volatility estimate
        volatility = self.estimate_volatility(state.last_12_balls)
        
        # Check if recent RR is significantly above average
        if recent_rr > (match_rr + self.std_threshold * volatility):
            # Session line likely adjusted UP, bet UNDER
            return {
                'id': f"sig_{int(time.time() * 1000)}",
                'strategy': self.name,
                'match_id': state.match_id,
                'action': 'UNDER',
                'market': 'session_runs',
                'odds': event.get('session_odds', 1.90),
                'confidence': 0.75,
                'reasoning': f'Recent RR {recent_rr:.1f} >> Match avg {match_rr:.1f}'
            }
        
        return None
    
    def estimate_volatility(self, balls: List[int]) -> float:
        """Simple standard deviation of run rate"""
        if len(balls) < 6:
            return 1.0
        
        import numpy as np
        return np.std(balls) if balls else 1.0
```

#### Strategy 3: Whale Shadow (Metadata Trading)
```python
# cortex/strategies/whale_shadow.py
class WhaleShadowStrategy:
    """
    Follows smart money signals from bookmaker behavior
    Target Win Rate: 70%+ (rare signals)
    Target ROI: 15-20%
    """
    
    def __init__(self):
        self.name = "whale_shadow"
        self.stake_limit_drop_threshold = 0.50  # 50% reduction
    
    def evaluate(self, state: MatchState, event: dict) -> Optional[dict]:
        signals = []
        
        # Signal 1: Stake limit drop
        if 'stake_limit' in event:
            if len(state.stake_limit_history) >= 2:
                prev_limit = state.stake_limit_history[-2][1]
                new_limit = event['stake_limit']
                drop_pct = (prev_limit - new_limit) / prev_limit
                
                if drop_pct > self.stake_limit_drop_threshold:
                    return {
                        'id': f"sig_{int(time.time() * 1000)}",
                        'strategy': self.name,
                        'match_id': state.match_id,
                        'action': 'WAIT_AND_FOLLOW',
                        'market': 'match_winner',
                        'confidence': 0.90,
                        'reasoning': f'Stake limit dropped {drop_pct*100:.0f}% - smart money detected',
                        'special_instruction': 'Wait for market direction after reopen'
                    }
        
        # Signal 2: Unexplained suspension
        if event.get('suspension_type') == 'unexplained':
            return {
                'id': f"sig_{int(time.time() * 1000)}",
                'strategy': self.name,
                'match_id': state.match_id,
                'action': 'CAUTION',
                'confidence': 0.95,
                'reasoning': 'Unexplained market suspension - possible insider activity',
                'special_instruction': 'Observe but do not bet until pattern clear'
            }
        
        return None
```

### C. Circuit Breaker System

```python
# cortex/circuit_breaker.py
class CircuitBreaker:
    """
    Automatically stops signal generation if system is losing money
    """
    
    def __init__(self, starting_bankroll: float = 100000):
        self.starting_bankroll = starting_bankroll
        self.current_pnl = 0
        self.session_pnl = 0
        self.is_open = False
        self.signal_history = []
    
    def track_signal_outcome(self, signal: dict, won: bool, stake: float, odds: float):
        """Record outcome of a signal"""
        pnl = stake * (odds - 1) if won else -stake
        
        self.current_pnl += pnl
        self.session_pnl += pnl
        
        self.signal_history.append({
            'signal_id': signal['id'],
            'outcome': 'WIN' if won else 'LOSS',
            'pnl': pnl,
            'timestamp': time.time()
        })
        
        self.check_circuit_breaker()
    
    def check_circuit_breaker(self):
        """Check if we should stop generating signals"""
        session_loss_pct = abs(self.session_pnl / self.starting_bankroll)
        
        if self.session_pnl < 0 and session_loss_pct > 0.05:  # -5%
            self.open_circuit()
        
        # Also check consecutive losses
        recent_signals = self.signal_history[-10:]
        if len(recent_signals) >= 5:
            consecutive_losses = all(s['outcome'] == 'LOSS' for s in recent_signals[-5:])
            if consecutive_losses:
                self.open_circuit()
    
    def open_circuit(self):
        """Stop all signal generation"""
        self.is_open = True
        print("🚨 CIRCUIT BREAKER ACTIVATED - Signal generation stopped")
        self.alert_admin()
    
    def should_suppress_signal(self) -> bool:
        """Check if signals should be suppressed"""
        return self.is_open
    
    def reset(self):
        """Manually reset circuit breaker"""
        self.is_open = False
        self.session_pnl = 0
        print("✅ Circuit breaker reset")
```

---

## 4. Component 3: The HUD (User Interface)
**"The Pilot's Cockpit"**

### A. Design Philosophy

**Core Principle:** The HUD is NOT a dashboard. It's a minimal, non-intrusive alert system that keeps the user's attention on the betting site.

**Form Factor:** Vertical sidebar (20% of screen width)

### B. Frontend Architecture

**Stack:**
- **Framework:** Next.js 14 (App Router)
- **Styling:** Tailwind CSS
- **Real-Time:** Socket.io client
- **State:** Zustand (lightweight state management)

**Project Structure:**
```
frontend/
├── app/
│   ├── layout.tsx
│   ├── page.tsx (main HUD)
│   └── api/
├── components/
│   ├── SignalCard.tsx
│   ├── MomentumGauge.tsx
│   ├── OddsChart.tsx
│   ├── ConnectionStatus.tsx
│   └── PnLTracker.tsx
├── hooks/
│   ├── useWebSocket.ts
│   ├── useAudioAlerts.ts
│   └── useSignalHistory.ts
└── lib/
    ├── socket.ts
    └── types.ts
```

**Main HUD Component:**
```tsx
// app/page.tsx
'use client';

import { useWebSocket } from '@/hooks/useWebSocket';
import { useAudioAlerts } from '@/hooks/useAudioAlerts';
import { SignalCard } from '@/components/SignalCard';
import { ConnectionStatus } from '@/components/ConnectionStatus';
import { PnLTracker } from '@/components/PnLTracker';
import { MomentumGauge } from '@/components/MomentumGauge';

export default function TitanHUD() {
  const { signals, isConnected, momentum } = useWebSocket();
  const { announceSignal, isEnabled, toggle } = useAudioAlerts();
  
  // Announce new signals via audio
  useEffect(() => {
    if (signals.length > 0) {
      const latestSignal = signals[0];
      announceSignal(latestSignal);
    }
  }, [signals]);
  
  return (
    <div className="h-screen w-full bg-gray-950 text-white p-4 overflow-y-auto">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-green-400">TITAN</h1>
        <ConnectionStatus isConnected={isConnected} />
      </div>
      
      {/* Audio Toggle */}
      <button
        onClick={toggle}
        className={`w-full mb-4 py-2 rounded ${
          isEnabled ? 'bg-green-600' : 'bg-gray-700'
        }`}
      >
        🔊 Audio: {isEnabled ? 'ON' : 'OFF'}
      </button>
      
      {/* Momentum Gauge */}
      <div className="mb-6">
        <MomentumGauge value={momentum} />
      </div>
      
      {/* P&L Tracker */}
      <div className="mb-6">
        <PnLTracker />
      </div>
      
      {/* Live Signals */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">Live Signals</h2>
        {signals.length === 0 ? (
          <p className="text-gray-500">Waiting for signals...</p>
        ) : (
          signals.map(signal => (
            <SignalCard key={signal.id} signal={signal} />
          ))
        )}
      </div>
    </div>
  );
}
```

**WebSocket Hook:**
```tsx
// hooks/useWebSocket.ts
import { useEffect, useState } from 'react';
import { io, Socket } from 'socket.io-client';

interface Signal {
  id: string;
  strategy: string;
  action: string;
  team?: string;
  odds: number;
  confidence: number;
  reasoning: string;
  timestamp: number;
}

export function useWebSocket() {
  const [socket, setSocket] = useState<Socket | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [momentum, setMomentum] = useState(0);
  
  useEffect(() => {
    const socketInstance = io(process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000', {
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });
    
    socketInstance.on('connect', () => {
      console.log('✅ Connected to TITAN backend');
      setIsConnected(true);
    });
    
    socketInstance.on('disconnect', () => {
      console.log('❌ Disconnected from TITAN backend');
      setIsConnected(false);
    });
    
    socketInstance.on('signal', (signal: Signal) => {
      console.log('📡 New signal received:', signal);
      setSignals(prev => [signal, ...prev].slice(0, 10)); // Keep latest 10
    });
    
    socketInstance.on('momentum_update', (data: { value: number }) => {
      setMomentum(data.value);
    });
    
    setSocket(socketInstance);
    
    return () => {
      socketInstance.disconnect();
    };
  }, []);
  
  return { socket, signals, isConnected, momentum };
}
```

**Audio Alerts Hook:**
```tsx
// hooks/useAudioAlerts.ts
import { useState, useRef, useCallback } from 'react';

interface Signal {
  action: string;
  team?: string;
  odds: number;
  confidence: number;
}

export function useAudioAlerts() {
  const [isEnabled, setIsEnabled] = useState(true);
  const synthRef = useRef(typeof window !== 'undefined' ? window.speechSynthesis : null);
  
  const announceSignal = useCallback((signal: Signal) => {
    if (!isEnabled || !synthRef.current) return;
    
    // Cancel any ongoing speech
    synthRef.current.cancel();
    
    // Create announcement
    const text = signal.team 
      ? `${signal.action} ${signal.team} at ${signal.odds.toFixed(2)}`
      : `${signal.action} at ${signal.odds.toFixed(2)}`;
    
    const utterance = new SpeechSynthesisUtterance(text);
    
    // Adjust parameters based on confidence
    utterance.rate = 1.2; // Slightly faster
    utterance.pitch = signal.confidence > 0.8 ? 1.1 : 0.9; // Higher pitch = high confidence
    utterance.volume = 0.8;
    utterance.lang = 'en-US';
    
    synthRef.current.speak(utterance);
  }, [isEnabled]);
  
  const toggle = useCallback(() => {
    setIsEnabled(prev => !prev);
  }, []);
  
  return { announceSignal, isEnabled, toggle };
}
```

**Signal Card Component:**
```tsx
// components/SignalCard.tsx
import { useState } from 'react';

interface SignalCardProps {
  signal: {
    id: string;
    strategy: string;
    action: string;
    team?: string;
    market?: string;
    odds: number;
    confidence: number;
    reasoning: string;
    timestamp: number;
  };
}

export function SignalCard({ signal }: SignalCardProps) {
  const [copied, setCopied] = useState(false);
  
  const confidenceColor = signal.confidence > 0.8 
    ? 'bg-green-500' 
    : signal.confidence > 0.6 
    ? 'bg-yellow-500' 
    : 'bg-orange-500';
  
  const handleCopy = () => {
    const text = signal.team
      ? `${signal.action} ${signal.team} @ ${signal.odds}`
      : `${signal.action} @ ${signal.odds}`;
    
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  
  const timeAgo = Math.floor((Date.now() - signal.timestamp * 1000) / 1000);
  
  return (
    <div className="border border-gray-700 rounded-lg p-4 bg-gray-900 hover:border-green-500 transition-colors">
      {/* Header */}
      <div className="flex justify-between items-start mb-2">
        <div>
          <span className="text-2xl font-bold text-white">
            {signal.action} {signal.team || ''}
          </span>
          <div className="text-xs text-gray-500 mt-1">
            {signal.strategy.replace('_', ' ').toUpperCase()}
          </div>
        </div>
        <div className={`${confidenceColor} text-white px-3 py-1 rounded-full text-sm font-semibold`}>
          {(signal.confidence * 100).toFixed(0)}%
        </div>
      </div>
      
      {/* Odds */}
      <div className="text-3xl font-mono text-green-400 my-3">
        @ {signal.odds.toFixed(2)}
      </div>
      
      {/* Market */}
      {signal.market && (
        <div className="text-sm text-gray-400 mb-2">
          Market: {signal.market.replace('_', ' ')}
        </div>
      )}
      
      {/* Reasoning */}
      <div className="text-sm text-gray-300 mb-3 p-2 bg-gray-800 rounded">
        {signal.reasoning}
      </div>
      
      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={handleCopy}
          className={`flex-1 py-2 rounded transition-colors ${
            copied 
              ? 'bg-green-600 text-white' 
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {copied ? '✓ Copied!' : '📋 Copy'}
        </button>
        <div className="text-xs text-gray-500 self-center">
          {timeAgo}s ago
        </div>
      </div>
    </div>
  );
}
```

### C. Responsive Positioning

**CSS for Sidebar Layout:**
```css
/* globals.css */
.titan-hud {
  position: fixed;
  right: 0;
  top: 0;
  height: 100vh;
  width: 20vw;
  min-width: 320px;
  max-width: 400px;
  z-index: 9999;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.5);
}

/* Compact mode for smaller screens */
@media (max-width: 1440px) {
  .titan-hud {
    width: 300px;
  }
}
```

---

## 5. Component 4: Infrastructure & Security (The Fortress)

### A. Production Deployment

**AWS Infrastructure:**
```
┌─────────────────────────────────────┐
│ Route 53 (DNS)                      │
│ titan-betting.com                   │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│ CloudFront CDN + WAF                │
│ (DDoS Protection, SSL)              │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│ Application Load Balancer (ALB)     │
│ Health checks, SSL termination      │
└────────┬───────────────┬────────────┘
         │               │
  ┌──────▼─────┐  ┌─────▼────────┐
  │ Frontend   │  │ Backend API  │
  │ (ECS)      │  │ (ECS)        │
  │ Next.js    │  │ FastAPI      │
  │ 2x t3.med  │  │ 3x t3.large  │
  └────────────┘  └──────┬───────┘
                         │
              ┌──────────┴──────────┐
              │                     │
      ┌───────▼────┐    ┌──────────▼──────┐
      │ ElastiCache│    │ RDS TimescaleDB │
      │ Redis      │    │ (Multi-AZ)      │
      │ Cluster    │    │ Primary + 2     │
      │ 3 nodes    │    │ Read Replicas   │
      └────────────┘    └─────────────────┘

┌──────────────────────────────────────┐
│ Scraper Farm (Private Subnet)        │
│ EC2 Instances with Playwright        │
│ 5x t3.medium                          │
│ → 4G Mobile Proxy Pool (External)    │
└──────────────────────────────────────┘
```

**Kubernetes Configuration (Alternative):**
```yaml
# k8s/deployments/api.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: titan-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: titan-api
  template:
    metadata:
      labels:
        app: titan-api
    spec:
      containers:
      - name: api
        image: titan/api:latest
        ports:
        - containerPort: 8000
        env:
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: titan-secrets
              key: redis-url
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: titan-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: titan-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

### B. Security Measures

**1. API Authentication:**
```python
# backend/middleware/auth.py
from fastapi import Security, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

**2. Rate Limiting:**
```python
# backend/middleware/rate_limit.py
from fastapi import Request
from redis import Redis
import time

redis_client = Redis()

async def rate_limit(request: Request):
    client_ip = request.client.host
    key = f"rate_limit:{client_ip}"
    
    requests = redis_client.incr(key)
    if requests == 1:
        redis_client.expire(key, 60)  # 1 minute window
    
    if requests > 100:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

**3. Secrets Management:**
```bash
# Store in AWS Secrets Manager
aws secretsmanager create-secret \
    --name titan/prod/database \
    --secret-string '{"username":"titan","password":"xxx"}'

# Access in code
import boto3

secrets_client = boto3.client('secretsmanager')
secret = secrets_client.get_secret_value(SecretId='titan/prod/database')
db_creds = json.loads(secret['SecretString'])
```

---

## 6. Execution Roadmap

| Phase | Duration | Deliverables | Success Criteria |
|-------|----------|--------------|------------------|
| **Phase 1: Foundation** | Week 1 | - Docker environment<br>- Dafabet scraper<br>- Database schema<br>- Redis setup | - Scraper runs 1 match without ban<br>- Data stored with <2% loss |
| **Phase 2: Intelligence** | Week 2 | - Signal processor<br>- 3 core strategies<br>- Backtesting framework<br>- Circuit breaker | - 5+ signals per match<br>- 55%+ win rate on historical data<br>- <10ms processing latency |
| **Phase 3: Interface** | Week 3 | - Next.js HUD<br>- WebSocket connection<br>- Audio alerts<br>- FastAPI backend | - Signal display <100ms after generation<br>- Audio works reliably<br>- UI stays responsive |
| **Phase 4: War Games** | Week 4 | - Live testing (10-15 matches)<br>- Performance tuning<br>- Production deployment | - Win rate ≥52% (with slippage)<br>- ROI >5%<br>- Zero critical failures |

---

## Document Approvals

**Reviewed and Approved by:**
- ✅ The Ghost (Elite Data Engineer)
- ✅ The Math (World-Class Quant)
- ✅ The Architect (Elite Full Stack)
- ✅ The General (Principal DevOps)
- ✅ The Oracle (Legendary Betting Expert)

**Status:** READY FOR IMPLEMENTATION

**Next Immediate Action:** Initialize project repository and begin Phase 1.

---

_End of TITAN Architecture Specification_


