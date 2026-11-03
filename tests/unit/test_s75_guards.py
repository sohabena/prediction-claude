"""S75: decision-time trade guards, the LAY cap sizing, draw void, the settled registry and the paper_sanity check.

The pure guards are tested directly, ``run_cycle`` with the S72 fakes, the monitor with injected fakes. Nothing here
touches the database: the ledger is a MemorySession.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.paper_bets import PaperBet
from scripts import data_quality_monitor as dqm
from strategy import trade_guards as tg
from strategy.base import OrderIntent
from strategy.model_edge import Book, Variant, decide
from tests.unit import test_s55_data_quality as s55
from tests.unit import test_s72_paper_trader as s72
from virtual_trading import paper_ledger as ledger
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession
from virtual_trading.replay_feed import ReplayFeed

UTC = timezone.utc
T0 = s72.T0
MID = s72.MID
NOW_TS = T0.timestamp()
CFG = tg.GuardConfig()
FRESH = {"back_home": 5.0, "lay_home": 5.0, "back_away": 5.0, "lay_away": 5.0}
BOOK = s72._book_for(0.5)  # consistent: the band is [0.475, 0.525] around 0.5, the spreads are about 10%


def _intent(reason="enter", selection="home", side="BACK", price=2.0, stake=1000.0):
    return OrderIntent(side, selection, price, stake, reason)


def refusal(intent=None, **overrides):
    args = dict(book=BOOK, ages=FRESH, in_play=True, recent=(), now=NOW_TS, cfg=CFG)
    args.update(overrides)
    return tg.intent_refusal(intent or _intent(), **args)


def _row(**overrides):
    base = dict(
        variant="w03_e03", match_id=MID, decided_at=T0, filled_at=None, status="unfilled", side="BACK",
        selection="home", team="Alpha", price_limit=2.0, price_filled=None, stake=1000.0, liability=None,
        reason="add", p_model=None, p_market=None, p_used=0.55, edge=0.1, source="prematch", in_play=False,
        score_state=None, book={}, pnl=None, settled_at=None, result=None, clv=None, closing_price=None,
    )
    base.update(overrides)
    return PaperBet(**base)


# ----------------------------------------------------------------------------- book guards
def test_p_market_inside_the_band_passes_and_outside_or_missing_is_refused():
    assert tg.book_refusal(BOOK, 0.5) is None
    assert tg.book_refusal(BOOK, 0.6) == "p_market_outside_band"
    assert tg.book_refusal(BOOK, None) == "p_market_outside_band"


def test_no_band_when_a_bound_is_missing():
    book = {"back_home": 2.0, "lay_home": None, "back_away": None, "lay_away": 2.0}
    assert tg.book_refusal(book, 0.5) == "no_band"


def test_contradictory_book_is_refused_as_band_contradiction():
    # The S72 fixture book before this story: its backs sum below 1 and its lays above 1, so no band exists.
    book = {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.9, "lay_away": 2.0}
    assert tg.book_refusal(book, 0.5) == "band_contradiction"


def test_crossed_book_is_refused_for_either_runner():
    assert tg.book_refusal(dict(BOOK, back_away=2.2, lay_away=2.1), 0.5) == "crossed_book"
    assert tg.book_refusal(dict(BOOK, back_home=2.1, lay_home=2.1), 0.5) == "crossed_book"  # equal is not back < lay


def test_spread_is_lay_minus_back_over_back_and_none_without_both_prices():
    assert tg.spread({"back_home": 2.0, "lay_home": 2.3}, "home") == pytest.approx(0.15)
    assert tg.spread({"back_home": 2.0}, "home") is None


@pytest.mark.asyncio
async def test_crossed_book_blocks_every_trade_on_the_match_including_a_reduce():
    # The book-level refusals run before any decision: with an open position a reduce is still refused.
    session = MemorySession([_row(status="filled", price_filled=2.0, filled_at=T0 - timedelta(minutes=5),
                                  decided_at=T0 - timedelta(minutes=6), reason="enter", stake=1000.0)])
    feed = _trader_feed(dict(BOOK, back_away=2.2, lay_away=2.1))
    report = await pt.run_cycle(session, feed, s72.FakeProbability(p_model=0.9, p_market=0.5),
                                pt.build_variants(), now=T0)
    assert report["intents"] == 0
    assert report["guard_refusals"] == {"crossed_book": 1}


# ----------------------------------------------------------------------------- new-risk guards
def test_stale_field_refuses_new_risk_at_60_seconds_in_play():
    assert refusal(ages=dict(FRESH, back_home=60.0), in_play=True) == "stale_book"
    assert refusal(ages=dict(FRESH, back_home=59.0), in_play=True) is None


def test_stale_threshold_before_the_start_is_15_minutes():
    assert refusal(ages=dict(FRESH, lay_away=61.0), in_play=False) is None
    assert refusal(ages=dict(FRESH, lay_away=899.0), in_play=False) is None
    assert refusal(ages=dict(FRESH, lay_away=900.0), in_play=False) == "stale_book"


def test_stale_field_is_judged_on_every_field_present_in_the_book():
    # A stale lay on the other runner refuses a bet on this runner: the decision read the whole book.
    assert refusal(_intent(selection="home"), ages=dict(FRESH, lay_away=120.0), in_play=True) == "stale_book"


def test_spread_over_the_maximum_refuses_new_risk_on_that_runner_only():
    wide_home = dict(BOOK, lay_home=2.4)  # (2.4 - 1.905) / 1.905 = 26%
    assert refusal(book=wide_home) == "spread_too_wide"
    assert refusal(_intent(reason="add"), book=wide_home) == "spread_too_wide"
    assert refusal(_intent(selection="away"), book=wide_home) is None  # the away runner is not wide
    assert refusal(book=BOOK, cfg=tg.GuardConfig(max_spread=0.10)) == "spread_too_wide"  # 10.5% over 10%


def test_reduce_and_green_up_pass_every_new_risk_guard():
    stale = dict(FRESH, back_home=500.0)
    wide = dict(BOOK, lay_home=2.6)  # 36%
    busy = [(NOW_TS - 10 * i, "enter") for i in range(5)] + [(NOW_TS - 30, "flip")]
    for reason in ("reduce", "green_up"):
        assert refusal(_intent(reason=reason), ages=stale, book=wide, recent=busy) is None


def test_flip_within_five_minutes_of_the_last_flip_is_refused():
    assert refusal(_intent(reason="flip"), recent=[(NOW_TS - 200, "flip")]) == "flip_cooldown"
    assert refusal(_intent(reason="flip"), recent=[(NOW_TS - 301, "flip")]) is None
    # The last flip counts, not the first
    assert refusal(_intent(reason="flip"), recent=[(NOW_TS - 400, "flip"), (NOW_TS - 100, "flip")]) == "flip_cooldown"


def test_flip_cooldown_applies_only_to_flips():
    recent = [(NOW_TS - 60, "flip")]
    assert refusal(_intent(reason="enter"), recent=recent) is None
    assert refusal(_intent(reason="add"), recent=recent) is None
    assert refusal(_intent(reason="reduce"), recent=recent) is None


def test_at_most_three_decisions_per_ten_minutes_for_new_risk():
    recent = [(NOW_TS - 300, "add"), (NOW_TS - 200, "reduce"), (NOW_TS - 100, "add")]
    assert refusal(_intent(reason="add"), recent=recent) == "trade_rate"  # reduce decisions count toward the budget
    assert refusal(_intent(reason="enter"), recent=recent[:2]) is None


def test_rate_window_rolls_forward_and_the_first_decision_leaves_at_ten_minutes():
    decisions = [(NOW_TS, "add"), (NOW_TS + 100, "add"), (NOW_TS + 200, "add")]

    def at(seconds):
        return tg.intent_refusal(_intent(), book=BOOK, ages=FRESH, in_play=True, recent=decisions,
                                 now=NOW_TS + seconds, cfg=CFG)

    assert at(250) == "trade_rate"
    assert at(599) == "trade_rate"  # the decision at 0 is still in the window (0, 599]
    assert at(600) is None  # and has left it at 600
    assert at(700) is None


def test_guard_config_rejects_negative_settings():
    with pytest.raises(ValueError):
        tg.GuardConfig(max_spread=-0.1)
    with pytest.raises(ValueError):
        tg.GuardConfig(max_trades_per_window=-1)
    with pytest.raises(ValueError):
        tg.GuardConfig(flip_cooldown_s=-5.0)


# ----------------------------------------------------------------------------- LAY sizing at the limit price
def test_lay_stake_keeps_the_worst_case_within_the_cap_at_its_limit_price():
    # A LAY bet's liability grows with its fill price, and the fill may be as high as its limit. Sized at the decision
    # price alone (2.10) the stake would put the worst case at about 133,000 if the fill came at the limit (2.4625).
    # S90: no no-trade band here (it is 40% of max_trade_risk), and in play, so the full 100,000 cap applies.
    variant = Variant("cap", w=0.0, min_edge=0.03, max_trade_risk=1e12, no_trade_share=0.0, market_only=True)
    prices = {"back_home": 2.0, "lay_home": 2.1, "back_away": 1.9, "lay_away": 2.0}
    intents = decide(variant, None, 0.4, prices, Book(), now=NOW_TS, last_trade_at=None, in_play=True)
    assert len(intents) == 1
    intent = intents[0]
    assert (intent.side, intent.selection, intent.reason) == ("LAY", "home", "enter")
    assert intent.price_limit == pytest.approx((1.0 - 0.015) / 0.4)
    assert intent.stake * (intent.price_limit - 1.0) <= variant.max_match_loss + 1e-6


# ----------------------------------------------------------------------------- run_cycle: guards in the trader
def _live_state(t):
    """A fresh live score row, so an in-play decision is priced by the in-play path (S83)."""
    return {"match_id": MID, "time": t, "is_live": True, "score": 80, "wickets": 2, "overs": 10.0, "innings": 1,
            "balls_remaining": 60, "batting_team": "Alpha", "bowling_team": "Beta", "run_rate": 8.0,
            "req_run_rate": None, "match_format": "T20", "status": "live"}


def _trader_feed(price_kwargs, start=T0 + timedelta(hours=2), price_time=T0 - s72.MIN, context=()):
    return s72.FakeFeed(
        registry=[s72._registry(start=start)],
        prices=[s72._price(price_time, **price_kwargs)],
        last={MID: T0},
        context=context,
    )


@pytest.mark.asyncio
async def test_cycle_refuses_a_book_with_no_band_on_every_variant_and_counts_it():
    session = MemorySession()
    one_sided = {"back_home": 2.0, "lay_home": None, "back_away": None, "lay_away": 2.0}
    feed = _trader_feed(one_sided)
    report = await pt.run_cycle(session, feed, s72.FakeProbability(p_model=0.9, p_market=0.5),
                                pt.build_variants(), now=T0)
    assert report["intents"] == 0 and session.rows == []
    assert report["guard_refusals"] == {"no_band": 1}  # one refusal for the match, whatever the variant count


@pytest.mark.asyncio
async def test_cycle_with_a_contradictory_book_has_no_market_probability_and_places_nothing():
    session = MemorySession()
    feed = _trader_feed({"back_home": 2.2, "lay_home": 2.3, "back_away": 1.9, "lay_away": 2.0})
    report = await pt.run_cycle(session, feed, s72.FakeProbability(p_model=0.9), pt.build_variants(), now=T0)
    assert report["intents"] == 0 and session.rows == []
    assert report["skipped_matches"] == {"no_market": 1}  # band None: the service gives no market probability


@pytest.mark.asyncio
async def test_in_play_stale_book_refuses_new_risk_and_a_fresh_book_does_not():
    variants = pt.build_variants()
    in_play_start = T0 - timedelta(minutes=30)
    live = [_live_state(T0 - timedelta(seconds=20))]
    stale = _trader_feed(s72._book_for(0.5), start=in_play_start, price_time=T0 - timedelta(seconds=90), context=live)
    report = await pt.run_cycle(s72.FakeSession(), stale, s72.FakeProbability(p_model=0.9), variants, now=T0)
    assert report["intents"] == 0
    assert report["guard_refusals"].get("stale_book", 0) >= 1
    assert report["matches"][0]["in_play"] is True

    fresh = _trader_feed(s72._book_for(0.5), start=in_play_start, price_time=T0 - timedelta(seconds=10), context=live)
    report = await pt.run_cycle(s72.FakeSession(), fresh, s72.FakeProbability(p_model=0.9), variants, now=T0)
    assert report["intents"] > 0
    assert "stale_book" not in report["guard_refusals"]


@pytest.mark.asyncio
async def test_recent_decisions_rate_limit_one_variant_and_not_the_others():
    session = MemorySession([_row(decided_at=T0 - timedelta(seconds=s), status="unfilled") for s in (300, 200, 100)])
    feed = _trader_feed(s72._book_for(0.5))
    report = await pt.run_cycle(session, feed, s72.FakeProbability(p_model=0.9), pt.build_variants(), now=T0)
    assert "w03_e03" not in report["intents_by_variant"]
    assert report["guard_refusals"].get("trade_rate", 0) >= 1
    assert report["intents_by_variant"].get("w05_e03", 0) >= 1


def test_price_cache_reports_each_field_age_and_book_at_is_unchanged():
    rows = [
        s72._price(T0 - timedelta(seconds=90), back_home=2.0, lay_home=2.1, back_away=1.9, lay_away=2.0),
        s72._price(T0 - timedelta(seconds=10), back_home=None, lay_home=None, back_away=None, lay_away=2.05,
                   source="match_page"),
    ]
    cache = pt.PriceCache(rows)
    book, ages = cache.book_and_ages_at(MID, T0)
    assert book == {"back_home": 2.0, "lay_home": 2.1, "back_away": 1.9, "lay_away": 2.05}
    assert ages == {"back_home": 90, "lay_home": 90, "back_away": 90, "lay_away": 10}
    assert cache.book_at(MID, T0) == book


# ----------------------------------------------------------------------------- draw void and the settled registry
def test_a_draw_resolves_to_void():
    result = {"winner": "Alpha", "result_type": "draw", "team_home": "Alpha", "team_away": "Beta"}
    assert ledger.resolve_outcome(result) == "void"
    assert ledger.resolve_outcome({**result, "result_type": " Draw "}) == "void"


@pytest.mark.asyncio
async def test_a_filled_bet_on_a_drawn_match_is_voided_with_zero_pnl():
    row = _row(status="filled", price_filled=2.0, filled_at=T0, stake=1000.0, side="BACK", selection="home")
    session = MemorySession([row])
    result = {"winner": "Alpha", "result_type": "draw", "team_home": "Alpha", "team_away": "Beta"}
    changed = await ledger.settle(session, {MID: result}, T0 + timedelta(hours=3))
    assert len(changed) == 1
    assert (row.status, row.result, row.pnl) == ("void", "void", 0.0)


def test_settled_result_types_are_the_registry_rule():
    assert ledger.SETTLED_RESULT_TYPES == frozenset({"win", "tie", "no_result", "draw", "abandoned"})
    assert "draw" in ledger.VOID_RESULT_TYPES


@pytest.mark.asyncio
async def test_database_registry_query_marks_every_settled_result_type():
    class Capture:
        sql = ""

        async def execute(self, stmt, params=None):
            Capture.sql = str(stmt)

            class Result:
                def mappings(self):
                    return self

                def all(self):
                    return []

            return Result()

    await pt.DbFeed(Capture()).registry([MID])
    for result_type in ("win", "tie", "no_result", "draw", "abandoned"):
        assert f"'{result_type}'" in Capture.sql
    assert "m.result_type IN (" in Capture.sql


@pytest.mark.asyncio
async def test_replay_registry_settles_on_each_decided_result_type_only():
    registry = [{"match_id": MID, "team_home": "A", "team_away": "B", "competition": "T20",
                 "match_start_time": T0, "is_simulated": False, "toss_winner": None, "toss_decision": None}]
    for result_type, settled in (("win", True), ("draw", True), ("tie", True), ("no_result", True),
                                 ("abandoned", True), ("unresolved", False)):
        result = {"match_id": MID, "winner": None, "loser": None, "result_type": result_type,
                  "team_home": "A", "team_away": "B", "completed_at": T0 - timedelta(minutes=1)}
        feed = ReplayFeed(prices=[], registry=registry, context=[], results=[result], end=T0 + timedelta(hours=1))
        feed.seek(T0)
        rows = await feed.registry([MID])
        assert rows[0]["settled"] is settled, result_type


# ----------------------------------------------------------------------------- monitor: paper_sanity
def _sanity(rows):
    return s55.by_name(s55.run(rows))["paper_sanity"]


def test_paper_sanity_is_ok_on_a_quiet_day_and_runs_last():
    assert _sanity(s55.healthy_rows())["status"] == "ok"
    assert dqm.CHECKS[-2] is dqm.check_paper_sanity  # S86 appended start_time_consistency after it


def test_control_placing_more_than_two_trades_in_a_day_fails():
    rows = s55.healthy_rows()
    rows["paper_sanity_trades"] = [{"variant": "market_only", "match_id": "m1", "trades": 3}]
    check = _sanity(rows)
    assert check["status"] == "fail"
    assert "market_only placed 3 trades in 24 h (max 2)" in check["detail"]


def test_control_with_two_trades_is_still_ok_and_other_variants_do_not_count_toward_it():
    rows = s55.healthy_rows()
    rows["paper_sanity_trades"] = [
        {"variant": "market_only", "match_id": "m1", "trades": 2},
        {"variant": "w05_e03", "match_id": "m1", "trades": 9},
    ]
    assert _sanity(rows)["status"] == "ok"


def test_filled_bet_with_a_probability_gap_above_twenty_points_warns():
    rows = s55.healthy_rows()
    rows["paper_sanity_edges"] = [  # p 0.75 at 2.0 (implied 0.50): a gap of 0.25
        {"variant": "w05_e03", "match_id": "m1", "side": "BACK", "selection": "home", "edge": 0.5, "price_filled": 2.0,
         "p_used": 0.75}
    ]
    check = _sanity(rows)
    assert check["status"] == "warn"  # reviewed, not a failure: late chases can genuinely differ by 20 points
    assert "probability gap over 20%" in check["detail"]


def test_a_large_edge_at_long_odds_with_a_small_probability_gap_is_not_implausible():
    """2026-11-03, Lumbini v Pokhara: p 0.085 at 23.0 (implied 0.043) is an edge of 0.95 but a gap of 0.04."""
    rows = s55.healthy_rows()
    rows["paper_sanity_edges"] = [
        {"variant": "w05_e03", "match_id": "m1", "side": "BACK", "selection": "away", "edge": 0.954,
         "price_filled": 23.0, "p_used": 0.085}
    ]
    assert _sanity(rows)["status"] == "ok"


def test_more_than_the_per_match_limit_of_trades_by_one_variant_fails():
    """Limit 35: a 30-ticket budget (operator, 2026-11-03) plus reduces, which the budget never blocks."""
    limit = dqm.PAPER_SANITY_MATCH_TRADES_MAX
    rows = s55.healthy_rows()
    rows["paper_sanity_trades"] = [{"variant": "w07_e06", "match_id": "m9", "trades": limit + 1}]
    check = _sanity(rows)
    assert check["status"] == "fail"
    assert f"w07_e06 m9 ({limit + 1})" in check["detail"]
    rows["paper_sanity_trades"] = [{"variant": "w07_e06", "match_id": "m9", "trades": limit}]
    assert _sanity(rows)["status"] == "ok"


def test_paper_sanity_queries_are_selects_over_the_ledger_for_24_hours():
    for name in ("paper_sanity_trades", "paper_sanity_edges"):
        sql = dqm.SQL[name].strip().upper()
        assert sql.startswith("SELECT") and "PAPER_BETS" in sql and "24 HOURS" in sql, name
