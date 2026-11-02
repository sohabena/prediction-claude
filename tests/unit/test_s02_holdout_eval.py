"""S02: honest, model-driven holdout evaluation."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtesting.baselines import HeuristicPolicy, HoldPolicy, RandomPolicy, estimate_in_play_prob  # noqa: E402
from scripts.train_format_specialists import (  # noqa: E402
    SpecialistEvaluationMetrics,
    _bootstrap_mean_ci,
    estimate_causal_in_play_prob,
    evaluate_specialist_holdout,
)
from shared.schemas import BettingAction  # noqa: E402


def _episode(match_id: str = "m1", n: int = 3, winner: str = "India") -> List[dict]:
    base = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    ticks = []
    for i in range(n):
        ticks.append({
            "match_id": match_id,
            "timestamp": base + timedelta(seconds=3 * i),
            "team_home": "India",
            "team_away": "Australia",
            "competition": "Test Cup",
            "back_home": 2.00,
            "lay_home": 2.04,
            "back_away": 2.10,
            "lay_away": 2.14,
            "back_draw": 15.0,
            "lay_draw": 18.0,
            "is_live": True,
        })
    ticks[0]["_meta"] = {"winner": winner, "loser": "Australia", "winner_is_home": winner == "India"}
    return ticks


class _ConstModel:
    """Mock model returning a constant action and recording every call."""

    def __init__(self, action: int) -> None:
        self.action = action
        self.calls = 0

    def predict(self, obs: Any, deterministic: bool = True):
        assert deterministic is True
        self.calls += 1
        return np.array([self.action]), None


class TestModelDriven:
    def test_model_predict_called_every_step_and_hold_means_no_bets(self) -> None:
        model = _ConstModel(int(BettingAction.HOLD))
        metrics, bets = evaluate_specialist_holdout(
            model, "t20i_men", "T20I Men", [_episode(n=5)], include_baselines=False
        )
        assert model.calls == 5
        assert metrics.total_bets == 0
        assert metrics.total_pnl == 0.0
        assert bets == []
        assert metrics.policy == "model"

    def test_always_back_home_pnl_matches_hand_calculation(self) -> None:
        model = _ConstModel(int(BettingAction.BACK_HOME_SM))
        metrics, bets = evaluate_specialist_holdout(
            model, "t20i_men", "T20I Men", [_episode(n=3, winner="India")],
            commission=0.02, include_baselines=False,
        )
        assert metrics.total_bets > 0
        assert len(bets) == metrics.total_bets
        gross = sum(b["stake"] * (b["odds"] - 1.0) for b in bets)
        assert gross > 0
        assert metrics.total_pnl == round(gross * 0.98, 2)
        assert metrics.commission_paid == round(gross * 0.02, 2)
        assert metrics.total_stake == round(sum(b["stake"] for b in bets), 2)

    def test_lose_case_has_no_commission(self) -> None:
        model = _ConstModel(int(BettingAction.BACK_HOME_SM))
        metrics, bets = evaluate_specialist_holdout(
            model, "t20i_men", "T20I Men", [_episode(n=3, winner="Australia")],
            include_baselines=False,
        )
        assert metrics.total_pnl == round(-sum(b["stake"] for b in bets), 2)
        assert metrics.commission_paid == 0.0

    def test_records_have_no_fabricated_constants(self) -> None:
        model = _ConstModel(int(BettingAction.BACK_HOME_SM))
        _, bets = evaluate_specialist_holdout(
            model, "ipl", "IPL", [_episode(n=4)], include_baselines=False
        )
        assert bets
        for b in bets:
            assert b["confidence"] is None  # mock model cannot report a distribution
            assert b["agent_version"].startswith("holdout_eval:ipl:")
            assert b["closing_odds"] == 2.00  # same-side price on the last tick
            assert abs(b["clv"] - (1 / b["odds"] - 1 / 2.00)) < 1e-9
        assert len({b["placed_at"] for b in bets}) == len(bets)  # unique placed_at

    def test_baselines_bootstrap_and_metadata(self) -> None:
        model = _ConstModel(int(BettingAction.BACK_HOME_SM))
        eps = [_episode(f"m{i}", winner="India" if i % 2 else "Australia") for i in range(6)]
        metrics, _ = evaluate_specialist_holdout(model, "t20i_men", "T20I Men", eps)
        assert set(metrics.baselines) == {"random", "hold"}
        assert metrics.baselines["hold"]["total_bets"] == 0
        assert metrics.edge_vs_random is not None
        assert metrics.pnl_ci95_low <= metrics.mean_match_pnl <= metrics.pnl_ci95_high
        assert metrics.n_matches == 6 and metrics.matches_tested == 6
        assert metrics.holdout_match_ids_hash and metrics.evaluated_at and metrics.code_version
        assert isinstance(metrics, SpecialistEvaluationMetrics)

    def test_bootstrap_is_seeded(self) -> None:
        vals = [10.0, -5.0, 3.0, 8.0, -2.0]
        assert _bootstrap_mean_ci(vals, 1000, 0) == _bootstrap_mean_ci(vals, 1000, 0)

    def test_fast_on_five_episodes(self) -> None:
        eps = [_episode(f"m{i}", n=40) for i in range(5)]
        t0 = time.time()
        evaluate_specialist_holdout(_ConstModel(0), "t20i_men", "T20I Men", eps)
        assert time.time() - t0 < 10.0

    def test_baseline_policy_selection(self) -> None:
        metrics, _ = evaluate_specialist_holdout(
            _ConstModel(0), "t20i_men", "T20I Men", [_episode(n=6)], policy="hold", include_baselines=False
        )
        assert metrics.policy == "hold" and metrics.total_bets == 0


class TestBaselines:
    def test_hold_and_random(self) -> None:
        assert HoldPolicy().predict(None, {}) == 0
        r1, r2 = RandomPolicy(seed=3), RandomPolicy(seed=3)
        a1 = [r1.predict(None, {}) for _ in range(20)]
        assert a1 == [r2.predict(None, {}) for _ in range(20)]
        assert all(0 <= a < 9 for a in a1)

    def test_heuristic_target_uses_overs_not_balls(self) -> None:
        cfg = {"max_overs": 20.0, "par_1": 160.0, "wicket_decay_power": 0.65, "div": 18.0}
        tick = {
            "team_home": "A", "team_away": "B", "batting_team": "A", "innings": 2,
            "overs": 10.0, "score": 80, "wickets": 3, "req_run_rate": 8.0,
        }
        # req 8 rpo over 10 remaining overs => 80 more runs => target 160 (not 80 + 8*10/6).
        with_target = dict(tick, target=160)
        assert abs(estimate_in_play_prob(tick, cfg) - estimate_in_play_prob(with_target, cfg)) < 1e-9
        # Wrapper agrees with the shared implementation.
        assert 0.02 <= estimate_causal_in_play_prob(tick, "t20i_men") <= 0.98

    def test_heuristic_never_reads_winner(self) -> None:
        pol = HeuristicPolicy({"max_overs": 20.0, "par_1": 160.0}, {}, n_ticks=20, max_bets=5)
        tick = {"team_home": "A", "team_away": "B", "back_home": 2.0, "lay_home": 2.04,
                "back_away": 2.0, "lay_away": 2.04, "_meta": {"winner": "A"}}
        assert 0 <= pol.predict(None, tick) < 9
