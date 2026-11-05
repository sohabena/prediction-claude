"""S03: environment execution realism (liability, spread, fills, commission)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rl.environment import CricketBettingEnv, betfair_tick_size, shift_price
from shared.schemas import BettingAction

BASE = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

CONTRACT_KEYS = [
    "action", "bet_placed", "bet_stake", "bet_liability", "bankroll", "equity",
    "equity_delta", "peak_equity", "current_drawdown", "total_liability",
    "worst_case_pnl", "best_case_pnl", "clv_delta", "profit_loss", "bet_settled",
    "commission_paid", "bets_this_episode", "episode_done", "win_rate", "roi",
]

META = {"winner": "India", "team_home": "India", "team_away": "Australia"}


def _ticks(n=30, bh=1.85, lh=1.90, ba=2.10, la=2.15, overrides=None):
    out = []
    for i in range(n):
        t = {
            "match_id": "m", "timestamp": BASE + timedelta(seconds=3 * i),
            "team_home": "India", "team_away": "Australia",
            "back_home": bh, "lay_home": lh, "back_away": ba, "lay_away": la,
            "is_live": True,
        }
        if overrides and i in overrides:
            t.update(overrides[i])
        out.append(t)
    return out


def _episode(n=30, winner="India", **kw):
    return {"_meta": dict(META, winner=winner), "ticks": _ticks(n, **kw)}


def _env(data=None, **kw):
    kw.setdefault("settlement_mode", "real")
    env = CricketBettingEnv(data=data or [_episode()], **kw)
    env.reset(seed=0)
    return env


def test_tick_ladder():
    assert betfair_tick_size(1.5) == 0.01
    assert betfair_tick_size(2.0) == 0.02
    assert betfair_tick_size(10.0) == 0.5
    assert shift_price(1.85, -1) == 1.84
    assert shift_price(2.0, -1) == 1.99
    assert shift_price(2.0, 1) == 2.02


def test_random_sampling_visits_many_episodes():
    data = [_episode(3) for _ in range(50)]
    env = CricketBettingEnv(data=data, episode_sampling="random")
    seen = set()
    for i in range(200):
        _, info = env.reset(seed=1 if i == 0 else None)
        seen.add(info["data_index"])
    assert len(seen) >= 40


def test_green_up_locks_equal_pnl_and_matches_analytic_equity():
    env = _env(exchange_commission=0.0, slippage_ticks=0)
    tick = env._current_episode_data[0]
    env._apply_fill(BettingAction.BACK_HOME_LG, 100.0, 2.0, tick)
    env._apply_fill(BettingAction.LAY_HOME_LG, 100.0 * 2.0 / 1.8, 1.8, tick)
    assert abs(env._worst_case() - env._best_case()) < 1.0
    assert env._worst_case() == pytest.approx(100 * (2.0 / 1.8 - 1), abs=0.01)
    # equity of a lone back 100@2.0 when the market lay price is 1.8
    env2 = _env(slippage_ticks=0, data=[_episode(lh=1.8, bh=1.78, ba=2.2, la=2.25)])
    env2._apply_fill(BettingAction.BACK_HOME_LG, 100.0, 2.0, env2._current_episode_data[0])
    expected = env2.initial_bankroll + 100 * (2.0 / 1.8 - 1)
    assert env2._compute_equity() == pytest.approx(expected, abs=0.01)


def test_lay_liability_capped():
    env = _env(
        data=[_episode(bh=9.8, lh=10.0, ba=1.1, la=1.12)],
        large_stake_pct=0.5, max_liability_pct_per_bet=0.05, slippage_ticks=0,
        max_total_liability_pct=1.0,
    )
    _, _, _, _, info = env.step(int(BettingAction.LAY_HOME_LG))
    si = info["step_info"]
    assert si["bet_placed"]
    assert si["bet_liability"] <= 0.05 * env.initial_bankroll + 1e-6
    assert si["bet_liability"] == pytest.approx(0.05 * env.initial_bankroll)


def test_spread_cost_makes_equity_delta_negative():
    env = _env()
    _, _, _, _, info = env.step(int(BettingAction.BACK_HOME_SM))
    si = info["step_info"]
    assert si["bet_placed"]
    assert si["equity_delta"] < 0


def test_fill_uses_next_tick_price_with_slippage():
    ticks = _ticks(10, overrides={1: {"back_home": 1.70, "lay_home": 1.75}})
    env = _env(data=[{"_meta": META, "ticks": ticks}], fill_delay_ticks=1, slippage_ticks=1)
    env.step(int(BettingAction.BACK_HOME_SM))
    assert len(env._open_bets) == 1
    assert env._open_bets[0].odds == pytest.approx(1.69)
    assert env._open_bets[0].placed_at == ticks[1]["timestamp"]


def test_no_fill_past_end():
    env = _env(data=[_episode(2)], fill_delay_ticks=1)
    env.step(0)
    _, _, done, _, info = env.step(int(BettingAction.BACK_HOME_SM))
    assert done and not info["step_info"]["bet_placed"]


def test_extra_bets_only_if_liability_reducing():
    env = _env(data=[_episode(60)], max_bets_per_episode=5, slippage_ticks=0)
    for _ in range(5):
        env.step(int(BettingAction.BACK_HOME_SM))
    assert env._bets_this_episode == 5
    _, _, _, _, info = env.step(int(BettingAction.BACK_HOME_SM))  # adds liability
    assert not info["step_info"]["bet_placed"]
    _, _, _, _, info = env.step(int(BettingAction.LAY_HOME_SM))  # reduces liability
    assert info["step_info"]["bet_placed"]
    assert env._bets_this_episode == 6
    _, _, _, _, info = env.step(int(BettingAction.LAY_HOME_SM))  # beyond tolerance cap (6)
    assert not info["step_info"]["bet_placed"]


def test_midepisode_drawdown_positive_when_price_moves_against():
    moved = {"back_home": 4.0, "lay_home": 4.1, "back_away": 1.3, "lay_away": 1.32}
    ticks = _ticks(20, overrides={i: moved for i in range(5, 20)})
    env = _env(data=[{"_meta": META, "ticks": ticks}], slippage_ticks=0)
    env.step(int(BettingAction.BACK_HOME_LG))
    dd = 0.0
    for _ in range(8):
        _, _, _, _, info = env.step(0)
        dd = info["step_info"]["current_drawdown"]
    assert dd > 0.005
    assert env._portfolio.current_balance == env.initial_bankroll  # still unrealised


def test_commission_on_net_positive():
    env = _env(data=[_episode(3, winner="India")], exchange_commission=0.02, slippage_ticks=0)
    env._apply_fill(BettingAction.BACK_HOME_LG, 1000.0, 2.0, env._current_episode_data[0])
    info = {}
    done = False
    while not done:
        _, _, done, _, info = env.step(0)
    si = info["step_info"]
    assert si["commission_paid"] == pytest.approx(20.0)
    assert si["profit_loss"] == pytest.approx(980.0)
    assert si["bet_settled"]
    assert env._portfolio.current_balance == pytest.approx(env.initial_bankroll + 980.0)


def test_void_result_returns_zero():
    ep = _episode(3)
    ep["_meta"]["result_type"] = "tie"
    env = _env(data=[ep])
    env._apply_fill(BettingAction.BACK_HOME_LG, 1000.0, 2.0, env._current_episode_data[0])
    done = False
    while not done:
        _, _, done, _, info = env.step(0)
    assert info["step_info"]["profit_loss"] == 0.0
    assert env._portfolio.current_balance == env.initial_bankroll


def test_step_info_contract_keys():
    env = _env()
    _, _, _, _, info = env.step(int(BettingAction.BACK_HOME_SM))
    for key in CONTRACT_KEYS:
        assert key in info["step_info"], key


def test_action_masks_reflect_missing_price():
    ticks = _ticks(10, overrides={1: {"lay_home": None}})
    env = _env(data=[{"_meta": META, "ticks": ticks}], fill_delay_ticks=1)
    mask = env.action_masks()
    assert mask[0] and not mask[int(BettingAction.LAY_HOME_SM)]
    assert mask[int(BettingAction.BACK_HOME_SM)]
