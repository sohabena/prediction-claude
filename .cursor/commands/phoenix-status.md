---
description: Check health and status of all PHOENIX services
---
You MUST execute the following command and report the results. Do NOT just print the command.

```powershell
.\phoenix.ps1 status
```

This checks:
- Infrastructure health (Redis, TimescaleDB)
- Application services (Backend API on port 8001, Frontend on port 3000, Scraper, Orchestrator)
- API health endpoint (`/api/health`)
- Orchestrator lifecycle state (accumulating / training / graduated)
- Lists all dashboard URLs

If a service is DOWN, suggest: `.\phoenix.ps1 start` to start everything, or `.\phoenix.ps1 restart -Service <name>` to restart a specific service.
