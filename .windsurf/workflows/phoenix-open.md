---
description: Open a PHOENIX dashboard page in the browser
---
Open a PHOENIX dashboard page. Use the MCP browser tools to navigate and show the page content.

Available pages and their URLs:

| Page | URL |
|------|-----|
| Main Dashboard | http://localhost:3000 |
| Training | http://localhost:3000/training |
| Virtual Trading | http://localhost:3000/trading |
| Graduation | http://localhost:3000/graduation |
| Live Matches | http://localhost:3000/matches |
| Advisor (Signals) | http://localhost:3000/advisor |
| API Health | http://localhost:8001/api/health |
| API Docs (Swagger) | http://localhost:8001/docs |
| PgAdmin | http://localhost:5050 |
| Redis Commander | http://localhost:8081 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 |

Steps:
1. Determine which page the user wants from their message. Default to the main dashboard.
2. Use `browser_navigate` to go to the URL.
3. Use `browser_snapshot` to capture the current state.
4. Report what you see on the page.

Alternatively, to open in the user's default browser:
```powershell
.\phoenix.ps1 open -Page <name>
```
where `<name>` is one of: dashboard, training, trading, graduation, matches, advisor, api, health, pgadmin, redis, prometheus, grafana.
