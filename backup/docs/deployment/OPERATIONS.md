# TITAN System Operations Guide

## Quick Start

### Starting All Services
```powershell
.\scripts\start_all.ps1
```

### Stopping All Services  
```powershell
.\scripts\stop_all.ps1
```

### Restarting All Services
```powershell
.\scripts\restart_all.ps1
```

### Checking Service Status
```powershell
.\scripts\status.ps1
```

---

## System Architecture

TITAN consists of 7 services that must run simultaneously:

### Docker Services (4)
1. **TimescaleDB** - Time-series database (Port 5432)
2. **Redis** - Message broker & cache (Port 6379)
3. **PgAdmin** - Database admin interface (Port 5050)
4. **Redis Commander** - Redis admin interface (Port 8081)

### Python Services (3)
1. **Backend API** - FastAPI REST & WebSocket server (Port 8000)
2. **Cortex Processor** - ML signal generation engine
3. **Scraper** - Live odds data collector

### Frontend Service (1)
1. **Next.js Dashboard** - React-based UI (Port 3000)

---

## Standard Startup Procedure

### Step 1: Start Docker Services
```powershell
docker-compose -f docker-compose.local.yml up -d
```

**Wait 10 seconds** for services to initialize.

Verify with:
```powershell
docker ps
```

You should see 4 containers running:
- `titan_timescaledb`
- `titan_redis`
- `titan_pgadmin`
- `titan_redis_commander`

### Step 2: Start Backend API
```powershell
# Open a new PowerShell terminal window
cd C:\Users\sohai\Downloads\prediction-claude
python -m uvicorn backend.main:app --reload --port 8000
```

**Wait 5 seconds** for API to start.

Verify at: http://localhost:8000/api/health

### Step 3: Start Cortex Processor
```powershell
# Open another new PowerShell terminal window
cd C:\Users\sohai\Downloads\prediction-claude
python -m cortex.processor
```

**Wait 3 seconds** for processor to start.

### Step 4: Start Scraper
```powershell
# Open another new PowerShell terminal window
cd C:\Users\sohai\Downloads\prediction-claude
python -m scraper.manager
```

**Wait 10 seconds** for browser automation to initialize.

### Step 5: Start Frontend
```powershell
# Open another new PowerShell terminal window
cd C:\Users\sohai\Downloads\prediction-claude\frontend
npm run dev
```

**Wait 10 seconds** for Next.js to compile.

Verify at: http://localhost:3000

---

## Standard Shutdown Procedure

### Step 1: Stop Frontend
Press `Ctrl+C` in the frontend terminal, or:
```powershell
Get-Process node | Stop-Process -Force
```

### Step 2: Stop Python Services
Press `Ctrl+C` in each Python terminal, or:
```powershell
Get-Process python | Stop-Process -Force
```

### Step 3: Stop Docker Services
```powershell
docker-compose -f docker-compose.local.yml down
```

---

## Troubleshooting

### Issue: "Connecting..." Status on Dashboard

**Symptoms:**
- Dashboard shows "Connecting..." at the top
- WebSocket not connecting
- Live matches not loading

**Cause:**
Backend API is not running or not responding on port 8000.

**Solution:**
```powershell
# 1. Check if backend is running
.\scripts\status.ps1

# 2. If "Backend API: Not responding", restart all services
.\scripts\restart_all.ps1

# 3. Wait 30 seconds for all services to initialize

# 4. Verify in browser at http://localhost:3000
# You should see "Connected" (green) status
```

### Issue: "No Live Matches" with Data in Redis

**Symptoms:**
- Dashboard shows "No Live Matches Found"
- Redis has matches (`redis-cli GET active_matches` returns data)
- Backend API is running

**Cause:**
Match data has `null` values for `overs` or `run_rate`, which are now handled gracefully.

**Solution:**
This is normal for matches where only betting odds are available (e.g., pre-match or non-cricket events on Micro999). Matches will display "N/A" for missing cricket statistics.

### Issue: PgAdmin Won't Start

**Symptoms:**
- PgAdmin container keeps restarting
- Error: "does not appear to be a valid email address"

**Cause:**
Invalid email configuration with `.local` domain.

**Solution:**
Already fixed in `docker-compose.local.yml`. Email is set to `admin@titan.com`.

### Issue: Port Already in Use

**Symptoms:**
- Error: "Address already in use"
- Services won't start

**Cause:**
Previous instance still running.

**Solution:**
```powershell
# Stop all services first
.\scripts\stop_all.ps1

# Wait 5 seconds
Start-Sleep -Seconds 5

# Start again
.\scripts\start_all.ps1
```

### Issue: Scraper Not Detecting Matches

**Symptoms:**
- No matches in Redis (`GET active_matches` returns `[]`)
- Scraper logs show "Filtered X non-live matches"

**Cause:**
No live matches currently available on Micro999, or browser automation failed.

**Solution:**
1. Check Micro999 manually: https://www.micro999.co/game/4
2. Verify there are matches with "Live Now" text
3. If matches exist but scraper doesn't detect:
   ```powershell
   # Restart scraper only
   Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*scraper.manager*"} | Stop-Process -Force
   python -m scraper.manager
   ```

