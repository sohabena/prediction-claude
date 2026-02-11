"""ORM model for match_results table -- stores final match outcomes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MatchResultRecord(Base):
    """Stores the verified final result of a completed cricket match."""

    __tablename__ = "match_results"

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    winner: Mapped[str] = mapped_column(String, nullable=False)
    loser: Mapped[str] = mapped_column(String, nullable=False)
    result_type: Mapped[str] = mapped_column(String, default="win")
    margin: Mapped[str] = mapped_column(String, default="")
    team_home: Mapped[str] = mapped_column(String, default="")
    team_away: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="cricbuzz")
