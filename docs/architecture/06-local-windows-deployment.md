# TITAN Local Windows Deployment Guide
## Running the Complete System on Your Laptop

**Target Hardware:** Windows Laptop  
**Specs:** 32GB RAM, Intel Ultra 19 185H (16 cores)  
**Deployment Type:** Local development with Docker Desktop

---

## Table of Contents

1. System Requirements & Optimization
2. Prerequisites Installation
3. Docker Compose Configuration
4. Local Infrastructure Setup
5. Resource Allocation Strategy
6. Running the System
7. Monitoring & Performance
8. Troubleshooting

---

## 1. System Requirements & Optimization

### Your Hardware Analysis

**Intel Ultra 19 185H Processor:**
- 16 cores (6 P-cores + 8 E-cores + 2 LP E-cores)
- Base: 2.3 GHz, Boost: 5.1 GHz
- Perfect for parallel processing (scrapers, signal processing)

**32GB RAM:**
- More than sufficient for entire TITAN stack
- Can run 5-10 scraper instances comfortably
- Redis, TimescaleDB, and all services simultaneously

**Recommended Resource Allocation:**

| Component | CPU Cores | RAM | Why |
|-----------|-----------|-----|-----|
| **Docker Desktop** | 12 cores | 24GB | Leave 4 cores + 8GB for Windows |
| Scraper Workers (5x) | 5 cores | 6GB | 1 core + 1.2GB each |
| TimescaleDB | 2 cores | 8GB | Database needs memory |
| Redis | 1 core | 2GB | In-memory cache |
| Backend API | 2 cores | 4GB | FastAPI + processing |
| Frontend (Next.js) | 1 core | 2GB | Development server |
| Other services | 1 core | 2GB | Monitoring, etc. |

---

## 2. Prerequisites Installation

### Step 1: Install Docker Desktop for Windows

```powershell
# Download from: https://www.docker.com/products/docker-desktop/

# After installation, configure Docker Desktop:
# Settings → Resources → Advanced
# - CPUs: 12
# - Memory: 24 GB
# - Swap: 2 GB
# - Disk image size: 100 GB
```

### Step 2: Install Python 3.11+

```powershell
# Download from: https://www.python.org/downloads/

# Or use winget:
winget install Python.Python.3.11

# Verify installation:
python --version  # Should show 3.11.x

# Install pip packages:
pip install playwright fastapi uvicorn redis pandas numpy
playwright install chromium
```

### Step 3: Install Node.js 18+

```powershell
# Download from: https://nodejs.org/

# Or use winget:
winget install OpenJS.NodeJS.LTS

# Verify:
node --version  # Should show v18.x or v20.x
npm --version

# Install global packages:
npm install -g pnpm  # Faster than npm
```

### Step 4: Install Git (if not already)

```powershell
winget install Git.Git

# Configure:
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

### Step 5: Install Visual Studio Code (Optional)

```powershell
winget install Microsoft.VisualStudioCode
```

---

## 3. Docker Compose Configuration

Create `docker-compose.local.yml` in project root:

```yaml
version: '3.8'

services:
  # TimescaleDB - Time-series database
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    container_name: titan-timescaledb
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: titan_betting
      POSTGRES_USER: titan
      POSTGRES_PASSWORD: titan_dev_password_change_in_prod
    volumes:
      - timescale_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 8G
    restart: unless-stopped
  
  # Redis - In-memory cache and pub/sub
  redis:
    image: redis:7-alpine
    container_name: titan-redis
    ports:
      - "6379:6379"
    command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 2G
    restart: unless-stopped
  
  # Redis Commander - Redis GUI (optional)
  redis-commander:
    image: rediscommander/redis-commander:latest
    container_name: titan-redis-gui
    ports:
      - "8081:8081"
    environment:
      - REDIS_HOSTS=local:redis:6379
    depends_on:
      - redis
    restart: unless-stopped
  
  # PgAdmin - PostgreSQL GUI (optional)
  pgadmin:
    image: dpage/pgadmin4:latest
    container_name: titan-pgadmin
    ports:
      - "5050:80"
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@titan.local
      PGADMIN_DEFAULT_PASSWORD: admin
    volumes:
      - pgadmin_data:/var/lib/pgadmin
    depends_on:
      - timescaledb
    restart: unless-stopped

