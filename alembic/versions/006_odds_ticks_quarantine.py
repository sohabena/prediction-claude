"""odds_ticks_quarantine table (S57)

Revision ID: 006_odds_ticks_quarantine
Revises: 005_market_ticks_json_fields
Create Date: 2026-11-03

Plain table holding odds_ticks rows that do not belong to their match (post-settlement resets and cross-match
copies). Rows are moved here by scripts/quarantine_ticks.py --apply, never deleted. This migration is NOT applied
by the story; the orchestrator applies it.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "006_odds_ticks_quarantine"
down_revision: Union[str, None] = "005_market_ticks_json_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_ticks_quarantine (
            time TIMESTAMPTZ NOT NULL,
            match_id VARCHAR NOT NULL,
            tick_seq BIGINT NOT NULL,
            team_home VARCHAR NOT NULL,
            team_away VARCHAR NOT NULL,
            competition VARCHAR,
            back_home DOUBLE PRECISION,
            lay_home DOUBLE PRECISION,
            back_draw DOUBLE PRECISION,
            lay_draw DOUBLE PRECISION,
            back_away DOUBLE PRECISION,
            lay_away DOUBLE PRECISION,
            volume_back_home DOUBLE PRECISION,
            volume_lay_home DOUBLE PRECISION,
            volume_back_away DOUBLE PRECISION,
            volume_lay_away DOUBLE PRECISION,
            implied_prob_home DOUBLE PRECISION,
            implied_prob_away DOUBLE PRECISION,
            overround DOUBLE PRECISION,
            score INTEGER,
            wickets INTEGER,
            overs DOUBLE PRECISION,
            innings INTEGER,
            is_live BOOLEAN NOT NULL,
            source VARCHAR NOT NULL,
            scrape_latency_ms INTEGER,
            reason TEXT NOT NULL,
            quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (time, match_id, tick_seq)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS odds_ticks_quarantine")
