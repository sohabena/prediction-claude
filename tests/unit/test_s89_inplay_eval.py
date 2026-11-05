"""S89 in-play model test: no look-ahead, soft-label log loss, match-clustered CIs, the gate rule and the gate file.

Synthetic states and a fake probability service only. No database, no network, no real clock, no model files.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from shared.metrics import cluster_bootstrap
from training import inplay_eval as ie
from training.evaluate import log_loss
from training.market import TRADING_DEVIG, market_prob_home

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- fixtures and fakes
def px(t: datetime, p: float, match_id: str = "lb_1") -> dict[str, Any]:
    """A listing price row whose band de-vig is exactly ``p`` for home (a two-runner book with a 1 point margin)."""
    return {"match_id": match_id, "time": t, "source": "listing",
            "back_home": 1.0 / (p + 0.01), "lay_home": 1.0 / (p - 0.01),
            "back_away": 1.0 / ((1.0 - p) + 0.01), "lay_away": 1.0 / ((1.0 - p) - 0.01)}


def ctx(t: datetime, *, live: bool = True, score: Any = 40, match_id: str = "lb_1", fmt: Any = "T20") -> dict[str, Any]:
    return {"match_id": match_id, "time": t, "is_live": live, "score": score, "wickets": 1, "overs": 6.0,
            "innings": 1, "balls_remaining": 84.0, "batting_team": "Alpha Kings", "bowling_team": "Beta Bolts",
            "run_rate": 6.0, "req_run_rate": None, "match_format": fmt, "status": "live"}


def registry(match_id: str = "lb_1", *, pipeline: str = "livescore-v2", simulated: bool = False) -> dict[str, Any]:
    return {"match_id": match_id, "team_home": "Alpha Kings", "team_away": "Beta Bolts",
            "competition": "Nepal Premier League", "match_start_time": T0, "is_simulated": simulated,
            "score_pipeline": pipeline, "toss_winner": None, "toss_decision": None}


def result(match_id: str = "lb_1", winner: str = "Alpha Kings", result_type: str = "win") -> dict[str, Any]:
    return {"match_id": match_id, "winner": winner, "loser": None, "result_type": result_type, "source": "test",
            "completed_at": None, "team_home": "Alpha Kings", "team_away": "Beta Bolts"}


class FakeService:
    """Records what it was given and returns a model probability from a function of the score history."""

    def __init__(self, p_model_fn=None, source: str = "inplay") -> None:
        self.calls: list[dict[str, Any]] = []
        self.p_model_fn = p_model_fn or (lambda history, as_of: 0.5 + 0.01 * len(history))
        self.source = source

    def estimate(self, match, book, state, *, as_of):
        self.calls.append({"as_of": as_of, "book": copy.deepcopy(book),
                           "history": [dict(r) for r in state] if state is not None else None})
        p_market = ie.market_home(book)
        p_model = self.p_model_fn(state, as_of) if self.source == "inplay" else 0.5
        return SimpleNamespace(p_model=p_model, p_market=p_market, source=self.source)


def rec(match_id: str, p_model: float, p_market: float, q: Any, y: int, family: str = "t20") -> dict[str, Any]:
    return {"match_id": match_id, "family": family, "t": "2026-11-03T07:00:00+00:00", "innings": 1,
            "p_model": p_model, "p_market": p_market, "q_later": q, "home_won": y}


def _market_of(p: float) -> float:
    return market_prob_home({"back_home": 1.0 / (p + 0.01), "lay_home": 1.0 / (p - 0.01),
                             "back_away": 1.0 / ((1.0 - p) + 0.01), "lay_away": 1.0 / ((1.0 - p) - 0.01)},
                            TRADING_DEVIG)


# --------------------------------------------------------------------------- no look-ahead
def _one_match_states() -> tuple[list, list]:
    contexts = [
        ctx(T0 - timedelta(minutes=2), live=False, score=None),  # a pre-match row: never a state
        ctx(T0),
        ctx(T0 + timedelta(minutes=1), score=55),
        ctx(T0 + timedelta(minutes=5), score=90),
    ]
    prices = [px(T0 - timedelta(minutes=1), 0.50), px(T0 + timedelta(seconds=30), 0.60),
              px(T0 + timedelta(minutes=10), 0.70)]
    return contexts, prices


def test_state_at_t_is_priced_only_with_prices_at_or_before_t():
    contexts, prices = _one_match_states()
    svc = FakeService()
    records, _ = ie.evaluate_match(svc, registry(), result(), contexts, prices)
    first = svc.calls[0]
    assert first["as_of"] == T0
    # The price rows at T0+30s and T0+10m exist but are after the state: the book at T0 is the T0-1m row.
    assert first["book"]["home"]["back"] == pytest.approx(1.0 / 0.51)
    assert records[0]["p_market"] == pytest.approx(0.50)


def test_model_never_sees_score_rows_after_t():
    contexts, prices = _one_match_states()
    svc = FakeService()
    ie.evaluate_match(svc, registry(), result(), contexts, prices)
    for call in svc.calls:
        assert all(ie._to_dt(r["time"]) <= call["as_of"] for r in call["history"])
    # At T0 the history is the pre-match row and the state itself, and nothing later.
    assert [r["time"] for r in svc.calls[0]["history"]] == [T0 - timedelta(minutes=2), T0]


def test_label_is_the_market_ten_minutes_later_and_is_not_an_input():
    contexts, prices = _one_match_states()
    svc = FakeService()
    records, _ = ie.evaluate_match(svc, registry(), result(), contexts, prices)
    assert records[0]["q_later"] == pytest.approx(_market_of(0.70))
    assert records[0]["q_later"] != pytest.approx(records[0]["p_market"])
    # The 10-minute price never appears in any book the model was given.
    for call in svc.calls:
        assert call["book"]["home"]["back"] != pytest.approx(1.0 / 0.71)


def test_history_until_drops_rows_after_t():
    rows = [ctx(T0 + timedelta(minutes=m)) for m in (0, 1, 2)]
    kept = ie.history_until(rows, T0 + timedelta(minutes=1))
    assert [r["time"] for r in kept] == [T0, T0 + timedelta(minutes=1)]


def test_a_state_without_a_later_price_is_kept_for_the_hard_target_only():
    # The state's own price is 14 min old (fresh enough at t), and it is older than 15 min by t + 10 min.
    contexts = [ctx(T0)]
    prices = [px(T0 - timedelta(minutes=14), 0.50)]
    records, skips = ie.evaluate_match(FakeService(), registry(), result(), contexts, prices)
    assert len(records) == 1 and records[0]["q_later"] is None
    assert skips["no_later_price"] == 1


def test_prematch_fallback_is_not_an_in_play_opinion():
    contexts, prices = _one_match_states()
    records, skips = ie.evaluate_match(FakeService(source="prematch"), registry(), result(), contexts, prices)
    assert records == []
    assert skips["not_inplay:prematch"] == 3


def test_no_market_skips_the_state():
    records, skips = ie.evaluate_match(FakeService(), registry(), result(), [ctx(T0)], [])
    assert records == [] and skips["no_market"] == 1


# --------------------------------------------------------------------------- population
def test_population_is_real_livescore_v2_with_a_decided_result():
    contexts, prices = _one_match_states()
    ctx_rows = contexts + [ctx(T0, match_id="lb_2"), ctx(T0, match_id="lb_3"), ctx(T0, match_id="lb_4")]
    price_rows = prices + [px(T0 - timedelta(minutes=1), 0.5, "lb_2"), px(T0 - timedelta(minutes=1), 0.5, "lb_3"),
                           px(T0 - timedelta(minutes=1), 0.5, "lb_4")]
    reg = [registry("lb_1"), registry("lb_2", simulated=True), registry("lb_3", pipeline="legacy"),
           registry("lb_4")]
    res = [result("lb_1"), result("lb_2"), result("lb_3"), result("lb_4", result_type="no result")]
    out = ie.evaluate_matches(FakeService(), reg, res, ctx_rows, price_rows)
    assert {r["match_id"] for r in out["records"]} == {"lb_1"}
    assert out["counts"]["simulated"] == 1
    assert out["counts"]["not_livescore_v2"] == 1
    assert out["counts"]["not_decided"] == 1
    assert out["counts"]["matches_evaluated"] == 1


def test_only_is_live_rows_with_a_score_are_states():
    contexts = [ctx(T0, live=False), ctx(T0 + timedelta(minutes=1), score=None), ctx(T0 + timedelta(minutes=2))]
    prices = [px(T0 - timedelta(minutes=1), 0.5)]
    svc = FakeService()
    records, _ = ie.evaluate_match(svc, registry(), result(), contexts, prices)
    assert [r["t"] for r in records] == [(T0 + timedelta(minutes=2)).isoformat()]


def test_decided_result_prefers_a_win():
    rows = [result(result_type="abandoned"), result(result_type="win")]
    assert ie.decided_result(rows)["result_type"] == "win"
    assert ie.decided_result([result(result_type="abandoned")]) is None


# --------------------------------------------------------------------------- scoring
def test_soft_log_loss_is_the_expected_log_loss_against_a_soft_target():
    p, q = 0.6, 0.3
    expected = -(q * np.log(p) + (1 - q) * np.log(1 - p))
    assert float(ie.soft_log_loss(p, q)) == pytest.approx(expected)


def test_soft_log_loss_is_minimised_when_the_forecast_equals_the_target():
    grid = np.linspace(0.01, 0.99, 981)
    losses = ie.soft_log_loss(grid, 0.3)
    assert grid[int(np.argmin(losses))] == pytest.approx(0.3, abs=1e-3)


def test_soft_log_loss_equals_the_hard_log_loss_for_binary_targets():
    p = np.array([0.1, 0.5, 0.9, 0.33])
    for y in (0, 1):
        assert np.allclose(ie.soft_log_loss(p, np.full(4, y)), log_loss(p, np.full(4, y)))


def test_soft_brier_matches_the_brier_score_for_binary_targets():
    p = np.array([0.2, 0.7])
    assert np.allclose(ie.soft_brier(p, np.array([1.0, 0.0])), [0.64, 0.49])
    assert float(ie.soft_brier(0.4, 0.1)) == pytest.approx(0.09)


# --------------------------------------------------------------------------- clustered CI
def _records_for_matches(n_matches: int, seed: int = 3) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_matches):
        y = int(rng.integers(0, 2))
        p_model = float(np.clip(0.5 + (0.3 if y else -0.3) * rng.uniform(0.2, 1.0), 0.02, 0.98))
        out.append(rec(f"m{i:02d}", p_model, 0.5, float(rng.uniform(0.3, 0.7)), y))
    return out


def test_ci_is_clustered_by_match(monkeypatch):
    calls = []

    def spy(values, clusters, **kwargs):
        calls.append({"n": len(list(values)), "clusters": list(clusters), "kwargs": kwargs})
        return cluster_bootstrap(values, clusters, **kwargs)

    monkeypatch.setattr(ie, "cluster_bootstrap", spy)
    records = _records_for_matches(12)
    ie.summarise(records, n_boot=50)
    assert calls, "the bootstrap must be called"
    for call in calls:
        assert sorted(set(call["clusters"])) == sorted(r["match_id"] for r in records)
        assert call["kwargs"]["statistic"] == "mean"
        assert call["kwargs"]["weights"] is not None


def test_repeating_a_match_does_not_narrow_the_ci():
    base = _records_for_matches(20)
    repeated = [copy.deepcopy(r) for r in base for _ in range(5)]
    a = ie.summarise(base, n_boot=400)["formats"]["t20"]["final"]["logloss"]["diff"]
    b = ie.summarise(repeated, n_boot=400)["formats"]["t20"]["final"]["logloss"]["diff"]
    assert a["matches"] == b["matches"] == 20
    for key in ("point", "lo", "hi", "se"):
        assert b[key] == pytest.approx(a[key])


def test_summary_is_deterministic_and_counts_matches_and_states():
    records = _records_for_matches(15) + [rec("m00", 0.6, 0.5, 0.6, 1)]
    first = ie.summarise(records, n_boot=200, evaluated_at="t")
    second = ie.summarise(records, n_boot=200, evaluated_at="t")
    assert first == second
    final = first["formats"]["t20"]["final"]
    assert final["matches"] == 15 and final["states"] == 16


def test_soft_target_uses_only_states_with_a_later_price():
    records = _records_for_matches(16)
    records[0]["q_later"] = None
    summary = ie.summarise(records, n_boot=100)["formats"]["t20"]
    assert summary["final"]["states"] == 16
    assert summary["soft"]["states"] == 15 and summary["soft"]["matches"] == 15


# --------------------------------------------------------------------------- gate rule
@pytest.mark.parametrize(
    "matches, point, lo, expected",
    [
        (14, -0.05, -0.10, False),  # fewer than 15 matches: closed whatever the numbers
        (15, -0.05, -0.10, True),   # enough matches, model better, CI reaches below zero
        (15, -0.05, 0.00, False),   # CI lower bound not below zero
        (20, 0.02, -0.10, False),   # model worse on the point estimate
        (20, 0.00, -0.10, True),    # boundary: point exactly zero
        (20, -0.01, None, False),   # no CI
        (40, None, None, False),    # no estimate
    ],
)
def test_gate_rule(matches, point, lo, expected):
    assert ie.gate_allowed(matches, point, lo) is expected


def _stronger_model(n: int, sign: float) -> list[dict[str, Any]]:
    rng = np.random.default_rng(11)
    out = []
    for i in range(n):
        y = int(rng.integers(0, 2))
        direction = 1.0 if y else -1.0
        p_model = 0.5 + sign * direction * 0.2 * rng.uniform(0.5, 1.0)
        out.append(rec(f"m{i:02d}", float(p_model), 0.5, None, y))
    return out


def test_gate_is_closed_below_fifteen_matches_even_when_the_model_is_better():
    summary = ie.summarise(_stronger_model(14, +1.0), n_boot=300)
    assert summary["gate"]["t20"]["inplay_model_allowed"] is False
    assert summary["gate"]["t20"]["matches"] == 14


def test_gate_opens_when_the_model_is_better_on_at_least_fifteen_matches():
    summary = ie.summarise(_stronger_model(16, +1.0), n_boot=300)
    entry = summary["gate"]["t20"]
    assert entry["matches"] == 16
    assert entry["logloss_diff"] < 0 and entry["ci"][0] < 0
    assert entry["inplay_model_allowed"] is True


def test_gate_stays_closed_when_the_model_is_worse():
    summary = ie.summarise(_stronger_model(16, -1.0), n_boot=300)
    assert summary["gate"]["t20"]["logloss_diff"] > 0
    assert summary["gate"]["t20"]["inplay_model_allowed"] is False


def test_each_format_is_gated_on_its_own_matches():
    better = [dict(r, family="t20") for r in _stronger_model(16, +1.0)]
    worse = [dict(r, family="odi", match_id="o" + r["match_id"]) for r in _stronger_model(16, -1.0)]
    summary = ie.summarise(better + worse, n_boot=200)
    assert summary["gate"]["t20"]["inplay_model_allowed"] is True
    assert summary["gate"]["odi"]["inplay_model_allowed"] is False
    assert summary["gate"]["multiday"]["inplay_model_allowed"] is False
    assert summary["gate"]["multiday"]["matches"] == 0


def test_no_states_gives_a_closed_gate_with_null_numbers():
    summary = ie.summarise([], n_boot=10)
    entry = summary["gate"]["t20"]
    assert entry["inplay_model_allowed"] is False and entry["matches"] == 0
    assert entry["logloss_diff"] is None and entry["ci"] == [None, None]


# --------------------------------------------------------------------------- gate file
def test_missing_gate_file_means_every_format_is_closed(tmp_path: Path):
    gate = ie.load_inplay_gate(tmp_path / "nope.json")
    assert set(gate) == set(ie.FORMATS)
    assert all(entry["inplay_model_allowed"] is False for entry in gate.values())
    assert ie.inplay_model_allowed(gate, "t20") is False


def test_unreadable_or_malformed_gate_file_means_closed(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ie.inplay_model_allowed(ie.load_inplay_gate(bad), "t20") is False
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert ie.inplay_model_allowed(ie.load_inplay_gate(listy), "t20") is False


def test_a_string_true_is_not_an_allowed_flag(tmp_path: Path):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps({"t20": {"inplay_model_allowed": "true", "matches": 99}}), encoding="utf-8")
    assert ie.inplay_model_allowed(ie.load_inplay_gate(path), "t20") is False


def test_gate_round_trips_and_nan_never_reaches_the_file(tmp_path: Path):
    path = tmp_path / "models" / "registry" / "inplay_gate.json"
    summary = ie.summarise(_stronger_model(16, +1.0), n_boot=200, evaluated_at="2026-11-03T00:00:00+00:00")
    summary["gate"]["odi"]["ci"] = [float("nan"), float("nan")]
    ie.write_gate(summary["gate"], path)
    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    loaded = ie.load_inplay_gate(path)
    assert ie.inplay_model_allowed(loaded, "t20") is True
    assert loaded["t20"]["evaluated_at"] == "2026-11-03T00:00:00+00:00"
    assert loaded["odi"]["ci"] == [None, None]
    assert ie.inplay_model_allowed(loaded, "odi") is False
    assert not list(path.parent.glob("*.tmp"))


def test_unknown_family_is_closed():
    assert ie.inplay_model_allowed(ie.closed_gate(), "test") is False
    assert ie.inplay_model_allowed({}, "t20") is False


# --------------------------------------------------------------------------- report
def test_render_markdown_states_the_rule_and_the_gate():
    summary = ie.summarise(_stronger_model(16, +1.0), n_boot=100, evaluated_at="2026-11-03T00:00:00+00:00")
    text = ie.render_markdown(summary, {"counts": {"registry": 1}, "skips": {"no_market": 2}})
    assert "inplay_model_allowed = matches >= 15" in text
    assert "ALLOWED" in text
    assert "no_market" in text
    empty = ie.render_markdown(ie.summarise([], n_boot=10, evaluated_at="x"), {"counts": {}, "skips": {}})
    assert "closed" in empty


def test_a_ci_from_fewer_than_ten_matches_is_labelled():
    summary = ie.summarise(_stronger_model(3, +1.0), n_boot=100, evaluated_at="x")
    text = ie.render_markdown(summary, {"counts": {}, "skips": {}})
    assert "(too few matches)" in text
