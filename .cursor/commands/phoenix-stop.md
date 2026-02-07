---
description: Stop all PHOENIX services gracefully
---
You MUST execute the following steps. Do NOT just print the commands.

**Step 1: Run phoenix.ps1 stop**
```powershell
.\phoenix.ps1 stop
```

If the user wants to keep the database running: `.\phoenix.ps1 stop -KeepInfra`

**Step 2: Kill any remaining processes on ports 8000 and 3000**
```powershell
$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
$p = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p[0].OwningProcess -Force -ErrorAction SilentlyContinue }
```

**Step 3: Verify**
Confirm that ports 8000 and 3000 are free and Docker containers are stopped (unless -KeepInfra). Report the results.
