# TITAN - Start All Services
# Standard startup script

Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host "   TITAN - Starting All Services" -ForegroundColor Cyan
Write-Host "================================================`n" -ForegroundColor Cyan

$ProjectRoot = $PSScriptRoot

# #region agent log
$logPath = "$ProjectRoot\.cursor\debug.log"
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:8';message='Script started';data=@{projectRoot=$ProjectRoot};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='ALL'})
# #endregion

# Stop any existing services first
Write-Host "Cleaning up existing services..." -ForegroundColor Yellow
# #region agent log
$existingPython = (Get-Process python -ErrorAction SilentlyContinue).Count
$existingNode = (Get-Process node -ErrorAction SilentlyContinue).Count
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:11';message='Existing processes before cleanup';data=@{pythonCount=$existingPython;nodeCount=$existingNode};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='ALL'})
# #endregion
Get-Process python,node -ErrorAction SilentlyContinue | Stop-Process -Force 2>$null
Start-Sleep -Seconds 2

# 1. Start Docker Services
Write-Host "`n[1/6] Starting Docker Services..." -ForegroundColor Cyan
# #region agent log
$logPath = "$ProjectRoot\.cursor\debug.log"
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:16';message='Starting Docker services';timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H1'})
# #endregion
docker-compose -f "$ProjectRoot/docker/docker-compose.yml" up -d
$dockerExitCode = $LASTEXITCODE
# #region agent log
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:17';message='Docker compose up completed';data=@{exitCode=$dockerExitCode};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H1'})
# #endregion
Write-Host "   Waiting 15 seconds for Docker to initialize..." -ForegroundColor Gray
Start-Sleep -Seconds 15
# #region agent log
$dockerStatus = docker ps --format "{{.Names}}: {{.Status}}" 2>$null
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:20';message='Docker containers after 15s wait';data=@{containers=($dockerStatus -join ', ')};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H1'})
# #endregion
Write-Host "   Docker services ready" -ForegroundColor Green

# 2. Start Backend API
Write-Host "`n[2/6] Starting Backend API..." -ForegroundColor Cyan
# #region agent log
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:24';message='Starting Backend API process';timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H2,H3,H5'})
# #endregion
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; Write-Host 'TITAN Backend API (Port 8000)' -ForegroundColor Green; python -m uvicorn backend.main:app --reload --port 8000"
Write-Host "   Waiting 8 seconds for backend to initialize..." -ForegroundColor Gray
Start-Sleep -Seconds 8

# Verify backend is responding
try {
    # #region agent log
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:30';message='Attempting backend health check';timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H2'})
    # #endregion
    $health = Invoke-RestMethod "http://localhost:8000/api/health" -TimeoutSec 5 -ErrorAction Stop
    # #region agent log
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:31';message='Backend health check SUCCESS';data=@{status=$health.status;redis=$health.redis;database=$health.database};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H2'})
    # #endregion
    Write-Host "   Backend API responding: $($health.status)" -ForegroundColor Green
} catch {
    # #region agent log
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:33';message='Backend health check FAILED';data=@{error=$_.Exception.Message};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H2'})
    # #endregion
    Write-Host "   Backend might still be initializing..." -ForegroundColor Yellow
}

# 3. Start Cortex Processor
Write-Host "`n[3/6] Starting Cortex Processor..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; Write-Host 'TITAN Cortex Processor' -ForegroundColor Magenta; python -m cortex.processor"
Write-Host "   Cortex processor started" -ForegroundColor Green
Start-Sleep -Seconds 3

# 4. Start Scraper
Write-Host "`n[4/6] Starting Scraper..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; Write-Host 'TITAN Scraper Manager' -ForegroundColor Blue; python -m scraper.manager"
Write-Host "   Scraper started" -ForegroundColor Green
Start-Sleep -Seconds 3

# 5. Start Cricket Stats Enricher
Write-Host "`n[5/6] Starting Cricket Stats Enricher..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; Write-Host 'TITAN Cricket Stats Enricher' -ForegroundColor DarkYellow; python -m scraper.cricket_stats_enricher"
Write-Host "   Cricket Stats Enricher started" -ForegroundColor Green
Start-Sleep -Seconds 2

# 7. Start Frontend
Write-Host "`n[6/6] Starting Frontend Dashboard..." -ForegroundColor Cyan
# #region agent log
$frontendNodeModules = Test-Path "$ProjectRoot/frontend/node_modules"
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:50';message='Starting Frontend - checking dependencies';data=@{nodeModulesExists=$frontendNodeModules};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='H4'})
# #endregion
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot/frontend'; Write-Host 'TITAN Frontend Dashboard (Port 3000)' -ForegroundColor Cyan; npm run dev"
Write-Host "   Frontend started" -ForegroundColor Green

Write-Host "`n================================================" -ForegroundColor Green
Write-Host "   ALL SERVICES STARTED" -ForegroundColor Green
Write-Host "================================================`n" -ForegroundColor Green

Write-Host "You should see 5 PowerShell windows:" -ForegroundColor White
Write-Host "   1. Backend API (Green header)" -ForegroundColor Gray
Write-Host "   2. Cortex Processor (Magenta header)" -ForegroundColor Gray
Write-Host "   3. Scraper Manager (Blue header)" -ForegroundColor Gray
Write-Host "   4. Cricket Stats Enricher (Yellow header)" -ForegroundColor Gray
Write-Host "   5. Frontend Dashboard (Cyan header)" -ForegroundColor Gray

Write-Host "`nAccess Points:" -ForegroundColor Cyan
Write-Host "   Dashboard:    http://localhost:3000" -ForegroundColor White
Write-Host "   Backend API:  http://localhost:8000" -ForegroundColor White
Write-Host "   API Docs:     http://localhost:8000/docs" -ForegroundColor White
Write-Host "   PgAdmin:      http://localhost:5050" -ForegroundColor White
Write-Host "   Redis:        http://localhost:8081" -ForegroundColor White

Write-Host "`nTo stop all services, run: .\stop.ps1`n" -ForegroundColor Yellow
Write-Host "Please wait 30 seconds for all services to fully initialize...`n" -ForegroundColor Gray

# #region agent log
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$envFileExists = Test-Path "$ProjectRoot\.env"
$pythonProcesses = (Get-Process python -ErrorAction SilentlyContinue).Count
$nodeProcesses = (Get-Process node -ErrorAction SilentlyContinue).Count
$dockerContainers = (docker ps -q 2>$null | Measure-Object).Count
Add-Content -Path $logPath -Value (ConvertTo-Json -Compress @{location='start.ps1:72';message='Script completed - final state';data=@{envFileExists=$envFileExists;pythonProcesses=$pythonProcesses;nodeProcesses=$nodeProcesses;dockerContainers=$dockerContainers};timestamp=$timestamp;sessionId='debug-session';runId='initial';hypothesisId='ALL'})
# #endregion