volumes:
  timescale_data:
    driver: local
  redis_data:
    driver: local
  pgadmin_data:
    driver: local

networks:
  default:
    name: titan-network
```

---

## 4. Local Infrastructure Setup

### Step 1: Start Core Services

```powershell
# Navigate to project directory
cd C:\Users\sohai\Downloads\prediction-claude

# Start infrastructure services
docker-compose -f docker-compose.local.yml up -d

# Verify services are running
docker-compose -f docker-compose.local.yml ps

# Expected output:
# titan-timescaledb   running   0.0.0.0:5432->5432/tcp
# titan-redis         running   0.0.0.0:6379->6379/tcp
# titan-redis-gui     running   0.0.0.0:8081->8081/tcp
# titan-pgadmin       running   0.0.0.0:5050->80/tcp
```

### Step 2: Initialize Database Schema

Create `scripts/init_database.py`:

```python
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Connection details
conn_params = {
    'host': 'localhost',
    'port': 5432,
    'user': 'titan',
    'password': 'titan_dev_password_change_in_prod',
    'database': 'titan_betting'
}

# Connect and create schema
conn = psycopg2.connect(**conn_params)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cursor = conn.cursor()

# Enable TimescaleDB extension
cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

# Create market_ticks hypertable
cursor.execute("""
CREATE TABLE IF NOT EXISTS market_ticks (
    time            TIMESTAMPTZ NOT NULL,
    match_id        TEXT NOT NULL,
    bookmaker       TEXT NOT NULL,
    market_type     TEXT NOT NULL,
    team            TEXT,
    back_odds       DECIMAL(10,2),
    lay_odds        DECIMAL(10,2),
    is_suspended    BOOLEAN DEFAULT FALSE,
    stake_limit     INTEGER,
    volume_back     BIGINT,
    volume_lay      BIGINT,
    score_snapshot  JSONB,
    PRIMARY KEY (time, match_id, market_type, bookmaker)
);
""")

# Convert to hypertable
cursor.execute("""
SELECT create_hypertable('market_ticks', 'time', if_not_exists => TRUE);
""")

