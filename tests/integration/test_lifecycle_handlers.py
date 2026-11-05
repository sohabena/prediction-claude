"""Integration tests for the orchestrator lifecycle handlers.

Tests the extracted handlers (DriftHandler, GraduationHandler,
TrainingHandler) in isolation from the orchestrator state machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rl.lifecycle.drift_handler import DriftHandler
from virtual_trading.shadow_trader import ShadowTrader, DriftStatus


# ================================================================
# DriftHandler
# ================================================================


def _make_drift_status(
    is_drifting: bool = False,
    should_demote: bool = False,
    consecutive_drift_days: int = 0,
    violations: list | None = None,
) -> DriftStatus:
    """Helper: build a DriftStatus with pre-set fields."""
    ds = DriftStatus()
    ds.is_drifting = is_drifting
    ds.should_demote = should_demote
    ds.consecutive_drift_days = consecutive_drift_days
    ds.violations = violations or []
    return ds


class TestDriftHandler:
    """Tests for the extracted drift detection handler."""

    def setup_method(self):
        self.shadow = ShadowTrader(initial_balance=100000)
        self.handler = DriftHandler(self.shadow)

    @pytest.mark.asyncio
    async def test_no_drift_returns_false(self):
        """Should return False when no drift detected."""
        with patch("rl.lifecycle.drift_handler.get_redis") as mock_redis:
            redis_mock = AsyncMock()
            redis_mock.set_json = AsyncMock()
            mock_redis.return_value = redis_mock

            stats: dict = {"demotions": 0}
            result = await self.handler.check(model_version=1, stats=stats)
            assert result is False
            assert stats["demotions"] == 0

    @pytest.mark.asyncio
    async def test_drift_triggers_demotion(self):
        """Should return True and update stats when drift demands demotion."""
        drift = _make_drift_status(
            is_drifting=True,
            should_demote=True,
            consecutive_drift_days=7,
            violations=["roi_below_threshold", "win_rate_dropping"],
        )
        with patch.object(self.shadow, "check_drift", return_value=drift):
            with patch("rl.lifecycle.drift_handler.get_redis") as mock_redis:
                redis_mock = AsyncMock()
                redis_mock.set_json = AsyncMock()
                mock_redis.return_value = redis_mock

                stats: dict = {"demotions": 0}
                result = await self.handler.check(model_version=1, stats=stats)
                assert result is True
                assert stats["demotions"] == 1
                assert "last_demotion" in stats

    @pytest.mark.asyncio
    async def test_drift_warning_no_demotion(self):
        """Should return False but log when drifting but not enough to demote."""
        drift = _make_drift_status(
            is_drifting=True,
            should_demote=False,
            consecutive_drift_days=2,
            violations=["roi_below_threshold"],
        )
        with patch.object(self.shadow, "check_drift", return_value=drift):
            with patch("rl.lifecycle.drift_handler.get_redis") as mock_redis:
                redis_mock = AsyncMock()
                redis_mock.set_json = AsyncMock()
                mock_redis.return_value = redis_mock

                stats: dict = {}
                result = await self.handler.check(model_version=1, stats=stats)
                assert result is False


# ================================================================
# TrainingHandler (basic contract tests)
# ================================================================


class TestTrainingHandlerContract:
    """Tests for the extracted training handler contract."""

    @pytest.mark.asyncio
    async def test_run_offline_returns_false_on_no_data(self):
        """Should return False when no training data available."""
        from rl.lifecycle.training_handler import TrainingHandler
        from rl.curriculum import CurriculumManager

        mock_loader = MagicMock()
        mock_loader.load_completed_matches = AsyncMock(return_value=[])

        handler = TrainingHandler(
            data_loader=mock_loader,
            curriculum=CurriculumManager(),
        )

        trainer_ref = [None]
        version_ref = [0]
        stats = {"training_runs": 0, "matches_trained_on": 0}

        result = await handler.run_offline(trainer_ref, version_ref, stats)
        assert result is False
        assert trainer_ref[0] is None

    @pytest.mark.asyncio
    async def test_run_evaluation_noop_without_trainer(self):
        """Should do nothing when trainer is None."""
        from rl.lifecycle.training_handler import TrainingHandler
        from rl.curriculum import CurriculumManager

        handler = TrainingHandler(
            data_loader=MagicMock(),
            curriculum=CurriculumManager(),
        )
        stats = {"eval_runs": 0}
        await handler.run_evaluation(trainer=None, stats=stats)
        assert stats["eval_runs"] == 0
