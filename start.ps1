# ============================================================
# PHOENIX - Start All Services (Windows PowerShell)
#
# Usage:
#   .\start.ps1              # Start core infra + app services
#   .\start.ps1 -Dev         # Include dev tools (pgadmin, redis-commander)
#   .\start.ps1 -Monitor     # Include monitoring (prometheus, grafana)
#   .\start.ps1 -All         # Everything
#   .\start.ps1 -InfraOnly   # Only Redis + TimescaleDB
# ============================================================

param(
    [switch]$Dev,
    [switch]$Monitor,
    [switch]$All,
    [switch]$InfraOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PHOENIX - Starting Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Check prerequisites ---
Write-Host "[1/7] Checking prerequisites..." -ForegroundColor Yellow

# Check Docker
try {
    docker info | Out-Null
    Write-Host "  Docker Desktop: Running" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Docker Desktop is not running. Please start it first." -ForegroundColor Red
    exit 1
}

# Check .env file
if (-not (Test-Path "$ProjectRoot\.env")) {
    Write-Host "  WARNING: .env file not found. Copying from .env.example..." -ForegroundColor Yellow
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host "  Created .env from .env.example. Edit it with your values." -ForegroundColor Green
}

# --- Step 2: Start infrastructure (Redis + TimescaleDB) ---
Write-Host ""
Write-Host "[2/7] Starting infrastructure (Redis + TimescaleDB)..." -ForegroundColor Yellow

docker compose up -d redis timescaledb
Start-Sleep -Seconds 5

# Wait for health checks
Write-Host "  Waiting for health checks..."
$retries = 0
$maxRetries = 30
while ($retries -lt $maxRetries) {
    $redisOk = docker exec phoenix-redis redis-cli ping 2>$null
    $pgOk = docker exec phoenix-timescaledb pg_isready -U phoenix 2>$null
    
    if ($redisOk -eq "PONG" -and $pgOk -match "accepting") {
        Write-Host "  Redis: Healthy" -ForegroundColor Green
        Write-Host "  TimescaleDB: Healthy" -ForegroundColor Green
        break
    }
    
    $retries++
    Start-Sleep -Seconds 2
}

if ($retries -ge $maxRetries) {
    Write-Host "  ERROR: Infrastructure services failed to start." -ForegroundColor Red
    exit 1
}

if ($InfraOnly) {
    Write-Host ""
    Write-Host "Infrastructure started. Ports:" -ForegroundColor Green
    Write-Host "  Redis:       localhost:6379"
    Write-Host "  TimescaleDB: localhost:5432"
    exit 0
}

# --- Step 3: Initialize database schema ---
Write-Host ""
Write-Host "[3/7] Initializing database schema..." -ForegroundColor Yellow

python -c "
import asyncio
from shared.db import get_engine
from sqlalchemy import text

async def init():
    engine = get_engine()
    async with engine.begin() as conn:
        # Create TimescaleDB extension
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
        print('  TimescaleDB extension: OK')

try:
    asyncio.run(init())
except Exception as e:
    print(f'  Schema init skipped (run manually): {e}')
"

# --- Step 4: Start Backend API ---
Write-Host ""
Write-Host "[4/7] Starting Backend API (port 8000)..." -ForegroundColor Yellow

$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:ProjectRoot
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
}
Start-Sleep -Seconds 3
Write-Host "  Backend API: Started (PID: $($backendJob.Id))" -ForegroundColor Green

# --- Step 5: Start Scraper ---
Write-Host ""
Write-Host "[5/7] Starting Scraper..." -ForegroundColor Yellow

$scraperJob = Start-Job -ScriptBlock {
    Set-Location $using:ProjectRoot
    python -m scraper.manager
}
Write-Host "  Scraper: Started (PID: $($scraperJob.Id))" -ForegroundColor Green

# --- Step 6: Start RL Trainer ---
Write-Host ""
Write-Host "[6/7] Starting RL Trainer..." -ForegroundColor Yellow

$rlJob = Start-Job -ScriptBlock {
    Set-Location $using:ProjectRoot
    python -m rl.trainer
}
Write-Host "  RL Trainer: Started (PID: $($rlJob.Id))" -ForegroundColor Green

# --- Step 7: Start dev/monitoring tools ---
if ($Dev -or $All) {
    Write-Host ""
    Write-Host "[7/7] Starting dev tools..." -ForegroundColor Yellow
    docker compose --profile dev up -d pgadmin redis-commander
    Write-Host "  PgAdmin:          http://localhost:5050" -ForegroundColor Cyan
    Write-Host "  Redis Commander:  http://localhost:8081" -ForegroundColor Cyan
}

if ($Monitor -or $All) {
    Write-Host ""
    Write-Host "[7/7] Starting monitoring..." -ForegroundColor Yellow
    docker compose --profile monitor up -d prometheus grafana
    Write-Host "  Prometheus:  http://localhost:9090" -ForegroundColor Cyan
    Write-Host "  Grafana:     http://localhost:3001" -ForegroundColor Cyan
}

# --- Summary ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  PHOENIX - All Services Running" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Backend API:     http://localhost:8000" -ForegroundColor White
Write-Host "  API Health:      http://localhost:8000/api/health" -ForegroundColor White
Write-Host "  API Docs:        http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "  Redis:           localhost:6379" -ForegroundColor White
Write-Host "  TimescaleDB:     localhost:5432" -ForegroundColor White
Write-Host ""
Write-Host "  Stop all:  .\stop.ps1" -ForegroundColor Yellow
Write-Host ""

# Save PIDs for stop script
@{
    backend = $backendJob.Id
    scraper = $scraperJob.Id
    rl_trainer = $rlJob.Id
} | ConvertTo-Json | Set-Content "$ProjectRoot\.phoenix_pids.json"
