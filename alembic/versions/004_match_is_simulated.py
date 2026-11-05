"""Simulated flag on the match registry (S50)

Revision ID: 004_match_is_simulated
Revises: 003_market_ticks
Create Date: 2026-11-03

Adds match_training_status.is_simulated (default false) and backfills it for SRL / virtual /
e-cricket matches by name, using the same tokens as scraper.match_filter.is_simulated_name.
This migration is NOT applied by the story; apply it per docs/runbooks/recording.md.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "004_match_is_simulated"
down_revision: Union[str, None] = "003_market_ticks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE match_training_status ADD COLUMN IF NOT EXISTS is_simulated BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        """
        UPDATE match_training_status SET is_simulated = true
        WHERE NOT is_simulated AND (
            (team_home || ' ' || team_away || ' ' || COALESCE(competition, ''))
            ~* '(^|[^a-z])(srl|simulated|virtual|e-?cricket)([^a-z]|$)'
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE match_training_status DROP COLUMN IF EXISTS is_simulated")
