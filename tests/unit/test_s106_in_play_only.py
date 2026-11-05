"""S106: no new risk before the off. Fakes only, no database.

Before the off a strategy variant (not the market-only control, not a placebo, not a market-dynamics variant) gets no
model probability, as in the S83 in-play path: it may reduce or green up a pre-match position, never enter, add or
flip. Each such variant-match is counted as ``prematch_off``. The market-only control keeps its probability, the
placebos copy only reductions, and ``PREMATCH_NEW_RISK = True`` restores pre-match trading. In play nothing changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from backend.models.paper_bets import PaperBet
from strategy.probability import Estimate
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession
from virtual_trading.paper_review import is_placebo

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
MID = "m1"
MIN = timedelta(minutes=1)
START_INPLAY = T0 - timedelta(hours=1)  # already off: in play
START_PREMATCH = T0 + timedelta(hours=2)  # not off: pre-match
NEW_RISK = {"enter", "add", "flip"}
# Strategy variants: the ones S106 closes before the off. The control and the S97 dynamics variants are not among them.
STRATEGY = [v for v in pt.build_variants() if not v.market_only and not v.placebo and v.name not in pt.DYNAMICS_VARIANTS]
STRATEGY_NAMES = {v.name for v in STRATEGY}
REFERENCE = "w05_e03"


# ---------------------------------------------------------------- fakes (as in test_s83_inplay_state)


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

    def __init__(self, p_model=0.9):
        self.p_model = p_model

    def estimate(self, match, book, state=None, *, as_of):
        from training.market import band_prob_home

        return Estimate(
            p_model=self.p_model,
            p_market=band_prob_home(book),
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
    """A filled bet already in the ledger, as the trader left it (placed before the off)."""
    return PaperBet(
        bet_id=None, variant=variant, match_id=MID, decided_at=decided_at, filled_at=decided_at + MIN,
        status="filled", side=side, selection=selection, team="Alpha", price_limit=price, price_filled=price,
        stake=stake, liability=stake, reason="enter", p_model=0.6, p_market=0.5, p_used=0.55, edge=0.1,
        source="prematch", in_play=False, score_state=None, book={},
    )


def _green_up_position(variant):
    """Home +70,000 and away +30,000 (balance +40,000, outside the no-trade band). At fair value (back 2.0) the hold's
    edge is 0, so the only trade a variant can make is green-up."""
    old = T0 - timedelta(minutes=30)
    return [
        _seed(variant, "BACK", "home", 3.0, 50_000.0, old),
        _seed(variant, "BACK", "away", 3.0, 50_000.0, old),
        _seed(variant, "BACK", "home", 2.0, 20_000.0, old),
    ]


def _placed(session):
    return [r for r in session.rows if r.status == "pending"]


# ---------------------------------------------------------------- the constant


def test_prematch_new_risk_is_off_by_default():
    """Operator decision, 2026-11-05."""
    assert pt.PREMATCH_NEW_RISK is False


# ---------------------------------------------------------------- cycle: pre-match, no new risk


@pytest.mark.asyncio
async def test_prematch_strategy_variants_place_nothing_and_count_prematch_off():
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_PREMATCH)], prices=[_price(T0 - MIN / 2)], last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["in_play"] is False
    assert report["guard_refusals"]["prematch_off"] == len(STRATEGY)
    assert "inplay_without_state" not in report["guard_refusals"]
    assert [r for r in session.rows if r.variant in STRATEGY_NAMES] == []
    assert [r for r in session.rows if is_placebo(r.variant)] == []


@pytest.mark.asyncio
async def test_prematch_strategy_variants_trade_again_when_the_switch_is_on(monkeypatch):
    """The same book with the constant True: the strategy variants enter on the pre-match model, as before S106."""
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_PREMATCH)], prices=[_price(T0 - MIN / 2)], last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert "prematch_off" not in report["guard_refusals"]
    placed = [r for r in session.rows if r.status == "pending" and r.variant in STRATEGY_NAMES]
    assert {r.variant for r in placed} == STRATEGY_NAMES
    assert {r.reason for r in placed} == {"enter"}
    assert all(r.source == "prematch" and r.in_play is False and r.p_model == pytest.approx(0.9) for r in placed)


@pytest.mark.asyncio
async def test_prematch_green_up_is_still_allowed_on_an_existing_position():
    """Before the off a strategy variant with a pre-match position may still green up: reduce, never new risk."""
    session = MemorySession(rows=[r for v in STRATEGY for r in _green_up_position(v.name)])
    feed = FakeFeed(registry=[_registry(START_PREMATCH)],
                    prices=[_price(T0 - timedelta(seconds=30), back_home=2.0, lay_home=2.1, back_away=2.0,
                                   lay_away=2.1)],
                    last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    placed = [r for r in _placed(session) if r.variant in STRATEGY_NAMES]
    assert {r.variant for r in placed} == STRATEGY_NAMES
    assert {r.reason for r in placed} == {"green_up"}
    assert all(r.source == "market_only" and r.in_play is False and r.p_model is None for r in placed)
    assert report["guard_refusals"]["prematch_off"] == len(STRATEGY)


@pytest.mark.asyncio
async def test_prematch_market_only_control_keeps_its_probability():
    """The control is not a strategy variant: before the off it keeps the model probability it always had (recorded
    with source prematch, not market_only) and may green up on its own pre-match position. It is not counted as
    prematch_off."""
    session = MemorySession(rows=_green_up_position("market_only"))
    feed = FakeFeed(registry=[_registry(START_PREMATCH)],
                    prices=[_price(T0 - timedelta(seconds=30), back_home=2.0, lay_home=2.1, back_away=2.0,
                                   lay_away=2.1)],
                    last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    control = [r for r in _placed(session) if r.variant == "market_only"]
    assert {r.reason for r in control} == {"green_up"}
    assert all(r.source == "prematch" and r.in_play is False and r.p_model == pytest.approx(0.9) for r in control)
    assert report["guard_refusals"]["prematch_off"] == len(STRATEGY)


@pytest.mark.asyncio
async def test_prematch_placebos_open_no_new_risk_but_copy_reductions(monkeypatch):
    """The reference's pre-match green-up is copied by the placebos only as a reduction: a flat placebo may not enter.
    With the constant True the same copy opens risk, so the placebo guard is what stops it."""
    session = MemorySession(rows=_green_up_position(REFERENCE))
    feed = FakeFeed(registry=[_registry(START_PREMATCH)],
                    prices=[_price(T0 - timedelta(seconds=30), back_home=2.0, lay_home=2.1, back_away=2.0,
                                   lay_away=2.1)],
                    last={MID: T0})
    await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0,
                       reference=REFERENCE)
    reference_rows = [r for r in _placed(session) if r.variant == REFERENCE]
    assert {r.reason for r in reference_rows} == {"green_up"}
    assert [r for r in _placed(session) if is_placebo(r.variant)] == []

    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)
    session = MemorySession(rows=_green_up_position(REFERENCE))
    feed = FakeFeed(registry=[_registry(START_PREMATCH)],
                    prices=[_price(T0 - timedelta(seconds=30), back_home=2.0, lay_home=2.1, back_away=2.0,
                                   lay_away=2.1)],
                    last={MID: T0})
    await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0,
                       reference=REFERENCE)
    assert [r for r in _placed(session) if is_placebo(r.variant)] != []


# ---------------------------------------------------------------- cycle: in play is unchanged


@pytest.mark.asyncio
async def test_in_play_with_fresh_score_still_trades_and_is_not_counted_as_prematch_off():
    session = MemorySession()
    feed = FakeFeed(registry=[_registry(START_INPLAY)], prices=[_price(T0 - MIN / 2)], last={MID: T0},
                    context=[_state(T0 - timedelta(seconds=20))])
    report = await pt.run_cycle(session, feed, FakeProbability(p_model=0.9), pt.build_variants(), now=T0)

    assert report["matches"][0]["source"] == "inplay"
    assert "prematch_off" not in report["guard_refusals"]
    placed = [r for r in session.rows if r.status == "pending" and r.variant in STRATEGY_NAMES]
    assert {r.variant for r in placed} == STRATEGY_NAMES
    assert {r.reason for r in placed} == {"enter"}
    assert all(r.source == "inplay" and r.in_play is True and r.p_model == pytest.approx(0.9) for r in placed)
