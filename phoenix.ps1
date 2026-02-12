<#
.SYNOPSIS
    PHOENIX CLI - Unified Application Management

.DESCRIPTION
    Manage all PHOENIX services: start, stop, status, logs, restart, test, db-init, open.

.EXAMPLE
    .\phoenix.ps1 start
    .\phoenix.ps1 start -InfraOnly
    .\phoenix.ps1 start -Dev
    .\phoenix.ps1 stop
    .\phoenix.ps1 stop -KeepInfra
    .\phoenix.ps1 status
    .\phoenix.ps1 logs
    .\phoenix.ps1 logs -Service scraper
    .\phoenix.ps1 restart -Service backend
    .\phoenix.ps1 test
    .\phoenix.ps1 test -Suite unit
    .\phoenix.ps1 db-init
    .\phoenix.ps1 open
    .\phoenix.ps1 open -Page advisor
    .\phoenix.ps1 orch
#>

param(
    [Parameter(Position = 0)]
    [string]$Command = "status",

    [switch]$InfraOnly,
    [switch]$Dev,
    [switch]$Monitor,
    [switch]$All,
    [switch]$KeepInfra,
    [string]$Service = "",
    [string]$Suite = "",
    [string]$Page = ""
)

$ProjectRoot = $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".phoenix_pids.json"

# ============================================================
# Helpers
# ============================================================

function Write-Banner([string]$Text, [string]$Color = "Cyan") {
    Write-Host ""
    Write-Host "  ========================================" -ForegroundColor $Color
    Write-Host "   PHOENIX - $Text" -ForegroundColor $Color
    Write-Host "  ========================================" -ForegroundColor $Color
    Write-Host ""
}

function Write-Step([string]$Number, [string]$Text) {
    Write-Host "  [$Number] $Text" -ForegroundColor Yellow
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Yellow
}