# Create indexes
cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_match_time 
ON market_ticks (match_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_suspended 
ON market_ticks (is_suspended, time) 
WHERE is_suspended = TRUE;
""")

# Create signals table
cursor.execute("""
CREATE TABLE IF NOT EXISTS signals (
    id              SERIAL PRIMARY KEY,
    signal_id       TEXT UNIQUE NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    match_id        TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    action          TEXT NOT NULL,
    market_type     TEXT NOT NULL,
    team            TEXT,
    odds            DECIMAL(10,2),
    confidence      DECIMAL(5,4),
    reasoning       TEXT,
    outcome         TEXT,
    pnl             DECIMAL(10,2)
);
""")

print("✅ Database schema initialized successfully!")

cursor.close()
conn.close()
```

Run initialization:

```powershell
python scripts/init_database.py
```

### Step 3: Set Up Environment Variables

Create `.env` file in project root:

```env
# Database
DATABASE_URL=postgresql://titan:titan_dev_password_change_in_prod@localhost:5432/titan_betting
TIMESCALE_HOST=localhost
TIMESCALE_PORT=5432
TIMESCALE_DB=titan_betting
TIMESCALE_USER=titan
TIMESCALE_PASSWORD=titan_dev_password_change_in_prod

# Redis
REDIS_URL=redis://localhost:6379
REDIS_HOST=localhost
REDIS_PORT=6379

# API
API_HOST=localhost
API_PORT=8000
SECRET_KEY=your-secret-key-change-in-production

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws

# Scraper
SCRAPER_HEADLESS=true
SCRAPER_WORKERS=5
TARGET_URL=https://sports.dafabet.com/in/live/sport/215-CRIC

# Proxy (optional - add your proxy list)
PROXY_LIST=

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/titan.log
```

### Step 4: Project Structure

```
C:\Users\sohai\Downloads\prediction-claude\
├── .env
├── docker-compose.local.yml
├── requirements.txt
├── package.json
├── README.md
│
├── scraper/
│   ├── __init__.py
│   ├── manager.py          # Scraper orchestrator
│   ├── worker.py           # Individual scraper worker
│   ├── parsers/
│   │   ├── dafabet.py      # Dafabet-specific parser
│   │   └── validators.py   # Data validation
│   └── proxies/
│       └── pool.py         # Proxy management
│
├── cortex/
│   ├── __init__.py
│   ├── processor.py        # Main signal processor
│   ├── strategies/
│   │   ├── panic_rebound.py
│   │   ├── mean_reversion.py
│   │   └── whale_shadow.py
│   ├── quality_gates/
│   │   ├── data_quality.py
│   │   ├── statistical.py
│   │   ├── context.py
│   │   ├── bookmaker.py
│   │   └── technical.py
│   └── circuit_breaker.py
│
├── backend/
│   ├── main.py             # FastAPI app
│   ├── websocket.py        # WebSocket handler
│   ├── models/
│   │   └── signal.py       # Data models
│   └── middleware/
│       └── rate_limit.py
│
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── app/
│   │   ├── page.tsx        # Main HUD
│   │   └── layout.tsx
│   ├── components/
│   │   ├── SignalCard.tsx
│   │   ├── MomentumGauge.tsx
│   │   └── ConnectionStatus.tsx
│   └── hooks/
│       ├── useWebSocket.ts
│       └── useAudioAlerts.ts
│
├── scripts/
│   ├── init_database.py
│   └── start_local.ps1     # Windows startup script
│
├── logs/
│   └── titan.log
│
└── docs/
    └── 06-local-windows-deployment.md
```

---

## 5. Resource Allocation Strategy

### Windows Task Manager Monitoring

Your laptop can handle all components simultaneously:

```
Total Resources: 16 cores, 32GB RAM
├── Windows OS: 4 cores, 8GB (reserved)
├── Docker Desktop: 12 cores, 24GB
│   ├── TimescaleDB: 2 cores, 8GB
│   ├── Redis: 1 core, 2GB
│   ├── Scrapers (5x): 5 cores, 6GB
│   ├── Backend: 2 cores, 4GB
│   ├── Frontend: 1 core, 2GB
│   └── Monitoring: 1 core, 2GB
└── Available for spikes: ~2GB buffer
```

### Optimizing for Cricket Matches

**During Inactive Periods (No Matches):**
```powershell
# Stop scrapers to save resources
docker-compose -f docker-compose.local.yml stop scraper-worker-*

# Keep database and Redis running
```

**During Active Matches (3-4 simultaneous):**
```powershell
# Start all scrapers
docker-compose -f docker-compose.local.yml up -d

# Monitor resource usage
docker stats
```

### Performance Optimization Tips

**1. Enable WSL 2 Backend (Recommended):**
```powershell
# Docker Desktop → Settings → General
# ✓ Use WSL 2 based engine

# Benefits:
# - 50% faster startup
# - Lower memory overhead
# - Better file system performance
```

**2. Allocate Swap Space:**
```
Docker Desktop → Resources → Advanced
Swap: 4 GB
```

**3. Disk Performance:**
- Store Docker volumes on SSD (not HDD)
- Disable Windows Defender real-time scanning for Docker folders

---

## 6. Running the System

### Quick Start Script

Create `scripts/start_local.ps1`:

```powershell
# TITAN Local Startup Script
Write-Host "🚀 Starting TITAN Cricket Betting Intelligence System..." -ForegroundColor Green

# Check if Docker is running
$dockerRunning = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker Desktop is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}

# Start infrastructure
Write-Host "`n📦 Starting infrastructure services..." -ForegroundColor Cyan
docker-compose -f docker-compose.local.yml up -d timescaledb redis

# Wait for services to be ready
Write-Host "⏳ Waiting for services to be ready..."
Start-Sleep -Seconds 10

# Check database connection
Write-Host "`n🔍 Checking database connection..."
python scripts/test_connection.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Database connection failed. Check logs." -ForegroundColor Red
    exit 1
}

# Start backend API
Write-Host "`n🔧 Starting backend API..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD; python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000"

# Wait for API to start
Start-Sleep -Seconds 5

# Start frontend
Write-Host "`n🎨 Starting frontend HUD..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD\frontend; npm run dev"

# Wait for frontend
Start-Sleep -Seconds 5

# Start scraper manager
Write-Host "`n🕷️  Starting scraper manager..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD; python -m scraper.manager"

Write-Host "`n✅ TITAN is now running!" -ForegroundColor Green
Write-Host "`n📊 Access Points:" -ForegroundColor Yellow
Write-Host "  - Frontend HUD:    http://localhost:3000"
Write-Host "  - Backend API:     http://localhost:8000"
Write-Host "  - API Docs:        http://localhost:8000/docs"
Write-Host "  - Redis GUI:       http://localhost:8081"
Write-Host "  - PgAdmin:         http://localhost:5050"
Write-Host "`n⏹️  To stop: Run scripts/stop_local.ps1" -ForegroundColor Yellow
```

Create `scripts/stop_local.ps1`:

```powershell
Write-Host "🛑 Stopping TITAN..." -ForegroundColor Red

# Stop Docker services
docker-compose -f docker-compose.local.yml down

# Kill all Python processes (scraper, backend)
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# Kill Node processes (frontend)
Get-Process node -ErrorAction SilentlyContinue | Where-Object {$_.Path -like "*prediction-claude*"} | Stop-Process -Force

Write-Host "✅ TITAN stopped successfully!" -ForegroundColor Green
```

### Manual Startup (Step by Step)

**Terminal 1: Infrastructure**
```powershell
docker-compose -f docker-compose.local.yml up
```

**Terminal 2: Backend API**
```powershell
cd C:\Users\sohai\Downloads\prediction-claude
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 3: Frontend**
```powershell
cd C:\Users\sohai\Downloads\prediction-claude\frontend
npm run dev
```

**Terminal 4: Scraper**
```powershell
cd C:\Users\sohai\Downloads\prediction-claude
python -m scraper.manager
```

**Terminal 5: Cortex (Signal Processor)**
```powershell
cd C:\Users\sohai\Downloads\prediction-claude
python -m cortex.processor
```

---

## 7. Monitoring & Performance

### Real-Time Monitoring Dashboard

Access via browser:
- **Redis Commander:** http://localhost:8081 (view cached data)
- **PgAdmin:** http://localhost:5050 (query database)
- **API Docs:** http://localhost:8000/docs (test endpoints)

### Windows Performance Monitor

```powershell
# Monitor Docker resource usage
docker stats

# Expected output:
CONTAINER           CPU %    MEM USAGE / LIMIT     NET I/O
titan-timescaledb   15%      6.5GB / 8GB           2MB / 1MB
titan-redis         5%       1.8GB / 2GB           500KB / 400KB
scraper-worker-1    25%      1.2GB / 1.2GB         10MB / 5MB
...
```

### Application Logs

```powershell
# View all logs
docker-compose -f docker-compose.local.yml logs -f

# View specific service
docker-compose -f docker-compose.local.yml logs -f timescaledb

# View Python logs
Get-Content -Path "logs\titan.log" -Wait

# View with filtering
Get-Content -Path "logs\titan.log" -Wait | Select-String "ERROR|SIGNAL"
```

### Performance Metrics

Create `scripts/monitor_performance.py`:

```python
import psutil
import time
from rich.console import Console
from rich.table import Table

console = Console()

def monitor_system():
    while True:
        table = Table(title="TITAN System Performance")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        # CPU
        cpu_percent = psutil.cpu_percent(interval=1, percpu=False)
        table.add_row("CPU Usage", f"{cpu_percent:.1f}%")
        
        # Memory
        mem = psutil.virtual_memory()
        table.add_row("Memory Used", f"{mem.used / (1024**3):.1f} GB / {mem.total / (1024**3):.1f} GB")
        table.add_row("Memory %", f"{mem.percent:.1f}%")
        
        # Disk
        disk = psutil.disk_usage('C:\\')
        table.add_row("Disk Free", f"{disk.free / (1024**3):.1f} GB")
        
        console.clear()
        console.print(table)
        time.sleep(2)

if __name__ == "__main__":
    monitor_system()
```

Run:
```powershell
pip install rich psutil
python scripts/monitor_performance.py
```

---

## 8. Troubleshooting

### Common Issues

#### Issue 1: Docker Desktop Won't Start

**Solution:**
```powershell
# Restart Docker service
Restart-Service -Name com.docker.service

# Or restart from GUI
# Right-click Docker Desktop tray icon → Restart
```

#### Issue 2: Port Already in Use

**Error:** `Error: bind: address already in use`

**Solution:**
```powershell
# Find process using port (example: 5432)
netstat -ano | findstr :5432

# Kill process by PID
taskkill /PID <PID> /F

# Or change port in docker-compose.local.yml
```

#### Issue 3: High Memory Usage

**Solution:**
```powershell
# Reduce Docker memory allocation
# Docker Desktop → Settings → Resources
# Memory: 20GB (instead of 24GB)

# Or reduce scraper workers
# In .env file:
SCRAPER_WORKERS=3  # Reduce from 5
```

#### Issue 4: Scraper Getting Blocked

**Solution:**
```python
# Add delays in scraper/worker.py
import random
await asyncio.sleep(random.uniform(3, 8))  # Random 3-8 second delays

# Use residential proxies (add to .env)
PROXY_LIST=http://proxy1:port,http://proxy2:port
```

#### Issue 5: Database Connection Timeout

**Solution:**
```powershell
# Check if TimescaleDB is running
docker ps | findstr timescaledb

# Restart database
docker-compose -f docker-compose.local.yml restart timescaledb

# Check logs
docker-compose -f docker-compose.local.yml logs timescaledb
```

### Performance Optimization Checklist

- [ ] WSL 2 backend enabled
- [ ] Docker allocated 12+ cores
- [ ] Docker allocated 20+ GB RAM
- [ ] Windows Defender exclusions added for Docker
- [ ] SSD storage for Docker volumes
- [ ] Latest Docker Desktop version
- [ ] Windows power plan set to "High Performance"
- [ ] Unnecessary background apps closed during matches

### Health Check Script

Create `scripts/health_check.py`:

```python
import requests
import psycopg2
import redis
from rich.console import Console

console = Console()

def check_health():
    checks = []
    
    # Check API
    try:
        r = requests.get("http://localhost:8000/health", timeout=5)
        checks.append(("Backend API", r.status_code == 200))
    except:
        checks.append(("Backend API", False))
    
    # Check Redis
    try:
        r = redis.Redis(host='localhost', port=6379)
        r.ping()
        checks.append(("Redis", True))
    except:
        checks.append(("Redis", False))
    
    # Check Database
    try:
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            user='titan',
            password='titan_dev_password_change_in_prod',
            database='titan_betting'
        )
        conn.close()
        checks.append(("TimescaleDB", True))
    except:
        checks.append(("TimescaleDB", False))
    
    # Print results
    console.print("\n[bold]TITAN Health Check[/bold]\n")
    for service, status in checks:
        emoji = "✅" if status else "❌"
        color = "green" if status else "red"
        console.print(f"{emoji} {service}: [{color}]{'UP' if status else 'DOWN'}[/{color}]")
    
    all_healthy = all(status for _, status in checks)
    if all_healthy:
        console.print("\n[green]✅ All systems operational![/green]")
    else:
        console.print("\n[red]❌ Some services are down. Check logs.[/red]")

if __name__ == "__main__":
    check_health()
```

Run:
```powershell
python scripts/health_check.py
```

---

## Quick Reference

### Daily Workflow

**Morning (Before Matches):**
```powershell
# Start TITAN
.\scripts\start_local.ps1

# Health check
python scripts\health_check.py

# Open HUD
Start-Process "http://localhost:3000"
```

**During Matches:**
- Keep HUD visible (20% of screen)
- Betting app on 80% of screen
- Audio alerts ON

**Evening (After Matches):**
```powershell
# Stop TITAN
.\scripts\stop_local.ps1

# Optional: Backup database
pg_dump -h localhost -U titan titan_betting > backup_$(Get-Date -Format "yyyy-MM-dd").sql
```

### Useful Commands

```powershell
# View live signals
python scripts/view_signals.py

# Query recent performance
python scripts/get_stats.py --last 7d

# Clear Redis cache
redis-cli FLUSHALL

# Reset database (WARNING: Deletes all data)
docker-compose -f docker-compose.local.yml down -v
python scripts/init_database.py
```

---

## Next Steps

1. ✅ Follow this guide to set up local environment
2. ✅ Test with paper trading (no real money)
3. ✅ Monitor performance for 10-20 matches
4. ✅ Tune quality gates based on results
5. ✅ Start with small real stakes when confident

---

_Your Intel Ultra 19 185H with 32GB RAM is more than capable of running the entire TITAN system locally. No cloud infrastructure needed!_

_End of Local Windows Deployment Guide_


