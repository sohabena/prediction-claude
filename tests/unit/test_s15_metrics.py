"""S15: canonical CLV / Sharpe / drawdown and graduation correctness."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from rl.graduation import GraduationEvaluator
from rl.lifecycle import graduation_handler as gh
from shared.constants import GRADUATION_CRITERIA
from shared.metrics import (
    clv_prob, daily_series, max_drawdown, per_match_pnl, profit_factor, sharpe,
)
from virtual_trading.settlement import SettlementEngine


def test_clv_side_aware() -> None:
    assert clv_prob("BACK", 2.0, 1.8) > 0
    assert clv_prob("LAY", 2.0, 1.8) < 0
    assert clv_prob("LAY", 1.8, 2.0) > 0
    assert clv_prob("BACK", 2.0, 2.0) == 0.0
    assert clv_prob("BACK", 1.0, 2.0) == 0.0
    assert SettlementEngine.compute_clv(2.0, 1.8, side="LAY") < 0
    assert SettlementEngine.compute_clv(2.0, 1.8) > 0


def test_zero_days_lower_sharpe() -> None:
    profit = [10.0, 12.0, 8.0, 11.0, 9.0]
    assert sharpe(profit + [0.0] * 10) < sharpe(profit)
    assert sharpe([1.0]) == 0.0


def test_daily_series_fills_zero_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bets = [(start, 5.0), (start + timedelta(days=3), -2.0), (start + timedelta(days=3), 1.0)]
    assert daily_series(bets, start, start + timedelta(days=4)) == [5.0, 0.0, 0.0, -1.0, 0.0]


def test_drawdown_pf_and_match_pnl() -> None:
    assert max_drawdown([100, 120, 90, 130]) == (30.0, pytest.approx(0.25))
    assert profit_factor([10, -5, -5]) == pytest.approx(1.0)
    assert math.isinf(profit_factor([1.0]))
    assert per_match_pnl([{"match_id": "a", "pnl": 1}, {"match_id": "a", "pnl": 2}]) == {"a": 3.0}


def test_criteria_have_no_win_rate() -> None:
    assert "win_rate" not in GRADUATION_CRITERIA
    assert {"avg_clv", "clv_ci_lower", "min_matches", "roi_on_turnover"} <= set(GRADUATION_CRITERIA)


def test_evaluator_ignores_win_rate_and_requires_new_metrics(tmp_path) -> None:
    ev = GraduationEvaluator(state_path=tmp_path / "g.json")
    good = dict(roi=0.05, sharpe_ratio=0.5, max_drawdown=0.05, profitable_days=11,
                total_bets=120, avg_clv=0.01)
    unmeasured = ev.evaluate(win_rate=0.0, **good)
    assert not any(c.met for c in unmeasured.criteria if c.name in ("clv_ci_lower", "min_matches"))
    ok = ev.evaluate(win_rate=0.0, clv_ci_lower=0.002, n_matches=40, **good)
    assert all(c.met for c in ok.criteria)
    few = ev.evaluate(clv_ci_lower=0.002, n_matches=10, **good)
    assert not next(c for c in few.criteria if c.name == "min_matches").met


def test_graduation_sql_filters_version_and_holdout() -> None:
    sql = gh.GRADUATION_BETS_SQL
    assert "agent_version = :agent_version" in sql
    assert "NOT LIKE 'holdout_eval:%'" in sql


def test_handler_passes_version_param(monkeypatch) -> None:
    captured: dict = {}

    class FakeResult:
        def fetchall(self):
            return []

    class FakeSession:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return FakeResult()

    class FakeCtx:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(gh, "get_session", lambda: FakeCtx())
    handler = gh.GraduationHandler(GraduationEvaluator(state_path=None))
    assert asyncio.run(handler.check(agent_version="ipl@abc123")) is False
    assert captured["params"]["agent_version"] == "ipl@abc123"
    assert "holdout_eval" in captured["sql"]


def test_compute_graduation_metrics_zero_days_and_matches() -> None:
    now = datetime(2026, 3, 30, 12, tzinfo=timezone.utc)
    rows = [
        (now - timedelta(days=2), "m1", 100.0, 10.0, 0.02),
        (now - timedelta(days=1), "m2", 100.0, -5.0, 0.01),
        (now, "m2", 100.0, 4.0, 0.03),
    ]
    m = gh.compute_graduation_metrics(rows, 10_000.0, now=now)
    assert m["n_matches"] == 2 and m["total_bets"] == 3
    assert m["roi"] == pytest.approx(9.0 / 300.0)
    assert m["avg_clv"] == pytest.approx(0.02)
    assert m["profitable_days"] == 2
    assert 0.0 < m["max_drawdown"] < 0.01
    assert m["clv_ci_lower"] == 0.0  # fewer than 5 CLV samples: not significant


def test_holdout_evaluator_uses_canonical_clv_sign() -> None:
    """S02's evaluator must agree with shared.metrics.clv_prob (BACK 2.0 -> 1.8 is positive)."""
    import inspect

    import scripts.train_format_specialists as tfs

    src = inspect.getsource(tfs.evaluate_specialist_holdout) + inspect.getsource(tfs._run_policy)
    assert "clv_prob(" in src
    assert tfs.clv_prob("BACK", 2.0, 1.8) > 0 > tfs.clv_prob("LAY", 2.0, 1.8)
