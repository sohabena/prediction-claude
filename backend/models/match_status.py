"""ORM model for match_training_status table.

Tracks per-match approval status for RL training.
Only 'approved' matches are loaded by the data loader.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func, text
from sqlalchemy.orm import Mapped, deferred, mapped_column

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

    # S50: True for simulated (SRL / virtual / e-cricket) matches. They are recorded but never approved,
    # published or used for training unless a reader opts in (include_simulated=True).
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False,
    )

    # S68: toss from the live score feed (raw feed text), and the score feed pipeline that served the match
    # ("livescore-v2" or "legacy"). All nullable; NULL until the feed has said so.
    toss_winner: Mapped[str | None] = mapped_column(String, nullable=True)
    toss_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    score_pipeline: Mapped[str | None] = mapped_column(String, nullable=True)

    # S86: when the match actually started (the off), and which signal said so. Set once, by the first of: the
    # match JSON's market turning in-play, the listing's live badge, the score feed's first ball. Nullable; NULL
    # until a signal arrives. Deferred: a plain SELECT of this model does not read them, so a database without the
    # S86 migration still works (the scraper writes them with its own guarded UPDATE).
    actual_start_time: Mapped[datetime | None] = deferred(mapped_column(
        DateTime(timezone=True), nullable=True,
    ))
    actual_start_source: Mapped[str | None] = deferred(mapped_column(String, nullable=True))

    # S96: a non-canonical twin (the same fixture listed under a second lb_ id) points at its canonical match id.
    # NULL for the canonical match and for every ordinary match. Deferred, like the S86 columns, so a plain SELECT
    # of this model works on a database without the S96 migration.
    canonical_match_id: Mapped[str | None] = deferred(mapped_column(String, nullable=True))

    # Total recorded odds/microstructure ticks for this match
    tick_count: Mapped[int] = mapped_column(Integer, default=0)

    # Quality metrics (populated during validation)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
