"""S20: graduation candidate selection and shadow-trader Sharpe."""
from __future__ import annotations

import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rl.orchestrator import Orchestrator, OrchestratorState
from virtual_trading.shadow_trader import ShadowTrader


def _orch(counts):
    o = object.__new__(Orchestrator)
    o._graduation_version_counts = AsyncMock(return_value=counts)
    o._graduation_handler = MagicMock()
    o._graduation_handler.check = AsyncMock(return_value=False)
    o._state = OrchestratorState.VIRTUAL_TRADING
    return o


def _zoo(keys):
    zoo = MagicMock()
    zoo.list_specialists.return_value = {k: {"tradeable": t} for k, t in keys.items()}
    return zoo


async def test_picks_version_with_most_bets():
    o = _orch({"ipl@aaa": 10, "bbl_men@bbb": 40})
    with patch("rl.model_zoo.get_model_zoo", return_value=_zoo({"ipl": True, "bbl_men": True})):
        assert await o._graduation_candidate_version() == "bbl_men@bbb"


async def test_untradeable_ignored_and_none_when_empty():
    o = _orch({"ipl@aaa": 10, "bbl_men@bbb": 40})
    with patch("rl.model_zoo.get_model_zoo", return_value=_zoo({"ipl": True, "bbl_men": False})):
        assert await o._graduation_candidate_version() == "ipl@aaa"
    o = _orch({})
    with patch("rl.model_zoo.get_model_zoo", return_value=_zoo({"ipl": True})):
        assert await o._graduation_candidate_version() is None


async def test_no_candidate_skips_check():
    from datetime import datetime, timezone

    o = _orch({})
    o._last_nightly_date = None
    o._transition_to = AsyncMock()
    o._training_handler = MagicMock()
    o._training_handler.run_nightly_retrain = AsyncMock()
    o._training_handler.run_evaluation = AsyncMock()
    o._trainer = None
    o._stats = None
    o._model_version = "x"
    with patch("rl.model_zoo.get_model_zoo", return_value=_zoo({"ipl": True})):
        await o._check_nightly_schedule(datetime(2026, 1, 6, 23, 59, tzinfo=timezone.utc))
    o._graduation_handler.check.assert_not_called()
    o._transition_to.assert_not_called()


async def test_candidate_passed_to_check():
    from datetime import datetime, timezone

    o = _orch({"ipl@aaa": 3})
    o._last_nightly_date = None
    o._transition_to = AsyncMock()
    o._training_handler = MagicMock()
    o._training_handler.run_nightly_retrain = AsyncMock()
    o._training_handler.run_evaluation = AsyncMock()
    o._trainer = None
    o._stats = None
    o._model_version = "x"
    with patch("rl.model_zoo.get_model_zoo", return_value=_zoo({"ipl": True})):
        await o._check_nightly_schedule(datetime(2026, 1, 6, 23, 59, tzinfo=timezone.utc))
    o._graduation_handler.check.assert_awaited_once_with(agent_version="ipl@aaa")


def test_shadow_sharpe_zero_days_lower_it():
    t = object.__new__(ShadowTrader)
    t._daily_pnl = {f"2026-01-0{d}": p for d, p in zip(range(1, 7), [5, -2, 4, -1, 3, 2])}
    dense = t._compute_sharpe()
    t._daily_pnl = {"2026-01-01": 5, "2026-01-03": 4, "2026-01-05": 3, "2026-01-08": 2, "2026-01-10": 6}
    sparse_days = t._compute_sharpe()
    assert dense > 0
    # same bet days with gaps filled by zeros must differ from a dense-only series
    t._daily_pnl = {"2026-01-01": 5, "2026-01-02": 4, "2026-01-03": 3, "2026-01-04": 2, "2026-01-05": 6}
    no_gaps = t._compute_sharpe()
    assert sparse_days < no_gaps


def test_no_sqrt_252_in_source():
    root = pathlib.Path(__file__).resolve().parents[2]
    for p in root.rglob("*.py"):
        if "tests" in p.parts or ".venv" in p.parts or "venv" in p.parts or "site-packages" in p.parts:
            continue
        assert "sqrt(252)" not in p.read_text(encoding="utf-8", errors="ignore"), p
