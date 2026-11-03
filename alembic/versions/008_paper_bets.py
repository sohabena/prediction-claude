"""paper_bets virtual-bet ledger (S71)

Revision ID: 008_paper_bets
Revises: 007_toss_fields
Create Date: 2026-11-03

Plain table of virtual (paper) bets with the decision context, fill and settlement. No real money moves; the
table is written by virtual_trading/paper_ledger.py. This migration is NOT applied by the story; the orchestrator
applies it.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "008_paper_bets"
down_revision: Union[str, None] = "007_toss_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_bets (
            bet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            variant TEXT NOT NULL,
            match_id TEXT NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL,
            filled_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'pending',
            side TEXT NOT NULL,
            selection TEXT NOT NULL,
            team TEXT NOT NULL,
            price_limit DOUBLE PRECISION,
            price_filled DOUBLE PRECISION,
            stake DOUBLE PRECISION NOT NULL,
            liability DOUBLE PRECISION,
            reason TEXT NOT NULL DEFAULT '',
            p_model DOUBLE PRECISION,
            p_market DOUBLE PRECISION,
            p_used DOUBLE PRECISION NOT NULL,
            edge DOUBLE PRECISION NOT NULL,
            source TEXT NOT NULL,
            in_play BOOLEAN NOT NULL DEFAULT FALSE,
            score_state JSONB,
            book JSONB NOT NULL,
            pnl DOUBLE PRECISION,
            settled_at TIMESTAMPTZ,
            result TEXT,
            clv DOUBLE PRECISION,
            closing_price DOUBLE PRECISION
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_bets_variant_decided ON paper_bets (variant, decided_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_bets_match ON paper_bets (match_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_paper_bets_match")
    op.execute("DROP INDEX IF EXISTS idx_paper_bets_variant_decided")
    op.execute("DROP TABLE IF EXISTS paper_bets")
