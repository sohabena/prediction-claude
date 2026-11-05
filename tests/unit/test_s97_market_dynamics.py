"""S97: market-dynamics model. Synthetic paths and fakes only: no database, no network, no real files written.

Covers: no look-ahead in features and labels, label alignment and the dropping rules, the no-scoreboard flag, the live
estimate (clipping, None), the variants through ``model_edge`` (caps, ticket budget, guards), a missing model and a
pre-match candidate opening no new risk, the placebos following the reference only, and the pure evaluation and the
weekly activation rule.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from strategy import model_edge
from strategy.market_dynamics import MarketDynamicsModel, load_current
from strategy.model_edge import Book
from strategy.probability import Estimate
from training import market_dynamics as md
from training.market import band_prob_home
from virtual_trading import paper_review
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
START = T0 - timedelta(minutes=30)  # the off: the match is in play from here
MID = "m1"
MIN = timedelta(minutes=1)
SEC = timedelta(seconds=1)


# ---------------------------------------------------------------- synthetic paths
def _book(p: float, half: float = 0.005) -> dict:
    """A book whose band is exactly [p - half, p + half]: its midpoint is p."""
    return {
        "back_home": 1.0 / (p + half),
        "lay_home": 1.0 / (p - half),
        "back_away": 1.0 / (1.0 - p + half),
        "lay_away": 1.0 / (1.0 - p - half),
    }


def _path(start: datetime, n: int, p_of, *, step: int = 30, mid: str = MID) -> list[dict]:
    """One price row every ``step`` seconds from ``start``; the band midpoint of row k is ``p_of(k)``."""
    rows = []
    for k in range(n):
        rows.append({"match_id": mid, "time": start + timedelta(seconds=step * k), **_book(p_of(k)), "source": "listing"})
    return rows


def _wiggle(k: int) -> float:
    return 0.5 + 0.08 * math.sin(k / 6.0) + 0.02 * math.sin(k / 1.7)


def _match(**overrides) -> dict:
    row = {
        "match_id": MID, "team_home": "Alpha", "team_away": "Beta", "competition": "Indian Premier League",
        "actual_start_time": START, "match_start_time": START, "is_simulated": False, "canonical_match_id": None,
        "toss_winner": None, "toss_decision": None, "settled": False,
    }
    row.update(overrides)
    return row


def _same(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) and math.isnan(x) and isinstance(y, float) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


def _score(t: datetime, *, score: int, wickets: int, overs: float, innings: int = 1, live: bool = True) -> dict:
    return {"match_id": MID, "time": t, "is_live": live, "score": score, "wickets": wickets, "overs": overs,
            "innings": innings, "balls_remaining": int(120 - round(overs * 6)), "batting_team": "Alpha",
            "bowling_team": "Beta", "run_rate": 0.0, "req_run_rate": None, "match_format": "T20", "status": "live"}


class _FakeEstimator:
    """Stands in for the fitted regressor: the same change for every row."""

    def __init__(self, delta: float) -> None:
        self.delta = delta

    def predict(self, X):
        return np.full(len(X), self.delta)


# ---------------------------------------------------------------- 1. no look-ahead in the features
def test_features_at_t_do_not_change_when_later_prices_change():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    base = md.MatchHistory(rows, [], off=START).features(t)
    assert base is not None
    poisoned = [r if r["time"] <= t else {**r, "back_home": 1.01, "lay_home": 1000.0, "back_away": 3.0}
                for r in rows]
    assert _same(base, md.MatchHistory(poisoned, [], off=START).features(t))
    # rows strictly after t are not even needed
    assert _same(base, md.MatchHistory([r for r in rows if r["time"] <= t], [], off=START).features(t))


def test_features_at_t_do_not_change_when_later_scores_change():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    scores = [_score(t - timedelta(minutes=3), score=40, wickets=1, overs=6.0)]
    base = md.MatchHistory(rows, scores, off=START).features(t)
    later = scores + [_score(t + SEC, score=999, wickets=9, overs=19.5)]
    assert _same(base, md.MatchHistory(rows, later, off=START).features(t))


def test_a_row_after_t_is_never_the_latest_score():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    feats = md.MatchHistory(rows, [_score(t + SEC, score=50, wickets=0, overs=5.0)], off=START).features(t)
    assert feats["no_scoreboard"] == 1.0 and math.isnan(feats["score"])


# ---------------------------------------------------------------- 2. labels: alignment and the dropping rules
def test_label_is_the_band_ten_minutes_after_t():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=5)  # on the 30 s grid from START
    k_t = int((t - (START - timedelta(minutes=20))).total_seconds() // 30)
    lab = md.MatchHistory(rows, [], off=START).label(t)
    assert lab is not None
    assert lab["p600"] == pytest.approx(_wiggle(k_t + 20), abs=1e-9)
    assert lab["p60"] == pytest.approx(_wiggle(k_t + 2), abs=1e-9)


def test_dataset_targets_are_labels_minus_the_price_now():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    out, stats = md.build_match_rows(_match(), rows, [])
    t = START + timedelta(minutes=5)
    row = next(r for r in out if r["t"] == t.isoformat())
    k_t = int((t - (START - timedelta(minutes=20))).total_seconds() // 30)
    assert row["p_now"] == pytest.approx(_wiggle(k_t), abs=1e-9)
    assert row["y10"] == pytest.approx(_wiggle(k_t + 20) - _wiggle(k_t), abs=1e-9)
    assert row["y60"] == pytest.approx(_wiggle(k_t + 2) - _wiggle(k_t), abs=1e-9)
    assert row["fill_to_ref"] == pytest.approx(row["y10"] - row["y60"], abs=1e-9)
    assert set(md.FEATURES) <= set(row)


def test_changing_the_price_at_t_plus_10_minutes_changes_the_label_not_the_features():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=5)
    k600 = int((t + timedelta(minutes=10) - (START - timedelta(minutes=20))).total_seconds() // 30)
    moved = [r if r["time"] != START - timedelta(minutes=20) + timedelta(seconds=30 * k600)
             else {**r, **_book(0.9)} for r in rows]
    a = md.MatchHistory(rows, [], off=START)
    b = md.MatchHistory(moved, [], off=START)
    assert _same(a.features(t), b.features(t))
    assert a.label(t)["p600"] != b.label(t)["p600"]


def test_a_gap_over_three_minutes_inside_the_label_window_drops_the_label():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=5)
    gapped = [r for r in rows if not (t + 100 * SEC < r["time"] < t + 400 * SEC)]  # a 5-minute hole
    assert md.MatchHistory(gapped, [], off=START).label(t) is None
    out, stats = md.build_match_rows(_match(), gapped, [])
    assert all(r["t"] != t.isoformat() for r in out)
    assert stats["label_window_gap"] > 0


def test_a_recording_that_ends_inside_the_label_window_drops_the_label():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=5)
    cut = [r for r in rows if r["time"] <= t + timedelta(minutes=4)]  # the last price is 6 minutes before t+10min
    assert md.MatchHistory(cut, [], off=START).label(t) is None


def test_snapshots_start_at_the_off_and_need_a_fresh_price():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    out, _ = md.build_match_rows(_match(), rows, [])
    assert out and min(r["t"] for r in out) == START.isoformat()
    # the market goes quiet for 4 minutes before t: no fresh price at t, no row
    t = START + timedelta(minutes=20)
    quiet = [r for r in rows if not (t - timedelta(minutes=4) < r["time"] <= t)]
    out_quiet, stats = md.build_match_rows(_match(), quiet, [])
    assert all(r["t"] != t.isoformat() for r in out_quiet)
    assert stats["no_fresh_price"] > 0


# ---------------------------------------------------------------- 3. the no-scoreboard flag
def test_no_score_rows_means_the_no_scoreboard_flag_and_missing_score_features():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    feats = md.MatchHistory(rows, [], off=START).features(t)
    assert feats["no_scoreboard"] == 1.0
    assert all(math.isnan(feats[f]) for f in md.SCORE_FEATURES if f != "no_scoreboard")


def test_a_fresh_live_score_clears_the_flag_and_a_stale_or_not_live_one_does_not():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    fresh = md.MatchHistory(rows, [_score(t - timedelta(minutes=1), score=80, wickets=2, overs=10.0)], off=START)
    assert fresh.features(t)["no_scoreboard"] == 0.0
    assert fresh.features(t)["score"] == 80.0
    stale = md.MatchHistory(rows, [_score(t - timedelta(minutes=8), score=80, wickets=2, overs=10.0)], off=START)
    assert stale.features(t)["no_scoreboard"] == 1.0
    not_live = md.MatchHistory(rows, [_score(t - timedelta(minutes=1), score=80, wickets=2, overs=10.0, live=False)],
                               off=START)
    assert not_live.features(t)["no_scoreboard"] == 1.0


def test_runs_and_wickets_over_the_last_overs_come_from_the_score_rows():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    t = START + timedelta(minutes=20)
    scores = [
        _score(t - timedelta(minutes=3), score=0, wickets=0, overs=0.0),
        _score(t - timedelta(minutes=2), score=6, wickets=0, overs=1.0),
        _score(t - timedelta(seconds=30), score=12, wickets=1, overs=2.0),
    ]
    feats = md.MatchHistory(rows, scores, off=START).features(t)
    assert feats["runs_1o"] == 6.0  # 12 now against 6 after one over
    assert feats["runs_3o"] == 12.0  # fewer than three overs: from the start of the innings
    assert feats["wkts_1o"] == 1.0 and feats["wkts_5o"] == 1.0
    assert feats["overs"] == pytest.approx(2.0)


# ---------------------------------------------------------------- 4. the live estimate
def test_estimate_is_none_without_fresh_prices():
    model = MarketDynamicsModel(_FakeEstimator(0.1))
    now = T0
    assert model.estimate([], [], now) is None
    stale = _path(now - timedelta(minutes=30), 40, lambda k: 0.5)  # last price 30 min before now
    assert model.estimate(stale, [], now) is None


def test_estimate_adds_the_predicted_change_and_clips_to_sane_bounds():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: 0.5)  # band midpoint 0.5 throughout
    now = rows[-1]["time"]
    assert model_p(MarketDynamicsModel(_FakeEstimator(0.1)), rows, now) == pytest.approx(0.6, abs=1e-9)
    assert model_p(MarketDynamicsModel(_FakeEstimator(5.0)), rows, now) == pytest.approx(0.99)
    assert model_p(MarketDynamicsModel(_FakeEstimator(-5.0)), rows, now) == pytest.approx(0.01)


def model_p(model, rows, now):
    return model.estimate(rows, [], now, off=START)


def test_the_clip_is_not_the_band_because_inside_the_band_no_takeable_bet_has_an_edge():
    """Why the probability is clipped to fixed bounds: at any p inside a book's band, every takeable price has an edge
    of zero or less, so a band clip would stop every trade."""
    book = _book(0.5)
    for p in (0.4955, 0.5, 0.5045):
        cands = model_edge._candidates(model_edge._takeable(book), p)
        assert all(c.edge <= 1e-12 for c in cands)


def test_missing_model_file_gives_none_not_an_error(tmp_path):
    assert load_current(root=tmp_path) is None
    (tmp_path / "models" / "market_dynamics").mkdir(parents=True)
    (tmp_path / "models" / "market_dynamics" / "current.json").write_text("{not json", encoding="utf-8")
    assert load_current(root=tmp_path) is None
    (tmp_path / "models" / "market_dynamics" / "current.json").write_text(
        '{"model": "models/market_dynamics/missing.joblib"}', encoding="utf-8")
    assert load_current(root=tmp_path) is None


# ---------------------------------------------------------------- 5. the variants go through model_edge
def test_the_dynamics_variants_are_w1_with_their_edges_and_the_placebos_are_unchanged():
    by_name = {v.name: v for v in pt.build_variants()}
    assert by_name["mkt_dyn"].w == 1.0 and by_name["mkt_dyn"].min_edge == 0.03
    assert by_name["mkt_dyn_e06"].w == 1.0 and by_name["mkt_dyn_e06"].min_edge == 0.06
    assert not by_name["mkt_dyn"].market_only and by_name["mkt_dyn"].placebo == ""
    assert by_name[paper_review.PLACEBO_RANDOM].placebo == "random"
    assert by_name[paper_review.PLACEBO_FAVOURITE].placebo == "favourite"
    assert by_name[paper_review.DEFAULT_REFERENCE].w == 0.5  # the reference is untouched


def test_dynamics_variant_is_capped_and_budgeted_by_model_edge():
    variant = next(v for v in pt.build_variants() if v.name == "mkt_dyn")
    book = _book(0.5)
    intents = model_edge.decide(variant, 0.7, 0.5, book, Book(), now=0.0, last_trade_at=None, in_play=True)
    assert [i.reason for i in intents] == ["enter"]
    assert intents[0].stake <= variant.max_trade_risk + 1e-6
    assert intents[0].stake <= variant.max_match_loss + 1e-6
    # the ticket budget (30, reserve 2): with 28 tickets used, new risk is refused
    assert model_edge.decide(variant, 0.7, 0.5, book, Book(trades=28), now=0.0, last_trade_at=None,
                             in_play=True) == []


def test_pre_match_the_dynamics_variant_opens_no_new_risk():
    variant = next(v for v in pt.build_variants() if v.name == "mkt_dyn")
    assert model_edge.decide(variant, None, 0.5, _book(0.5), Book(), now=0.0, last_trade_at=None,
                             in_play=False) == []


# ---------------------------------------------------------------- 6. run_cycle with fakes (no database)
class _FakeFeed:
    def __init__(self, registry, prices, context=()):
        self.registry_rows = list(registry)
        self.prices = list(prices)
        self.context_list = list(context)

    async def last_prices(self, now):
        times = [r["time"] for r in self.prices if r["time"] <= now]
        return {MID: max(times)} if times and now - max(times) <= pt.FRESH_PRICE else {}

    async def registry(self, match_ids):
        return [dict(r) for r in self.registry_rows if r["match_id"] in set(match_ids)]

    async def price_rows(self, match_ids, since):
        return [dict(r) for r in self.prices if r["match_id"] in set(match_ids) and r["time"] >= since]

    async def context_rows(self, match_ids, since, now):
        return [dict(r) for r in self.context_list if r["match_id"] in set(match_ids) and since <= r["time"] <= now]

    async def results(self, match_ids):
        return {}


class _FakeProbability:
    """A fixed model probability (0.7) and the band midpoint as the market. With a live score row the source is
    ``inplay`` (as ProbabilityService reports it), so the model variants may enter in play."""

    def estimate(self, match, book, state=None, *, as_of):
        return Estimate(p_model=0.7, p_market=band_prob_home(book), source="inplay" if state else "prematch",
                        has_model=True, features={}, reason="")


def _registry_row(start: datetime) -> dict:
    return {"match_id": MID, "team_home": "Alpha", "team_away": "Beta", "competition": "Indian Premier League",
            "match_start_time": start, "actual_start_time": start, "is_simulated": False, "canonical_match_id": None,
            "toss_winner": None, "toss_decision": None, "settled": False}


def _cycle(prices, now, start, model, context=()):
    session = MemorySession()
    feed = _FakeFeed([_registry_row(start)], prices, context)
    report = pt_run(session, feed, now, model)
    return session, report


def _live_score(now):
    """One fresh live score row: the match is in play with a scoreboard."""
    return _score(now - MIN, score=80, wickets=2, overs=10.0)


def pt_run(session, feed, now, model):
    import asyncio

    return asyncio.run(pt.run_cycle(session, feed, _FakeProbability(), pt.build_variants(), now=now,
                                    market_dynamics=model))


def _in_play_prices(now):
    return _path(now - timedelta(minutes=25), 51, lambda k: 0.5)  # 0.5 band midpoint, last row at now


def test_in_play_with_a_model_the_dynamics_variant_trades_and_is_labelled():
    now = T0
    prices = _in_play_prices(now)
    session, report = _cycle(prices, now, now - timedelta(minutes=25), MarketDynamicsModel(_FakeEstimator(0.2)),
                             context=[_live_score(now)])
    dyn = [r for r in session.rows if r.variant == "mkt_dyn"]
    assert dyn and all(r.source == "market_dynamics" for r in dyn)
    assert all(r.p_model is not None and r.p_model > 0.5 for r in dyn)
    assert report["market_dynamics"] == {"loaded": True, "estimated": 1}


def test_without_a_model_the_dynamics_variants_open_no_new_risk():
    now = T0
    session, report = _cycle(_in_play_prices(now), now, now - timedelta(minutes=25), None,
                             context=[_live_score(now)])
    assert not [r for r in session.rows if r.variant in ("mkt_dyn", "mkt_dyn_e06")]
    assert report["market_dynamics"] == {"loaded": False}
    # the strategy variants are not affected by the missing dynamics model: they still trade this match
    assert [r for r in session.rows if r.variant == "w05_e03"]


def test_a_pre_match_candidate_gets_no_dynamics_probability_and_no_new_risk():
    now = T0
    start_future = now + timedelta(hours=1)
    prices = _path(now - timedelta(minutes=25), 51, lambda k: 0.5)
    session, report = _cycle(prices, now, start_future, MarketDynamicsModel(_FakeEstimator(0.2)))
    assert not [r for r in session.rows if r.variant in ("mkt_dyn", "mkt_dyn_e06")]
    assert report["market_dynamics"] == {"loaded": True}  # no estimate was made before the off


def test_placebos_copy_the_reference_decision_times_only():
    now = T0
    session, _ = _cycle(_in_play_prices(now), now, now - timedelta(minutes=25), MarketDynamicsModel(_FakeEstimator(0.2)),
                        context=[_live_score(now)])
    ref_times = {r.decided_at for r in session.rows if r.variant == paper_review.DEFAULT_REFERENCE}
    placebo_times = {r.decided_at for r in session.rows if paper_review.is_placebo(r.variant)}
    assert ref_times and placebo_times and placebo_times <= ref_times


# ---------------------------------------------------------------- 7. dataset rules and the evaluation (pure)
def test_build_dataset_skips_simulated_matches_and_twin_event_ids():
    rows = _path(START - timedelta(minutes=20), 200, lambda k: _wiggle(k))
    registry = [
        _match(),
        _match(match_id="srl", is_simulated=True),
        _match(match_id="twin", canonical_match_id="m1"),
    ]
    prices = {MID: rows, "srl": rows, "twin": rows}
    out, stats = md.build_dataset(registry, prices, {})
    assert {r["match_id"] for r in out} == {MID}
    assert stats["skipped_simulated"] == 1 and stats["skipped_twin"] == 1


def test_simulate_trades_side_cost_cooldown_and_threshold():
    base = {"match_id": "m", "fill_to_ref": 0.04, "half_spread_fill": 0.01, "no_scoreboard": 1.0}
    rows = [
        {**base, "t": "2026-11-03T12:00:00+00:00"},
        {**base, "t": "2026-11-03T12:01:00+00:00"},  # inside the 2-minute cooldown
        {**base, "t": "2026-11-03T12:03:00+00:00"},
        {**base, "t": "2026-11-03T12:06:00+00:00"},
    ]
    trades = md.simulate_trades(rows, [0.05, 0.05, -0.05, 0.005], 0.02)
    assert [t["t"] for t in trades] == ["2026-11-03T12:00:00+00:00", "2026-11-03T12:03:00+00:00"]
    assert trades[0]["side"] == "BACK" and trades[0]["clv"] == pytest.approx(0.04 - 0.01)
    assert trades[1]["side"] == "LAY" and trades[1]["clv"] == pytest.approx(-0.04 - 0.01)


def test_leave_one_match_out_predicts_every_row_when_there_are_two_matches():
    rows = []
    for mid in ("a", "b", "c"):
        for k in range(60):
            rows.append({**{f: 0.0 for f in md.FEATURES}, "match_id": mid, "t": f"t{k:03d}", "y10": 0.01 * (k % 3),
                         "y60": 0.0, "p60": 0.5, "p600": 0.5, "fill_to_ref": 0.0, "half_spread_fill": 0.01,
                         "half_spread_now": 0.01})
    pred = md.leave_one_match_out(rows)
    assert np.isfinite(pred).all()
    single = [r for r in rows if r["match_id"] == "a"]
    assert np.isnan(md.leave_one_match_out(single)).all()


def test_activation_rule():
    assert md.decide_activation(None, None, current_exists=False, compared_matches=0)[0] is True
    assert md.decide_activation(-0.02, -0.01, current_exists=True, compared_matches=5)[0] is False
    assert md.decide_activation(-0.01, -0.01, current_exists=True, compared_matches=5)[0] is True  # a tie is not worse
    assert md.decide_activation(0.02, -0.01, current_exists=True, compared_matches=5)[0] is True
    assert md.decide_activation(0.02, -0.01, current_exists=True, compared_matches=0)[0] is False


def test_per_match_models_leave_their_own_match_out():
    rows = []
    for mid, value in (("a", 0.0), ("b", 0.2)):
        for k in range(40):
            rows.append({**{f: 0.0 for f in md.FEATURES}, "match_id": mid, "t": f"t{k:03d}", "y10": value,
                         "y60": 0.0, "p60": 0.5, "p600": 0.5, "fill_to_ref": 0.0, "half_spread_fill": 0.01,
                         "half_spread_now": 0.01})
    models = md.fit_leave_one_match_out_models(rows)
    assert set(models) == {"a", "b"}
    x = md.design(rows[:1])
    # the model for "a" was fitted on "b" only, so it predicts b's value
    assert models["a"].predict(x)[0] == pytest.approx(0.2, abs=0.05)