function Write-Err([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Red
}

function Write-Info([string]$Text) {
    Write-Host "    $Text" -ForegroundColor White
}

function Test-Port([int]$Port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $Port)
        $tcp.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Save-Pids($pidsObj) {
    $pidsObj | ConvertTo-Json | Set-Content -Path $PidFile -Force
}

function Load-Pids {
    if (Test-Path $PidFile) {
        return (Get-Content -Path $PidFile -Raw | ConvertFrom-Json)
    }
    return $null
}

# ============================================================
# START
# ============================================================

function Invoke-Start {
    Write-Banner "Starting Services"

    # --- Prerequisites ---
    Write-Step "1/8" "Checking prerequisites..."

    $dockerOk = $false
    try {
        $null = docker info 2>&1
        $dockerOk = ($LASTEXITCODE -eq 0)
    }
    catch { }

    if ($dockerOk) {
        Write-Ok "Docker Desktop: Running"
    }
    else {
        Write-Err "Docker Desktop is not running. Please start it first."
        return
    }

    $envPath = Join-Path $ProjectRoot ".env"
    $envExamplePath = Join-Path $ProjectRoot ".env.example"
    if (-not (Test-Path $envPath)) {
        if (Test-Path $envExamplePath) {
            Copy-Item $envExamplePath $envPath
            Write-Warn ".env created from .env.example - edit with your values"
        }
        else {
            Write-Warn "No .env file found (using defaults)"
        }
    }

    # --- Infrastructure ---
    Write-Step "2/8" "Starting infrastructure (Redis + TimescaleDB)..."
    $null = docker compose up -d redis timescaledb 2>&1
    Start-Sleep -Seconds 5

    # Wait for health checks
    $retries = 0
    $maxRetries = 30
    $infraReady = $false
    while ($retries -lt $maxRetries) {
        $redisResult = ""
        $pgResult = ""
        try { $redisResult = docker exec phoenix-redis redis-cli ping 2>&1 } catch { }
        try { $pgResult = docker exec phoenix-timescaledb pg_isready -U phoenix 2>&1 } catch { }

        if (($redisResult -match "PONG") -and ($pgResult -match "accepting")) {
            Write-Ok "Redis:       Healthy (port 6379)"
            Write-Ok "TimescaleDB: Healthy (port 5432)"
            $infraReady = $true
            break
        }
        $retries++
        Start-Sleep -Seconds 2
    }

    if (-not $infraReady) {
        Write-Err "Infrastructure failed to start within 60s"
        return
    }

    if ($InfraOnly) {
        Write-Banner "Infrastructure Ready" "Green"
        return
    }

    # --- Database init ---
    Write-Step "3/8" "Initializing database schema..."
    $env:POSTGRES_PASSWORD = "phoenix_secure_2026"
    $env:PYTHONPATH = $ProjectRoot
    try {
        $null = python -m backend.db_init 2>&1
        Write-Ok "Database schema initialized"
    }
    catch {
        Write-Warn "Schema init skipped (may already exist)"
    }

    # Logs directory for process output
    $logsDir = Join-Path $ProjectRoot "logs"
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

    # --- Backend API ---
    Write-Step "4/8" "Starting Backend API (port 8001)..."
    # Kill any process listening on 8001 (including zombie uvicorn reload children)
    Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        $null = cmd /c "taskkill /PID $($_.OwningProcess) /T /F 2>nul"
    }
    Start-Sleep -Seconds 2
    $env:PYTHONPATH = $ProjectRoot
    $env:POSTGRES_PASSWORD = "phoenix_secure_2026"
    $env:PHOENIX_PROJECT_ROOT = $ProjectRoot
    Get-ChildItem -Path $ProjectRoot -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # NOTE: No --reload flag. Reload spawns child processes that become zombies on Windows
    # when the parent is killed. For dev, restart manually: .\phoenix.ps1 restart -Service backend
    $backendProc = Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "backend.out") -RedirectStandardError (Join-Path $logsDir "backend.err")
    Start-Sleep -Seconds 3
    Write-Ok "Backend API started (PID $($backendProc.Id))"

    # --- Scraper ---
    Write-Step "5/8" "Starting Scraper..."
    $env:SCRAPER_HEADLESS = "true"
    $scraperProc = Start-Process -FilePath "python" -ArgumentList "-m", "scraper.manager" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "scraper.out") -RedirectStandardError (Join-Path $logsDir "scraper.err")
    Write-Ok "Scraper started (PID $($scraperProc.Id))"

    # --- Orchestrator ---
    Write-Step "6/8" "Starting Orchestrator (autonomous training)..."
    $orchestratorProc = Start-Process -FilePath "python" -ArgumentList "-m", "rl.orchestrator" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "orchestrator.out") -RedirectStandardError (Join-Path $logsDir "orchestrator.err")
    Write-Ok "Orchestrator started (PID $($orchestratorProc.Id))"

    # --- Frontend ---
    Write-Step "7/8" "Starting Frontend Dashboard (port 3000)..."
    # Free port 3000 if in use (stale processes from previous runs)
    $port3000 = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($port3000) {
        try {
            Stop-Process -Id $port3000.OwningProcess -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            Write-Ok "Freed port 3000 (was PID $($port3000.OwningProcess))"
        } catch { }
    }
    $env:PORT = "3000"
    $frontendPath = Join-Path $ProjectRoot "frontend"
    $nextPath = Join-Path $frontendPath "node_modules\next\dist\bin\next"
    if (Test-Path $nextPath) {
        $frontendProc = Start-Process -FilePath "node" -ArgumentList $nextPath, "dev" -WorkingDirectory $frontendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "frontend.out") -RedirectStandardError (Join-Path $logsDir "frontend.err")
    } else {
        $frontendProc = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory $frontendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "frontend.out") -RedirectStandardError (Join-Path $logsDir "frontend.err")
    }
    Write-Ok "Frontend started (PID $($frontendProc.Id))"

    # --- Dev / Monitor ---
    if ($Dev -or $All) {
        Write-Step "8/8" "Starting dev tools..."
        $null = docker compose --profile dev up -d pgadmin redis-commander 2>&1
        Write-Ok "PgAdmin:          http://localhost:5050"
        Write-Ok "Redis Commander:  http://localhost:8081"
    }
    if ($Monitor -or $All) {
        Write-Step "8/8" "Starting monitoring..."
        $null = docker compose --profile monitor up -d prometheus grafana 2>&1
        Write-Ok "Prometheus:  http://localhost:9090"
        Write-Ok "Grafana:     http://localhost:3001"
    }

    # Save PIDs (process IDs so status/stop work from any terminal)
    $pids = @{
        backend      = $backendProc.Id
        scraper      = $scraperProc.Id
        orchestrator = $orchestratorProc.Id
        frontend     = $frontendProc.Id
    }
    Save-Pids $pids

    # --- Summary ---
    Write-Banner "All Services Running" "Green"
    Write-Info "Dashboard:       http://localhost:3000"
    Write-Info "Advisor:         http://localhost:3000/advisor"
    Write-Info "API Health:      http://localhost:8001/api/health"
    Write-Info "API Docs:        http://localhost:8001/docs"
    Write-Info ""
    Write-Info "Redis:           localhost:6379"
    Write-Info "TimescaleDB:     localhost:5432"
    Write-Host ""
    Write-Host "  Stop:    .\phoenix.ps1 stop" -ForegroundColor Yellow
    Write-Host "  Status:  .\phoenix.ps1 status" -ForegroundColor Yellow
    Write-Host "  Logs:    .\phoenix.ps1 logs" -ForegroundColor Yellow
    Write-Host ""
}

