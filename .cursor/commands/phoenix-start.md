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

**Step 3: Verify**
Wait 5 seconds, then run `.\phoenix.ps1 status` to verify all services are healthy. Report the results to the user.

This starts ALL services: Redis, TimescaleDB, Backend API (port 8000), Scraper (LotusBook polling), Orchestrator (autonomous training), and Frontend (port 3000).
