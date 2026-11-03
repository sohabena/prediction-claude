"""S83: no pre-match opinions in play. Fakes only, no database.

In play, a model variant may use a model probability only when the source is ``inplay`` (a fresh live score). Without
one it is priced as market-only: it may reduce or green up, never enter, add or flip. Pre-match decisions are unchanged,
and the switch happens at the off (``Candidate.in_play``: start <= now).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from backend.models.paper_bets import PaperBet
from strategy import model_edge
from strategy.model_edge import Book
from strategy.probability import Estimate
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession
from virtual_trading.paper_review import is_placebo

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
MID = "m1"
MIN = timedelta(minutes=1)
START_INPLAY = T0 - timedelta(hours=1)  # already off: in play
START_PREMATCH = T0 + timedelta(hours=2)  # not off: pre-match
NEW_RISK = {"enter", "add", "flip"}
MODEL_VARIANTS = [v for v in pt.build_variants() if not v.market_only]
MODEL_NAMES = {v.name for v in MODEL_VARIANTS}


# ---------------------------------------------------------------- fakes (as in test_s72_paper_trader)


class FakeFeed:
    def __init__(self, registry=(), prices=(), last=None, context=()):
        self.registry_rows = list(registry)
        self.prices = list(prices)
        self.last = dict(last or {})
        self.context_list = list(context)

    async def last_prices(self, now):
        return dict(self.last)

    async def registry(self, match_ids):
        return [dict(r) for r in self.registry_rows if r["match_id"] in set(match_ids)]

    async def price_rows(self, match_ids, since):
        return [dict(r) for r in self.prices if r["match_id"] in set(match_ids) and r["time"] >= since]

    async def context_rows(self, match_ids, since, now):
        return [dict(r) for r in self.context_list if r["match_id"] in set(match_ids) and since <= r["time"] <= now]

    async def results(self, match_ids):
        return {}


class FakeProbability:
    """Source follows ProbabilityService: ``inplay`` when a live score row is passed, else ``prematch``."""

    def __init__(self, p_model=0.9, p_market=None):
        self.p_model = p_model
        self.p_market = p_market

    def estimate(self, match, book, state=None, *, as_of):
        from training.market import band_prob_home

        p_market = self.p_market if self.p_market is not None else band_prob_home(book)
        return Estimate(
            p_model=self.p_model,
            p_market=p_market,
            source="inplay" if state else "prematch",
            has_model=True,
            features={},
            reason="",
        )


def _registry(start):
    return {
        "match_id": MID,
        "team_home": "Alpha",
        "team_away": "Beta",
        "competition": "Indian Premier League",
        "match_start_time": start,
        "is_simulated": False,
        "toss_winner": None,
        "toss_decision": None,
        "settled": False,
    }


def _price(t, back_home=2.2, lay_home=2.3, back_away=1.8, lay_away=1.9):
    return {"match_id": MID, "time": t, "back_home": back_home, "lay_home": lay_home,
            "back_away": back_away, "lay_away": lay_away, "source": "listing"}


def _state(t):
    """One fresh live score row, with the match_context columns the in-play path reads."""
    return {"match_id": MID, "time": t, "is_live": True, "score": 80, "wickets": 2, "overs": 10.0, "innings": 1,
            "balls_remaining": 60, "batting_team": "Alpha", "bowling_team": "Beta", "run_rate": 8.0,
            "req_run_rate": None, "match_format": "T20", "status": "live"}


def _seed(variant, side, selection, price, stake, decided_at):
    """A filled bet already in the ledger, as the trader left it."""
    return PaperBet(
        bet_id=None, variant=variant, match_id=MID, decided_at=decided_at, filled_at=decided_at + MIN,
        status="filled", side=side, selection=selection, team="Alpha", price_limit=price, price_filled=price,
        stake=stake, liability=stake, reason="enter", p_model=0.6, p_market=0.5, p_used=0.55, edge=0.1,
        source="prematch", in_play=False, score_state=None, book={},
    )


def _green_up_position(variant):
    """Home +70,000 and away +30,000 (balance +40,000 on home, outside the S90 no-trade band of 10,000). With the book
    at fair value (back 2.0), the hold's edge is 0, inside the hedge band, and the locked profit is positive: the only
    trade a variant can make is green-up."""
    old = T0 - timedelta(minutes=30)
    return [
        _seed(variant, "BACK", "home", 3.0, 50_000.0, old),
        _seed(variant, "BACK", "away", 3.0, 50_000.0, old),
        _seed(variant, "BACK", "home", 2.0, 20_000.0, old),
    ]


def _new_rows(session, decided_at):
    return [r for r in session.rows if r.decided_at == decided_at and r.status == "pending"]


# ---------------------------------------------------------------- model_edge: the rule itself


def test_model_edge_without_p_model_opens_no_new_risk():
    """Flat book, no model probability: the market alone gives no entry. With a model probability the same book
    enters, so the block comes from the missing model and not from the price."""
    prices = {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9}
    for variant in MODEL_VARIANTS:
        assert model_edge.decide(variant, None, 0.5, prices, Book(), now=0.0, last_trade_at=None) == []
        with_model = model_edge.decide(variant, 0.9, 0.5, prices, Book(), now=0.0, last_trade_at=None)
        assert [i.reason for i in with_model] == ["enter"], variant.name


def test_model_edge_without_p_model_still_reduces():
    """Favoured home, and the market prices the back below fair: reduce is allowed on the market alone. The book is
    not a consistent band (a reduce needs a lay with positive edge), so this checks the rule, not a replay case."""
    prices = {"back_home": 1.9, "lay_home": 1.95, "back_away": 2.2, "lay_away": 2.3}
    # Reduce is sized by Kelly on the bankroll (about 160,000 here, about 350,000 of balance change at this price). A
    # balance of 20,000 would be crossed (a flip, blocked in play); a balance of 410,000 is only shrunk: a reduce.
    position = Book(home_pnl=400_000.0, away_pnl=-10_000.0, trades=1)
    for variant in MODEL_VARIANTS:
        intents = model_edge.decide(variant, None, 0.45, prices, position, now=0.0, last_trade_at=None)
        assert [i.reason for i in intents] == ["reduce"], variant.name
        assert intents[0].side == "LAY" and intents[0].selection == "home"
    # A small balance would be crossed: the flip is blocked without a model (S83), and S90 cuts it to its close-out
    # part, a reduce towards flat. Never new risk.
    small = Book(home_pnl=10_000.0, away_pnl=-10_000.0, trades=1)
    for variant in MODEL_VARIANTS:
        intents = model_edge.decide(variant, None, 0.45, prices, small, now=0.0, last_trade_at=None)
        assert [i.reason for i in intents] in ([], ["reduce"]), variant.name


def test_model_edge_without_p_model_still_greens_up():
    prices = {"back_home": 2.0, "lay_home": 2.1, "back_away": 2.0, "lay_away": 2.1}
    position = Book(home_pnl=70_000.0, away_pnl=30_000.0, trades=3)  # balance outside the S90 no-trade band
    for variant in MODEL_VARIANTS:
        intents = model_edge.decide(variant, None, 0.5, prices, position, now=0.0, last_trade_at=None)
        assert [i.reason for i in intents] == ["green_up"], variant.name


# ---------------------------------------------------------------- cycle: in play, no fresh score


@pytest.mark.asyncio
async def test_in_play_without_state_refuses_new_risk_and_counts_each_model_variant():
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_INPLAY)], prices=[_price(T0 - MIN / 2)], last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["in_play"] is True
    assert report["matches"][0]["source"] == "prematch"
    # Every model variant was restricted once; the market-only control is not a model variant.
    assert report["guard_refusals"]["inplay_without_state"] == len(MODEL_VARIANTS)
    assert [r for r in session.rows if r.reason in NEW_RISK] == []
    assert session.rows == []


@pytest.mark.asyncio
async def test_in_play_without_state_allows_green_up_and_records_market_only():
    session = MemorySession(rows=[r for v in MODEL_VARIANTS for r in _green_up_position(v.name)])
    feed = FakeFeed(registry=[_registry(START_INPLAY)],
                    prices=[_price(T0 - timedelta(seconds=30), back_home=2.0, lay_home=2.1, back_away=2.0,
                                   lay_away=2.1)],
                    last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    placed = [r for r in session.rows if r.status == "pending" and not is_placebo(r.variant)]  # S85: model rows
    assert {r.variant for r in placed} == MODEL_NAMES
    assert {r.reason for r in placed} == {"green_up"}
    assert all(r.source == "market_only" and r.in_play is True and r.p_model is None for r in placed)
    assert report["guard_refusals"]["inplay_without_state"] == len(MODEL_VARIANTS)


@pytest.mark.asyncio
async def test_in_play_stale_score_row_is_treated_as_no_state():
    """A live score row older than the freshness limit is not a live state: the trader must not use it."""
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_INPLAY)], prices=[_price(T0 - MIN / 2)], last={MID: T0},
                    context=[_state(T0 - timedelta(minutes=10))])
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["source"] == "prematch"
    assert report["guard_refusals"]["inplay_without_state"] == len(MODEL_VARIANTS)
    assert [r for r in session.rows if r.reason in NEW_RISK] == []


# ---------------------------------------------------------------- cycle: in play with a fresh score


@pytest.mark.asyncio
async def test_in_play_with_fresh_score_and_inplay_source_trades_normally():
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_INPLAY)], prices=[_price(T0 - MIN / 2)], last={MID: T0},
                    context=[_state(T0 - timedelta(seconds=20))])
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["source"] == "inplay"
    assert "inplay_without_state" not in report["guard_refusals"]
    placed = [r for r in session.rows if r.status == "pending" and not is_placebo(r.variant)]  # S85: model rows
    assert {r.variant for r in placed} == MODEL_NAMES
    assert {r.reason for r in placed} == {"enter"}
    assert all(r.source == "inplay" and r.in_play is True and r.p_model == pytest.approx(0.9) for r in placed)


# ---------------------------------------------------------------- cycle: pre-match is unchanged


@pytest.mark.asyncio
async def test_pre_match_decisions_use_the_prematch_model_as_before():
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_PREMATCH)], prices=[_price(T0 - MIN / 2)], last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["in_play"] is False
    assert "inplay_without_state" not in report["guard_refusals"]
    placed = [r for r in session.rows if r.status == "pending" and not is_placebo(r.variant)]  # S85: model rows
    assert {r.variant for r in placed} == MODEL_NAMES
    assert {r.reason for r in placed} == {"enter"}
    assert all(r.source == "prematch" and r.in_play is False and r.p_model == pytest.approx(0.9) for r in placed)


# ---------------------------------------------------------------- cycle: the switch at the off


@pytest.mark.asyncio
async def test_switch_at_the_off_pre_match_model_then_market_only():
    """Before the off the pre-match model enters. From the off, with no live score, the same variants may not add."""
    session = MemorySession()
    variants = pt.build_variants()
    prob = FakeProbability(p_model=0.9)
    start = START_PREMATCH - timedelta(hours=1)  # the off
    feed = FakeFeed(registry=[_registry(start)], prices=[], last={})

    # Before the off: pre-match model, orders placed.
    before = start - 2 * MIN
    feed.prices.append(_price(before - MIN / 2))
    feed.last = {MID: before}
    await pt.run_cycle(session, feed, prob, variants, now=before)
    pre_rows = [r for r in _new_rows(session, before) if not is_placebo(r.variant)]  # S85: model rows
    assert pre_rows and {r.reason for r in pre_rows} == {"enter"}
    assert all(r.source == "prematch" and r.in_play is False for r in pre_rows)

    # Orders are filled 60 s after decision, still before the off.
    fill_at = before + MIN
    feed.prices.append(_price(fill_at - MIN / 4))
    feed.last = {MID: fill_at}
    await pt.run_cycle(session, feed, prob, variants, now=fill_at)
    assert all(r.status != "pending" for r in session.rows if r.decided_at <= before)

    # From the off (start <= now) with no live score: no new risk, and each model variant is counted once.
    after = start + timedelta(seconds=30)
    feed.prices.append(_price(after - MIN / 4))
    feed.last = {MID: after}
    report = await pt.run_cycle(session, feed, prob, variants, now=after)
    assert report["matches"][-1]["in_play"] is True
    assert report["guard_refusals"].get("inplay_without_state") == len(MODEL_VARIANTS)
    after_rows = [r for r in _new_rows(session, after) if not is_placebo(r.variant)]  # S85: model rows
    assert all(r.reason not in NEW_RISK for r in after_rows)
    assert all(r.source == "market_only" and r.in_play is True and r.p_model is None for r in after_rows)


@pytest.mark.asyncio
async def test_a_live_score_row_before_the_off_does_not_switch_to_the_in_play_model():
    """2026-11-03: the score feed sent a "live" 0/0 row 37 min before a 17:00 start; pre-match stays pre-match."""
    start = T0 + timedelta(minutes=30)
    feed = FakeFeed(registry=[_registry(start)], prices=[_price(T0 - MIN / 2)], last={MID: T0},
                    context=[_state(T0 - timedelta(seconds=20))])
    report = await pt.run_cycle(MemorySession(), feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)
    assert report["matches"][0]["in_play"] is False
    assert report["matches"][0]["source"] == "prematch"
