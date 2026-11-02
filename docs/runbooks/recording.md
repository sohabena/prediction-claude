# Runbook: recording real matches (S37)

Goal: every real live cricket match is recorded with LotusBook prices, match state and result,
with simulated (SRL / Virtual / E-Cricket) and synthetic (`cricsheet_official`) rows kept out of
`odds_ticks`. Recording no longer depends on training approval: `SCRAPER_RECORD_ALL` (default on)
stores ticks for every qualifying live match. Approval still decides which matches are published
to live consumers.

All steps below are for the operator. Nothing in the repo runs migrations, deletes rows or
installs the scheduled task on its own.

## 1. Keep the scraper running on Windows

The watchdog `scripts/run_scraper_forever.py` runs the scraper in a child process. It restarts the
child with backoff when it exits, and also when a match is live but no live event has been
processed for `SCRAPER_STALL_MINUTES` (default 10).

Install once, from the repo root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1
```

This registers the task `PHOENIX scraper`, which starts the watchdog at logon and daily at 06:00
(override with `-DailyAt 05:30`). Running the installer again replaces the task.

Check it:

```powershell
python scripts\run_scraper_forever.py --status      # watchdog heartbeat (restarts today, matches live)
python scripts\recording_health.py --days 1          # per-day coverage and freshness
Get-Content logs\scraper_watchdog.log -Tail 20       # watchdog events
```

Uninstall:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1 -Uninstall
# or: Unregister-ScheduledTask -TaskName "PHOENIX scraper" -Confirm:$false
```

Files written under `logs/`: `scraper_watchdog.log`, `scraper_heartbeat.json`, `scraper_child.log`
(scraper stdout and stderr) and `scraper_feed.json` (feed heartbeat from the scraper).

## 2. Apply the S08 migration that creates `odds_ticks_historical`

`scripts/export_recorded_corpus.py` reads `odds_ticks_historical` when it exists and falls back to
`odds_ticks` only when it does not. The full procedure, with backup and verification, is in
[db-migration-002.md](db-migration-002.md). Short form:

```bash
pg_dump -Fc -h localhost -U phoenix phoenix_betting > backup_pre_002.dump
# stop the scraper first (Ctrl+C in its window, or End the "PHOENIX scraper" task)
alembic stamp 001_initial          # existing database only
alembic upgrade head
python -m backend.db_init
```

The migration copies historical rows into `odds_ticks_historical`. It does not delete rows from
`odds_ticks`. Verify with the queries in that runbook, then restart the scraper.

## 3. Move synthetic rows out of `odds_ticks`

`odds_ticks` must hold only LotusBook rows. Rows with `source` in `cricsheet_official`,
`synthetic_cricsheet`, `synthetic_archetype` or `betfair_historical`, and `cric_*` match ids, are
synthetic or not LotusBook. They belong in `odds_ticks_historical` (step 2 copies them there).

Check first:

```sql
SELECT source, count(*) FROM odds_ticks
 WHERE source <> 'lotusbook' OR match_id LIKE 'cric\_%' GROUP BY source;
SELECT count(*) FROM odds_ticks_historical;   -- must cover the rows you are about to remove
```

After a fresh backup, delete them from the live table (operator runs this once, inside a
transaction so you can check the count before committing):

```sql
BEGIN;
DELETE FROM odds_ticks
 WHERE source IN ('cricsheet_official', 'synthetic_cricsheet', 'synthetic_archetype', 'betfair_historical')
    OR match_id LIKE 'cric\_%';
-- check the reported row count against the first query, then:
COMMIT;   -- or ROLLBACK;
```

Simulated matches (`... SRL`, `Simulated ...`, `Virtual ...`, `E-Cricket ...`) that were recorded
before S37 are real rows in `odds_ticks`. Find them with:

```sql
SELECT match_id, team_home, team_away, competition, count(*) FROM odds_ticks
 WHERE team_home ~* '\m(srl|simulated|virtual|e-?cricket)\M'
    OR team_away ~* '\m(srl|simulated|virtual|e-?cricket)\M'
    OR competition ~* '\m(srl|simulated|virtual|e-?cricket)\M'
 GROUP BY 1, 2, 3, 4;
```

Delete them only after the same backup, with the same transaction pattern. The exporter already
excludes them, so deleting is about table hygiene, not correctness.

## 4. Daily check

```powershell
python scripts\recording_health.py --days 7
```

Exit codes:

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Clean and the recorder is fresh | none |
| 1 | Synthetic or simulated rows exist in `odds_ticks` | run step 3 |
| 2 | Watchdog or feed heartbeat is stale or missing | run `--status`, check `logs\scraper_child.log` |

Export the recorded corpus when there is enough data:

```powershell
python scripts\export_recorded_corpus.py --cadence decision
```

With no qualifying matches it prints "No recorded matches" and exits 0.

## 5. Environment settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPER_RECORD_ALL` | `1` | Record every qualifying live match, regardless of approval. Set `0` to restore approval-gated recording. |
| `SCRAPER_STALL_MINUTES` | `10` | Restart the scraper when a live match has no processed event for this long. |
