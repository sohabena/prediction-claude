"""ORM model for market_ticks hypertable (S47): Bookmaker and session markets from LotusBook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketTick(Base):
    """One changed snapshot row of a non-match-odds market."""

    __tablename__ = "market_ticks"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    market_type: Mapped[str] = mapped_column(String, primary_key=True)  # bookmaker | session | other
    market_name: Mapped[str] = mapped_column(String, primary_key=True)
    selection: Mapped[str] = mapped_column(String, primary_key=True, default="")

    back: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_no: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_no: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")  # open | suspended | ball_running
    source: Mapped[str] = mapped_column(String, default="lotusbook")
