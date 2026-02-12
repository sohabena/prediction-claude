"""ORM model for match_training_status table.

Tracks per-match approval status for RL training.
Only 'approved' matches are loaded by the data loader.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MatchTrainingStatus(Base):
    """Per-match training approval record."""

    __tablename__ = "match_training_status"

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_home: Mapped[str] = mapped_column(String, nullable=False)
    team_away: Mapped[str] = mapped_column(String, nullable=False)
    competition: Mapped[str] = mapped_column(String, default="")
    match_start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Scrape gate: discovered | scrape_approved | scrape_rejected
    # Only scrape_approved matches have their tick data collected
    scrape_status: Mapped[str] = mapped_column(String, default="discovered")

    # Training gate: pending | approved | rejected
    # Only applies after data is collected (scrape_approved)
    training_status: Mapped[str] = mapped_column(String, default="pending")

    # True when the system auto-approved (ICC/International/Major Franchise)
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    # Quality metrics (populated during validation)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
