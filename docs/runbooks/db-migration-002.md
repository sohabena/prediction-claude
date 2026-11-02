# Runbook: migration 002 (TimescaleDB hardening)

Splits live vs historical ticks, switches to collision-free keys, resizes chunks, caps pools.

## 0. Backup
```bash
pg_dump -Fc -h localhost -U phoenix phoenix_betting > backup_pre_002.dump
```
Stop writers (scraper, backend, orchestrator, trainers) first.

## 1. Pre-checks
```sql
SELECT count(*) FROM odds_ticks;
SELECT count(*) FROM odds_ticks WHERE source IN ('cricsheet_official','synthetic_cricsheet',
  'synthetic_archetype','betfair_historical') OR match_id LIKE 'cric\_%';
SELECT count(*) FROM virtual_bets;
-- compressed chunks block PK changes:
SELECT chunk_name FROM timescaledb_information.chunks
 WHERE is_compressed AND hypertable_name IN ('odds_ticks','virtual_bets');
-- if any: SELECT decompress_chunk(c, true) FROM show_chunks('odds_ticks') c;
```

## 2. Migration order
1. `alembic stamp 001_initial` (existing DB only), then `alembic upgrade head`.
   Order inside 002: remove retention -> create `odds_ticks_historical` -> copy by month ->
   PKs/indexes/chunks -> re-add retention on the live table only.
2. `python -m backend.db_init` (idempotent; removes the 90-day retention job if the historical
   copy is incomplete and prints what it did).
3. Existing chunks keep their old interval; `set_chunk_time_interval` affects new chunks only.

## 3. Verification
```sql
SELECT count(*) FROM odds_ticks_historical;  -- equals the historical-looking count from step 1
SELECT count(*) FROM odds_ticks;             -- unchanged (migration does NOT delete rows)
SELECT * FROM timescaledb_information.jobs
 WHERE proc_name IN ('policy_retention','policy_compression');
-- retention must list odds_ticks only, never odds_ticks_historical
INSERT INTO odds_ticks (time, match_id, team_home, team_away)
 VALUES ('2030-01-01','t','a','b'),('2030-01-01','t','a','b');  -- must succeed
```

## 4. Manual cleanup (optional, after verification and a fresh backup)
```sql
DELETE FROM odds_ticks WHERE source IN ('cricsheet_official','synthetic_cricsheet',
  'synthetic_archetype','betfair_historical') OR match_id LIKE 'cric\_%';
```
Then repoint historical writers (`scripts/cricsheet_harmonizer.py`,
`scraper/importers/betfair_importer.py`) at `odds_ticks_historical`.

## 5. Rollback
`alembic downgrade 001_initial` restores old PKs, index and 1-hour chunks, and drops
`odds_ticks_historical`. If step 4 was run, first restore the rows from `backup_pre_002.dump`
(`pg_restore -a -t odds_ticks`) or they are lost. Full rollback:
`pg_restore -c -d phoenix_betting backup_pre_002.dump`.

## 6. Connection budget (Postgres default max_connections=100; target <= 80)
Env: `DB_POOL_SIZE` (default 5), `DB_MAX_OVERFLOW` (default 5), `DB_POOL_TIMEOUT` (default 10 s).
Max per process = pool_size + max_overflow = 10.

| Service | Processes | Max connections |
|---------|-----------|-----------------|
| backend API | 2 | 20 |
| scraper | 1 | 10 |
| orchestrator | 1 | 10 |
| trainer | 1 | 10 |
| scripts / backfills | 2 | 20 |
| **Total** | 7 | **70** |
