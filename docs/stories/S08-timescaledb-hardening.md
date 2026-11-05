# S08 — TimescaleDB retention, chunking, keys and pools

**Priority:** P0 for the retention fix, P1 for the rest · **Depends on:** none · **Wave:** 1

## Story

As the operator, I need the database to keep the historical training corpus, store live
ticks efficiently, never drop rows on key collisions, and stay within connection limits
when all services run together.

## Background

- `backend/db_init.py:121` — `add_retention_policy('odds_ticks', INTERVAL '90 days')`.
  The Cricsheet-derived ticks are timestamped at their real match dates (years ago), so the
  next policy run **deletes the entire historical corpus**.
- `backend/db_init.py:39-40` — `chunk_time_interval => 1 hour` for `odds_ticks` and
  `match_context`. Historical data spanning ~20 years creates tens of thousands of chunks
  (planner overhead, compression jobs per chunk).
- `alembic/versions/001_initial_schema.py:54,97` — primary keys `(time, match_id)` on
  `odds_ticks` and `(placed_at, match_id)` on `virtual_bets`: two rows for the same match at
  the same timestamp collide (synthetic generators and batch backfills produce these).
- `shared/db.py:33-40` — `pool_size=10, max_overflow=20` hard-coded per process; backend,
  scraper, orchestrator, trainer and scripts together can exceed Postgres' default
  `max_connections=100`.
- Compression after 7 days on a table receiving historical backfills into old chunks.

## Context pack

- `backend/db_init.py` (157 lines): `HYPERTABLE_STATEMENTS` L38-44 (odds_ticks & match_context 1-hour chunks,
  virtual_bets on `placed_at`, training_metrics, graduation_snapshots); `ADD_VIRTUAL_BETS_ID` L46-57
  (**`virtual_bets.id BIGSERIAL` already exists** — use it in the new PK); `MIGRATION_STATEMENTS` L59-109;
  `INDEX_STATEMENTS` L111-118; `RETENTION_STATEMENTS` L120-123; `init_db()` L127-157 runs `(stmt, kind)` tuples and
  tolerates per-statement failures. `create_hypertable(..., if_not_exists => TRUE)` does **not** change an existing
  table's chunk interval → also emit `SELECT set_chunk_time_interval('odds_ticks', INTERVAL '1 day')` (new chunks only).
- `alembic/versions/001_initial_schema.py`: `revision = "001_initial"`, `down_revision = None`; odds_ticks PK
  `(time, match_id)` L54 + indexes L56-57; match_context PK L75 + index L77; virtual_bets PK `(placed_at, match_id)` L97
  + index L99; training_metrics / graduation_snapshots PK `(time)` L117/L132; match_results L151;
  match_training_status L170-172. Yours: `revision = "002_timescale_hardening"`, `down_revision = "001_initial"`.
  Hypertable PK changes: `ALTER TABLE ... DROP CONSTRAINT <name>` + `ADD PRIMARY KEY (...)` including the time
  column; compressed chunks block this → document decompress steps in the runbook.
- **ORM models must match (you own them):** `backend/models/odds.py` (`__tablename__ = "odds_ticks"`, `time`/`match_id`
  primary_key L18-19) and `backend/models/bets.py` (`placed_at`/`match_id` primary_key L18-19). Add a historical
  ticks model.
- Writers of `odds_ticks` (report only): `scraper/manager.py` `_store_odds_tick` (live — stays),
  `scraper/importers/betfair_importer.py` and `scripts/cricsheet_harmonizer.py:393-504` (historical — should move).
- `shared/db.py:28-42 get_engine()` → `create_async_engine(settings.database.url, pool_size=10, max_overflow=20,
  pool_pre_ping=True, pool_recycle=300)`. `shared/config.py:25 class DatabaseConfig(BaseSettings)` with `url` L35 and
  `sync_url` L39 properties — add `pool_size`, `max_overflow`, `pool_timeout` fields (follow that class's env prefix).

## Owned files

- `backend/db_init.py`
- New: `alembic/versions/002_timescale_hardening.py`
- `backend/models/odds.py`, `backend/models/bets.py` (ORM primary keys must match the migration)
- `shared/db.py`
- `shared/config.py` (pool settings only)
- New: `tests/unit/test_s08_db_init.py`
- New: `docs/runbooks/db-migration-002.md`

## Tasks

1. **Split live vs historical:** create `odds_ticks_historical` (same columns plus
   `source`), hypertable with `chunk_time_interval => INTERVAL '30 days'`, **no retention
   policy**, compression after 30 days. Live `odds_ticks` keeps retention 90 days but with
   `chunk_time_interval => INTERVAL '1 day'`.
   Migration copies rows where `source IN ('cricsheet_official','synthetic_cricsheet',
   'synthetic_archetype','betfair_historical')` or `match_id LIKE 'cric_%'` from
   `odds_ticks` into `odds_ticks_historical` **before** anything else, in batches by month.
   Do not delete them from `odds_ticks` in the migration — document a manual cleanup step
   in the runbook.
2. **Immediate safety:** `db_init.py` must remove the 90-day retention job if it already
   exists and the historical table is not yet populated
   (`SELECT remove_retention_policy('odds_ticks', if_exists => TRUE)`), then re-add it only
   after the copy succeeds. Print exactly what it did.
3. `match_context`: chunk 1 day, same live/historical consideration (retention only on live).
4. **Keys:** add a `tick_seq BIGINT GENERATED ALWAYS AS IDENTITY` (or `bet_id UUID
   DEFAULT gen_random_uuid()` for `virtual_bets`) and make the PK `(time, match_id, tick_seq)`
   / `(placed_at, bet_id)`. Hypertable PKs must include the time column. Provide downgrade.
5. **Indexes:** keep `(match_id, time DESC)`; replace `idx_odds_live (is_live, time DESC)`
   with a partial index `(time DESC) WHERE is_live`. Add `(match_id, time)` on the
   historical table.
6. **Pools:** read `DB_POOL_SIZE` (default 5) and `DB_MAX_OVERFLOW` (default 5) from
   settings; add `DB_POOL_TIMEOUT=10`. Document a budget table in the runbook
   (service → max connections) that stays ≤ 80.
7. Find every writer of historical ticks (`grep -rn "INSERT INTO odds_ticks"`) and list
   them in the report so the orchestrator can redirect them to `odds_ticks_historical`
   (do not edit files you do not own).

## Success criteria

- [ ] `db_init.py` contains no retention policy that can touch historical rows; test parses
      the statement lists and asserts retention is only applied to live tables.
- [ ] Chunk intervals: live 1 day, historical 30 days (test on statement list).
- [ ] Migration 002 upgrade/downgrade both run on an empty Timescale DB
      (`alembic upgrade head && alembic downgrade 001 && alembic upgrade head`) — if no DB is
      available, say so and provide the SQL dry-run output (`alembic upgrade head --sql`).
- [ ] Inserting two ticks with identical `(time, match_id)` succeeds after migration.
- [ ] Pool size configurable via env; defaults 5/5.
- [ ] Runbook lists backup command, migration order, verification queries
      (`SELECT count(*) ...` before/after), and rollback.

## Verification

```bash
python -m pytest tests/unit/test_s08_db_init.py -q
alembic upgrade head --sql > /dev/null
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Repointing scripts/importers at the new table (orchestrator follow-up from task 7 list).
