"""
Database initialization script for PHOENIX.
Creates all tables and TimescaleDB hypertables.

For schema migrations, prefer `alembic upgrade head`.
This script remains for TimescaleDB-specific DDL (hypertables,
retention policies) that Alembic cannot natively manage.

Usage:
    python -m backend.db_init          # Full init (tables + hypertables)
    alembic upgrade head               # Schema-only via Alembic
    alembic stamp head                 # Mark existing DB as current
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.models.base import Base
from backend.models import (  # noqa: F401 - ensure all models are registered
    OddsTick,
    MatchContextRecord,
    VirtualBetRecord,
    TrainingMetricRecord,
    GraduationSnapshot,
    MatchResultRecord,
    MatchTrainingStatus,
)
from backend.models.odds import OddsTickHistorical  # noqa: F401
from backend.models.odds_quarantine import OddsTickQuarantine  # noqa: F401
from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("db_init")


HYPERTABLE_STATEMENTS = [
    "SELECT create_hypertable('odds_ticks', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day')",
    "SELECT create_hypertable('odds_ticks_historical', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days')",
    "SELECT create_hypertable('match_context', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day')",
    "SELECT create_hypertable('virtual_bets', 'placed_at', if_not_exists => TRUE)",
    "SELECT create_hypertable('training_metrics', 'time', if_not_exists => TRUE)",
    "SELECT create_hypertable('graduation_snapshots', 'time', if_not_exists => TRUE)",
    "SELECT create_hypertable('market_ticks', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day')",
]

# create_hypertable(if_not_exists) never changes an existing table's interval;
# these affect newly created chunks only.
CHUNK_INTERVAL_STATEMENTS = [
    "SELECT set_chunk_time_interval('odds_ticks', INTERVAL '1 day')",
    "SELECT set_chunk_time_interval('odds_ticks_historical', INTERVAL '30 days')",
    "SELECT set_chunk_time_interval('match_context', INTERVAL '1 day')",
]

# Add id column to virtual_bets if missing (for API compatibility)
ADD_VIRTUAL_BETS_ID = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'virtual_bets' AND column_name = 'id'
        ) THEN
            ALTER TABLE virtual_bets ADD COLUMN id BIGSERIAL;
        END IF;
    END $$;
"""

MIGRATION_STATEMENTS = [
    # Add columns introduced in forensic fix (safe: IF NOT EXISTS)
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_training_status' AND column_name='match_start_time')
        THEN ALTER TABLE match_training_status ADD COLUMN match_start_time TIMESTAMPTZ;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_training_status' AND column_name='quality_score')
        THEN ALTER TABLE match_training_status ADD COLUMN quality_score FLOAT DEFAULT 0.0;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_training_status' AND column_name='completeness')
        THEN ALTER TABLE match_training_status ADD COLUMN completeness FLOAT DEFAULT 0.0;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_training_status' AND column_name='tick_count')
        THEN ALTER TABLE match_training_status ADD COLUMN tick_count INTEGER DEFAULT 0;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_results' AND column_name='closing_back_home')
        THEN ALTER TABLE match_results ADD COLUMN closing_back_home FLOAT;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_results' AND column_name='closing_back_away')
        THEN ALTER TABLE match_results ADD COLUMN closing_back_away FLOAT;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_results' AND column_name='closing_lay_home')
        THEN ALTER TABLE match_results ADD COLUMN closing_lay_home FLOAT;
        END IF;
    END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_results' AND column_name='closing_lay_away')
        THEN ALTER TABLE match_results ADD COLUMN closing_lay_away FLOAT;
        END IF;
    END $$;""",
    # S50: simulated (SRL / virtual / e-cricket) flag on the match registry, default false.
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='match_training_status' AND column_name='is_simulated')
        THEN ALTER TABLE match_training_status ADD COLUMN is_simulated BOOLEAN NOT NULL DEFAULT false;
        END IF;
    END $$;""",
    # S50: backfill the flag from team and competition names (same tokens as scraper.match_filter.is_simulated_name).
    """UPDATE match_training_status SET is_simulated = true
       WHERE NOT is_simulated AND (
           (team_home || ' ' || team_away || ' ' || COALESCE(competition, ''))
           ~* '(^|[^a-z])(srl|simulated|virtual|e-?cricket)([^a-z]|$)'
       )""",
    # S56: market JSON fields on market_ticks (all nullable). One ALTER so the statement runs as one command.
    """ALTER TABLE market_ticks
       ADD COLUMN IF NOT EXISTS market_id TEXT,
       ADD COLUMN IF NOT EXISTS back_size DOUBLE PRECISION,
       ADD COLUMN IF NOT EXISTS lay_size DOUBLE PRECISION,
       ADD COLUMN IF NOT EXISTS runner_status TEXT,
       ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ""",
    # S68: toss and score feed pipeline on the match registry (all nullable).
    """ALTER TABLE match_training_status
       ADD COLUMN IF NOT EXISTS toss_winner TEXT,
       ADD COLUMN IF NOT EXISTS toss_decision TEXT,
       ADD COLUMN IF NOT EXISTS score_pipeline TEXT""",
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_odds_match_time ON odds_ticks (match_id, time DESC)",
    "DROP INDEX IF EXISTS idx_odds_live",
    "CREATE INDEX IF NOT EXISTS idx_odds_live_partial ON odds_ticks (time DESC) WHERE is_live",
    "CREATE INDEX IF NOT EXISTS idx_odds_hist_match_time ON odds_ticks_historical (match_id, time)",
    "CREATE INDEX IF NOT EXISTS idx_context_match_time ON match_context (match_id, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bets_match ON virtual_bets (match_id, placed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_match ON match_results (match_id)",
    "CREATE INDEX IF NOT EXISTS idx_training_status ON match_training_status (training_status)",
]

