---
description: Start all PHOENIX services (infra + backend + scraper + orchestrator + frontend)
---
You MUST execute the following steps. Do NOT just print the commands.

**Step 1: Stop any stale processes on ports 8000 and 3000**
```powershell
$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
$p = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
```

**Step 2: Run phoenix.ps1 start**
```powershell
.\phoenix.ps1 start
```

If the user asked for a variation:
- "infrastructure only" or "just the database": `.\phoenix.ps1 start -InfraOnly`
- "with dev tools" or "with pgadmin": `.\phoenix.ps1 start -Dev`
- "with monitoring" or "with grafana": `.\phoenix.ps1 start -Monitor`
- "start everything" or "all tools": `.\phoenix.ps1 start -All`

**Step 3: Fix frontend if it failed to start**
`phoenix.ps1` may fail to launch the Next.js frontend on Windows (`Start-Process npm` error).
Check if port 3000 is listening:
```powershell
$f = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue; if ($f) { Write-Host "Frontend already running" } else { Write-Host "Frontend NOT running -- starting manually" }
```
If the frontend is NOT running, start it as a background shell command (use `block_until_ms: 0`):
```powershell
Set-Location frontend; npx next dev --port 3000
```
Then wait ~10 seconds for it to compile.

**Step 4: Verify**
Run `.\phoenix.ps1 status` (from the project root) and confirm ALL of these are healthy:
- Redis: Healthy
- TimescaleDB: Healthy
- Backend API: Running (port 8000)
- Frontend: Running (port 3000)
- Scraper: Running
- Orchestrator: Running

Report the full status table to the user.

This starts ALL services: Redis, TimescaleDB, Backend API (port 8000), Scraper (LotusBook polling), Orchestrator (autonomous training), and Frontend (port 3000).
