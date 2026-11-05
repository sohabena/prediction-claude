"""PHOENIX initial schema baseline

Revision ID: 001_initial
Revises: None
Create Date: 2026-04-19

Captures the existing schema as the Alembic baseline.
Run `alembic stamp head` on existing databases to mark them as current.
New databases should run `alembic upgrade head` instead of db_init.py.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- odds_ticks ---
    op.create_table(
        "odds_ticks",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("team_home", sa.String(), nullable=False),
        sa.Column("team_away", sa.String(), nullable=False),
        sa.Column("competition", sa.String(), nullable=True),
        sa.Column("back_home", sa.Float(), nullable=True),
        sa.Column("lay_home", sa.Float(), nullable=True),
        sa.Column("back_draw", sa.Float(), nullable=True),
        sa.Column("lay_draw", sa.Float(), nullable=True),
        sa.Column("back_away", sa.Float(), nullable=True),
        sa.Column("lay_away", sa.Float(), nullable=True),
        sa.Column("volume_back_home", sa.Float(), nullable=True),
        sa.Column("volume_lay_home", sa.Float(), nullable=True),
        sa.Column("volume_back_away", sa.Float(), nullable=True),
        sa.Column("volume_lay_away", sa.Float(), nullable=True),
        sa.Column("implied_prob_home", sa.Float(), nullable=True),
        sa.Column("implied_prob_away", sa.Float(), nullable=True),
        sa.Column("overround", sa.Float(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("wickets", sa.Integer(), nullable=True),
        sa.Column("overs", sa.Float(), nullable=True),
        sa.Column("innings", sa.Integer(), nullable=True),
        sa.Column("is_live", sa.Boolean(), default=False),
        sa.Column("source", sa.String(), default="lotusbook"),
        sa.Column("scrape_latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("time", "match_id"),
    )
    op.create_index("idx_odds_match_time", "odds_ticks", ["match_id", sa.text("time DESC")])
    op.create_index("idx_odds_live", "odds_ticks", ["is_live", sa.text("time DESC")])

    # --- match_context ---
    op.create_table(
        "match_context",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("wickets", sa.Integer(), nullable=True),
        sa.Column("overs", sa.Float(), nullable=True),
        sa.Column("run_rate", sa.Float(), nullable=True),
        sa.Column("req_run_rate", sa.Float(), nullable=True),
        sa.Column("innings", sa.Integer(), nullable=True),
        sa.Column("balls_remaining", sa.Integer(), nullable=True),
        sa.Column("batting_team", sa.String(), nullable=True),
        sa.Column("bowling_team", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("match_format", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("time", "match_id"),
    )
    op.create_index("idx_context_match_time", "match_context", ["match_id", sa.text("time DESC")])

    # --- virtual_bets ---
    op.create_table(
        "virtual_bets",
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column("stake", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("profit_loss", sa.Float(), default=0.0),
        sa.Column("closing_odds", sa.Float(), nullable=True),
        sa.Column("clv", sa.Float(), nullable=True),
        sa.Column("agent_version", sa.String(), default=""),
        sa.Column("observation_hash", sa.String(), default=""),
        sa.Column("reward", sa.Float(), default=0.0),
        sa.PrimaryKeyConstraint("placed_at", "match_id"),
    )
    op.create_index("idx_bets_match", "virtual_bets", ["match_id", sa.text("placed_at DESC")])

    # --- training_metrics ---
    op.create_table(
        "training_metrics",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("total_timesteps", sa.Integer(), nullable=True),
        sa.Column("episode_reward", sa.Float(), nullable=True),
        sa.Column("episode_length", sa.Integer(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("roi", sa.Float(), nullable=True),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("policy_loss", sa.Float(), nullable=True),
        sa.Column("value_loss", sa.Float(), nullable=True),
        sa.Column("entropy", sa.Float(), nullable=True),
        sa.Column("agent_version", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("time"),
    )

    # --- graduation_snapshots ---
    op.create_table(
        "graduation_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("roi", sa.Float(), nullable=True),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("profitable_days", sa.Integer(), nullable=True),
        sa.Column("total_bets", sa.Integer(), nullable=True),
        sa.Column("all_criteria_met", sa.Boolean(), nullable=True),
        sa.Column("consecutive_days", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("time"),
    )

    # --- match_results ---
    op.create_table(
        "match_results",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("winner", sa.String(), nullable=False),
        sa.Column("loser", sa.String(), nullable=False),
        sa.Column("result_type", sa.String(), default="win"),
        sa.Column("margin", sa.String(), default=""),
        sa.Column("team_home", sa.String(), default=""),
        sa.Column("team_away", sa.String(), default=""),
        sa.Column("source", sa.String(), default="lotusbook_odds"),
        sa.Column("closing_back_home", sa.Float(), nullable=True),
        sa.Column("closing_back_away", sa.Float(), nullable=True),
        sa.Column("closing_lay_home", sa.Float(), nullable=True),
        sa.Column("closing_lay_away", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index("idx_results_match", "match_results", ["match_id"])

    # --- match_training_status ---
    op.create_table(
        "match_training_status",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("team_home", sa.String(), nullable=False),
        sa.Column("team_away", sa.String(), nullable=False),
        sa.Column("competition", sa.String(), default=""),
        sa.Column("match_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scrape_status", sa.String(), default="discovered"),
        sa.Column("training_status", sa.String(), default="pending"),
        sa.Column("auto_approved", sa.Boolean(), default=False),
        sa.Column("quality_score", sa.Float(), default=0.0),
        sa.Column("completeness", sa.Float(), default=0.0),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index("idx_training_status", "match_training_status", ["training_status"])

    # Note: TimescaleDB hypertables and retention policies are still
    # applied by db_init.py since they're Timescale-specific DDL.


def downgrade() -> None:
    op.drop_table("match_training_status")
    op.drop_table("match_results")
    op.drop_table("graduation_snapshots")
    op.drop_table("training_metrics")
    op.drop_table("virtual_bets")
    op.drop_table("match_context")
    op.drop_table("odds_ticks")
