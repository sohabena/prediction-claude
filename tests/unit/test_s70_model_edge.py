"""S70: model-edge strategy (strategy.model_edge). Hand-computed numbers; pure maths, no I/O."""
from __future__ import annotations

import random

import pytest

from shared.schemas import BettingAction
from strategy.base import MatchedBet
from strategy.model_edge import (
    AWAY,
    BACK,
    HOME,
    LAY,
    REASONS,
    Book,
    Variant,
    blend_probability,
    decide,
)
from virtual_trading.portfolio import outcome_pnls, worst_loss

CAP = 100_000.0
NOW = 1_000.0

# Variant used for the hand-computed sequences. Bankroll 1,000,000 keeps the Kelly stakes in the tens of thousands,
# so the cap, not the bankroll, decides the size once the book is large.

def _limit_ok(intent, decision_price):
    """The limit is never better than the decision price: a BACK accepts the same or a lower price, a LAY the same or
    a higher one (half the minimum edge must remain)."""
    if intent.side == BACK:
        return intent.price_limit <= decision_price + 1e-9
    return intent.price_limit >= decision_price - 1e-9

V = Variant(name="test", w=0.5, min_edge=0.03, bankroll=1_000_000.0, max_trade_risk=1e12)  # sizing tests: no per-trade cap

# Prices used by the sequence. Edges at p_home = 0.6: BACK home 0.14, LAY away 0.08, LAY home -0.20, BACK away -0.12.
P1 = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}


def _fill(side: str, selection: str, price: float, stake: float) -> MatchedBet:
    return MatchedBet(
        side=side, selection=selection, price=price, stake=stake,
        placed_at=0.0, matched_at=0.0, fill_index=0, reason="test",
    )


def _run(variant, p_model, p_market, prices, book, *, now=NOW, last_trade_at=None):
    return decide(variant, p_model, p_market, prices, book, now=now, last_trade_at=last_trade_at)


# ----------------------------------------------------------------------------- variant and blend
def test_variant_rejects_out_of_range_settings():
    with pytest.raises(ValueError):
        Variant(name="bad", w=1.5, min_edge=0.03)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=-0.01)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=0.03, max_match_loss=0.0)


def test_variant_defaults_match_the_story():
    v = Variant(name="d", w=0.5, min_edge=0.03, max_trade_risk=1e12)
    assert (v.kelly_fraction, v.max_match_loss, v.bankroll, v.hedge_edge) == (0.25, 100_000, 5_000_000, 0.01)
    assert (v.max_trades_per_match, v.cooldown_s, v.market_only) == (40, 120, False)


def test_blend_hand_computed():
    # 0.5 * 0.8 + 0.5 * 0.4 = 0.6 ; 0.3 * 0.8 + 0.7 * 0.4 = 0.24 + 0.28 = 0.52
    assert blend_probability(V, 0.8, 0.4) == pytest.approx(0.6)
    w03 = Variant(name="w03", w=0.3, min_edge=0.03, max_trade_risk=1e12)
    assert blend_probability(w03, 0.8, 0.4) == pytest.approx(0.52)
    # market_only ignores the model; no model falls back to the market
    control = Variant(name="ctl", w=0.5, min_edge=0.03, market_only=True, max_trade_risk=1e12)
    assert blend_probability(control, 0.9, 0.4) == pytest.approx(0.4)
    assert blend_probability(V, None, 0.4) == pytest.approx(0.4)


# ----------------------------------------------------------------------------- book convention
def test_book_matches_portfolio_convention():
    from shared.schemas import BettingAction as A

    # BACK home 35,000 at 1.9: home wins +31,500, away wins -35,000 (portfolio.outcome_pnls agrees)
    book = Book()
    book.apply(_fill(BACK, HOME, 1.9, 35_000))
    assert outcome_pnls(A.BACK_HOME_SM, 1.9, 35_000) == pytest.approx((31_500, -35_000))
    assert (book.home_pnl, book.away_pnl) == pytest.approx((31_500, -35_000))

    # LAY home 25,000 at 2.2: liability 30,000; home wins -30,000, away wins +25,000
    lay = Book()
    lay.apply(_fill(LAY, HOME, 2.2, 25_000))
    assert outcome_pnls(A.LAY_HOME_SM, 2.2, 25_000) == pytest.approx((-30_000, 25_000))
    assert (lay.home_pnl, lay.away_pnl) == pytest.approx((-30_000, 25_000))
    assert lay.worst() == pytest.approx(worst_loss((-30_000, 25_000)))
    assert lay.worst() == pytest.approx(30_000)

    # BACK away 65,000 at 2.2: away wins +78,000, home wins -65,000
    away = Book()
    away.apply(_fill(BACK, AWAY, 2.2, 65_000))
    assert outcome_pnls(A.BACK_AWAY_SM, 2.2, 65_000) == pytest.approx((-65_000, 78_000))
    assert (away.home_pnl, away.away_pnl) == pytest.approx((-65_000, 78_000))


