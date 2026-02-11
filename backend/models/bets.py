"""ORM model for virtual_bets hypertable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class VirtualBetRecord(Base):
    """Record of virtual bets placed by the RL agent."""

    __tablename__ = "virtual_bets"

    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    team: Mapped[str] = mapped_column(String, nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Settlement
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)

    # CLV (Closing Line Value)
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv: Mapped[float | None] = mapped_column(Float, nullable=True)

    # RL metadata
    agent_version: Mapped[str] = mapped_column(String, default="")
    observation_hash: Mapped[str] = mapped_column(String, default="")
    reward: Mapped[float] = mapped_column(Float, default=0.0)
