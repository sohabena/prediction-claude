"""Market JSON fields on market_ticks (S56)

Revision ID: 005_market_ticks_json_fields
Revises: 004_match_is_simulated
Create Date: 2026-11-03

Adds nullable columns for the page's own market JSON: market_id, back_size, lay_size, runner_status and
source_updated_at. DOM-parsed rows leave them empty. This migration is NOT applied by the story; apply it
per docs/runbooks/recording.md.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "005_market_ticks_json_fields"
down_revision: Union[str, None] = "004_match_is_simulated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE market_ticks
            ADD COLUMN IF NOT EXISTS market_id TEXT,
            ADD COLUMN IF NOT EXISTS back_size DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS lay_size DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS runner_status TEXT,
            ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE market_ticks
            DROP COLUMN IF EXISTS source_updated_at,
            DROP COLUMN IF EXISTS runner_status,
            DROP COLUMN IF EXISTS lay_size,
            DROP COLUMN IF EXISTS back_size,
            DROP COLUMN IF EXISTS market_id
        """
    )