---

## Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| **Frontend Dashboard** | http://localhost:3000 | Main application UI |
| **Backend API** | http://localhost:8000 | REST API & WebSocket |
| **API Docs** | http://localhost:8000/docs | Interactive API documentation |
| **PgAdmin** | http://localhost:5050 | Database admin (admin@titan.com / titan_admin_2025) |
| **Redis Commander** | http://localhost:8081 | Redis browser |

---

## Monitoring & Logs

### Real-time Logs

Each Python service logs to its terminal. Key log lines:

**Backend API:**
- ✅ Redis connection established
- ✅ Database connection established  
- ✅ WebSocket connection accepted

**Scraper:**
- 🌐 Scraper starting (worker 1)
- 📊 Extracted X odds from X live matches
- 📊 Updated active_matches in Redis: X matches

**Cortex:**
- 🧠 Cortex Processor starting...
- ⚡ Generated X signals

### Check Active Matches in Redis
```powershell
docker exec -it titan_redis redis-cli GET active_matches
```

### Check Database Connectivity
```powershell
# Inside PgAdmin (http://localhost:5050):
# - Server: localhost:5432
# - Database: titan
# - Username: titan_user
# - Password: titan_db_2025

# Query:
SELECT COUNT(*) FROM market_ticks WHERE timestamp > NOW() - INTERVAL '1 hour';
```

---

## Performance Tips

1. **Keep Docker Running**: Don't stop Docker services frequently. They persist data.

2. **Hot Reload**: Backend and Frontend support hot reload. Just edit files and save.

3. **Memory Usage**: 
   - Docker services: ~1.5 GB
   - Python services: ~500 MB each
   - Frontend: ~200 MB
   - Total: ~3-4 GB RAM required

4. **Browser Cache**: Clear browser cache if dashboard doesn't update after code changes.

---

## Daily Workflow

### Morning Startup
```powershell
# Full system start
.\scripts\start_all.ps1

# Wait 1 minute
# Open http://localhost:3000
# Verify "Connected" status
```

### Development Session
```powershell
# Backend/Frontend auto-reload on file changes
# Just edit and save files
# Refresh browser if needed
```

### Evening Shutdown
```powershell
# Clean shutdown
.\scripts\stop_all.ps1
```

### Emergency Stop
```powershell
# Force stop everything
Get-Process python,node | Stop-Process -Force
docker-compose -f docker-compose.local.yml down
```

---

## Automation (Optional)

### Create a Desktop Shortcut

1. Create `Start-TITAN.bat`:
```batch
@echo off
cd C:\Users\sohai\Downloads\prediction-claude
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1
pause
```

2. Create shortcut on desktop pointing to `Start-TITAN.bat`

3. Right-click shortcut > Properties > Change Icon > Choose icon

---

## Health Check Checklist

Before assuming system is working correctly, verify:

- [ ] Docker: 4 containers running (`docker ps`)
- [ ] Backend: http://localhost:8000/api/health returns `{"status":"healthy"}`
- [ ] Frontend: http://localhost:3000 loads
- [ ] WebSocket: Dashboard shows "Connected" (green)
- [ ] Data Flow: Live matches appear within 30 seconds
- [ ] Scraper: Redis has `active_matches` key with data
- [ ] Database: PgAdmin connects successfully

---

## Common Mistakes to Avoid

1. ❌ **Starting services in wrong order** → Always start Docker first
2. ❌ **Not waiting between steps** → Each service needs time to initialize
3. ❌ **Running scripts from wrong directory** → Always run from project root
4. ❌ **Multiple instances** → Stop all before starting again
5. ❌ **Ignoring logs** → Check terminal output for errors
6. ❌ **Skipping status check** → Always run `.\scripts\status.ps1` if unsure

---

## Advanced Operations

### Restart Only One Service

**Backend API:**
```powershell
Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*uvicorn*"} | Stop-Process -Force
python -m uvicorn backend.main:app --reload --port 8000
```

**Scraper:**
```powershell
Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*scraper.manager*"} | Stop-Process -Force
python -m scraper.manager
```

**Frontend:**
```powershell
Get-Process node | Stop-Process -Force
cd frontend
npm run dev
```

### Clear All Data (Reset)
```powershell
# WARNING: This deletes all historical data!
docker-compose -f docker-compose.local.yml down -v
docker-compose -f docker-compose.local.yml up -d
```

### View Logs from All Services
```powershell
# Docker logs
docker-compose -f docker-compose.local.yml logs -f

# Backend logs (check terminal)
# Scraper logs (check terminal)
# Cortex logs (check terminal)
```

---

## Support

If services still won't connect after following this guide:

1. Check Windows Firewall isn't blocking ports 3000, 8000, 8081, 5050
2. Verify no other applications are using these ports
3. Restart your computer (clears all ports and processes)
4. Check `.\scripts\status.ps1` output carefully
5. Look for error messages in terminal windows

---

**Last Updated:** December 7, 2025  
**System Version:** TITAN v1.0  
**Maintainer:** Betting Intelligence Team

