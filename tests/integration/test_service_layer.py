"""Integration tests for the Service Layer (API → Service → Repository).

These tests validate the service-layer refactoring by verifying
that MatchService, AgentService, and TrainingService correctly
orchestrate their repository calls and return proper shapes.

These tests DO NOT require a running database — they use mocking
at the module-level repo/redis boundary to verify the service contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.match_service import MatchService
from backend.services.agent_service import AgentService
from backend.services.training_service import TrainingService


# ================================================================
# MatchService
# ================================================================


class TestMatchService:
    """Tests for MatchService business logic."""

    def setup_method(self):
        self.svc = MatchService()

    @pytest.mark.asyncio
    async def test_get_active_matches_returns_dict(self):
        """Service should return a dict with matches list."""
        with patch("backend.services.match_service.get_redis") as mock_redis:
            redis_mock = AsyncMock()
            redis_mock.get_json = AsyncMock(return_value={
                "matches": [
                    {"match_id": "m1", "team_home": "India", "team_away": "Australia"},
                ],
                "updated_at": "2026-01-01T00:00:00Z",
            })
            mock_redis.return_value = redis_mock

            result = await self.svc.get_active_matches()
            assert "matches" in result
            assert isinstance(result["matches"], list)
            assert len(result["matches"]) == 1

    @pytest.mark.asyncio
    async def test_get_active_matches_empty_fallback(self):
        """Service should return empty matches on Redis miss."""
        with patch("backend.services.match_service.get_redis") as mock_redis:
            redis_mock = AsyncMock()
            redis_mock.get_json = AsyncMock(return_value=None)
            mock_redis.return_value = redis_mock

            result = await self.svc.get_active_matches()
            assert result["matches"] == []

    @pytest.mark.asyncio
    async def test_get_match_odds_delegates_to_repo(self):
        """Service should delegate to repo and return shaped response."""
        with patch("backend.services.match_service.repo") as mock_repo:
            mock_repo.get_odds_ticks = AsyncMock(return_value=[
                {"time": "2026-01-01T00:00:00Z", "back_home": 1.85},
                {"time": "2026-01-01T00:00:03Z", "back_home": 1.86},
            ])

            result = await self.svc.get_match_odds("m1", limit=10)
            assert result["match_id"] == "m1"
            assert result["count"] == 2
            assert len(result["ticks"]) == 2

    @pytest.mark.asyncio
    async def test_get_match_context_delegates_to_repo(self):
        """Service should delegate context fetching to repo."""
        with patch("backend.services.match_service.repo") as mock_repo:
            mock_repo.get_match_context = AsyncMock(return_value=[])

            result = await self.svc.get_match_context("m1", limit=50)
            assert result["match_id"] == "m1"
            assert result["count"] == 0
            assert result["context"] == []

    @pytest.mark.asyncio
    async def test_get_training_status_computes_summary(self):
        """Service should compute status summary from repo data."""
        with patch("backend.services.match_service.repo") as mock_repo:
            mock_repo.get_all_training_statuses = AsyncMock(return_value=[
                {"match_id": "m1", "training_status": "approved", "scrape_status": "scrape_approved"},
                {"match_id": "m2", "training_status": "pending", "scrape_status": "discovered"},
                {"match_id": "m3", "training_status": "pending", "scrape_status": "discovered"},
                {"match_id": "m4", "training_status": "rejected", "scrape_status": "scrape_rejected"},
            ])

            result = await self.svc.get_training_status(None)
            assert result["total"] == 4
            assert result["summary"]["approved"] == 1
            assert result["summary"]["pending"] == 2
            assert result["summary"]["rejected"] == 1

    @pytest.mark.asyncio
    async def test_validate_raises_on_no_data(self):
        """Validation should raise LookupError when no tick data."""
        with patch("backend.services.match_service.repo") as mock_repo:
            mock_repo.get_full_odds_episode = AsyncMock(return_value=[])

            with pytest.raises(LookupError, match="No tick data"):
                await self.svc.validate_match_data("m_missing")


# ================================================================
# AgentService
# ================================================================


class TestAgentService:
    """Tests for AgentService business logic."""

    def setup_method(self):
        self.svc = AgentService()

    @pytest.mark.asyncio
    async def test_get_agent_state_returns_dict(self):
        """Service should return mapped agent state from orchestrator."""
        with patch("backend.services.agent_service.get_redis") as mock_redis:
            redis_mock = AsyncMock()
            redis_mock.get_json = AsyncMock(return_value={
                "state": "graduated",
                "model_version": 3,
            })
            mock_redis.return_value = redis_mock

            result = await self.svc.get_agent_state()
            assert isinstance(result, dict)
            # Service maps orchestrator state to AgentState enum
            assert "state" in result

    @pytest.mark.asyncio
    async def test_get_agent_state_fallback_on_error(self):
        """Service should return safe defaults when Redis fails."""
        with patch("backend.services.agent_service.get_redis") as mock_redis:
            mock_redis.side_effect = Exception("Redis down")

            result = await self.svc.get_agent_state()
            assert result["state"] == "paused"
            assert result["version"] == "none"

    @pytest.mark.asyncio
    async def test_get_virtual_bets_delegates_to_repo(self):
        """Service should delegate to repo and shape response."""
        with patch("backend.services.agent_service.repo") as mock_repo:
            mock_repo.get_virtual_bets = AsyncMock(return_value=[
                {"match_id": "m1", "action": "BACK_HOME_SM"},
            ])

            result = await self.svc.get_virtual_bets(50, None)
            assert result["count"] == 1
            assert len(result["bets"]) == 1

    @pytest.mark.asyncio
    async def test_get_performance_delegates_to_repo(self):
        """Performance should delegate to repo."""
        with patch("backend.services.agent_service.repo") as mock_repo:
            mock_repo.get_performance_summary = AsyncMock(return_value={
                "total_bets": 42, "win_rate": 0.55, "total_pnl": 1200.0,
            })

            result = await self.svc.get_performance()
            assert result["total_bets"] == 42
            assert result["win_rate"] == 0.55


# ================================================================
# TrainingService
# ================================================================


class TestTrainingService:
    """Tests for TrainingService business logic."""

    def setup_method(self):
        self.svc = TrainingService()

    @pytest.mark.asyncio
    async def test_get_training_progress_returns_defaults_when_no_file(self):
        """Should return idle defaults when progress file doesn't exist."""
        with patch("backend.services.training_service._find_progress_file", return_value=None):
            result = await self.svc.get_training_progress()
            assert result["current_step"] == 0
            assert result["total_steps"] == 0
            assert result["status"] == "idle"

    @pytest.mark.asyncio
    async def test_get_training_metrics_delegates_to_repo(self):
        """Metrics should delegate to repo."""
        with patch("backend.services.training_service.repo") as mock_repo:
            mock_repo.get_training_metrics = AsyncMock(return_value=[
                {"time": "2026-01-01", "episode": 1, "win_rate": 0.5},
                {"time": "2026-01-02", "episode": 2, "win_rate": 0.55},
            ])

            result = await self.svc.get_training_metrics(100)
            assert result["count"] == 2
            assert len(result["metrics"]) == 2

    @pytest.mark.asyncio
    async def test_get_training_summary_returns_dict(self):
        """Summary should always return a dict with progress."""
        with patch("backend.services.training_service._find_progress_file", return_value=None):
            with patch("backend.services.training_service.repo") as mock_repo:
                mock_repo.get_training_summary = AsyncMock(return_value={
                    "total_timesteps": 100000,
                    "total_episodes": 50,
                    "recent_win_rate": 0.55,
                    "recent_roi": 0.02,
                    "recent_sharpe": 1.2,
                    "latest_version": "v3",
                })

                result = await self.svc.get_training_summary()
                assert isinstance(result, dict)
                assert result["total_timesteps"] == 100000
                assert result["progress"] is None  # No progress file

    @pytest.mark.asyncio
    async def test_get_graduation_history_delegates_to_repo(self):
        """Graduation history should delegate to repo."""
        with patch("backend.services.training_service.repo") as mock_repo:
            mock_repo.get_graduation_history = AsyncMock(return_value=[
                {"time": "2026-01-01", "all_criteria_met": False},
            ])

            result = await self.svc.get_graduation_history(30)
            assert result["count"] == 1
