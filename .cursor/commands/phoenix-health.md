---
description: Quick health check of PHOENIX backend API, Redis, and database
---
You MUST execute the following command and report the results. Do NOT just print the command.

```powershell
python -c "import httpx; r=httpx.get('http://localhost:8001/api/health', timeout=5); d=r.json(); [print(f'{k}: {v}') for k,v in d.items()]"
```

This returns the health status of:
- Backend API status (healthy / degraded / unhealthy)
- Redis connection
- TimescaleDB connection
- Scraper status and last update time
- Agent state and version

If the endpoint is not reachable, the backend is down. Suggest running `/phoenix-start` to start all services.

For a full service overview, suggest running `/phoenix-status`.
