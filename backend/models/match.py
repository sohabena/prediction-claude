"""ORM model for match_context hypertable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MatchContextRecord(Base):
    """Time-series record of match statistics from Cricbuzz enricher."""

    __tablename__ = "match_context"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    wickets: Mapped[int] = mapped_column(Integer, default=0)
    overs: Mapped[float] = mapped_column(Float, default=0.0)
    run_rate: Mapped[float] = mapped_column(Float, default=0.0)
    req_run_rate: Mapped[float] = mapped_column(Float, default=0.0)
    innings: Mapped[int] = mapped_column(Integer, default=1)
    balls_remaining: Mapped[int] = mapped_column(Integer, default=0)
    batting_team: Mapped[str | None] = mapped_column(String, nullable=True)
    bowling_team: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="scheduled")
    match_format: Mapped[str] = mapped_column(String, default="T20")
