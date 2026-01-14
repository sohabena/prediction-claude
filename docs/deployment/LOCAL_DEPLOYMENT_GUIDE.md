# 🚀 TITAN Local Deployment Guide

**Last Updated**: December 6, 2025  
**Version**: 2.0 (Production Foundation)  
**Deployment Type**: Local Windows System

---

## 📋 PREREQUISITES

### **1. Software Requirements:**
- ✅ **Python 3.10+** (Check: `python --version`)
- ✅ **Node.js 18+** (Check: `node --version`)
- ✅ **Docker Desktop** (for Redis, TimescaleDB)
- ✅ **Git** (for version control)

### **2. API Keys (Required):**
```bash
# Get free API keys from:
CRICAPI_KEY=your_key_here          # https://www.cricapi.com/ (Free: 100 req/hour)
OPENWEATHER_API_KEY=your_key_here  # https://openweathermap.org/ (Free: 60 req/min)
```

### **3. Hardware Requirements:**
- **RAM**: 4GB minimum (8GB recommended)
- **CPU**: 2 cores minimum (4 cores recommended)
- **Disk**: 10GB free space

---

## 🏗️ STEP 1: INITIAL SETUP

### **A. Clone & Navigate:**
```bash
cd C:\Users\sohai\Downloads\prediction-claude
```

### **B. Create Environment File:**
Create `.env` file in project root:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/titan_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# API Keys
CRICAPI_KEY=your_cricapi_key_here
OPENWEATHER_API_KEY=your_openweather_key_here

# Backend
API_PORT=8001
API_RELOAD=true

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8001
NEXT_PUBLIC_WS_URL=ws://localhost:8001

# Auto-Bet Configuration
AUTOBET_ENABLED=false  # Set to 'true' to enable auto-betting
AUTOBET_MIN_CONFIDENCE=0.75
AUTOBET_MIN_EDGE=0.05
AUTOBET_KELLY_MULTIPLIER=0.25
```

### **C. Install Python Dependencies:**
```bash
pip install -r requirements.txt
```

### **D. Install Frontend Dependencies:**
```bash
cd frontend
npm install
cd ..
```

---

## 🐳 STEP 2: START DOCKER SERVICES

### **A. Start Docker Containers:**
```bash
docker-compose -f docker-compose.local.yml up -d
```

This starts:
- ✅ Redis (port 6379)
- ✅ TimescaleDB/PostgreSQL (port 5432)
- ✅ pgAdmin (port 5050)
- ✅ Redis Commander (port 8081)

### **B. Verify Docker Services:**
```bash
# Check containers are running
docker ps

# Expected output:
# - redis
# - timescaledb
# - pgadmin
# - redis-commander
```

### **C. Initialize Database:**
```bash
# Create tables and seed default budget
python -m backend.db.init_schema

# Expected output:
# ✅ Database schema initialized
# ✅ Default budget seeded ($50,000)
```

---

## 🎯 STEP 3: START TITAN COMPONENTS

You have **3 options** for running TITAN:

### **OPTION 1: Master Startup Script (Recommended)** ⭐

```bash
python start_titan.py
```

This starts everything in order:
1. Backend API (FastAPI)
2. Signal Orchestrator (24/7 signal generation)
3. Auto-Bet Engine (if enabled)
4. Frontend (Next.js)

### **OPTION 2: Manual (For Debugging)**

**Terminal 1 - Backend API:**
```bash
cd backend
set PYTHONPATH=..
python -m uvicorn main:app --port 8001 --reload
```

**Terminal 2 - Signal Orchestrator:**
```bash
cd backend
set PYTHONPATH=..
python orchestrator/signal_orchestrator.py
```

**Terminal 3 - Auto-Bet Engine (Optional):**
```bash
cd backend
set PYTHONPATH=..
python services/auto_bet_engine_integrated.py
```

**Terminal 4 - Frontend:**
```bash
cd frontend
npm run dev
```

### **OPTION 3: PowerShell Script**
```powershell
.\scripts\start_local.ps1
```

---

## 🌐 STEP 4: ACCESS TITAN

### **Main Applications:**
| Service | URL | Purpose |
|---------|-----|---------|
| **TITAN Dashboard** | http://localhost:3000 | Main UI |
| **Backend API** | http://localhost:8001 | REST API |
| **API Docs** | http://localhost:8001/docs | Interactive API docs |
| **pgAdmin** | http://localhost:5050 | Database admin |
| **Redis Commander** | http://localhost:8081 | Redis browser |

### **Default Credentials:**
```
pgAdmin:
  Email: admin@titan.com
  Password: admin

