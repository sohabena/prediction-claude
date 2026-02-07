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

After restarting, run `.\phoenix.ps1 status` and report results to the user.
