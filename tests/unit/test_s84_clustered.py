"""S84 match-clustered evidence: the shared cluster bootstrap, the daily review and replay headlines, the pre-match and
in-play split, the 'too few matches' label, and the R2 / R3 gates that cannot pass below their match minimum.

Synthetic inputs only. No database, no network, no real clock.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from rl.graduation import GraduationEvaluator
from scripts import paper_report as rep
from shared.constants import TRUST_CRITERIA
from shared.metrics import MIN_CLUSTERS_FOR_CI, cluster_bootstrap
from strategy import trust
from training import evaluate as ev
from virtual_trading import paper_review as rv

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 2)
BANKROLL = 5_000_000.0


def _bet(match_id="m1", clv=0.02, stake=1_000.0, status="settled", in_play=False, pnl=None, variant="w05_e03"):
    if pnl is None and status in ("settled", "void"):
        pnl = 0.0 if status == "void" else rv.bet_gross_pnl("BACK", "home", 2.0, stake, "home")
    settled = status in ("settled", "void")
    return {
        "variant": variant, "match_id": match_id, "status": status, "side": "BACK", "selection": "home",
        "price_filled": 2.0 if status not in ("pending", "unfilled") else None, "stake": stake, "clv": clv,
        "pnl": pnl, "result": ("home" if settled else None), "p_used": 0.5,
        "settled_at": datetime(2026, 11, 2, 15, 0, tzinfo=UTC) if settled else None,
        "decided_at": datetime(2026, 11, 2, 14, 0, tzinfo=UTC), "team": "Alpha", "in_play": in_play,
    }


# ----------------------------------------------------------------------------- the shared bootstrap
def test_perfectly_correlated_bets_give_the_same_interval_as_one_bet_per_match():
    rng = np.random.default_rng(11)
    per_match = rng.normal(0.02, 0.05, size=12)  # one CLV value per match
    # Four bets per match, all with that same value: perfectly correlated within the match.
    values = np.repeat(per_match, 4)
    matches = [f"m{i}" for i in np.repeat(np.arange(12), 4)]
    clustered = cluster_bootstrap(values, matches, n_boot=2000, seed=5)
    one_each = cluster_bootstrap(per_match, [f"m{i}" for i in range(12)], n_boot=2000, seed=5)
    assert clustered["n_clusters"] == one_each["n_clusters"] == 12
    assert clustered["n_items"] == 48 and one_each["n_items"] == 12
    assert clustered["ci_lower"] == pytest.approx(one_each["ci_lower"])
    assert clustered["ci_upper"] == pytest.approx(one_each["ci_upper"])
    assert clustered["estimate"] == pytest.approx(one_each["estimate"])


def test_resampling_bets_instead_of_matches_understates_the_interval():
    rng = np.random.default_rng(3)
    per_match = rng.normal(0.0, 0.05, size=10)
    values = np.repeat(per_match, 6)
    matches = [f"m{i}" for i in np.repeat(np.arange(10), 6)]
    by_match = cluster_bootstrap(values, matches, n_boot=2000, seed=9)
    by_bet = cluster_bootstrap(values, list(range(60)), n_boot=2000, seed=9)  # every bet its own cluster
    width = lambda r: r["ci_upper"] - r["ci_lower"]  # noqa: E731
    assert width(by_match) > 2 * width(by_bet)


def test_sum_per_cluster_of_correlated_pnl_matches_the_one_bet_case_times_the_bet_count():
    pnl_per_match = [100.0, -50.0, 30.0, 80.0, -20.0, 60.0, 10.0, -90.0, 40.0, 25.0]
    values = [v for v in pnl_per_match for _ in range(3)]
    matches = [f"m{i}" for i in range(10) for _ in range(3)]
    many = cluster_bootstrap(values, matches, statistic="sum_per_cluster", n_boot=1000, seed=2)
    one = cluster_bootstrap(pnl_per_match, [f"m{i}" for i in range(10)], statistic="sum_per_cluster", n_boot=1000, seed=2)
    assert many["estimate"] == pytest.approx(3 * one["estimate"])
    assert many["ci_lower"] == pytest.approx(3 * one["ci_lower"])
    assert many["ci_upper"] == pytest.approx(3 * one["ci_upper"])


def test_bootstrap_is_deterministic_for_a_seed_and_depends_on_the_seed():
    values = [0.01, 0.03, -0.02, 0.04, 0.00, 0.02, 0.05, -0.01]
    matches = ["a", "a", "b", "c", "c", "d", "e", "e"]
    first = cluster_bootstrap(values, matches, seed=20261103)
    second = cluster_bootstrap(values, matches, seed=20261103)
    assert first == second
    other = cluster_bootstrap(values, matches, seed=1)
    assert (other["ci_lower"], other["ci_upper"]) != (first["ci_lower"], first["ci_upper"])


def test_bootstrap_does_not_depend_on_the_order_of_the_items():
    values = [0.01, 0.03, -0.02, 0.04, 0.00, 0.02, 0.05, -0.01]
    matches = ["a", "a", "b", "c", "c", "d", "e", "e"]
    order = [5, 2, 7, 0, 3, 6, 1, 4]
    a = cluster_bootstrap(values, matches, seed=4)
    b = cluster_bootstrap([values[i] for i in order], [matches[i] for i in order], seed=4)
    assert b["ci_lower"] == pytest.approx(a["ci_lower"]) and b["ci_upper"] == pytest.approx(a["ci_upper"])


def test_stake_weighted_mean_and_missing_values_are_handled():
    res = cluster_bootstrap([1.0, 2.0, 3.0], ["x", "y", "z"], weights=[1.0, 0.0, 3.0], n_boot=200)
    assert res["estimate"] == pytest.approx((1.0 + 9.0) / 4.0)
    assert res["weighted"] is True
    dropped = cluster_bootstrap([1.0, None, 3.0, math.nan], ["x", "y", "z", "w"], n_boot=200)
    assert dropped["n_items"] == 2 and dropped["n_clusters"] == 2
    assert dropped["estimate"] == pytest.approx(2.0)


def test_population_counts_units_with_no_items_as_clusters():
    res = cluster_bootstrap([0.1, 0.1], ["a", "b"], population=["a", "b", "c", "d"], n_boot=200)
    assert res["n_clusters"] == 4 and res["n_items"] == 2
    assert res["estimate"] == pytest.approx(0.1)


def test_no_interval_without_two_clusters_and_nothing_for_no_items():
    one = cluster_bootstrap([0.1, 0.2], ["a", "a"], n_boot=200)
    assert one["estimate"] == pytest.approx(0.15)
    assert math.isnan(one["ci_lower"]) and math.isnan(one["ci_upper"])
    empty = cluster_bootstrap([], [], n_boot=200)
    assert math.isnan(empty["estimate"]) and empty["n_clusters"] == 0


def test_invalid_calls_are_refused():
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0, 2.0], ["a"])
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0], ["a"], statistic="median")
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0], ["a"], weights=[1.0], statistic="sum_per_cluster")
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0, 2.0], ["a", "b"], weights=[1.0, -1.0])


# ----------------------------------------------------------------------------- the too-few-matches label
def test_a_cluster_ci_from_fewer_than_ten_matches_is_flagged():
    nine = cluster_bootstrap([0.01 * i for i in range(18)], [f"m{i // 2}" for i in range(18)], n_boot=200)
    ten = cluster_bootstrap([0.01 * i for i in range(20)], [f"m{i // 2}" for i in range(20)], n_boot=200)
    assert nine["n_clusters"] == 9 and nine["too_few_clusters"] is True
    assert ten["n_clusters"] == MIN_CLUSTERS_FOR_CI and ten["too_few_clusters"] is False


def test_headlines_name_the_match_and_bet_counts_and_the_label():
    few = rv.headline(0.02, -0.01, 0.05, matches=3, bets=60)
    assert "3 matches / 60 bets" in few and "too few matches" in few
    enough = rv.headline(0.02, -0.01, 0.05, matches=10, bets=60)
    assert "10 matches / 60 bets" in enough and "too few matches" not in enough


# ----------------------------------------------------------------------------- daily review: clustered and split
def test_summary_headlines_are_clustered_by_match_not_by_bet():
    # 3 matches, 20 bets each, all bets on a match share one CLV: the per-bet CI would look like proof.
    rows = [_bet(match_id=f"m{i % 3}", clv=(0.05, -0.02, 0.01)[i % 3]) for i in range(60)]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["clv_matches"] == 3 and s["clv_bets"] == 60
    assert s["clv_too_few_matches"] is True
    assert s["clv_n"] == 60  # bets still counted
    naive_lo, naive_hi = s["detail"]["clv_bet_ci_lo"], s["detail"]["clv_bet_ci_hi"]
    assert s["clv_ci_hi"] - s["clv_ci_lo"] > naive_hi - naive_lo
    assert s["clv_ci_lo"] < 0.0 < s["clv_ci_hi"]  # three matches cannot show the edge
    assert s["r3_met"] is False


def test_pre_match_and_in_play_are_split_and_add_up():
    rows = [
        _bet(match_id="pre1", clv=0.02, in_play=False),
        _bet(match_id="pre1", clv=0.03, in_play=False),
        _bet(match_id="pre2", clv=0.01, in_play=False, status="filled"),
        _bet(match_id="live1", clv=-0.04, in_play=True),
        _bet(match_id="live1", clv=-0.02, in_play=True, status="void"),
        _bet(match_id="live2", clv=-0.01, in_play=True, status="filled", pnl=None),
    ]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    pre, live = s["segments"]["prematch"], s["segments"]["inplay"]
    assert (pre["matches"], pre["bets"], pre["clv_matches"], pre["clv_bets"]) == (2, 3, 2, 3)
    assert (live["matches"], live["bets"], live["clv_matches"], live["clv_bets"]) == (2, 3, 2, 3)
    assert pre["clv_mean"] == pytest.approx((0.02 + 0.03 + 0.01) / 3)
    assert live["clv_mean"] == pytest.approx((-0.04 - 0.02 - 0.01) / 3)
    assert pre["pnl"] + live["pnl"] == pytest.approx(s["pnl"])
    assert s["clv_bets"] == pre["clv_bets"] + live["clv_bets"]


def test_review_report_shows_matches_and_bets_and_the_split_and_the_detail_table():
    rows = [_bet(match_id=f"m{i % 4}", clv=0.02, in_play=(i % 2 == 0)) for i in range(40)]
    state = rv.review(rows, {"w05_e03": BANKROLL}, now=NOW, n_resamples=200, draws=200)
    text = rep.render(state, rows, NOW)
    assert "### Detail: CLV and P&L by segment, match-clustered" in text  # S88: the segment detail, after the headline
    assert "| w05_e03 | pre-match |" in text and "| w05_e03 | in-play |" in text
    assert "too few matches" in text  # 4 matches
    assert "## Detail: per-bet intervals" not in text  # S88: per-bet intervals are Tier C and leave the report


def test_review_is_deterministic_for_the_same_ledger():
    rows = [_bet(match_id=f"m{i % 12}", clv=0.01 * (i % 5)) for i in range(120)]
    a = rv.review(rows, {"w05_e03": BANKROLL}, now=NOW, n_resamples=300, draws=300)
    b = rv.review(rows, {"w05_e03": BANKROLL}, now=NOW, n_resamples=300, draws=300)
    assert a == b


# ----------------------------------------------------------------------------- R3 (paper review)
def test_r3_cannot_pass_on_300_bets_from_three_matches():
    rows = [_bet(match_id=f"m{i % 3}", clv=0.02 + 0.001 * (i % 7)) for i in range(300)]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["settled_bets"] == 300 and s["settled_matches"] == 3
    assert s["clv_ci_lo"] > 0  # a bet-level reading would call this proof
    assert s["r3_met"] is False  # the match minimum is 30, whatever the bet count


def test_r3_needs_thirty_matches_and_the_clustered_interval():
    rows = [_bet(match_id=f"m{i % 29}", clv=0.02) for i in range(300)]
    assert rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)["r3_met"] is False
    rows = [_bet(match_id=f"m{i % 30}", clv=0.02 + 0.001 * (i % 7)) for i in range(300)]
    assert rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)["r3_met"] is True


# ----------------------------------------------------------------------------- R2 (referee) and the trust gate
def _interval(point: float, lo: float, hi: float) -> dict[str, float]:
    return {"point": point, "lo": lo, "hi": hi}


def test_r2_cannot_pass_below_one_hundred_and_fifty_matches_whatever_the_bet_count():
    clv = _interval(0.05, 0.04, 0.06)
    pnl = _interval(1e5, 5e4, 1.5e5)
    assert ev._r2(149, 50_000, clv, pnl, 50_000)["status"] == "insufficient"
    assert ev._r2(150, 50_000, clv, pnl, 50_000)["status"] == "pass"
    summary = ev.summarise_gates([{"arena": "prematch", "segment": "all",
                                   "r1": {"status": "fail"}, "r2": ev._r2(149, 50_000, clv, pnl, 50_000)}])
    assert summary["r2_pass"] == [] and summary["signals_allowed"] is False


def test_referee_bootstrap_resamples_matches_and_labels_one_match_as_undefined():
    values = np.array([0.1, 0.2, 0.3, 0.4])
    groups = np.array([0, 0, 1, 1])
    point, lo, hi = ev._bootstrap(values, groups, 2, "mean")
    assert point == pytest.approx(0.25)
    assert lo is not None and hi is not None and lo <= point <= hi
    assert ev._bootstrap(np.array([0.1]), np.array([0]), 1, "mean") == (pytest.approx(0.1), None, None)


def _trust_bets(n_matches: int, per_match: int, clv_of) -> list[dict[str, Any]]:
    start = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    out = []
    for i in range(n_matches * per_match):
        m = i % n_matches
        out.append({
            "match_id": f"m{m}", "placed_at": start, "settled_at": start + timedelta(hours=2),
            "action": "BACK_HOME", "odds": 2.0, "profit_loss": 10.0 if i % 2 else -5.0,
            "reference_clv": clv_of(m, i), "clv": clv_of(m, i), "is_real": True,
        })
    return out


def test_trust_gate_refuses_300_bets_over_three_matches():
    bets = _trust_bets(3, 100, lambda m, i: 0.02)
    annotated = [dict(b, in_play=False) for b in bets]
    metrics = trust.trust_metrics(annotated, allocated_bankroll=10_000.0, n_bootstrap=200)
    assert metrics["bet_volume"] == 300.0 and metrics["min_matches"] == 3.0
    status = GraduationEvaluator(state_path=None, criteria=TRUST_CRITERIA).evaluate_metrics(metrics, version="v")
    met = {c.name: c.met for c in status.criteria}
    assert met["min_matches"] is False
    assert status.ready is False


def test_trust_clv_interval_is_match_clustered_and_wider_than_per_bet():
    # Three matches with different means: the per-bet interval sees 300 nearly identical values and is narrow.
    means = {0: 0.06, 1: -0.03, 2: 0.01}
    bets = _trust_bets(3, 100, lambda m, i: means[m] + (0.0005 if i % 2 else -0.0005))
    metrics = trust.trust_metrics(bets, allocated_bankroll=10_000.0, n_bootstrap=500)
    from shared.metrics import portfolio_clv

    per_bet = portfolio_clv(bets, field="reference_clv", n_bootstrap=500)
    assert metrics["clv_ci_lower"] < per_bet["ci95_lower"]
    assert metrics["clv_ci_lower"] < 0.0  # three matches cannot clear zero


def test_trust_metrics_pass_when_thirty_matches_back_a_clear_edge():
    bets = _trust_bets(30, 10, lambda m, i: 0.03 + 0.001 * (m % 4))
    metrics = trust.trust_metrics(bets, allocated_bankroll=10_000.0, n_bootstrap=500)
    assert metrics["min_matches"] == 30.0
    assert metrics["clv_ci_lower"] > 0.0


# ----------------------------------------------------------------------------- replay summary
def test_replay_markdown_uses_match_clustered_headlines_and_a_split_table():
    import importlib

    replay = importlib.import_module("scripts.replay_paper")
    rows = [_bet(match_id=f"m{i % 3}", clv=0.02, in_play=(i % 2 == 0), variant="w05_e03") for i in range(30)]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    summary = {
        "window": {"from": "a", "to": "b", "matches_considered": 3, "cycles_run": 1, "grid_steps_skipped": 0},
        "settings": {}, "skipped_registry": {}, "skipped_matches": {}, "guard_refusals": {},
        "variants": {"w05_e03": {
            **s,
            "intents": 30, "intents_by_side": {"BACK_home": 30}, "max_worst_case_per_match": 0.0,
            "churn": {"trades_per_match": 10.0, "tiny_trades": 0, "tiny_share": 0.0},
        }},
        "per_match": [],
    }
    text = replay.render_markdown(summary)
    assert "## Pre-match and in-play, separately (match-clustered)" in text
    assert "| w05_e03 | pre-match |" in text and "| w05_e03 | in-play |" in text
    assert "3 matches / 30 bets; too few matches" in text
    assert "## Detail: per-bet intervals" in text
