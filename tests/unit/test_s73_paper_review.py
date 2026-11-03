"""S73 paper review: per-variant scorecard, ranking, daily-loss suspension, state file, daily hook and report text.

Synthetic ledgers only (plain dicts shaped like paper_bets rows). No database, no network, no real clock.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from scripts import paper_report as rep
from tests.unit import test_s72_paper_trader as s72
from virtual_trading import paper_review as rv
from virtual_trading import paper_trader as pt

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 2)  # the last full UTC day at NOW
BANKROLL = 5_000_000.0


def _bet(
    variant="w05_e03",
    match_id="m1",
    status="settled",
    side="BACK",
    selection="home",
    price=2.0,
    stake=1_000.0,
    clv=0.01,
    pnl=None,
    result="home",
    p_used=0.5,
    settled_at=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
    decided_at=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
    team="Alpha",
    in_play=False,
) -> dict[str, Any]:
    if pnl is None and status in ("settled", "void"):
        pnl = 0.0 if status == "void" else rv.bet_gross_pnl(side, selection, price, stake, result)
    return {
        "variant": variant, "match_id": match_id, "status": status, "side": side, "selection": selection,
        "price_filled": price if status != "pending" and status != "unfilled" else None, "stake": stake,
        "clv": clv, "pnl": pnl, "result": result if status in ("settled", "void") else None,
        "p_used": p_used, "settled_at": settled_at if status in ("settled", "void") else None,
        "decided_at": decided_at, "team": team, "in_play": in_play,
    }


def _many(n, **kw):
    return [_bet(match_id=f"m{i % 40}", **kw) for i in range(n)]


# ----------------------------------------------------------------------------- the day and the clock
def test_review_day_is_the_last_full_utc_day():
    assert rv.review_day_for(NOW) == DAY
    assert rv.review_day_for(datetime(2026, 11, 3, 0, 4, tzinfo=UTC)) == DAY


def test_review_is_not_due_before_five_past_midnight_utc():
    assert rv.review_due(datetime(2026, 11, 3, 0, 4, 59, tzinfo=UTC), DAY - timedelta(days=1)) is None


def test_review_is_due_once_per_day_and_catches_up_after_a_gap():
    now = datetime(2026, 11, 3, 0, 5, tzinfo=UTC)
    assert rv.review_due(now, None) == DAY
    assert rv.review_due(now, DAY) is None
    # The trader was down for three days: the day before yesterday's review is the one still owed.
    assert rv.review_due(datetime(2026, 11, 5, 9, 0, tzinfo=UTC), date(2026, 11, 1)) == date(2026, 11, 4)


def test_review_rejects_naive_now():
    with pytest.raises(ValueError):
        rv.review([], {"w05_e03": BANKROLL}, now=datetime(2026, 11, 3, 12, 0))


# ----------------------------------------------------------------------------- statistics
def test_mean_ci_matches_the_bootstrap_bounds_and_is_seeded():
    values = [0.01, 0.02, -0.01, 0.03, 0.00, 0.02, 0.01]
    a = rv.mean_ci(values, n_resamples=500)
    b = rv.mean_ci(values, n_resamples=500)
    assert a == b  # same seed, same interval
    mean, lo, hi = a
    assert mean == pytest.approx(np.mean(values))
    assert lo < mean < hi


def test_mean_ci_gives_no_interval_below_five_values():
    mean, lo, hi = rv.mean_ci([0.1, 0.2, 0.3])
    assert mean == pytest.approx(0.2)
    assert math.isnan(lo) and math.isnan(hi)
    assert all(math.isnan(x) for x in rv.mean_ci([]))


def test_worst_exposure_is_the_worst_home_or_away_outcome_per_match():
    fills = [
        _bet(match_id="m1", selection="home", price=2.0, stake=100.0, status="filled"),  # home +100, away -100
        _bet(match_id="m2", selection="home", price=2.0, stake=100.0, status="filled"),
        _bet(match_id="m2", selection="away", price=2.0, stake=100.0, status="filled"),  # m2 hedged: 0
    ]
    exposure = rv.worst_exposure(fills)
    assert exposure["m1"] == pytest.approx(100.0)
    assert exposure["m2"] == pytest.approx(0.0)


def test_brier_scores_only_settled_bets_with_a_home_or_away_result():
    rows = [
        _bet(p_used=0.8, selection="home", result="home"),  # (0.8 - 1)^2 = 0.04
        _bet(p_used=0.8, selection="home", result="away"),  # (0.8 - 0)^2 = 0.64
        _bet(status="void", p_used=0.5),  # excluded
        _bet(status="filled", p_used=0.5),  # excluded: not settled
    ]
    score, n = rv.brier(rows)
    assert n == 2
    assert score == pytest.approx((0.04 + 0.64) / 2)


# ----------------------------------------------------------------------------- scorecard
def test_summary_counts_fills_fill_rate_and_settled_bets():
    rows = [
        _bet(status="settled"),
        _bet(status="void", match_id="m2"),
        _bet(status="filled", match_id="m3", clv=None, pnl=None),
        {**_bet(status="pending"), "price_filled": None},
        {**_bet(status="unfilled"), "price_filled": None},
    ]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["bets"] == 5
    assert s["filled"] == 3 and s["unfilled"] == 1 and s["pending"] == 1
    assert s["fill_rate"] == pytest.approx(3 / 4)
    assert s["settled_bets"] == 1 and s["void_bets"] == 1
    assert s["settled_matches"] == 1
    assert s["clv_n"] == 2  # the open bet has no CLV yet


def test_summary_clv_mean_and_pnl_total():
    rows = _many(6, clv=0.02, status="settled", stake=1_000.0, result="home", selection="home", price=2.0)
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["clv_mean"] == pytest.approx(0.02)
    assert s["pnl"] == pytest.approx(6 * 1_000.0)
    assert s["day_pnl"] == pytest.approx(6 * 1_000.0)  # all settled on DAY


def test_day_pnl_counts_only_bets_settled_on_the_review_day():
    rows = [
        _bet(stake=1_000.0, result="away", selection="home"),  # settled 2026-11-02 at 15:00: loss -1000
        _bet(stake=500.0, result="home", selection="home", settled_at=datetime(2026, 11, 1, 15, 0, tzinfo=UTC)),
    ]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["day_pnl"] == pytest.approx(-1_000.0)
    assert s["pnl"] == pytest.approx(-1_000.0 + 500.0)


def test_r3_progress_counts_bets_and_matches_left():
    rows = [_bet(match_id=f"m{i}", clv=0.01) for i in range(299)]  # 299 matches, 299 settled bets
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["r3_bets_left"] == 1
    assert s["r3_matches_left"] == 0
    assert s["r3_met"] is False  # one bet short


def test_r3_is_met_only_with_bets_matches_and_a_positive_clv_interval():
    rows = [_bet(match_id=f"m{i % 30}", clv=0.02 + 0.001 * (i % 7)) for i in range(300)]
    s = rv.summarise_variant("w05_e03", rows, bankroll=BANKROLL, review_day=DAY)
    assert s["settled_bets"] == 300 and s["settled_matches"] == 30
    assert s["clv_ci_lo"] > 0
    assert s["r3_met"] is True
    # Same volume but CLV centred on zero: the lower bound is negative, so R3 is not met.
    noisy = [_bet(match_id=f"m{i % 30}", clv=(0.05 if i % 2 else -0.05)) for i in range(300)]
    assert rv.summarise_variant("w05_e03", noisy, bankroll=BANKROLL, review_day=DAY)["r3_met"] is False


# ----------------------------------------------------------------------------- suspension
def test_variant_is_suspended_at_the_daily_loss_rule_and_not_below_it():
    limit = rv.KILL_DAILY_LOSS * BANKROLL  # 5% of 5,000,000 = 250,000
    breach = [_bet(stake=limit, result="away", selection="home")]  # loss of exactly the limit
    just_under = [_bet(stake=limit - 1, result="away", selection="home")]
    s_breach = rv.summarise_variant("w05_e03", breach, bankroll=BANKROLL, review_day=DAY)
    s_under = rv.summarise_variant("w03_e03", just_under, bankroll=BANKROLL, review_day=DAY)
    out = rv.suspensions([s_breach, s_under], review_day=DAY)
    assert set(out) == {"w05_e03"}
    assert "daily paper loss" in out["w05_e03"] and "2026-11-02" in out["w05_e03"]


def test_a_loss_on_another_day_does_not_suspend():
    old = [_bet(stake=300_000.0, result="away", selection="home",
                settled_at=datetime(2026, 11, 1, 15, 0, tzinfo=UTC))]
    s = rv.summarise_variant("w05_e03", old, bankroll=BANKROLL, review_day=DAY)
    assert rv.suspensions([s], review_day=DAY) == {}


# ----------------------------------------------------------------------------- ranking
def _summary(name, n, mean, lo, se=0.001):
    return {"variant": name, "clv_n": n, "clv_mean": mean, "clv_ci_lo": lo, "clv_se": se, "bankroll": BANKROLL,
            "day_pnl": 0.0}


def test_ranking_needs_thirty_clv_bets_and_picks_the_highest_lower_bound():
    summaries = [
        _summary("a", 100, 0.030, 0.010),
        _summary("b", 100, 0.020, 0.015),  # highest CI lower bound: primary
        _summary("c", 10, 0.500, 0.300),  # too few bets to rank, however good
    ]
    ranking = rv.rank(summaries, draws=2_000)
    assert ranking["eligible"] == ["a", "b"]
    assert ranking["primary"] == "b"
    assert set(ranking["p_best"]) == {"a", "b"}
    assert sum(ranking["p_best"].values()) == pytest.approx(1.0)


def test_thompson_p_best_favours_the_clearly_better_variant():
    summaries = [_summary("a", 500, 0.05, 0.04, se=0.002), _summary("b", 500, 0.00, -0.01, se=0.002)]
    p = rv.rank(summaries, draws=5_000)["p_best"]
    assert p["a"] > 0.99


def test_suspended_variant_cannot_be_primary_or_drawn():
    summaries = [_summary("a", 100, 0.03, 0.02), _summary("b", 100, 0.01, 0.005)]
    ranking = rv.rank(summaries, suspended={"a"}, draws=1_000)
    assert ranking["primary"] == "b"
    assert "a" not in ranking["p_best"]


def test_no_primary_when_nothing_is_eligible():
    ranking = rv.rank([_summary("a", 5, 0.1, 0.0)])
    assert ranking["primary"] is None and ranking["p_best"] == {}


# ----------------------------------------------------------------------------- review and state file
def test_review_produces_json_safe_state_and_reason(tmp_path):
    rows = _many(40, clv=0.02, status="settled", stake=1_000.0, result="home", selection="home", price=2.0)
    rows += [_bet(variant="market_only", status="unfilled", match_id="z")]
    state = rv.review(rows, {"w05_e03": BANKROLL, "market_only": BANKROLL}, now=NOW, n_resamples=200, draws=500)
    assert state["review_day"] == DAY.isoformat()
    assert state["primary"] == "w05_e03"  # 40 CLV bets, identical CLV: CI lower bound 0.02; market_only has none
    assert state["ranking"]["eligible"] == ["w05_e03"]
    assert state["primary_reason"]
    path = rv.write_state(state, tmp_path / "models" / "paper" / "primary.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert "NaN" not in path.read_text(encoding="utf-8")
    assert loaded["bets_seen"] == len(rows)
    assert rv.last_review_day(rv.load_state(path)) == DAY
    assert rv.suspended_names(rv.load_state(path)) == frozenset()


def test_review_names_the_primary_and_the_suspension(tmp_path):
    good = _many(40, clv=0.03, status="settled", stake=1_000.0, result="home", selection="home", price=2.0)
    bad = [_bet(variant="w03_e03", stake=300_000.0, result="away", selection="home", clv=-0.01)]
    state = rv.review(good + bad, {"w05_e03": BANKROLL, "w03_e03": BANKROLL}, now=NOW, n_resamples=200, draws=500)
    assert "w03_e03" in state["suspended"]
    assert state["primary"] == "w05_e03"
    assert "w03_e03" not in state["ranking"]["p_best"]


def test_load_state_is_empty_when_missing_or_corrupt(tmp_path):
    assert rv.load_state(tmp_path / "none.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert rv.load_state(bad) == {}
    assert rv.suspended_names({}) == frozenset()
    assert rv.last_review_day({"review_day": "not-a-date"}) is None


# ----------------------------------------------------------------------------- the trader honours the state
@pytest.mark.asyncio
async def test_suspended_variant_opens_no_new_risk_and_the_others_still_trade():
    variants = pt.build_variants()
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(s72.T0 - s72.MIN)], last={s72.MID: s72.T0})
    prob = s72.FakeProbability(p_model=0.6)

    report = await pt.run_cycle(
        session, feed, prob, variants, now=s72.T0, suspended={"w05_e03"}, primary="w03_e05"
    )
    assert report["intents_by_variant"].get("w05_e03", 0) == 0
    assert all(r.variant != "w05_e03" for r in session.rows)
    others = [v.name for v in variants if v.name != "w05_e03" and report["intents_by_variant"].get(v.name)]
    assert others, "the other variants keep trading"
    assert report["primary"] == "w03_e05" and report["suspended"] == ["w05_e03"]


# ----------------------------------------------------------------------------- daily hook and report
@pytest.mark.asyncio
async def test_daily_hook_runs_once_per_due_day(monkeypatch, tmp_path):
    calls = []

    async def fake_run_review(now=None, **kwargs):
        calls.append(now)
        return {}, "", None

    monkeypatch.setattr(rep, "run_review", fake_run_review)
    day = await rep.run_due_review(datetime(2026, 11, 3, 0, 4, tzinfo=UTC), None)
    assert day is None and calls == []
    day = await rep.run_due_review(datetime(2026, 11, 3, 0, 6, tzinfo=UTC), None)
    assert day == DAY and len(calls) == 1
    again = await rep.run_due_review(datetime(2026, 11, 3, 9, 0, tzinfo=UTC), day)
    assert again == DAY and len(calls) == 1


def test_report_renders_every_variant_and_the_suspension(tmp_path):
    good = _many(40, clv=0.02, status="settled", stake=1_000.0, result="home", selection="home", price=2.0)
    bad = [_bet(variant="w03_e03", stake=300_000.0, result="away", selection="home", clv=-0.01, match_id="m99")]
    state = rv.review(good + bad, {"w05_e03": BANKROLL, "w03_e03": BANKROLL}, now=NOW, n_resamples=200, draws=500)
    text = rep.render(state, good + bad, NOW)
    assert text.startswith("# PHOENIX paper trading report, 2026-11-03 12:00 UTC")
    for name in ("w05_e03", "w03_e03"):
        assert f"| {name} |" in text
    assert "Suspended: **w03_e03**" in text
    assert "Progress to R3" in text and "Best:" in text and "Worst:" in text


def test_report_handles_an_empty_ledger():
    state = rv.review([], {"w05_e03": BANKROLL}, now=NOW, n_resamples=100, draws=100)
    text = rep.render(state, [], NOW)
    assert "No paper bets yet." in text
    assert "No settled paper bets yet." in text
    assert "Primary: none" in text


def test_run_review_writes_report_and_state_only(monkeypatch, tmp_path):
    rows = _many(3, clv=0.01)

    class _NoRows:  # S76: the report also reads the results-missing list; an empty result is enough here
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def execute(self, *args, **kwargs):
            return _NoRows()

    class Ctx:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *exc):
            return False

    async def fake_load_rows(session):
        return rows

    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "get_session", lambda: Ctx())
    monkeypatch.setattr(rv, "load_rows", fake_load_rows)
    monkeypatch.setattr(rep, "bankrolls", lambda: {"w05_e03": BANKROLL})

    import asyncio

    state, text, path = asyncio.run(
        rep.run_review(NOW, state_path=tmp_path / "primary.json", report_dir=tmp_path / "reports")
    )
    assert path == tmp_path / "reports" / "20261103.md" and path.exists()
    assert json.loads((tmp_path / "primary.json").read_text(encoding="utf-8"))["bets_seen"] == 3
    assert "PHOENIX paper trading report" in text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["primary.json", "reports"]
