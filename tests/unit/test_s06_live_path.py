"""S06: live execution path (embedded context, delayed fills, model zoo caching/reload)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import rl.model_zoo as zoo_mod
import virtual_trading.live_trading_loop as loop_mod
from rl.model_zoo import ModelZoo
from shared.schemas import BettingAction, MatchContext, OddsEvent
from virtual_trading.live_trading_loop import LiveVirtualTradingLoop


def _event(back_home: float = 2.0, lay_home: float = 2.02, is_live: bool = True) -> OddsEvent:
    return OddsEvent(
        match_id="m1",
        timestamp=datetime.now(timezone.utc),
        team_home="India",
        team_away="Australia",
        competition="IPL",
        back_home=back_home,
        lay_home=lay_home,
        back_away=3.0,
        lay_away=3.05,
        is_live=is_live,
        source="lotusbook",
        scrape_method="dom",
    )


def _make_loop(monkeypatch) -> LiveVirtualTradingLoop:
    loop = LiveVirtualTradingLoop(agent=None)
    loop._engine.rejection_rate = 0.0
    loop._engine.slippage_pct = 0.0
    loop._running = True
    monkeypatch.setattr(loop_mod, "BET_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(loop_mod, "BET_DELAY_MAX_SECONDS", 0)
    monkeypatch.setattr(loop_mod, "persist_bet", AsyncMock())
    return loop


# ----------------------------------------------------------- embedded context
async def test_embedded_context_used_not_cache(monkeypatch):
    monkeypatch.setenv("PHOENIX_STRATEGY", "ppo")  # exercises the PPO decision path (value is the S40 default)
    loop = _make_loop(monkeypatch)
    cache = AsyncMock(side_effect=AssertionError("cache must not be read"))
    monkeypatch.setattr(loop_mod, "get_match_context", cache)
    monkeypatch.setattr(loop, "_get_watched_match_id", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._feature_store, "store", AsyncMock())
    monkeypatch.setattr(zoo_mod, "get_model_zoo", MagicMock(side_effect=RuntimeError("no zoo")))
    seen = {}

    def fake_compute(**kwargs):
        seen["context"] = kwargs["context"]
        return np.zeros(4, dtype=np.float32)

    monkeypatch.setattr(loop._feature_pipeline, "compute", fake_compute)
    agent = MagicMock()
    agent.predict.return_value = (BettingAction.HOLD, None)
    loop._agent = agent
    log = MagicMock()
    monkeypatch.setattr(loop_mod, "logger", log)

    ctx = MatchContext(match_id="m1", timestamp=datetime.now(timezone.utc), is_live=True, score=123)
    data = _event().model_dump(mode="json")
    data["context"] = ctx.model_dump(mode="json")
    await loop._handle_match_event(data)

    assert seen["context"].score == 123
    cache.assert_not_called()
    # Latency line carries all four timestamps
    calls = [c for c in log.info.call_args_list if c.args and c.args[0] == "tick_latency"]
    assert calls
    for key in ("t_scraped", "t_received", "t_obs", "t_action"):
        assert key in calls[0].kwargs


# ------------------------------------------------------------- delayed fills
async def _proposal(loop, event):
    proposal = loop._engine.propose(int(BettingAction.BACK_HOME_SM), event, confidence=1.0)
    assert proposal is not None
    assert proposal.reservation_id in loop._portfolio.reservations
    return proposal


async def test_price_moves_three_ticks_no_bet_and_reservation_released(monkeypatch):
    loop = _make_loop(monkeypatch)
    ev = _event(back_home=2.0)
    proposal = await _proposal(loop, ev)
    loop._latest_events["m1"] = _event(back_home=1.97)  # 3 ticks worse for a back bet
    await loop._delayed_place_bet(proposal, 2.0, "v")
    assert not loop._open_bets
    assert not loop._portfolio.reservations
    loop_mod.persist_bet.assert_not_called()


async def test_price_moves_one_tick_commits_at_new_price(monkeypatch):
    loop = _make_loop(monkeypatch)
    ev = _event(back_home=2.0)
    proposal = await _proposal(loop, ev)
    loop._latest_events["m1"] = _event(back_home=1.99)
    await loop._delayed_place_bet(proposal, 2.0, "model@abc")
    (bet, _), = loop._open_bets["m1"]
    assert bet.odds == pytest.approx(1.99)
    assert bet.agent_version == "model@abc"
    assert not loop._portfolio.reservations
    loop_mod.persist_bet.assert_awaited_once()


async def test_match_no_longer_live_releases_reservation(monkeypatch):
    loop = _make_loop(monkeypatch)
    proposal = await _proposal(loop, _event())
    loop._latest_events["m1"] = _event(is_live=False)
    await loop._delayed_place_bet(proposal, 2.0, "v")
    assert not loop._open_bets
    assert not loop._portfolio.reservations


# ------------------------------------------------------------------ model zoo
def _zoo(tmp_path, monkeypatch, load):
    monkeypatch.setattr(zoo_mod, "DEFAULT_MODEL_PATH", tmp_path / "nope.zip")
    monkeypatch.setattr(zoo_mod.PhoenixAgent, "load", staticmethod(load))
    # These tests cover load/reload mechanics, not the S17 provenance gate
    monkeypatch.setenv("PHOENIX_ALLOW_UNVERIFIED_MODELS", "1")
    (tmp_path / "metadata.json").write_text("{}")  # isolate from repo metadata
    return ModelZoo(models_dir=tmp_path)


def test_corrupt_model_not_retried_within_ttl(tmp_path, monkeypatch):
    attempts = []

    def bad_load(path, env=None):
        attempts.append(str(path))
        raise ValueError("corrupt")

    zoo = _zoo(tmp_path, monkeypatch, bad_load)
    (tmp_path / "ipl.zip").write_bytes(b"garbage")
    assert zoo.get_agent("ipl") is None
    assert zoo.get_agent("ipl") is None
    assert len(attempts) == 1
    # After the TTL the load is attempted again
    for k in zoo._load_failures:
        zoo._load_failures[k] -= zoo_mod.LOAD_FAILURE_TTL_SECONDS + 1
    zoo.get_agent("ipl")
    assert len(attempts) == 2


async def test_reload_if_changed_updates_version(tmp_path, monkeypatch):
    zoo = _zoo(tmp_path, monkeypatch, lambda path, env=None: object())
    f = tmp_path / "ipl.zip"
    f.write_bytes(b"weights-A")
    old_agent = zoo.get_agent("ipl")
    v1 = zoo.get_agent_version("ipl")
    assert v1.startswith("ipl@")

    assert await zoo.reload_if_changed() == []  # unchanged
    f.write_bytes(b"weights-B")
    later = time.time() + 10
    os.utime(f, (later, later))
    swapped = await zoo.reload_if_changed()
    assert "ipl" in swapped
    assert zoo.get_agent_version("ipl") != v1
    assert zoo.get_agent("ipl") is not old_agent


def test_allow_load_false_never_loads(tmp_path, monkeypatch):
    calls = []
    zoo = _zoo(tmp_path, monkeypatch, lambda path, env=None: calls.append(path) or object())
    (tmp_path / "ipl.zip").write_bytes(b"x")
    assert zoo.get_agent("ipl", allow_load=False) is None
    assert not calls


# ------------------------------------------------------- predict latency (task 5)
def test_predict_latency_median_under_5ms():
    """Median predict latency decides whether predict may stay inline in the event loop."""
    pytest.importorskip("torch")
    pytest.importorskip("stable_baselines3")
    from pathlib import Path

    from rl.agent import PhoenixAgent

    path = Path("models/best_model.zip")
    if path.exists():
        agent = PhoenixAgent.load(path, env=None)
        obs_dim = agent.model.observation_space.shape[0]
    else:  # same architecture on a dummy env
        import gymnasium as gym

        obs_dim = 64

        class _Env(gym.Env):
            observation_space = gym.spaces.Box(-1, 1, (obs_dim,), np.float32)
            action_space = gym.spaces.Discrete(10)

            def reset(self, *, seed=None, options=None):
                return np.zeros(obs_dim, np.float32), {}

            def step(self, a):
                return np.zeros(obs_dim, np.float32), 0.0, True, False, {}

        agent = PhoenixAgent(_Env(), n_steps=64, batch_size=64)
    obs = np.zeros(obs_dim, dtype=np.float32)
    agent.predict(obs, deterministic=True)  # warm-up
    times = []
    for _ in range(200):
        t = time.perf_counter()
        agent.predict(obs, deterministic=True)
        times.append((time.perf_counter() - t) * 1000)
    median = sorted(times)[len(times) // 2]
    print(f"median predict latency: {median:.3f} ms")
    assert median < 5.0, f"median {median:.2f} ms >= 5 ms: move predict to asyncio.to_thread"


# ------------------------------------------------------------ context_stale
@pytest.mark.parametrize("age_s,expect_stale", [(30, True), (2, False)])
async def test_context_stale_logged_only_when_old(monkeypatch, age_s, expect_stale):
    from datetime import timedelta

    loop = _make_loop(monkeypatch)
    ev = _event()
    ctx = MatchContext(match_id="m1", timestamp=ev.timestamp - timedelta(seconds=age_s), is_live=True)
    monkeypatch.setattr(loop_mod, "get_match_context", AsyncMock(return_value=ctx))
    log = MagicMock()
    monkeypatch.setattr(loop_mod, "logger", log)
    got = await loop._resolve_context(ev, None)
    assert got is ctx
    stale = [c for c in log.warning.call_args_list if c.args and c.args[0] == "context_stale"]
    assert bool(stale) is expect_stale


# ------------------------------------------- cancel before first step
async def test_cancel_before_first_step_releases_reservation(monkeypatch):
    import asyncio

    loop = _make_loop(monkeypatch)
    proposal = await _proposal(loop, _event())
    loop._schedule_placement(proposal, 2.0, "v")
    assert proposal.reservation_id in loop._portfolio.reservations
    await loop.stop()  # cancels before the task ever ran
    await asyncio.sleep(0)
    assert not loop._portfolio.reservations
