"""Market ticks for LotusBook Bookmaker and session markets (S47)

Revision ID: 003_market_ticks
Revises: 002_timescale_hardening
Create Date: 2026-11-02

Creates the table only. The hypertable conversion is listed in backend/db_init.py
(HYPERTABLE_STATEMENTS), which the operator runs. This migration is NOT applied by the
story; apply it per docs/runbooks/recording.md.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "003_market_ticks"
down_revision: Union[str, None] = "002_timescale_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS market_ticks (
            time TIMESTAMPTZ NOT NULL,
            match_id TEXT NOT NULL,
            market_type TEXT NOT NULL,
            market_name TEXT NOT NULL,
            selection TEXT NOT NULL DEFAULT '',
            back DOUBLE PRECISION,
            lay DOUBLE PRECISION,
            line_no DOUBLE PRECISION,
            rate_no DOUBLE PRECISION,
            line_yes DOUBLE PRECISION,
            rate_yes DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'open',
            source TEXT NOT NULL DEFAULT 'lotusbook',
            PRIMARY KEY (time, match_id, market_type, market_name, selection)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_market_ticks_match_time ON market_ticks (match_id, time DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_market_ticks_match_time")
    op.execute("DROP TABLE IF EXISTS market_ticks")