# S53: one harmonised view of every match, and one best-known back/lay view per match and time.
# Runs after the indexes. Plain SELECTs on PostgreSQL 15 with TimescaleDB; the team match is
# case- and whitespace-insensitive, so "Rawalpindiz Srl" and "Rawalpindiz SRL" are the same runner.
VIEW_STATEMENTS = [
    r"""CREATE OR REPLACE VIEW v_match_odds AS
    WITH registry AS (
        SELECT match_id,
               lower(btrim(regexp_replace(team_home, '\s+', ' ', 'g'))) AS home_key,
               lower(btrim(regexp_replace(team_away, '\s+', ' ', 'g'))) AS away_key
        FROM match_training_status
    ),
    listing AS (
        SELECT DISTINCT ON (t.match_id, t.time)
               t.match_id, t.time, t.back_home, t.lay_home, t.back_away, t.lay_away,
               'listing'::text AS source
        FROM odds_ticks t
        WHERE t.source = 'lotusbook'
        ORDER BY t.match_id, t.time, t.tick_seq DESC
    ),
    match_page AS (
        SELECT m.match_id, m.time,
               max(m.back) FILTER (WHERE lower(btrim(regexp_replace(m.selection, '\s+', ' ', 'g'))) = r.home_key) AS back_home,
               max(m.lay)  FILTER (WHERE lower(btrim(regexp_replace(m.selection, '\s+', ' ', 'g'))) = r.home_key) AS lay_home,
               max(m.back) FILTER (WHERE lower(btrim(regexp_replace(m.selection, '\s+', ' ', 'g'))) = r.away_key) AS back_away,
               max(m.lay)  FILTER (WHERE lower(btrim(regexp_replace(m.selection, '\s+', ' ', 'g'))) = r.away_key) AS lay_away,
               'match_page'::text AS source
        FROM market_ticks m
        JOIN registry r ON r.match_id = m.match_id
        WHERE m.market_type = 'match_odds' AND m.source = 'lotusbook'
        GROUP BY m.match_id, m.time, r.home_key, r.away_key
    ),
    all_rows AS (
        SELECT * FROM listing
        UNION ALL
        SELECT * FROM match_page
        WHERE back_home IS NOT NULL OR lay_home IS NOT NULL
           OR back_away IS NOT NULL OR lay_away IS NOT NULL
    ),
    one_per_time AS (
        SELECT DISTINCT ON (match_id, time) *
        FROM all_rows
        ORDER BY match_id, time, CASE WHEN source = 'listing' THEN 0 ELSE 1 END
    )
    SELECT match_id, time, back_home, lay_home, back_away, lay_away, source
    FROM one_per_time
    ORDER BY match_id, time""",
    r"""CREATE OR REPLACE VIEW v_matches AS
    WITH listing_ticks AS (
        SELECT match_id, count(*) AS listing_ticks,
               min(time) AS first_listing, max(time) AS last_listing
        FROM odds_ticks
        WHERE source = 'lotusbook'
        GROUP BY match_id
    ),
    match_page_ticks AS (
        SELECT match_id, count(DISTINCT time) AS match_page_ticks,
               min(time) AS first_match_page, max(time) AS last_match_page
        FROM market_ticks
        WHERE market_type = 'match_odds' AND source = 'lotusbook'
        GROUP BY match_id
    )
    SELECT r.match_id,
           r.team_home,
           r.team_away,
           r.competition,
           r.match_start_time,
           r.is_simulated,
           r.training_status,
           r.scrape_status,
           COALESCE(l.listing_ticks, 0) AS listing_ticks,
           COALESCE(p.match_page_ticks, 0) AS match_page_ticks,
           LEAST(l.first_listing, p.first_match_page) AS first_tick_at,
           GREATEST(l.last_listing, p.last_match_page) AS last_tick_at,
           res.winner AS result_winner,
           res.result_type,
           res.completed_at AS result_completed_at
    FROM match_training_status r
    LEFT JOIN listing_ticks l ON l.match_id = r.match_id
    LEFT JOIN match_page_ticks p ON p.match_id = r.match_id
    LEFT JOIN match_results res ON res.match_id = r.match_id""",
]

