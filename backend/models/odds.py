"""ORM model for odds_ticks hypertable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class OddsTick(Base):
    """Time-series record of odds scraped from LotusBook."""

    __tablename__ = "odds_ticks"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_home: Mapped[str] = mapped_column(String, nullable=False)
    team_away: Mapped[str] = mapped_column(String, nullable=False)
    competition: Mapped[str | None] = mapped_column(String, nullable=True)

    # 1X2 Odds
    back_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Derived
    implied_prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_prob_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    overround: Mapped[float | None] = mapped_column(Float, nullable=True)

    # State
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)

    # Metadata
    source: Mapped[str] = mapped_column(String, default="lotusbook")
