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
| `SCRAPER_MARKET_PAGES` | `1` | S47: open one read-only match page per live recorded match and store Bookmaker and session markets to `market_ticks`. Set `0` to turn off. |

## 6. Bookmaker and session markets (S47)

Table `market_ticks` (migration `003_market_ticks`, hypertable in `backend/db_init.py`). The migration is
not applied by the story. Apply it with the operator's usual step only after checking the table is empty
or the change is intended. Rows are written only when a market's content changes.

- `market_type`: `bookmaker`, `session` (names like `session:6_over:India`), or `other`.
- `status`: `open`, `suspended`, or `ball_running`.
- Selectors are UNVERIFIED for Bookmaker and session blocks. See the header of
  `scraper/parsers/lotusbook_markets.py`.

Health query to add to `scripts/recording_health.py` (not edited by S47):

```sql
SELECT match_id, market_type, COUNT(*) AS rows, MAX(time) AS last_seen
FROM market_ticks
WHERE time > NOW() - INTERVAL '1 day'
GROUP BY match_id, market_type
ORDER BY last_seen DESC;
```

## 7. 30-day recording run in Docker (2026-11-02)

**What runs:** `docker compose --profile recording up -d scraper` starts `phoenix-scraper` (with `phoenix-timescaledb`
and `phoenix-redis`). The container runs the S37 watchdog, which restarts the scraper on a crash or a stall. Docker
restarts the container (`restart: unless-stopped`) whenever Docker Desktop is running.

**What is recorded:** every real match on the LotusBook cricket page (simulated SRL matches are always rejected):

- live matches: prices plus score state (`match_context`) on every change;
- upcoming matches: pre-match prices, only when a price changes (at most every 30 s per match, plus a 15-minute heartbeat).

`SCRAPER_INTERNATIONAL_ONLY` is false for this run. The Redis override `config:scraper:international_only` must also
be `false`: `docker exec phoenix-redis redis-cli SET config:scraper:international_only false`.

**Database state after setup:** migration 002 was applied by hand (alembic is not stamped on this DB):

- `odds_ticks.tick_seq` is a sequence default, because hypertables cannot take an identity column;
- `odds_ticks_historical` exists;
- the `virtual_bets` key is `(placed_at, bet_id)`.

**No retention policy** on `odds_ticks`: recorded LotusBook ticks are the only real price history. `db_init` adds
retention only with `ODDS_TICKS_RETENTION=on`.

Pre-run backup: `data/backups/phoenix_pre_recording_20261102.dump`. Restore with `pg_restore`, as in the 002 runbook.

**Keeping it running for 30 days:**

1. Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**: on. Without it, a reboot stops
   recording until Docker is opened.
2. Sleep: no program can record while the PC is asleep.
   - Idle sleep is already "Never" in this PC's power plan (checked 2026-11-02).
   - Keep the lid open, or set the lid action to "Do nothing" when plugged in:
     `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0` then `powercfg /setactive SCHEME_CURRENT`.
   - Optional safety net that does not change settings:
     `powershell -ExecutionPolicy Bypass -File scripts\install_keep_awake_task.ps1`. It keeps Windows from idle-sleeping
     only while `phoenix-scraper` runs.
3. Check daily:
   - `docker ps` (phoenix-scraper Up);
   - `python scripts/recording_health.py`;
   - the tick count: `docker exec phoenix-timescaledb psql -U phoenix -d phoenix_betting -c "SELECT date(time), is_live,
     count(*), count(distinct match_id) FROM odds_ticks WHERE source='lotusbook' GROUP BY 1,2 ORDER BY 1 DESC LIMIT 6"`.
