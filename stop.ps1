# TITAN - Stop All Services
# Standard shutdown script

Write-Host "`n================================================" -ForegroundColor Yellow
Write-Host "   TITAN - Stopping All Services" -ForegroundColor Yellow
Write-Host "================================================`n" -ForegroundColor Yellow

$ProjectRoot = $PSScriptRoot

# Stop Python services (Backend, Cortex, Scraper)
Write-Host "Stopping Python services..." -ForegroundColor Cyan
$pythonProcesses = Get-Process python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | Stop-Process -Force
    Write-Host "   Stopped $($pythonProcesses.Count) Python process(es)" -ForegroundColor Green
} else {
    Write-Host "   No Python processes running" -ForegroundColor Gray
}

# Stop Node (Frontend)
Write-Host "`nStopping Frontend..." -ForegroundColor Cyan
$nodeProcesses = Get-Process node -ErrorAction SilentlyContinue
if ($nodeProcesses) {
    $nodeProcesses | Stop-Process -Force
    Write-Host "   Stopped $($nodeProcesses.Count) Node process(es)" -ForegroundColor Green
} else {
    Write-Host "   No Node processes running" -ForegroundColor Gray
}

Start-Sleep -Seconds 2

# Stop Docker services
Write-Host "`nStopping Docker services..." -ForegroundColor Cyan
docker-compose -f "$ProjectRoot/docker/docker-compose.yml" down 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Docker services stopped" -ForegroundColor Green
} else {
    Write-Host "   Docker services may already be stopped" -ForegroundColor Gray
}

Write-Host "`n================================================" -ForegroundColor Green
Write-Host "   ALL SERVICES STOPPED" -ForegroundColor Green
Write-Host "================================================`n" -ForegroundColor Green

# Verification
Write-Host "Verification:" -ForegroundColor Cyan
$docker = docker ps -q 2>$null
$python = Get-Process python -ErrorAction SilentlyContinue
$node = Get-Process node -ErrorAction SilentlyContinue

if ($docker) {
    Write-Host "   Docker:  Still running (warning)" -ForegroundColor Yellow
} else {
    Write-Host "   Docker:  Stopped" -ForegroundColor Green
}

if ($python) {
    Write-Host "   Python:  $($python.Count) processes (warning)" -ForegroundColor Yellow
} else {
    Write-Host "   Python:  Stopped" -ForegroundColor Green
}

if ($node) {
    Write-Host "   Node:    $($node.Count) processes (warning)" -ForegroundColor Yellow
} else {
    Write-Host "   Node:    Stopped" -ForegroundColor Green
}

Write-Host "`nTo start again, run: .\start.ps1`n" -ForegroundColor Cyan