TimescaleDB:
  Host: localhost
  Port: 5432
  Database: titan_db
  User: user
  Password: password
```

---

## ✅ STEP 5: VERIFY SYSTEM IS WORKING

### **A. Check Backend Health:**
```bash
# Test health endpoint
curl http://localhost:8001/api/health

# Expected output:
# {"status": "healthy", "redis": "connected", ...}
```

### **B. Check WebSocket Connection:**
Open browser console on http://localhost:3000:
```
✅ WebSocket connected
✅ Received welcome message
```

### **C. Generate Test Signals:**
```bash
# In a new terminal
python scripts/test_ui_signals.py

# This publishes test signals to Redis
# You should see them appear in the UI immediately
```

### **D. Check Database:**
```bash
# View pgAdmin at http://localhost:5050
# Connect to titan_db
# Check tables: betting_budgets, virtual_bets, odds_snapshots
```

---

## 🤖 STEP 6: ENABLE AUTO-BETTING (OPTIONAL)

### **A. Initialize Budget:**
1. Go to http://localhost:3000
2. Click "Initialize Budget" in Budget Panel
3. Set initial amount (default: $50,000)

### **B. Configure Auto-Bet:**
Edit `.env`:
```bash
AUTOBET_ENABLED=true
AUTOBET_MIN_CONFIDENCE=0.75  # Only bet on 75%+ confidence signals
AUTOBET_MIN_EDGE=0.05        # Require 5% edge
AUTOBET_KELLY_MULTIPLIER=0.25 # Quarter Kelly (conservative)
```

### **C. Restart Auto-Bet Engine:**
```bash
# Stop existing engine (Ctrl+C)
# Restart
python backend/services/auto_bet_engine_integrated.py
```

### **D. Monitor Auto-Bets:**
- Check UI: "Active Bets" section
- Check logs: Look for "AUTO-BET PLACED" messages
- Check audit log in Redis: `autobet:audit_log`

---

## 🎛️ SYSTEM CONFIGURATION

### **Risk Limits (Edit `backend/services/risk_manager.py`):**
```python
class RiskLimits:
    max_bet_percent: float = 5.0       # Max 5% per bet
    max_match_exposure: float = 20.0   # Max 20% per match
    max_daily_loss: float = 10.0       # Kill switch at -10%
    min_edge_percent: float = 3.0      # Require 3% edge
    min_confidence: float = 0.65       # Require 65% confidence
```

### **Signal Orchestrator (Edit `backend/orchestrator/signal_orchestrator.py`):**
```python
# Polling intervals
match_monitor_interval = 10  # seconds
signal_processing_interval = 5  # seconds
odds_snapshot_interval = 5  # seconds
```

### **Auto-Bet Engine (Edit `.env`):**
```bash
AUTOBET_MIN_CONFIDENCE=0.75    # 75% minimum
AUTOBET_MIN_EDGE=0.05          # 5% edge
AUTOBET_KELLY_MULTIPLIER=0.25  # Quarter Kelly
```

---

## 🔧 TROUBLESHOOTING

### **Problem 1: Backend won't start**
```bash
# Error: ModuleNotFoundError
# Solution: Set PYTHONPATH
cd backend
set PYTHONPATH=..
python -m uvicorn main:app --port 8001
```

### **Problem 2: Redis connection failed**
```bash
# Check Docker is running
docker ps

# Restart Redis
docker restart <redis-container-id>

# Check Redis is accessible
redis-cli ping
# Should return: PONG
```

### **Problem 3: Database connection failed**
```bash
# Check TimescaleDB is running
docker logs <timescaledb-container-id>

# Recreate database
python -m backend.db.init_schema
```

### **Problem 4: Frontend not connecting to backend**
```bash
# Check .env has correct URLs
NEXT_PUBLIC_API_URL=http://localhost:8001
NEXT_PUBLIC_WS_URL=ws://localhost:8001

# Restart frontend
cd frontend
npm run dev
```

### **Problem 5: No signals appearing**
```bash
# Generate test signals
python scripts/test_ui_signals.py

# Check orchestrator is running
# Check for logs: "📡 Signal published"
```

### **Problem 6: Auto-bet not placing bets**
```bash
# Check auto-bet is enabled
# .env: AUTOBET_ENABLED=true

# Check logs for rejection reasons:
# - Confidence too low
# - Edge too small
# - Risk limits exceeded

