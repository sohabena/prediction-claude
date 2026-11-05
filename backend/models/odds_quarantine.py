"""ORM model for odds_ticks_quarantine (S57): odds rows that do not belong to the match they are stored under.

A plain table (not a hypertable). Rows are copied here by scripts/quarantine_ticks.py --apply and removed from
odds_ticks in the same transaction. Nothing is deleted outright.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class OddsTickQuarantine(Base):
    """odds_ticks row moved out of the recording, with the rule that moved it."""

    __tablename__ = "odds_ticks_quarantine"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    tick_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_home: Mapped[str] = mapped_column(String, nullable=False)
    team_away: Mapped[str] = mapped_column(String, nullable=False)
    competition: Mapped[str | None] = mapped_column(String, nullable=True)

    back_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    lay_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    volume_back_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_lay_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_back_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_lay_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    implied_prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_prob_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    overround: Mapped[float | None] = mapped_column(Float, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wickets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overs: Mapped[float | None] = mapped_column(Float, nullable=True)
    innings: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="lotusbook")
    scrape_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str] = mapped_column(String, nullable=False)
    quarantined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
