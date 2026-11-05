"""S04 invariants for the reward function."""

from __future__ import annotations

import numpy as np
import pytest

from rl.reward import RewardFunction
from tests.rl.test_environment import _make_episode_data
from rl.environment import CricketBettingEnv

B = 100_000.0


def _info(**kw):
    base = {"equity_delta": 0.0, "current_drawdown": 0.0, "worst_case_pnl": 0.0,
            "clv_delta": 0.0, "episode_done": False}
    base.update(kw)
    return base


def _plain() -> RewardFunction:
    return RewardFunction(initial_bankroll=B, reward_scale=1.0,
                          lambda_dd=0.0, lambda_tail=0.0, lambda_clv=0.0)


def test_sum_reward_equals_net_pnl_over_bankroll() -> None:
    rng = np.random.default_rng(0)
    for ep in range(20):
        env = CricketBettingEnv(data=[_make_episode_data(40)], exchange_commission=0.02,
                                episode_sampling="sequential")
        env.reward_fn = _plain()
        env.reset(seed=ep)
        total, done, info = 0.0, False, {}
        while not done:
            _, r, term, trunc, info = env.step(int(rng.integers(0, 9)))
            total += r
            done = term or trunc
        final = env._portfolio.current_balance - env.initial_bankroll
        assert total == pytest.approx(final / env.initial_bankroll, abs=1e-6)


def test_hold_only_is_zero() -> None:
    env = CricketBettingEnv(data=[_make_episode_data(30)], episode_sampling="sequential")
    env.reset(seed=1)
    total, done = 0.0, False
    while not done:
        _, r, term, trunc, _ = env.step(0)
        total += r
        done = term or trunc
    assert total == 0.0


def test_round_trip_negative_total() -> None:
    env = CricketBettingEnv(data=[_make_episode_data(30)], exchange_commission=0.02,
                            episode_sampling="sequential")
    env.reset(seed=2)
    total = 0.0
    for a in [1, 5] + [0] * 40:  # back home then lay home
        _, r, term, trunc, _ = env.step(a)
        total += r
        if term or trunc:
            break
    assert total < 0.0


def test_clv_terms_scale_with_stake() -> None:
    r = RewardFunction(initial_bankroll=B, reward_scale=1.0, lambda_clv=1.0)
    c1 = r.components(_info(clv_delta=0.001))["clv"]
    c2 = r.components(_info(clv_delta=0.002))["clv"]
    assert c2 / c1 == pytest.approx(2.0)


def test_adverse_moves_never_positive() -> None:
    r = RewardFunction(initial_bankroll=B, lambda_clv=1.0)
    comps = r.components(_info(clv_delta=-0.01, equity_delta=-100.0,
                               current_drawdown=0.2, worst_case_pnl=-30_000.0))
    assert all(v <= 0.0 for v in comps.values())


def test_drawdown_penalty_incremental() -> None:
    r = RewardFunction(initial_bankroll=B, reward_scale=1.0)
    assert r.compute(0, _info(current_drawdown=0.03)) == 0.0
    first = r.compute(0, _info(current_drawdown=0.15))
    assert first < 0.0
    assert r.compute(0, _info(current_drawdown=0.15)) == pytest.approx(0.0)
    assert r.compute(0, _info(current_drawdown=0.15)) == pytest.approx(0.0)
    assert r.compute(0, _info(current_drawdown=0.05)) > 0.0  # recovery


def test_components_sum_to_compute() -> None:
    r = RewardFunction(initial_bankroll=B, lambda_clv=0.5)
    info = _info(equity_delta=500.0, current_drawdown=0.2, worst_case_pnl=-25_000.0, clv_delta=0.003)
    expected = sum(r.components(info).values())
    assert r.compute(1, info) == pytest.approx(expected)


def _clv_component_for_stake(stake: float) -> tuple[float, float]:
    from datetime import datetime, timedelta, timezone

    from shared.schemas import BettingAction

    base = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    ticks = []
    for i in range(10):
        shortened = i >= 1  # market shortens after the entry tick
        ticks.append({
            "match_id": "m", "timestamp": base + timedelta(seconds=3 * i),
            "team_home": "India", "team_away": "Australia",
            "back_home": 1.80 if shortened else 2.0, "lay_home": 1.82 if shortened else 2.02,
            "back_away": 2.2, "lay_away": 2.25, "is_live": True,
        })
    ep = {"_meta": {"winner": "India", "team_home": "India", "team_away": "Australia"},
          "ticks": ticks}
    env = CricketBettingEnv(data=[ep], settlement_mode="real", slippage_ticks=0,
                            episode_sampling="sequential")
    env.reward_fn = RewardFunction(initial_bankroll=env.initial_bankroll, reward_scale=1.0,
                                   lambda_clv=1.0)
    env.reset(seed=0)
    env._apply_fill(BettingAction.BACK_HOME_LG, stake, 2.0, env._current_episode_data[0])
    _, _, _, _, info = env.step(0)
    si = info["step_info"]
    return si["clv_delta"], env.reward_fn.components(si)["clv"]


def test_clv_component_scales_with_real_stake_in_env() -> None:
    d1, c1 = _clv_component_for_stake(100.0)
    d2, c2 = _clv_component_for_stake(200.0)
    assert d1 > 0 and c1 > 0
    assert c2 / c1 == pytest.approx(2.0, abs=1e-6)