# ============================================================
# STOP
# ============================================================

function Invoke-Stop {
    Write-Banner "Stopping Services" "Yellow"

    # --- Stop application processes (by PID, kill process tree) ---
    Write-Step "1/3" "Stopping application services..."
    $pids = Load-Pids
    if ($pids) {
        $services = @("backend", "scraper", "orchestrator", "frontend")
        foreach ($svc in $services) {
            $pidVal = $pids.$svc
            if ($pidVal) {
                try {
                    $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
                    if ($proc) {
                        Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
                        # Kill process tree on Windows so child processes (e.g. node from npm) are stopped
                        $null = cmd /c "taskkill /PID $pidVal /T /F 2>nul"
                        Write-Ok "Stopped: $svc (PID $pidVal)"
                    }
                    else {
                        Write-Info "Already stopped: $svc"
                    }
                }
                catch {
                    Write-Info "Already stopped: $svc"
                }
            }
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
    else {
        Write-Warn "No PID file found"
    }

    # --- Stop Docker ---
    Write-Step "2/3" "Stopping Docker services..."
    if ($KeepInfra) {
        $null = docker compose --profile dev --profile monitor stop pgadmin redis-commander prometheus grafana 2>&1
        Write-Ok "App containers stopped. Infrastructure still running."
    }
    else {
        $null = docker compose --profile dev --profile monitor down 2>&1
        Write-Ok "All Docker containers stopped."
    }

    # --- Cleanup ---
    Write-Step "3/3" "Cleanup..."

    Write-Banner "All Services Stopped" "Green"
    if ($KeepInfra) {
        Write-Info "Infrastructure still running: Redis (6379), TimescaleDB (5432)"
        Write-Host "  To stop everything:  .\phoenix.ps1 stop" -ForegroundColor Yellow
    }
}

# ============================================================
# STATUS
# ============================================================

function Invoke-Status {
    Write-Banner "Service Status"

    # --- Infrastructure ---
    Write-Host "  Infrastructure:" -ForegroundColor White

    $redisOk = $false
    try {
        $redisResult = docker exec phoenix-redis redis-cli ping 2>&1
        if ($redisResult -match "PONG") { $redisOk = $true }
    }
    catch { }
    if ($redisOk) { Write-Ok "Redis:           Healthy (port 6379)" }
    else          { Write-Err "Redis:           DOWN" }

    $pgOk = $false
    try {
        $pgResult = docker exec phoenix-timescaledb pg_isready -U phoenix 2>&1
        if ($pgResult -match "accepting") { $pgOk = $true }
    }
    catch { }
    if ($pgOk) { Write-Ok "TimescaleDB:     Healthy (port 5432)" }
    else       { Write-Err "TimescaleDB:     DOWN" }

    # --- App services ---
    Write-Host ""
    Write-Host "  Application:" -ForegroundColor White

    if (Test-Port 8001) { Write-Ok "Backend API:     Running (port 8001)" }
    else                { Write-Err "Backend API:     DOWN" }

    if (Test-Port 3000) { Write-Ok "Frontend:        Running (port 3000)" }
    else                { Write-Err "Frontend:        DOWN" }

    # Background processes (scraper, orchestrator) - PIDs work from any terminal
    $pids = Load-Pids
    if ($pids) {
        foreach ($svc in @("scraper", "orchestrator")) {
            $pidVal = $pids.$svc
            $label = $svc.PadRight(16)
            if ($pidVal) {
                try {
                    $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
                    if ($proc) {
                        Write-Ok "${label} Running (PID $pidVal)"
                    }
                    else {
                        Write-Err "${label} Not running (PID $pidVal)"
                    }
                }
                catch {
                    Write-Err "${label} Not found"
                }
            }
        }
    }
    else {
        Write-Warn "Scraper:         Unknown (no PID file)"
        Write-Warn "Orchestrator:    Unknown (no PID file)"
    }

    # --- API health ---
    Write-Host ""
    Write-Host "  API Health:" -ForegroundColor White
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8001/api/health" -TimeoutSec 5 -ErrorAction Stop
        Write-Info "Status:   $($health.status)"
        Write-Info "Redis:    $($health.redis)"
        Write-Info "Database: $($health.database)"
    }
    catch {
        Write-Warn "API health endpoint not reachable"
    }

    # --- Orchestrator ---
    Write-Host ""
    Write-Host "  Orchestrator:" -ForegroundColor White
    try {
        $orch = Invoke-RestMethod -Uri "http://localhost:8001/api/advisor/state" -TimeoutSec 5 -ErrorAction Stop
        Write-Info "State:           $($orch.state)"
        Write-Info "Model Version:   v$($orch.model_version)"
        Write-Info "Curriculum:      $($orch.curriculum_stage)"
    }
    catch {
        Write-Warn "Orchestrator state not reachable"
    }

    # --- URLs ---
    Write-Host ""
    Write-Host "  Dashboard URLs:" -ForegroundColor White
    Write-Info "Main:       http://localhost:3000"
    Write-Info "Training:   http://localhost:3000/training"
    Write-Info "Trading:    http://localhost:3000/trading"
    Write-Info "Graduation: http://localhost:3000/graduation"
    Write-Info "Matches:    http://localhost:3000/matches"
    Write-Info "Advisor:    http://localhost:3000/advisor"
    Write-Info "API Docs:   http://localhost:8001/docs"
    Write-Host ""
}

# ============================================================
# LOGS
# ============================================================

function Invoke-Logs {
    $logsDir = Join-Path $ProjectRoot "logs"
    if ($Service -ne "") {
        Write-Banner "Logs: $Service"

        switch ($Service.ToLower()) {
            "redis"      { docker logs phoenix-redis --tail 50 }
            "timescaledb" { docker logs phoenix-timescaledb --tail 50 }
            default {
                $outFile = Join-Path $logsDir "$Service.out"
                $errFile = Join-Path $logsDir "$Service.err"
                if (Test-Path $outFile) {
                    Write-Host "  --- stdout ---" -ForegroundColor Cyan
                    Get-Content $outFile -Tail 30
                }
                if (Test-Path $errFile) {
                    Write-Host "  --- stderr ---" -ForegroundColor Cyan
                    Get-Content $errFile -Tail 30
                }
                if (-not (Test-Path $outFile) -and -not (Test-Path $errFile)) {
                    Write-Warn "No log files for $Service (or service not started with current script)"
                    Write-Info "Available: backend, scraper, orchestrator, frontend, redis, timescaledb"
                }
            }
        }
    }
    else {
        Write-Banner "Recent Logs (all services)"
        if (Test-Path $logsDir) {
            foreach ($svc in @("backend", "scraper", "orchestrator", "frontend")) {
                Write-Host ""
                Write-Host "  --- $svc ---" -ForegroundColor Cyan
                $outFile = Join-Path $logsDir "$svc.out"
                $errFile = Join-Path $logsDir "$svc.err"
                if (Test-Path $outFile) {
                    Get-Content $outFile -Tail 10 | ForEach-Object { Write-Host "    $_" }
                }
                if (Test-Path $errFile) {
                    Get-Content $errFile -Tail 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow }
                }
                if (-not (Test-Path $outFile) -and -not (Test-Path $errFile)) {
                    Write-Info "(no output yet)"
                }
            }
        }
        else {
            Write-Warn "No logs directory. Start services first: .\phoenix.ps1 start"
        }
    }
}