# Rows that belong in odds_ticks_historical (kept in sync with migration 002).
HISTORICAL_PREDICATE = (
    "(source IN ('cricsheet_official','synthetic_cricsheet',"
    "'synthetic_archetype','betfair_historical') OR match_id LIKE 'cric\\_%')"
)

# Immediate safety: drop the live retention job until the historical copy is verified.
SAFETY_STATEMENTS = [
    "SELECT remove_retention_policy('odds_ticks', if_exists => TRUE)",
]

# Retention applies ONLY to the live table. Never add it to odds_ticks_historical.
RETENTION_STATEMENTS = [
    "SELECT add_retention_policy('odds_ticks', INTERVAL '90 days', if_not_exists => TRUE)",
]

COMPRESSION_STATEMENTS = [
    "ALTER TABLE odds_ticks SET (timescaledb.compress, timescaledb.compress_segmentby = 'match_id', timescaledb.compress_orderby = 'time DESC')",
    "SELECT add_compression_policy('odds_ticks', INTERVAL '7 days', if_not_exists => TRUE)",
    "ALTER TABLE odds_ticks_historical SET (timescaledb.compress, timescaledb.compress_segmentby = 'match_id', timescaledb.compress_orderby = 'time DESC')",
    "SELECT add_compression_policy('odds_ticks_historical', INTERVAL '30 days', if_not_exists => TRUE)",
]


async def _historical_copy_complete(engine) -> bool:
    """True when every historical-looking row in odds_ticks exists in odds_ticks_historical."""
    async with engine.begin() as conn:
        legacy = (await conn.execute(text(
            f"SELECT count(*) FROM odds_ticks WHERE {HISTORICAL_PREDICATE}"))).scalar() or 0
        copied = (await conn.execute(text(
            "SELECT count(*) FROM odds_ticks_historical"))).scalar() or 0
    logger.info("historical_copy_check", legacy_rows=legacy, copied_rows=copied)
    return copied >= legacy


async def init_database() -> None:
    """Create all tables, hypertables, indexes, and retention policies."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, echo=False)

    logger.info("creating_tables")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("tables_created")

    # Run each statement in its own transaction so one failure doesn't cascade
    all_statements = (
        [(s, "hypertable") for s in HYPERTABLE_STATEMENTS]
        + [(ADD_VIRTUAL_BETS_ID, "virtual_bets_id")]
        + [(s, "migration") for s in MIGRATION_STATEMENTS]
        + [(s, "index") for s in INDEX_STATEMENTS]
        + [(s, "view") for s in VIEW_STATEMENTS]
        + [(s, "chunk_interval") for s in CHUNK_INTERVAL_STATEMENTS]
        + [(s, "compression") for s in COMPRESSION_STATEMENTS]
    )
    for stmt, kind in all_statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
                logger.info(f"{kind}_created", statement=stmt[:60])
        except Exception as e:
            logger.warning(f"{kind}_skip", statement=stmt[:60], error=str(e))

    # Retention is opt-in (ODDS_TICKS_RETENTION=on). Recorded LotusBook ticks are the project's only real
    # price history (Betfair is unavailable) and the historical copy only moves synthetic rows, so a
    # default retention policy would delete real data. When enabled, it still waits for the historical copy.
    try:
        retention_on = os.getenv("ODDS_TICKS_RETENTION", "off").strip().lower() in ("on", "1", "true")
        if not retention_on:
            for stmt in SAFETY_STATEMENTS:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
            print("db_init: retention disabled (ODDS_TICKS_RETENTION=off) -> no retention policy on odds_ticks")
        elif await _historical_copy_complete(engine):
            for stmt in RETENTION_STATEMENTS:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
            print("db_init: historical copy complete -> live retention policy (90 days) applied to odds_ticks")
        else:
            for stmt in SAFETY_STATEMENTS:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
            print("db_init: historical copy INCOMPLETE -> removed retention policy on odds_ticks; not re-added")
    except Exception as e:
        logger.warning("retention_step_failed", error=str(e))
        print(f"db_init: retention step failed ({e}); no retention policy was added")

    await engine.dispose()
    logger.info("database_initialized")


if __name__ == "__main__":
    asyncio.run(init_database())