# ----------------------------------------------------------------------------- basic rules
def test_no_edge_no_trade():
    # p_home = 0.5: BACK at 1.9 is -0.05, LAY at 2.0 is 0.0; both sides. Nothing clears min_edge 0.03.
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 1.9, "lay_away": 2.0}
    assert _run(V, 0.5, 0.5, prices, Book()) == []


def test_positive_edge_backs_at_back_price():
    # p = 0.6, back home 1.9: edge 0.6 * 1.9 - 1 = 0.14. Kelly: 0.14 / 0.9 = 0.15556 of bankroll,
    # times 0.25 = 0.03889 * 1,000,000 = 38,888.9 -> round down to 35,000. Price limit = decision price 1.9.
    intents = _run(V, 0.6, 0.6, P1, Book())
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.reason) == (BACK, HOME, "enter")
    assert i.stake == pytest.approx(35_000)
    assert _limit_ok(i, 1.9)


def test_lay_maths_hand_checked():
    # p_home = 0.4, LAY home at 2.2 (the only price on the book): edge = 0.6 - 0.4 * 1.2 = 0.12 per unit.
    # Kelly liability = 0.25 * 0.12 * 1,000,000 = 30,000; stake = 30,000 / 1.2 = 25,000 (a round unit).
    # Book: home wins -25,000 * 1.2 = -30,000; away wins +25,000. Expected P&L change = 0.4 * -30,000 + 0.6 * 25,000
    # = 3,000 = 25,000 * 0.12, which is the edge check.
    intents = _run(V, 0.4, 0.4, {"lay_home": 2.2}, Book())
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.reason) == (LAY, HOME, "enter")
    assert i.stake == pytest.approx(25_000)
    assert _limit_ok(i, 2.2)
    book = Book()
    book.apply(_fill(i.side, i.selection, 2.2, i.stake))
    assert (book.home_pnl, book.away_pnl) == pytest.approx((-30_000, 25_000))
    expected_change = 0.4 * book.home_pnl + 0.6 * book.away_pnl
    assert expected_change == pytest.approx(25_000 * 0.12)


def test_negative_edge_on_held_side_lays_to_reduce():
    # Held: BACK home 100,000 at 1.9 -> book (90,000, -100,000), balance 190,000 (favours home).
    # p_home falls to 0.4: BACK home edge = 0.4 * 1.9 - 1 = -0.24 <= -0.01, so LAY home at 2.0.
    # LAY edge = 1 - 0.4 * 2.0 = 0.2; Kelly risk 0.25 * 0.2 * 1,000,000 = 50,000; stake = 50,000 / 1.0 = 50,000.
    # Balance moves by -2 * 50,000 = -100,000, to 90,000: still home, so it is a reduce.
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, 0.4, 0.4, P1, held)
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.stake, i.reason) == (LAY, HOME, pytest.approx(50_000), "reduce")
    assert _limit_ok(i, 2.0)


# ----------------------------------------------------------------------------- green-up
def test_green_up_equalises_outcome_pnls():
    # Book (60,000, 20,000), balance 40,000, locked 20,000. p_home = 0.5, BACK home 2.0: edge 0, inside the hedge band.
    # Candidates that cut the balance: LAY home at 2.5 (stake 40,000 / 2.5 = 16,000, common P&L 60,000 - 16,000 * 1.5
    # = 36,000) and BACK away at 2.5 (stake 40,000 / 2.5 = 16,000, common P&L 60,000 - 16,000 = 44,000). BACK away wins.
    # After the fill: home 60,000 - 16,000 = 44,000; away 20,000 + 16,000 * 1.5 = 44,000.
    held = Book(home_pnl=60_000, away_pnl=20_000, trades=2)
    prices = {"back_home": 2.0, "lay_home": 2.5, "back_away": 2.5}
    intents = _run(V, 0.5, 0.5, prices, held)
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.reason) == (BACK, AWAY, "green_up")
    assert i.stake == pytest.approx(16_000)
    assert _limit_ok(i, 2.5)
    held.apply(_fill(i.side, i.selection, 2.5, i.stake))
    assert held.home_pnl == pytest.approx(held.away_pnl)
    assert held.home_pnl == pytest.approx(44_000)


