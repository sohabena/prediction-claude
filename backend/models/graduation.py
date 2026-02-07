"""ORM model for graduation_snapshots hypertable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraduationSnapshot(Base):
    """Daily snapshot of graduation criteria evaluation."""

    __tablename__ = "graduation_snapshots"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    roi: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    profitable_days: Mapped[int] = mapped_column(Integer, default=0)
    total_bets: Mapped[int] = mapped_column(Integer, default=0)
    all_criteria_met: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_days: Mapped[int] = mapped_column(Integer, default=0)
