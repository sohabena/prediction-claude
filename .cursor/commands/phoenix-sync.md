---
description: Trigger weekly Cricsheet delta sync and incremental specialist PPO retraining
---
You MUST execute the following command and report the results. Do NOT just print the command.

```powershell
.\phoenix.ps1 sync
```

This script:
1. Downloads `recently_added_7_json.zip` from Cricsheet.
2. Identifies new match fixtures via unique signature hashing (`team1_vs_team2_date`).
3. Appends new match sequences to the appropriate specialist dataset in `data/specialists/`.
4. Runs 4,000 steps of incremental PPO fine-tuning for all updated specialists.
5. Updates `models/specialists/catalog.json` with fresh timestamps and metrics.

You can also inspect or trigger the sync status via REST API:
- Status: `Invoke-RestMethod http://localhost:8001/api/training/cricsheet-sync/status`
- Trigger: `Invoke-RestMethod -Method Post http://localhost:8001/api/training/cricsheet-sync/trigger`