def test_green_up_picks_lay_when_it_leaves_more_profit():
    # Same book, but BACK away only at 1.5: stake 40,000 / 1.5 = 26,666.67, common P&L 60,000 - 26,666.67 = 33,333.33.
    # LAY home at 2.5 gives 36,000, so LAY home wins with stake 16,000.
    # After the fill: home 60,000 - 16,000 * 1.5 = 36,000; away 20,000 + 16,000 = 36,000.
    held = Book(home_pnl=60_000, away_pnl=20_000, trades=2)
    prices = {"back_home": 2.0, "lay_home": 2.5, "back_away": 1.5}
    intents = _run(V, 0.5, 0.5, prices, held)
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.reason) == (LAY, HOME, "green_up")
    assert i.stake == pytest.approx(16_000)
    held.apply(_fill(i.side, i.selection, 2.5, i.stake))
    assert held.home_pnl == pytest.approx(held.away_pnl)
    assert held.home_pnl == pytest.approx(36_000)


def test_no_green_up_when_edge_is_at_hedge_or_no_locked_profit():
    # Edge of BACK home at 2.04 with p 0.5 is 0.02, which is >= hedge_edge, so hold. No other candidate clears 0.03.
    held = Book(home_pnl=60_000, away_pnl=20_000, trades=2)
    assert _run(V, 0.5, 0.5, {"back_home": 2.04}, held) == []
    # Locked profit is negative (-10,000 away), so there is nothing to lock: hold.
    unlocked = Book(home_pnl=60_000, away_pnl=-10_000, trades=2)
    assert _run(V, 0.5, 0.5, {"back_home": 2.0, "lay_home": 2.5}, unlocked) == []


# ----------------------------------------------------------------------------- cap and the hand-computed sequence
def test_cap_binds_exactly_at_minus_100000_on_both_runners():
    """Eight decisions on one match. Both outcomes reach exactly -100,000, the cap, with trades on both runners.

    Step  p_home  trade                  stake   reason   book (home, away) after
     1    0.6     BACK home @1.9         35,000  enter    (31,500, -35,000)
     2    0.6     BACK home @1.9         35,000  add      (63,000, -70,000)
     3    0.6     BACK home @1.9         30,000  add      (90,000, -100,000)  cap binds on away
     4    0.4     LAY home @2.0          50,000  reduce   (40,000, -50,000)
     5    0.4     LAY home @2.0          50,000  flip     (-10,000, 0)
     6    0.4     BACK away @2.2         65,000  add      (-75,000, 78,000)
     7    0.4     BACK away @2.2         25,000  add      (-100,000, 108,000) cap binds on home
     8    0.4     none: no room on either side
    """
    steps = [
        (0.6, BACK, HOME, 35_000, "enter", 31_500, -35_000),
        (0.6, BACK, HOME, 35_000, "add", 63_000, -70_000),
        (0.6, BACK, HOME, 30_000, "add", 90_000, -100_000),
        (0.4, LAY, HOME, 50_000, "reduce", 40_000, -50_000),
        (0.4, LAY, HOME, 50_000, "flip", -10_000, 0),
        (0.4, BACK, AWAY, 65_000, "add", -75_000, 78_000),
        (0.4, BACK, AWAY, 25_000, "add", -100_000, 108_000),
    ]
    book = Book()
    ledger_home = ledger_away = 0.0
    for p, side, selection, stake, reason, want_home, want_away in steps:
        intents = _run(V, p, p, P1, book)
        assert len(intents) == 1, (p, side, selection, reason)
        i = intents[0]
        price = P1[f"{'back' if side == BACK else 'lay'}_{selection}"]
        assert (i.side, i.selection, i.reason) == (side, selection, reason)
        assert i.stake == pytest.approx(stake)
        assert _limit_ok(i, price)
        book.apply(_fill(i.side, i.selection, price, i.stake))
        # Independent check against the portfolio convention, using the BettingAction for this leg.
        action = {
            (BACK, HOME): BettingAction.BACK_HOME_SM, (LAY, HOME): BettingAction.LAY_HOME_SM,
            (BACK, AWAY): BettingAction.BACK_AWAY_SM, (LAY, AWAY): BettingAction.LAY_AWAY_SM,
        }[(side, selection)]
        leg_home, leg_away = outcome_pnls(action, price, stake)
        ledger_home += leg_home
        ledger_away += leg_away
        assert (book.home_pnl, book.away_pnl) == pytest.approx((want_home, want_away))
        assert (ledger_home, ledger_away) == pytest.approx((want_home, want_away))
        assert book.worst() <= CAP + 1e-9
    # Both outcomes sit exactly on the cap after the run.
    assert book.worst() == pytest.approx(CAP, abs=1e-9)
    assert min(book.home_pnl, book.away_pnl) == pytest.approx(-CAP, abs=1e-9)
    assert book.home_pnl == pytest.approx(-CAP, abs=1e-9)  # step 7 binds on home
    # Step 8: no room on either side, so nothing trades.
    assert _run(V, 0.4, 0.4, P1, book) == []


