"""ORM model for training_metrics hypertable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class TrainingMetricRecord(Base):
    """Time-series record of RL training metrics."""

    __tablename__ = "training_metrics"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    episode: Mapped[int] = mapped_column(Integer, default=0)
    total_timesteps: Mapped[int] = mapped_column(Integer, default=0)
    episode_reward: Mapped[float] = mapped_column(Float, default=0.0)
    episode_length: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    roi: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    policy_loss: Mapped[float] = mapped_column(Float, default=0.0)
    value_loss: Mapped[float] = mapped_column(Float, default=0.0)
    entropy: Mapped[float] = mapped_column(Float, default=0.0)
    agent_version: Mapped[str] = mapped_column(String, default="")
