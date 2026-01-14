# TITAN Scripts

Operational scripts for managing the TITAN system.

## 📁 Directory Structure

```
scripts/
├── startup/         # Service startup scripts
├── shutdown/        # Service shutdown scripts  
├── management/      # System management utilities
└── legacy/          # Archived old scripts
```

## 🚀 Startup Scripts (`startup/`)

### Main Launcher
- **`start_all_windows.bat`** ⭐ **RECOMMENDED**
  - Starts all 5 services in separate windows
  - Automatic timing between services
  - Visible logs for monitoring
  - **Usage:** Double-click to run

### Individual Service Launchers
- **`start_docker.bat`** - Start Docker services (TimescaleDB, Redis, PgAdmin, Redis Commander)
- **`start_backend.bat`** - Start Backend API (port 8000)
- **`start_cortex.bat`** - Start Cortex AI Processor
- **`start_scraper.bat`** - Start Live Odds Scraper  
- **`start_frontend.bat`** - Start Frontend Dashboard (port 3000)

**Usage:** Double-click any `.bat` file to start that service in a new window.

## 🛑 Shutdown Scripts (`shutdown/`)

- **`stop_all.ps1`** - Stop all TITAN services cleanly
  - Stops Frontend (Node)
  - Stops Python services (Backend, Cortex, Scraper)
  - Stops Docker containers
  - Cleans up zombie processes

**Usage:** Right-click → "Run with PowerShell"

## 🔧 Management Scripts (`management/`)

- **`status.ps1`** - Check status of all services
  - Shows Docker container status
  - Checks Backend API health
  - Verifies Frontend accessibility
  - Counts running processes

- **`restart_all.ps1`** - Full system restart
  - Stops all services
  - Waits for clean shutdown
  - Starts all services fresh

**Usage:** Run from PowerShell: `.\scripts\management\status.ps1`

## 📝 Common Workflows

### Daily Startup
```powershell
# Easy way (recommended)
.\scripts\startup\start_all_windows.bat

# Manual way
.\scripts\startup\start_docker.bat      # Wait 15 sec
.\scripts\startup\start_backend.bat     # Wait 5 sec
.\scripts\startup\start_cortex.bat      # Wait 3 sec
.\scripts\startup\start_scraper.bat     # Wait 3 sec
.\scripts\startup\start_frontend.bat    # Wait 10 sec
```

### Check System Status
```powershell
.\scripts\management\status.ps1
```

### Full Restart
```powershell
.\scripts\management\restart_all.ps1
```

### Clean Shutdown
```powershell
.\scripts\shutdown\stop_all.ps1
```

## ⚙️ Environment Requirements

- **Windows 10/11**
- **PowerShell 5.1+**
- **Docker Desktop** (running)
- **Python 3.8+**
- **Node.js 18+**

## 🔍 Troubleshooting

### Scripts won't run
```powershell
# Enable script execution
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Services won't start
1. Check Docker Desktop is running
2. Verify ports are not in use (3000, 8000, 5432, 6379)
3. Run `status.ps1` to diagnose

### "Connecting..." on Dashboard
- Backend API not responding
- Run `restart_all.ps1` to fix

## 📚 Documentation

For detailed operations guide, see: [docs/deployment/OPERATIONS.md](../docs/deployment/OPERATIONS.md)
