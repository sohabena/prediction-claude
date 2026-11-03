"""ORM model for paper_bets (S71): one row per virtual fill, with the decision context and its settlement.

A plain table (not a hypertable). Paper trading places no real money; every row is virtual.
Rows are written by virtual_trading/paper_ledger.py. The table is created by create_all (db_init) and by
alembic 008_paper_bets.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class PaperBet(Base):
    """A virtual bet: what the model knew, the price asked, the price filled, and the settled result."""

    __tablename__ = "paper_bets"
    __table_args__ = (
        Index("idx_paper_bets_variant_decided", "variant", "decided_at"),
        Index("idx_paper_bets_match", "match_id"),
    )

    bet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    variant: Mapped[str] = mapped_column(String, nullable=False)
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    side: Mapped[str] = mapped_column(String, nullable=False)  # BACK | LAY
    selection: Mapped[str] = mapped_column(String, nullable=False)  # home | away
    team: Mapped[str] = mapped_column(String, nullable=False)

    price_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_filled: Mapped[float | None] = mapped_column(Float, nullable=True)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    liability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")

    p_model: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_used: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # prematch | inplay | market_only
    in_play: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    book: Mapped[dict] = mapped_column(JSONB, nullable=False)

    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)  # home | away | void
    clv: Mapped[float | None] = mapped_column(Float, nullable=True)
    closing_price: Mapped[float | None] = mapped_column(Float, nullable=True)
