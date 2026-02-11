---
description: Restart a specific PHOENIX service or all services
---
You MUST execute the following steps. Do NOT just print the commands.

If the user specified a service name, restart just that service:
```powershell
.\phoenix.ps1 restart -Service <name>
```
Available services: `backend`, `scraper`, `orchestrator`, `frontend`

If restarting backend, also kill any stale process on port 8000 first:
```powershell
$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
```

If restarting frontend, also kill any stale process on port 3000 first:
```powershell
$p = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
```

If no service specified, restart everything:
```powershell
.\phoenix.ps1 restart
```

**Fix frontend if it failed to start:**
After a full restart or a frontend-specific restart, check if port 3000 is listening:
```powershell
$f = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue; if ($f) { Write-Host "Frontend already running" } else { Write-Host "Frontend NOT running -- starting manually" }
```
If the frontend is NOT running, start it as a background shell command (use `block_until_ms: 0`):
```powershell
Set-Location frontend; npx next dev --port 3000
```
Then wait ~10 seconds for it to compile.

**Verify:**
Run `.\phoenix.ps1 status` (from the project root) and confirm the restarted service(s) are healthy. Report results to the user.
