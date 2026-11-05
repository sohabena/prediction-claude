"""S14: calibrated fair-value model and value policy."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from shared.schemas import BettingAction as A
from strategy.fair_value import FairValueModel, family_of
from strategy.value_policy import ValuePolicy


def _episode(i: int, home_wins: bool, n: int = 30) -> List[Dict[str, Any]]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
    ticks = []
    for k in range(n):
        frac = k / n
        # chasing side is home in innings 2; home wins iff it is ahead of the rate
        ahead = 1.0 if home_wins else -1.0
        ticks.append({
            "match_id": f"m{i}", "time": (base + timedelta(seconds=3 * k)).isoformat(),
            "timestamp": base + timedelta(seconds=3 * k),
            "team_home": "H", "team_away": "A", "match_format": "T20", "competition": "x",
            "batting_team": "H", "innings": 2, "wickets": int(5 - ahead * 2 * frac),
            "overs": 20 * frac, "balls_remaining": int(120 * (1 - frac)),
            "score": 100 + int(60 * frac), "run_rate": 8.0 + ahead * 2, "req_run_rate": 8.0 - ahead * 2 * frac,
            "back_home": 1.9, "lay_home": 1.95, "back_away": 2.0, "lay_away": 2.05,
            "is_live": True,
            "_meta": {"winner": "H" if home_wins else "A", "winner_is_home": home_wins},
        })
    return ticks


def _corpus(n: int = 60) -> List[List[Dict[str, Any]]]:
    rng = np.random.default_rng(1)
    return [_episode(i, bool(rng.integers(0, 2))) for i in range(n)]


class _Fixed:
    def __init__(self, p: float) -> None:
        self.p = p

    def predict_home(self, tick: Dict[str, Any]) -> float:
        return self.p


class TestFairValueModel:
    def test_family_mapping(self) -> None:
        assert family_of("T20") == "t20"
        assert family_of("ODI") == "odi"
        assert family_of("Test") == "multiday"

    def test_fit_predict_in_unit_interval(self) -> None:
        eps = _corpus()
        m = FairValueModel("t20").fit(eps)
        p = m.predict_home(eps[0][10])
        assert 0.0 < p < 1.0

    def test_leak_free_predictions(self) -> None:
        eps = _corpus()
        m = FairValueModel("t20").fit(eps)
        tick = eps[3][12]
        stripped = {k: v for k, v in tick.items() if k not in ("_meta", "winner", "loser", "winner_is_home")}
        assert m.predict_home(tick) == m.predict_home(stripped)
        flipped = copy.deepcopy(tick)
        flipped["_meta"]["winner_is_home"] = not tick["_meta"]["winner_is_home"]
        assert m.predict_home(flipped) == m.predict_home(tick)

    def test_train_ids_recorded_and_save_load(self, tmp_path: Path) -> None:
        eps = _corpus(40)
        m = FairValueModel("t20").fit(eps[:30])
        holdout_ids = {e[0]["match_id"] for e in eps[30:]}
        assert not holdout_ids & set(m.train_match_ids)
        m.save(tmp_path / "t20.joblib")
        m2 = FairValueModel.load(tmp_path / "t20.joblib")
        assert m2.predict_home(eps[0][5]) == pytest.approx(m.predict_home(eps[0][5]))


class TestValuePolicy:
    @staticmethod
    def _tick(back: float, lay: float) -> Dict[str, Any]:
        return {"back_home": back, "lay_home": lay, "back_away": None, "lay_away": None}

    def test_backs_when_price_beats_fair(self) -> None:
        pol = ValuePolicy(_Fixed(0.6))  # type: ignore[arg-type]
        a = pol.predict(None, self._tick(2.0, 2.02))
        assert a in (A.BACK_HOME_SM, A.BACK_HOME_LG)

    def test_holds_when_no_edge(self) -> None:
        pol = ValuePolicy(_Fixed(0.6))  # type: ignore[arg-type]
        assert pol.predict(None, self._tick(1.6, 1.65)) == A.HOLD

    def test_lays_when_price_too_short(self) -> None:
        pol = ValuePolicy(_Fixed(0.3))  # type: ignore[arg-type]
        a = pol.predict(None, self._tick(2.0, 2.05))
        assert a in (A.LAY_HOME_SM, A.LAY_HOME_LG)

    def test_large_size_for_big_edge_and_green_up(self) -> None:
        pol = ValuePolicy(_Fixed(0.6))  # type: ignore[arg-type]
        assert pol.predict(None, self._tick(2.0, 2.02)) == A.BACK_HOME_LG
        # price shortens a lot: lay to lock profit
        assert pol.predict(None, self._tick(1.6, 1.62)) == A.LAY_HOME_SM
        assert pol.win_prob(self._tick(2.0, 2.02), A.BACK_AWAY_SM) == pytest.approx(0.4)

    def test_max_bets_cap(self) -> None:
        pol = ValuePolicy(_Fixed(0.6), max_bets=1, green_up_at=99.0)  # type: ignore[arg-type]
        assert pol.predict(None, self._tick(2.0, 2.02)) != A.HOLD
        assert pol.predict(None, self._tick(2.0, 2.02)) == A.HOLD


class TestEvaluatorDispatch:
    def test_value_policy_runs_on_fixture(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.train_format_specialists import evaluate_specialist_holdout

        eps = _corpus(40)
        monkeypatch.chdir(tmp_path)
        FairValueModel("t20").fit(eps[:35]).save(tmp_path / "models" / "fair_value" / "t20.joblib")
        metrics, _rows = evaluate_specialist_holdout(
            None, "t20i_men", "T20", eps[35:], policy="value", include_baselines=True  # type: ignore[arg-type]
        )
        assert metrics.policy == "value"
        assert metrics.n_matches == 5
        assert "hold" in metrics.baselines

    def test_value_policy_missing_model_is_clear_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.train_format_specialists import evaluate_specialist_holdout

        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="train_fair_value"):
            evaluate_specialist_holdout(
                None, "t20i_men", "T20", _corpus(5), policy="value"  # type: ignore[arg-type]
            )
