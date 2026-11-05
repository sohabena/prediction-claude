"""TimescaleDB hardening: live/historical split, chunking, keys, indexes

Revision ID: 002_timescale_hardening
Revises: 001_initial
Create Date: 2026-11-02

Order matters: (1) remove retention, (2) create + copy historical rows,
(3) keys/indexes/chunks, (4) re-add retention (live table only).
Rows are NOT deleted from odds_ticks (see docs/runbooks/db-migration-002.md).
Compressed chunks block PK changes: decompress first (see runbook).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "002_timescale_hardening"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HISTORICAL_PREDICATE = (
    "(source IN ('cricsheet_official','synthetic_cricsheet',"
    "'synthetic_archetype','betfair_historical') OR match_id LIKE 'cric\\_%')"
)

COLUMNS = (
    "time, match_id, team_home, team_away, competition, back_home, lay_home, back_draw, "
    "lay_draw, back_away, lay_away, volume_back_home, volume_lay_home, volume_back_away, "
    "volume_lay_away, implied_prob_home, implied_prob_away, overround, score, wickets, "
    "overs, innings, is_live, source, scrape_latency_ms"
)

UPGRADE_STATEMENTS = [
    # 1. Safety: stop the 90-day job before anything else.
    "SELECT remove_retention_policy('odds_ticks', if_exists => TRUE)",
    # 2. Historical table.
    """CREATE TABLE IF NOT EXISTS odds_ticks_historical (
        time TIMESTAMPTZ NOT NULL,
        match_id VARCHAR NOT NULL,
        tick_seq BIGINT GENERATED ALWAYS AS IDENTITY,
        team_home VARCHAR NOT NULL,
        team_away VARCHAR NOT NULL,
        competition VARCHAR,
        back_home DOUBLE PRECISION, lay_home DOUBLE PRECISION,
        back_draw DOUBLE PRECISION, lay_draw DOUBLE PRECISION,
        back_away DOUBLE PRECISION, lay_away DOUBLE PRECISION,
        volume_back_home DOUBLE PRECISION, volume_lay_home DOUBLE PRECISION,
        volume_back_away DOUBLE PRECISION, volume_lay_away DOUBLE PRECISION,
        implied_prob_home DOUBLE PRECISION, implied_prob_away DOUBLE PRECISION,
        overround DOUBLE PRECISION,
        score INTEGER, wickets INTEGER, overs DOUBLE PRECISION, innings INTEGER,
        is_live BOOLEAN DEFAULT FALSE,
        source VARCHAR DEFAULT 'cricsheet_official',
        scrape_latency_ms INTEGER,
        PRIMARY KEY (time, match_id, tick_seq)
    )""",
    "SELECT create_hypertable('odds_ticks_historical', 'time', if_not_exists => TRUE, "
    "chunk_time_interval => INTERVAL '30 days')",
    # Copy month by month (one INSERT per month) BEFORE any other change.
    f"""DO $$
    DECLARE m TIMESTAMPTZ; maxt TIMESTAMPTZ;
    BEGIN
        SELECT date_trunc('month', min(time)), max(time) INTO m, maxt
        FROM odds_ticks WHERE {HISTORICAL_PREDICATE};
        IF m IS NULL THEN RETURN; END IF;
        WHILE m <= maxt LOOP
            INSERT INTO odds_ticks_historical ({COLUMNS})
            SELECT {COLUMNS} FROM odds_ticks
            WHERE {HISTORICAL_PREDICATE}
              AND time >= m AND time < m + INTERVAL '1 month';
            m := m + INTERVAL '1 month';
        END LOOP;
    END $$""",
    "ALTER TABLE odds_ticks_historical SET (timescaledb.compress, "
    "timescaledb.compress_segmentby = 'match_id', timescaledb.compress_orderby = 'time DESC')",
    "SELECT add_compression_policy('odds_ticks_historical', INTERVAL '30 days', if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS idx_odds_hist_match_time ON odds_ticks_historical (match_id, time)",
    # 3. Keys: odds_ticks
    "ALTER TABLE odds_ticks ADD COLUMN IF NOT EXISTS tick_seq BIGINT GENERATED ALWAYS AS IDENTITY",
    "ALTER TABLE odds_ticks DROP CONSTRAINT IF EXISTS odds_ticks_pkey",
    "ALTER TABLE odds_ticks ADD PRIMARY KEY (time, match_id, tick_seq)",
    # virtual_bets
    "ALTER TABLE virtual_bets ADD COLUMN IF NOT EXISTS bet_id UUID NOT NULL DEFAULT gen_random_uuid()",
    "ALTER TABLE virtual_bets DROP CONSTRAINT IF EXISTS virtual_bets_pkey",
    "ALTER TABLE virtual_bets ADD PRIMARY KEY (placed_at, bet_id)",
    # chunks (affect new chunks only)
    "SELECT set_chunk_time_interval('odds_ticks', INTERVAL '1 day')",
    "SELECT set_chunk_time_interval('match_context', INTERVAL '1 day')",
    # 5. Indexes
    "DROP INDEX IF EXISTS idx_odds_live",
    "CREATE INDEX IF NOT EXISTS idx_odds_live_partial ON odds_ticks (time DESC) WHERE is_live",
    # 4. Re-add retention on the LIVE table only, after the copy above succeeded.
    "SELECT add_retention_policy('odds_ticks', INTERVAL '90 days', if_not_exists => TRUE)",
]

DOWNGRADE_STATEMENTS = [
    "SELECT remove_retention_policy('odds_ticks', if_exists => TRUE)",
    "DROP INDEX IF EXISTS idx_odds_live_partial",
    "CREATE INDEX IF NOT EXISTS idx_odds_live ON odds_ticks (is_live, time DESC)",
    "SELECT set_chunk_time_interval('odds_ticks', INTERVAL '1 hour')",
    "SELECT set_chunk_time_interval('match_context', INTERVAL '1 hour')",
    "ALTER TABLE virtual_bets DROP CONSTRAINT IF EXISTS virtual_bets_pkey",
    "ALTER TABLE virtual_bets DROP COLUMN IF EXISTS bet_id",
    "ALTER TABLE virtual_bets ADD PRIMARY KEY (placed_at, match_id)",
    "ALTER TABLE odds_ticks DROP CONSTRAINT IF EXISTS odds_ticks_pkey",
    "ALTER TABLE odds_ticks DROP COLUMN IF EXISTS tick_seq",
    "ALTER TABLE odds_ticks ADD PRIMARY KEY (time, match_id)",
    # Historical rows were never deleted from odds_ticks, so dropping the copy is lossless
    # unless the manual cleanup step in the runbook was performed.
    "DROP TABLE IF EXISTS odds_ticks_historical",
    "SELECT add_retention_policy('odds_ticks', INTERVAL '90 days', if_not_exists => TRUE)",
]


def upgrade() -> None:
    for stmt in UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)