# ============================================================
# RESTART
# ============================================================

function Invoke-Restart {
    if ($Service -ne "") {
        Write-Banner "Restarting: $Service"

        $pids = Load-Pids
        if (-not $pids) {
            Write-Err "No PID file found. Start services first."
            return
        }

        # Stop by PID (process tree)
        $pidVal = $pids.$Service
        if ($pidVal) {
            try {
                $null = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
                Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
                $null = cmd /c "taskkill /PID $pidVal /T /F 2>nul"
                Write-Ok "Stopped: $Service"
            }
            catch { }
            Start-Sleep -Seconds 2
        }
        if ($Service -eq "backend") {
            Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
                $null = cmd /c "taskkill /PID $($_.OwningProcess) /T /F 2>nul"
            }
            Start-Sleep -Seconds 2
        }

        # Restart as process
        $logsDir = Join-Path $ProjectRoot "logs"
        if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
        $frontendPath = Join-Path $ProjectRoot "frontend"
        $env:PYTHONPATH = $ProjectRoot
        $env:POSTGRES_PASSWORD = "phoenix_secure_2026"
        $newProc = $null
        switch ($Service.ToLower()) {
            "backend" {
                $env:PHOENIX_PROJECT_ROOT = $ProjectRoot
                Get-ChildItem -Path $ProjectRoot -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
                $newProc = Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "backend.out") -RedirectStandardError (Join-Path $logsDir "backend.err")
            }
            "scraper" {
                $env:SCRAPER_HEADLESS = "true"
                $newProc = Start-Process -FilePath "python" -ArgumentList "-m", "scraper.manager" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "scraper.out") -RedirectStandardError (Join-Path $logsDir "scraper.err")
            }
            "orchestrator" {
                $newProc = Start-Process -FilePath "python" -ArgumentList "-m", "rl.orchestrator" -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "orchestrator.out") -RedirectStandardError (Join-Path $logsDir "orchestrator.err")
            }
            "frontend" {
                $port3000 = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($port3000) {
                    try { Stop-Process -Id $port3000.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 } catch { }
                }
                $env:PORT = "3000"
                $nextPath = Join-Path $frontendPath "node_modules\next\dist\bin\next"
                if (Test-Path $nextPath) {
                    $newProc = Start-Process -FilePath "node" -ArgumentList $nextPath, "dev" -WorkingDirectory $frontendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "frontend.out") -RedirectStandardError (Join-Path $logsDir "frontend.err")
                } else {
                    $newProc = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory $frontendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsDir "frontend.out") -RedirectStandardError (Join-Path $logsDir "frontend.err")
                }
            }
            default {
                Write-Err "Unknown service: $Service"
                Write-Info "Available: backend, scraper, orchestrator, frontend"
                return
            }
        }

        if ($newProc) {
            $pids.$Service = $newProc.Id
            Save-Pids $pids
            Write-Ok "Restarted: $Service (PID $($newProc.Id))"
        }
    }
    else {
        Write-Banner "Restarting all app services"
        Invoke-Stop
        Start-Sleep -Seconds 2
        Invoke-Start
    }
}

