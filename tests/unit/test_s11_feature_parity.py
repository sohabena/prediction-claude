"""S11: feature pipeline train/serve parity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from features.pipeline import FeaturePipeline
from rl.environment import CricketBettingEnv
from shared.constants import OBSERVATION_SIZE

BASE = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
# Portfolio (25-29) and position (30-33) groups are masked out of parity checks.
PORTFOLIO_POSITION = list(range(25, 34))
# History-based groups: momentum, market, volume, bookmaker, match momentum.
HISTORY_IDX = list(range(7, 18)) + list(range(34, 42)) + [48, 49]


def _fixture(n: int = 30, step: float = 60.0, mid: str = "m1", seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    data = []
    bh = 1.85
    for i in range(n):
        bh = max(1.2, bh + rng.normal(0, 0.01 * (step / 60.0) ** 0.5))
        data.append({
            "match_id": mid,
            "timestamp": BASE + timedelta(seconds=i * step),
            "team_home": "India", "team_away": "Australia",
            "competition": "Test Cup",
            "back_home": bh, "lay_home": bh + 0.05,
            "back_away": 2.10, "lay_away": 2.15,
            "back_draw": 15.0, "lay_draw": 18.0,
            "is_live": True,
            "overs": float(i // 6), "score": i * 5, "wickets": i // 10,
            "run_rate": 7.0, "req_run_rate": 0.0, "innings": 1,
            "balls_remaining": 120 - i,
        })
    return data


def test_no_gap_detected_at_training_cadence() -> None:
    p = FeaturePipeline()
    env = CricketBettingEnv(data=[_fixture()])
    for t in _fixture(step=90.0):
        p.compute(t["match_id"], env._tick_to_event(t), env._tick_to_context(t))
        assert p._gap_detected["m1"] is False


def test_output_independent_of_other_matches() -> None:
    env = CricketBettingEnv(data=[_fixture()])
    a, b = _fixture(mid="A"), _fixture(mid="B", seed=3)

    def run(p: FeaturePipeline, tick: dict) -> np.ndarray:
        return p.compute(tick["match_id"], env._tick_to_event(tick), env._tick_to_context(tick))

    p1 = FeaturePipeline()
    for t in a[:10]:
        run(p1, t)
    ref = run(p1, a[10])

    p2 = FeaturePipeline()
    for t in a[:10]:
        run(p2, t)
    for t in b:
        run(p2, t)  # other-match traffic in between
    # Re-feed the same A tick in a pipeline whose normaliser state was disturbed.
    p3 = FeaturePipeline()
    for t in b:
        run(p3, t)
    for t in a[:10]:
        run(p3, t)
    assert np.allclose(run(p3, a[10]), ref, atol=1e-6)


def test_env_and_live_paths_match() -> None:
    data = _fixture(30, step=3.0)
    env = CricketBettingEnv(data=[data])
    env.reset()
    live = FeaturePipeline()
    env.feature_pipeline = FeaturePipeline()  # drop the reset() observation from history
    mask = np.ones(OBSERVATION_SIZE, dtype=bool)
    mask[PORTFOLIO_POSITION] = False
    # Rebuild env observations through its own pipeline, tick by tick.
    for i, t in enumerate(data):
        env._current_episode_data = data
        env._step_idx = i
        env_obs = env._get_observation()
        live_obs = live.compute(t["match_id"], env._tick_to_event(t), env._tick_to_context(t),
                                env._portfolio, env._build_position_state(t))
        assert np.allclose(env_obs[mask], live_obs[mask], atol=1e-6)


def test_cadence_skew_bounded() -> None:
    env = CricketBettingEnv(data=[_fixture()])

    def run(step: float) -> np.ndarray:
        p = FeaturePipeline()
        out = []
        for t in _fixture(30, step=step):
            out.append(p.compute(t["match_id"], env._tick_to_event(t), env._tick_to_context(t)))
        return np.array(out)

    slow, fast = run(60.0), run(3.0)
    names = FeaturePipeline.feature_names()
    diff = np.abs(slow - fast).mean(axis=0)
    bad = [(names[i], round(float(diff[i]), 3)) for i in HISTORY_IDX if diff[i] > 1.0]
    # Timing-free history features must stay within a loose bound of 1.0
    # (raw units); features that cannot are reported in the failure message.
    assert not bad, f"history features exceeding skew bound: {bad}"


def test_feature_names() -> None:
    names = FeaturePipeline.feature_names()
    assert len(names) == OBSERVATION_SIZE == len(set(names))
