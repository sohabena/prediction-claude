---
name: "source-command-phoenix-logs"
description: "View logs from PHOENIX services"
---

# source-command-phoenix-logs

Use this skill when the user asks to run the migrated source command `phoenix-logs`.

## Command Template

You MUST execute the following command and report the results. Do NOT just print the command.

To see recent logs from all services:
```powershell
.\phoenix.ps1 logs
```

To see logs from a specific service (if the user mentioned one):
```powershell
.\phoenix.ps1 logs -Service <name>
```

Available services: `backend`, `scraper`, `orchestrator`, `frontend`, `redis`, `timescaledb`

If the user asked for a specific service, use that name. Otherwise show all.
Report any errors or warnings found in the logs.