# ============================================================
# TEST
# ============================================================

function Invoke-Test {
    Write-Banner "Running Tests"

    $testPath = "tests/"
    switch ($Suite.ToLower()) {
        "unit"        { $testPath = "tests/unit/" }
        "rl"          { $testPath = "tests/rl/" }
        "e2e"         { $testPath = "tests/e2e/" }
        "integration" { $testPath = "tests/integration/" }
    }

    Write-Info "Test path: $testPath"
    Write-Host ""

    Set-Location $ProjectRoot
    python -m pytest $testPath -v --tb=short
}

# ============================================================
# DB-INIT
# ============================================================

function Invoke-DbInit {
    Write-Banner "Database Initialization"

    Set-Location $ProjectRoot
    python -m backend.db_init
    Write-Ok "Database schema initialized"
}

# ============================================================
# OPEN
# ============================================================

function Invoke-Open {
    $urls = @{
        ""            = "http://localhost:3000"
        "dashboard"   = "http://localhost:3000"
        "training"    = "http://localhost:3000/training"
        "trading"     = "http://localhost:3000/trading"
        "graduation"  = "http://localhost:3000/graduation"
        "matches"     = "http://localhost:3000/matches"
        "advisor"     = "http://localhost:3000/advisor"
        "api"         = "http://localhost:8001/docs"
        "health"      = "http://localhost:8001/api/health"
        "pgadmin"     = "http://localhost:5050"
        "redis"       = "http://localhost:8081"
        "prometheus"  = "http://localhost:9090"
        "grafana"     = "http://localhost:3001"
    }

    $target = $Page.ToLower()
    if ($urls.ContainsKey($target)) {
        $url = $urls[$target]
        Write-Banner "Opening: $url"
        Start-Process $url
    }
    else {
        Write-Err "Unknown page: $Page"
        Write-Host ""
        Write-Host "  Available pages:" -ForegroundColor White
        $urls.GetEnumerator() | Where-Object { $_.Key -ne "" } | Sort-Object Key |
            ForEach-Object { Write-Info "$($_.Key.PadRight(14))  $($_.Value)" }
    }
}

# ============================================================
# ORCHESTRATOR STATUS
# ============================================================