def test_cap_drops_trade_below_minimum_stake():
    # Book (-99,995, 0): BACK away 2.2 at p_away 0.6 is an edge of 0.32 and would want 65,000,
    # but the room is only 100,000 - 99,995 = 5, below the minimum stake of 10. Dropped.
    book = Book(home_pnl=-99_995, away_pnl=0, trades=3)
    assert _run(V, 0.4, 0.4, P1, book) == []


def test_cooldown_blocks_entries_but_not_reduce():
    # Empty book, entry would be BACK home 35,000. Last trade 60 s ago with a 120 s cooldown: blocked.
    assert _run(V, 0.6, 0.6, P1, Book(), now=NOW, last_trade_at=NOW - 60) == []
    # 122 s ago: allowed.
    intents = _run(V, 0.6, 0.6, P1, Book(), now=NOW, last_trade_at=NOW - 122)
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(BACK, HOME, 35_000, "enter")]
    # A reduce is risk-reducing, so the cooldown does not stop it: LAY home 50,000 as in the reduce test.
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, 0.4, 0.4, P1, held, now=NOW, last_trade_at=NOW - 10)
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(LAY, HOME, 50_000, "reduce")]


def test_trade_limit_blocks_new_risk_not_reduce():
    full = Book(trades=40)
    assert _run(V, 0.6, 0.6, P1, full) == []
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=40)
    intents = _run(V, 0.4, 0.4, P1, held)
    assert [(i.side, i.selection, i.reason) for i in intents] == [(LAY, HOME, "reduce")]


def test_no_model_means_no_new_risk():
    # p_model None: no entry even with a 0.14 edge on the market probability, so nothing trades.
    assert _run(V, None, 0.6, P1, Book()) == []
    # The reduce still runs on the market probability: LAY home 50,000 at p_market 0.4.
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, None, 0.4, P1, held)
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(LAY, HOME, 50_000, "reduce")]


def test_market_only_control_uses_market_probability():
    # Control with p_model 0.9 ignored and p_market 0.6: the same decision as the first step (BACK home 35,000).
    control = Variant(name="ctl", w=0.5, min_edge=0.03, bankroll=1_000_000.0, market_only=True, max_trade_risk=1e12)
    intents = _run(control, 0.9, 0.6, P1, Book())
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(BACK, HOME, 35_000, "enter")]


def test_random_walk_never_breaches_the_cap():
    """300 decisions on random prices and probabilities, each filled at its price_limit. Worst case stays <= 100,000."""
    rng = random.Random(70)
    book = Book()
    traded = 0
    for step in range(300):
        p_model = rng.uniform(0.3, 0.7)
        p_market = rng.uniform(0.3, 0.7)
        back_home = round(rng.uniform(1.5, 4.0), 2)
        back_away = round(rng.uniform(1.5, 4.0), 2)
        prices = {
            "back_home": back_home, "lay_home": round(back_home + 0.05, 2),
            "back_away": back_away, "lay_away": round(back_away + 0.05, 2),
        }
        intents = decide(
            V, p_model, p_market, prices, book, now=float(step * 15), last_trade_at=None,
        )
        for i in intents:
            assert i.reason in REASONS
            decision_price = prices[f"{'back' if i.side == BACK else 'lay'}_{i.selection}"]
            assert _limit_ok(i, decision_price)
            book.apply(_fill(i.side, i.selection, decision_price, i.stake))
            traded += 1
            assert book.worst() <= CAP + 1e-6, (step, book.home_pnl, book.away_pnl)
    assert traded > 20


def test_one_trade_adds_at_most_max_trade_risk_so_the_budget_lasts():
    v = Variant(name="cap25", w=1.0, min_edge=0.03, bankroll=5_000_000.0)  # default max_trade_risk 25,000
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
    (i,) = decide(v, 0.6, 0.5, prices, Book(), now=NOW, last_trade_at=None)
    assert i.side == BACK and i.stake == pytest.approx(25_000.0)


def test_limit_keeps_half_the_minimum_edge():
    v = Variant(name="lim", w=1.0, min_edge=0.04, max_trade_risk=1e12, bankroll=1_000_000.0)
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
    (i,) = decide(v, 0.6, 0.5, prices, Book(), now=NOW, last_trade_at=None)
    assert i.side == BACK and i.price_limit == pytest.approx(1.02 / 0.6)  # p·o − 1 = 0.02 at the limit
