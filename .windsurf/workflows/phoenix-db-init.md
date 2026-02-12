---
description: Initialize the PHOENIX TimescaleDB database schema
---
You MUST execute the following commands. Do NOT just print them.

**Step 1: Ensure infrastructure is running**
```powershell
docker exec phoenix-timescaledb pg_isready -U phoenix
```
If not running, start it first: `.\phoenix.ps1 start -InfraOnly`

**Step 2: Initialize the database**
```powershell
$env:POSTGRES_PASSWORD = "phoenix_secure_2026"; $env:PYTHONPATH = "."; python -m backend.db_init
```

This creates:
1. TimescaleDB extension
2. All tables (odds_ticks, match_context, virtual_bets, training_metrics, graduation_snapshots)
3. Hypertables for time-series optimization
4. Indexes and retention policies

Report the results to the user.