# Check budget is initialized
# Go to UI → Budget Panel → Initialize Budget
```

---

## 📊 MONITORING & LOGS

### **View Logs:**
```bash
# Backend API logs
# Printed to console

# Signal Orchestrator logs
# Printed to console with 🎯 prefix

# Auto-Bet Engine logs
# Printed to console with 🤖 prefix

# Save logs to file:
python orchestrator/signal_orchestrator.py > logs/orchestrator.log 2>&1
```

### **Redis Monitoring:**
```bash
# View Redis data
http://localhost:8081

# Key patterns:
# - signals: signal_history (last 100 signals)
# - odds:snapshot:* (latest odds)
# - odds:history:* (1-hour history)
# - risk:* (risk management state)
# - autobet:* (auto-bet audit logs)
```

### **Database Queries:**
```sql
-- View recent bets
SELECT * FROM virtual_bets ORDER BY placed_at DESC LIMIT 10;

-- View budget status
SELECT * FROM betting_budgets WHERE match_id IS NULL;

-- View odds history
SELECT * FROM odds_snapshots 
WHERE match_id = 'your_match_id' 
ORDER BY timestamp DESC LIMIT 100;
```

---

## 🛑 SHUTTING DOWN

### **Stop All Services:**
```bash
# Option 1: Stop startup script (Ctrl+C)

# Option 2: Manual shutdown
# Stop each terminal with Ctrl+C

# Option 3: Stop Docker
docker-compose -f docker-compose.local.yml down

# Option 4: Emergency stop (for auto-bet only)
# In Python console:
from backend.services.auto_bet_engine_integrated import get_auto_bet_engine
engine = get_auto_bet_engine()
engine.emergency_stop()
```

---

## 🔄 UPDATING TITAN

### **Pull Latest Code:**
```bash
git pull origin main
```

### **Update Dependencies:**
```bash
# Python
pip install -r requirements.txt --upgrade

# Frontend
cd frontend
npm install
cd ..
```

### **Run Database Migrations:**
```bash
python -m backend.db.migrate_match_budgets
```

---

## 📈 PERFORMANCE TUNING

### **For Better Performance:**

**1. Increase Docker Resources:**
- Docker Desktop → Settings → Resources
- CPU: 4 cores
- Memory: 8GB

**2. Optimize Database:**
```sql
-- Create additional indexes
CREATE INDEX idx_virtual_bets_match_placed 
ON virtual_bets(match_id, placed_at);

-- Analyze tables
ANALYZE virtual_bets;
ANALYZE odds_snapshots;
```

**3. Adjust Polling Intervals:**
```python
# In signal_orchestrator.py
poll_interval = 10  # Reduce for faster signals (but more CPU)
```

**4. Enable TimescaleDB Compression:**
```sql
-- Compress old odds data
SELECT add_compression_policy('odds_snapshots', INTERVAL '7 days');
```

---

## 🎯 NEXT STEPS AFTER DEPLOYMENT

### **1. Test with Paper Trading:**
- Enable auto-bet with small virtual budget
- Monitor for 1-2 days
- Review betting history and P&L

### **2. Tune Risk Parameters:**
- Adjust based on observed win rate
- Fine-tune Kelly multiplier
- Set appropriate stop losses

### **3. Monitor System Health:**
- Check logs daily
- Review auto-bet audit trail
- Ensure no memory leaks

### **4. Backtest Strategies:**
- Collect historical data for 1 month
- Run backtests on collected data
- Optimize strategy parameters

---

## 📞 SUPPORT

### **Common Issues:**
- Check `TROUBLESHOOTING` section above
- Review logs for error messages
- Check Docker containers are running
- Verify API keys are set correctly

### **Debug Mode:**
```bash
# Enable debug logging
# In .env:
LOG_LEVEL=DEBUG

# Run with verbose output
python orchestrator/signal_orchestrator.py --log-level DEBUG
```

---

## ✅ DEPLOYMENT CHECKLIST

Before going live with real money:

- [ ] All Docker containers running
- [ ] Database initialized with schema
- [ ] Budget initialized in UI
- [ ] Test signals appearing in UI
- [ ] Risk limits configured appropriately
- [ ] Auto-bet tested with small amounts
- [ ] Logs reviewed for errors
- [ ] Emergency stop tested
- [ ] Backup database manually
- [ ] Monitor for 24 hours without issues

---

**🎉 Congratulations! TITAN is now running locally. Happy betting (responsibly)!** 🏆

**Remember**: Start with paper trading. Never risk more than you can afford to lose.

