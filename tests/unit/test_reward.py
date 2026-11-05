"""Unit tests for the equity-based reward function."""

from __future__ import annotations

import pytest

from rl.reward import RewardFunction

B = 100_000.0


def _info(**kw):
    base = {"equity_delta": 0.0, "current_drawdown": 0.0, "worst_case_pnl": 0.0,
            "clv_delta": 0.0, "episode_done": False}
    base.update(kw)
    return base


def test_hold_is_zero() -> None:
    assert RewardFunction().compute(0, _info()) == 0.0


def test_equity_delta_scaled() -> None:
    r = RewardFunction(initial_bankroll=B, reward_scale=1.0)
    assert r.compute(1, _info(equity_delta=1000.0)) == pytest.approx(0.01)
    assert r.compute(1, _info(equity_delta=-1000.0)) == pytest.approx(-0.01)


def test_tail_penalty_only_above_threshold() -> None:
    r = RewardFunction(initial_bankroll=B, reward_scale=1.0)
    assert r.compute(1, _info(worst_case_pnl=-5_000.0)) == 0.0
    assert r.compute(1, _info(worst_case_pnl=-20_000.0)) < 0.0


def test_default_constructor_and_reset() -> None:
    r = RewardFunction()
    r.compute(0, _info(current_drawdown=0.2))
    r.reset()
    assert r._prev_dd_term == 0.0
