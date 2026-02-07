# ============================================================
# PHOENIX - Stop All Services (Windows PowerShell)
#
# Usage:
#   .\stop.ps1              # Stop all services gracefully
#   .\stop.ps1 -KeepInfra   # Stop app services, keep Redis + DB
# ============================================================

param(
    [switch]$KeepInfra
)

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  PHOENIX - Stopping Services" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# --- Stop PowerShell background jobs ---
Write-Host "[1/3] Stopping application services..." -ForegroundColor Yellow

$pidFile = "$ProjectRoot\.phoenix_pids.json"
if (Test-Path $pidFile) {
    $pids = Get-Content $pidFile | ConvertFrom-Json
    
    foreach ($service in @("backend", "scraper", "rl_trainer")) {
        $jobId = $pids.$service
        if ($jobId) {
            try {
                Stop-Job -Id $jobId -ErrorAction SilentlyContinue
                Remove-Job -Id $jobId -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped: $service (Job $jobId)" -ForegroundColor Green
            } catch {
                Write-Host "  Already stopped: $service" -ForegroundColor Gray
            }
        }
    }
    
    Remove-Item $pidFile -ErrorAction SilentlyContinue
} else {
    Write-Host "  No PID file found. Stopping any Python processes..." -ForegroundColor Yellow
    # Fallback: stop known Python processes
    Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.MainWindowTitle -eq "" 
    } | Stop-Process -Force -ErrorAction SilentlyContinue
}

# --- Stop Docker services ---
Write-Host ""
Write-Host "[2/3] Stopping Docker services..." -ForegroundColor Yellow

if ($KeepInfra) {
    # Stop only app containers, keep infra
    docker compose --profile dev --profile monitor stop pgadmin redis-commander prometheus grafana 2>$null
    Write-Host "  App containers stopped. Infrastructure (Redis, TimescaleDB) still running." -ForegroundColor Cyan
} else {
    # Stop everything
    docker compose --profile dev --profile monitor down
    Write-Host "  All Docker containers stopped." -ForegroundColor Green
}

# --- Cleanup ---
Write-Host ""
Write-Host "[3/3] Cleanup..." -ForegroundColor Yellow

# Remove any stale background jobs
Get-Job | Where-Object { $_.State -eq "Failed" -or $_.State -eq "Completed" } | Remove-Job -Force

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  PHOENIX - All Services Stopped" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if ($KeepInfra) {
    Write-Host "  Infrastructure still running:" -ForegroundColor Cyan
    Write-Host "    Redis:       localhost:6379"
    Write-Host "    TimescaleDB: localhost:5432"
    Write-Host ""
    Write-Host "  To stop everything: .\stop.ps1" -ForegroundColor Yellow
}
