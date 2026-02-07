---
description: Check PHOENIX orchestrator lifecycle state, data accumulation, and graduation progress
---
You MUST execute the following command and report the results. Do NOT just print the command.

```powershell
.\phoenix.ps1 orch
```

This queries the API to show:
- **Lifecycle state**: accumulating, offline_training, online_training, virtual_trading, or graduated
- **Model version** and curriculum stage
- **Data accumulation**: total matches, qualifying matches, total ticks, ready-to-train status
- **Graduation progress**: consecutive passing days out of 14 required

If the API is not reachable, the backend may be down. Suggest running `/phoenix-start`.

You can also query individual endpoints directly:
- State: `Invoke-RestMethod http://localhost:8000/api/advisor/state`
- Stats: `Invoke-RestMethod http://localhost:8000/api/advisor/stats`
- Shadow: `Invoke-RestMethod http://localhost:8000/api/advisor/shadow/performance`
- Drift: `Invoke-RestMethod http://localhost:8000/api/advisor/shadow/drift`
- Graduation: `Invoke-RestMethod http://localhost:8000/api/graduation/status`
