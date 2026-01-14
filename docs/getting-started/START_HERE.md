# 🚀 TITAN System - Quick Start Guide

## Starting the System (Easy Way)

### Option 1: Start Everything at Once
Double-click: **`scripts/start_all_windows.bat`**

This will automatically open 5 windows:
1. ✅ Docker Services
2. ✅ Backend API
3. ✅ Cortex Processor  
4. ✅ Scraper
5. ✅ Frontend Dashboard

Wait **30 seconds**, then open: **http://localhost:3000**

---

### Option 2: Start Services Individually

Double-click each file in order:

1. **`scripts/start_docker.bat`** *(wait 15 seconds)*
2. **`scripts/start_backend.bat`** *(wait 5 seconds)*
3. **`scripts/start_cortex.bat`** *(wait 3 seconds)*
4. **`scripts/start_scraper.bat`** *(wait 3 seconds)*
5. **`scripts/start_frontend.bat`** *(wait 10 seconds)*

Then open: **http://localhost:3000**

---

## Stopping the System

### Easy Way
Double-click: **`scripts/stop_all.ps1`**

*(Right-click > "Run with PowerShell")*

### Manual Way
Close each terminal window (press `Ctrl+C` first in each window)

---

## What You Should See

### ✅ Healthy System
- Dashboard shows **"Connected"** (green) at the top
- Live matches appear within 30 seconds
- 5 terminal windows are open and showing logs

### ❌ Connection Issues
- Dashboard shows **"Connecting..."** (yellow/orange)
- **Solution**: 
  1. Run `scripts/stop_all.ps1`
  2. Wait 10 seconds
  3. Run `scripts/start_all_windows.bat` again

---

## Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| 🎯 **Dashboard** | http://localhost:3000 | Main UI |
| 🔌 **Backend API** | http://localhost:8000 | REST API |
| 📚 **API Docs** | http://localhost:8000/docs | API Reference |
| 🗄️ **PgAdmin** | http://localhost:5050 | Database Admin |
| 📊 **Redis** | http://localhost:8081 | Redis Browser |

### PgAdmin Login
- Email: `admin@titan.com`
- Password: `titan_admin_2025`

---

## Troubleshooting

### "Connecting..." Status Persists
**Cause**: Backend not running or port conflict

**Fix**:
```powershell
# Stop everything
.\scripts\stop_all.ps1

# Check status
.\scripts\status.ps1

# Start again
.\scripts\start_all_windows.bat
```

### Port Already in Use
**Cause**: Previous instance still running

**Fix**:
```powershell
# Force stop all Python/Node
Get-Process python,node | Stop-Process -Force

# Stop Docker
docker-compose -f docker-compose.local.yml down

# Start fresh
.\scripts\start_all_windows.bat
```

### Docker Won't Start
**Cause**: Docker Desktop not running

**Fix**:
1. Open Docker Desktop
2. Wait for it to fully start (green icon in system tray)
3. Run `scripts/start_docker.bat` again

### No Live Matches
**Cause**: No live matches on Micro999 right now, or scraper starting

**Fix**:
1. Check https://www.micro999.co/game/4 manually
2. If matches exist, wait 30 seconds for scraper to detect
3. Matches with "Live Now" will appear automatically

---

## Daily Workflow

### Morning
1. Double-click `scripts/start_all_windows.bat`
2. Wait 30 seconds
3. Open http://localhost:3000
4. Verify "Connected" status

### During Day
- Keep windows open
- Services auto-reload on code changes
- Monitor logs in terminal windows

### Evening
1. Double-click `scripts/stop_all.ps1`
2. Close all terminal windows

---

## File Organization

```
prediction-claude/
├── scripts/
│   ├── start_all_windows.bat  ⭐ START HERE
│   ├── start_docker.bat       (Docker services)
│   ├── start_backend.bat      (Backend API)
│   ├── start_cortex.bat       (Cortex processor)
│   ├── start_scraper.bat      (Scraper)
│   ├── start_frontend.bat     (Frontend dashboard)
│   ├── stop_all.ps1           (Stop everything)
│   ├── status.ps1             (Check status)
│   └── restart_all.ps1        (Full restart)
├── START_HERE.md              📖 This file
└── OPERATIONS.md              📚 Detailed guide
```

---

## Need More Help?

- **Detailed Guide**: See `OPERATIONS.md`
- **Status Check**: Run `scripts/status.ps1`
- **Logs**: Check terminal windows for errors

---

**Last Updated**: December 7, 2025  
**Version**: TITAN v1.0

