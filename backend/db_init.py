"""
Database initialization script for PHOENIX.
Creates all tables and TimescaleDB hypertables.

Usage:
    python -m backend.db_init
"""

from __future__ import annotations

import asyncio

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
from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("db_init")


HYPERTABLE_STATEMENTS = [
    "SELECT create_hypertable('odds_ticks', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 hour')",
    "SELECT create_hypertable('match_context', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 hour')",
    "SELECT create_hypertable('virtual_bets', 'placed_at', if_not_exists => TRUE)",
    "SELECT create_hypertable('training_metrics', 'time', if_not_exists => TRUE)",
    "SELECT create_hypertable('graduation_snapshots', 'time', if_not_exists => TRUE)",
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
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_odds_match_time ON odds_ticks (match_id, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_odds_live ON odds_ticks (is_live, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_context_match_time ON match_context (match_id, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bets_match ON virtual_bets (match_id, placed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_match ON match_results (match_id)",
    "CREATE INDEX IF NOT EXISTS idx_training_status ON match_training_status (training_status)",
]

RETENTION_STATEMENTS = [
    "SELECT add_retention_policy('odds_ticks', INTERVAL '90 days', if_not_exists => TRUE)",
    "SELECT add_compression_policy('odds_ticks', INTERVAL '7 days', if_not_exists => TRUE)",
]


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
        + [(s, "retention") for s in RETENTION_STATEMENTS]
    )
    for stmt, kind in all_statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
                logger.info(f"{kind}_created", statement=stmt[:60])
        except Exception as e:
            logger.warning(f"{kind}_skip", statement=stmt[:60], error=str(e))

    await engine.dispose()
    logger.info("database_initialized")


if __name__ == "__main__":
    asyncio.run(init_database())