function Invoke-OrchStatus {
    Write-Banner "Orchestrator Status"

    try {
        $state = Invoke-RestMethod -Uri "http://localhost:8001/api/advisor/state" -TimeoutSec 5 -ErrorAction Stop
        Write-Info "State:           $($state.state)"
        Write-Info "Model Version:   v$($state.model_version)"
        Write-Info "Curriculum:      $($state.curriculum_stage)"
        Write-Info "Updated:         $($state.updated_at)"
    }
    catch {
        Write-Warn "Cannot reach orchestrator state endpoint"
    }

    Write-Host ""

    try {
        $stats = Invoke-RestMethod -Uri "http://localhost:8001/api/advisor/stats" -TimeoutSec 5 -ErrorAction Stop
        Write-Info "Training Runs:   $($stats.training_runs)"
        Write-Info "Eval Runs:       $($stats.eval_runs)"
        Write-Info "Matches Trained: $($stats.matches_trained_on)"

        if ($stats.accumulation) {
            Write-Host ""
            Write-Host "  Accumulation:" -ForegroundColor White
            Write-Info "Total Matches:   $($stats.accumulation.total_matches)"
            Write-Info "Qualifying:      $($stats.accumulation.qualifying_matches) / $($stats.accumulation.min_required)"
            Write-Info "Total Ticks:     $($stats.accumulation.total_ticks)"
            Write-Info "Ready to Train:  $($stats.accumulation.ready_to_train)"
        }
    }
    catch {
        Write-Warn "Cannot reach orchestrator stats endpoint"
    }

    Write-Host ""

    try {
        $grad = Invoke-RestMethod -Uri "http://localhost:8001/api/graduation/status" -TimeoutSec 5 -ErrorAction Stop
        Write-Host "  Graduation:" -ForegroundColor White
        Write-Info "Ready:           $($grad.ready)"
        Write-Info "Consecutive:     $($grad.consecutive_days) / $($grad.required_days) days"
    }
    catch {
        Write-Warn "Cannot reach graduation endpoint"
    }
}

# ============================================================
# BACKUP / RESTORE
# ============================================================

function Invoke-Backup {
    Write-Banner "Database Backup"

    $backupDir = Join-Path $ProjectRoot "backups"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupFile = Join-Path $backupDir "phoenix_backup_$timestamp.sql"

    Write-Step "1/2" "Dumping database..."
    try {
        docker exec phoenix-timescaledb pg_dump -U phoenix -d phoenix_betting > $backupFile
        $size = (Get-Item $backupFile).Length / 1MB
        Write-Ok "Backup saved: $backupFile ($([math]::Round($size, 2)) MB)"
    }
    catch {
        Write-Err "Backup failed: $_"
        return
    }

    # Cleanup: keep only last 10 backups
    Write-Step "2/2" "Cleaning old backups..."
    $backups = Get-ChildItem $backupDir -Filter "phoenix_backup_*.sql" | Sort-Object LastWriteTime -Descending
    if ($backups.Count -gt 10) {
        $backups | Select-Object -Skip 10 | Remove-Item -Force
        Write-Ok "Cleaned up old backups (keeping last 10)"
    }

    Write-Banner "Backup Complete" "Green"
}

function Invoke-Restore {
    Write-Banner "Database Restore" "Yellow"

    if ($Service -eq "") {
        $backupDir = Join-Path $ProjectRoot "backups"
        $latest = Get-ChildItem $backupDir -Filter "phoenix_backup_*.sql" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $latest) {
            Write-Err "No backup files found in $backupDir"
            return
        }
        $restoreFile = $latest.FullName
        Write-Info "Restoring latest backup: $($latest.Name)"
    }
    else {
        $restoreFile = $Service
    }

    if (-not (Test-Path $restoreFile)) {
        Write-Err "Backup file not found: $restoreFile"
        return
    }

    Write-Warn "This will OVERWRITE the current database. Press Ctrl+C to cancel."
    Start-Sleep -Seconds 3

    Write-Step "1/1" "Restoring database..."
    try {
        Get-Content $restoreFile | docker exec -i phoenix-timescaledb psql -U phoenix -d phoenix_betting
        Write-Ok "Database restored from: $restoreFile"
    }
    catch {
        Write-Err "Restore failed: $_"
    }
}

# ============================================================
# DISPATCH
# ============================================================

switch ($Command.ToLower()) {
    "start"   { Invoke-Start }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    "logs"    { Invoke-Logs }
    "restart" { Invoke-Restart }
    "test"    { Invoke-Test }
    "db-init" { Invoke-DbInit }
    "open"    { Invoke-Open }
    "orch"    { Invoke-OrchStatus }
    "backup"  { Invoke-Backup }
    "restore" { Invoke-Restore }
    default   { Invoke-Status }
}
