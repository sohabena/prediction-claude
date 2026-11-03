"""match_training_status toss and score feed pipeline (S68)

Revision ID: 007_toss_fields
Revises: 006_odds_ticks_quarantine
Create Date: 2026-11-03

Adds nullable ``toss_winner``, ``toss_decision`` (from the live score feed) and ``score_pipeline`` ("livescore-v2"
or "legacy") to match_training_status. The scraper's db_init adds the same columns on startup. This migration is
NOT applied by the story; the orchestrator applies it.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "007_toss_fields"
down_revision: Union[str, None] = "006_odds_ticks_quarantine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE match_training_status
            ADD COLUMN IF NOT EXISTS toss_winner TEXT,
            ADD COLUMN IF NOT EXISTS toss_decision TEXT,
            ADD COLUMN IF NOT EXISTS score_pipeline TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE match_training_status
            DROP COLUMN IF EXISTS toss_winner,
            DROP COLUMN IF EXISTS toss_decision,
            DROP COLUMN IF EXISTS score_pipeline
        """
    )
