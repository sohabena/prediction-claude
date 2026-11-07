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

V = Variant(name="test", w=0.5, min_edge=0.03, bankroll=1_000_000.0, max_trade_risk=1e12, no_trade_share=0.0)  # sizing tests: no per-trade cap

# Prices used by the sequence. Edges at p_home = 0.6: BACK home 0.14, LAY away 0.08, LAY home -0.20, BACK away -0.12.
P1 = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}


def _fill(side: str, selection: str, price: float, stake: float) -> MatchedBet:
    return MatchedBet(
        side=side, selection=selection, price=price, stake=stake,
        placed_at=0.0, matched_at=0.0, fill_index=0, reason="test",
    )


def _run(variant, p_model, p_market, prices, book, *, now=NOW, last_trade_at=None, in_play=False):
    return decide(
        variant, p_model, p_market, prices, book, now=now, last_trade_at=last_trade_at, in_play=in_play,
    )


# ----------------------------------------------------------------------------- variant and blend
def test_variant_rejects_out_of_range_settings():
    with pytest.raises(ValueError):
        Variant(name="bad", w=1.5, min_edge=0.03)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=-0.01)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=0.03, max_match_loss=0.0)


def test_variant_defaults_match_the_story():
    v = Variant(name="d", w=0.5, min_edge=0.03, max_trade_risk=1e12, no_trade_share=0.0)
    assert (v.kelly_fraction, v.max_match_loss, v.bankroll, v.hedge_edge) == (0.25, 100_000, 5_000_000, 0.01)
    assert (v.max_trades_per_match, v.cooldown_s, v.market_only) == (40, 120, False)


def test_blend_hand_computed():
    # 0.5 * 0.8 + 0.5 * 0.4 = 0.6 ; 0.3 * 0.8 + 0.7 * 0.4 = 0.24 + 0.28 = 0.52
    assert blend_probability(V, 0.8, 0.4) == pytest.approx(0.6)
    w03 = Variant(name="w03", w=0.3, min_edge=0.03, max_trade_risk=1e12, no_trade_share=0.0)
    assert blend_probability(w03, 0.8, 0.4) == pytest.approx(0.52)
    # market_only ignores the model; no model falls back to the market
    control = Variant(name="ctl", w=0.5, min_edge=0.03, market_only=True, max_trade_risk=1e12, no_trade_share=0.0)
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


def test_negative_edge_on_held_side_moves_to_the_kelly_target():
    # S90: the target is the Kelly position on the opposite side, not a Kelly increment. Held: BACK home 100,000 at
    # 1.9 -> book (90,000, -100,000), balance 190,000. p_home falls to 0.4: BACK home edge = 0.4 * 1.9 - 1 = -0.24, so
    # the target is LAY home at 2.0 with Kelly stake 50,000: balance -2 * 50,000 = -100,000. The gap is -290,000, so
    # one ticket closes the 190,000 to zero and opens the short: a flip. The new-risk part is cut by the cap. A LAY is
    # sized at its limit (2.4625, half the minimum edge left): the cap room is (90,000 + 100,000) / 1.4625 = 129,914.53.
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, 0.4, 0.4, P1, held, in_play=True)
    assert len(intents) == 1
    i = intents[0]
    assert (i.side, i.selection, i.reason) == (LAY, HOME, "flip")
    assert i.stake == pytest.approx(190_000 / 1.4625)  # (90,000 + 100,000) / (2.4625 - 1)
    assert _limit_ok(i, 2.0)
    held.apply(_fill(i.side, i.selection, i.price_limit, i.stake))
    assert held.home_pnl == pytest.approx(-100_000)  # the cap binds on home, exactly


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
def test_cap_binds_exactly_at_minus_100000_in_play():
    """Five decisions on one match, in play (full cap). The cap binds exactly on home at step 4, and step 5 is refused.

    Step  p_home  trade                       stake        reason   book (home, away) after
     1    0.6     BACK home @1.9 (target 66,500)  35,000     enter    (31,500, -35,000)
     2    0.6     on target: nothing             -           -        (31,500, -35,000)
     3    0.7     BACK home @1.9 (target 171,000) 55,000     add      (81,000, -90,000)
     4    0.4     LAY home, target -100,000      123,760.68  flip     (-100,000, 33,760.68)  cap binds on home
     5    0.4     BACK away, target -143,000:   none: no room, home is at -100,000
    """
    steps = [
        (0.6, BACK, HOME, 35_000, "enter", 31_500, -35_000),
        (0.6, None, None, None, None, 31_500, -35_000),
        (0.7, BACK, HOME, 55_000, "add", 81_000, -90_000),
        (0.4, LAY, HOME, 123_760.68, "flip", -100_000, 33_760.68),
    ]
    book = Book()
    for p, side, selection, stake, reason, want_home, want_away in steps:
        intents = _run(V, p, p, P1, book, in_play=True)
        if side is None:
            assert intents == []
        else:
            assert len(intents) == 1, (p, side, selection, reason)
            i = intents[0]
            assert (i.side, i.selection, i.reason) == (side, selection, reason)
            assert i.stake == pytest.approx(stake, rel=1e-6)
            price = i.price_limit if i.side == LAY else P1[f"{'back' if side == BACK else 'lay'}_{selection}"]
            assert _limit_ok(i, P1[f"{'back' if side == BACK else 'lay'}_{selection}"])
            book.apply(_fill(i.side, i.selection, price, i.stake))
        assert book.worst() <= CAP + 1e-6
        assert book.home_pnl == pytest.approx(want_home, rel=1e-6)
        assert book.away_pnl == pytest.approx(want_away, rel=1e-6)
    # Step 5: the target is a BACK away of 65,000 (balance -143,000), but home already sits at -100,000: no room.
    assert _run(V, 0.4, 0.4, P1, book, in_play=True) == []
    assert book.worst() == pytest.approx(CAP, abs=1e-6)


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
    # A reduce is risk-reducing, so the cooldown does not stop it. The target flip is blocked as new risk, so it is
    # cut to its close-out part: LAY home 95,000 at 2.0 closes the balance of 190,000 to zero (pre-match cap 50,000:
    # the room is (90,000 + 50,000) / 1.4625 = 95,726).
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, 0.4, 0.4, P1, held, now=NOW, last_trade_at=NOW - 10)
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(LAY, HOME, 95_000, "reduce")]


def test_trade_limit_blocks_new_risk_not_reduce():
    full = Book(trades=40)
    assert _run(V, 0.6, 0.6, P1, full) == []
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=40)
    intents = _run(V, 0.4, 0.4, P1, held)
    assert [(i.side, i.selection, i.reason) for i in intents] == [(LAY, HOME, "reduce")]


def test_no_model_means_no_new_risk():
    # p_model None: no entry even with a 0.14 edge on the market probability, so nothing trades.
    assert _run(V, None, 0.6, P1, Book()) == []
    # The reduce still runs on the market probability. The flip is blocked as new risk, so it closes to flat:
    # LAY home 95,000 at 2.0 (the balance of 190,000 divided by the price).
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=1)
    intents = _run(V, None, 0.4, P1, held)
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(LAY, HOME, 95_000, "reduce")]


def test_market_only_control_uses_market_probability():
    # Control with p_model 0.9 ignored and p_market 0.6: the same decision as the first step (BACK home 35,000).
    control = Variant(
        name="ctl", w=0.5, min_edge=0.03, bankroll=1_000_000.0, market_only=True, max_trade_risk=1e12,
        no_trade_share=0.0,
    )
    intents = _run(control, 0.9, 0.6, P1, Book())
    assert [(i.side, i.selection, i.stake, i.reason) for i in intents] == [(BACK, HOME, 35_000, "enter")]


@pytest.mark.parametrize("in_play", [False, True])
def test_random_walk_never_breaches_the_cap(in_play):
    """300 decisions on random prices and probabilities, each filled at its price_limit (LAY) or decision price (BACK).
    Worst case stays <= the pre-match share before the off (50,000) and <= 100,000 in play."""
    rng = random.Random(70)
    cap = V.match_cap(in_play)
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
            V, p_model, p_market, prices, book, now=float(step * 15), last_trade_at=None, in_play=in_play,
        )
        for i in intents:
            assert i.reason in REASONS
            decision_price = prices[f"{'back' if i.side == BACK else 'lay'}_{i.selection}"]
            assert _limit_ok(i, decision_price)
            fill = i.price_limit if i.side == LAY else decision_price  # a LAY may fill as high as its limit
            book.apply(_fill(i.side, i.selection, fill, i.stake))
            traded += 1
            assert book.worst() <= cap + 1e-6, (step, book.home_pnl, book.away_pnl)
    assert traded > 3


def test_one_trade_adds_at_most_max_trade_risk_so_the_budget_lasts():
    v = Variant(name="cap25", w=1.0, min_edge=0.03, bankroll=5_000_000.0)  # default max_trade_risk 25,000
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
    (i,) = decide(v, 0.6, 0.5, prices, Book(), now=NOW, last_trade_at=None)
    assert i.side == BACK and i.stake == pytest.approx(25_000.0)


def test_limit_keeps_half_the_minimum_edge():
    v = Variant(name="lim", w=1.0, min_edge=0.04, max_trade_risk=1e12, no_trade_share=0.0, bankroll=1_000_000.0)
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
    (i,) = decide(v, 0.6, 0.5, prices, Book(), now=NOW, last_trade_at=None)
    assert i.side == BACK and i.price_limit == pytest.approx(1.02 / 0.6)  # p·o − 1 = 0.02 at the limit


def test_trades_below_the_minimum_trade_stake_are_dropped():
    v = Variant(name="min", w=1.0, min_edge=0.03, bankroll=10_000.0, max_trade_risk=1e12, no_trade_share=0.0)  # tiny Kelly stakes
    prices = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
    assert decide(v, 0.6, 0.5, prices, Book(), now=NOW, last_trade_at=None) == []


# ----------------------------------------------------------------------------- S112: auto green-up
def test_auto_green_up_variant_locks_profit_when_opportunity_arises():
    v_green = Variant(name="test_green", w=0.5, min_edge=0.03, auto_green_up=True)
    # Suppose we previously backed home @ 2.5 with stake 20,000
    # Home PnL = +30,000, Away PnL = -20,000. Balance = +50,000.
    book = Book(home_pnl=30_000, away_pnl=-20_000, trades=1)

    # Market odds for Lay Home drop to 1.60:
    # Greening up with LAY home: stake = 50,000 / 1.60 = 31,250.
    # Home PnL: 30,000 - 31,250 * 0.60 = +11,250
    # Away PnL: -20,000 + 31,250 = +11,250
    # Common profit = +11,250 > 0!
    prices = {"back_home": 1.55, "lay_home": 1.60, "back_away": 2.6, "lay_away": 2.7}
    intents = _run(v_green, 0.6, 0.6, prices, book)
    assert len(intents) == 1
    assert intents[0].reason == "green_up"
    assert intents[0].side == LAY
    assert intents[0].selection == HOME
    assert intents[0].stake == pytest.approx(31_250)

    # Apply the fill: outcome PnLs equalise and locked profit is strictly positive
    book.apply(_fill(intents[0].side, intents[0].selection, 1.60, intents[0].stake))
    assert book.locked == pytest.approx(11_250)
    assert book.worst() == 0.0

    # Once locked, auto_green_up stops taking new risk on this match
    assert _run(v_green, 0.6, 0.6, prices, book) == []


def test_auto_green_up_does_not_fire_when_equalized_profit_would_be_negative():
    v_green = Variant(name="test_green", w=0.5, min_edge=0.03, auto_green_up=True)
    # Backed home @ 2.0 with stake 25,000 -> Home PnL +25,000, Away PnL -25,000
    book = Book(home_pnl=25_000, away_pnl=-25_000, trades=1)

    # Market moved against us: Lay Home is at 3.0. Greening up would result in common PnL < 0
    prices = {"back_home": 2.8, "lay_home": 3.0, "back_away": 1.45, "lay_away": 1.50}
    intents = _run(v_green, 0.5, 0.35, prices, book)
    # Does not trigger green_up at a loss
    assert not any(i.reason == "green_up" for i in intents)


def test_guard_double_negative_blocks_trades_creating_double_loss():
    # S113: If a trade would result in both outcomes being negative, reject it
    v_guarded = Variant(name="test_guarded", w=0.5, min_edge=0.03, guard_double_negative=True, bankroll=1_000_000.0)
    # Position: Home +10,000, Away -10,000
    book = Book(home_pnl=10_000.0, away_pnl=-10_000.0, trades=1)
    # Market moved adversely: home is now an underdog (lay 3.0). An adverse reduce or flip would lock double loss
    prices = {"back_home": 2.8, "lay_home": 3.0, "back_away": 1.45, "lay_away": 1.50}
    intents = _run(v_guarded, 0.2, 0.3, prices, book)
    # Any order that would result in home < 0 and away < 0 is blocked
    for i in intents:
        vh = 1.0 - i.price_limit if i.side == LAY and i.selection == HOME else (i.price_limit - 1.0 if i.side == BACK and i.selection == HOME else (-1.0 if i.side == BACK else 1.0))
        va = 1.0 if i.side == LAY and i.selection == HOME else (-1.0 if i.side == BACK and i.selection == HOME else (i.price_limit - 1.0 if i.side == BACK else 1.0 - i.price_limit))
        assert not (book.home_pnl + i.stake * vh < 0 and book.away_pnl + i.stake * va < 0)


def test_hard_ticket_limit_enforced_strictly():
    # S113: Once budget is closed, hard_ticket_limit prevents any further orders
    v_hard = Variant(name="test_hard", w=0.5, min_edge=0.03, ticket_budget=15, ticket_reserve=2, hard_ticket_limit=True)
    # 13 trades out of 15 means 2 left -> budget is closed (15 - 13 <= 2)
    book = Book(home_pnl=70_000.0, away_pnl=30_000.0, trades=13)
    prices = {"back_home": 2.0, "lay_home": 2.1, "back_away": 2.0, "lay_away": 2.1}
    intents = _run(v_hard, 0.5, 0.5, prices, book)
    assert intents == []


# ----------------------------------------------------------------------------- S115: multi-cycle green compounding & loss margin
def test_multi_cycle_green_allows_reentry_when_edge_exists():
    v_single = Variant(name="single", w=0.5, min_edge=0.03, auto_green_up=True, multi_cycle_green=False)
    v_multi = Variant(name="multi", w=0.5, min_edge=0.03, auto_green_up=True, multi_cycle_green=True, high_scenario_edge=0.06, reentry_loss_margin=0.25)

    # Greened-up book with +5,000 locked across both sides
    book = Book(home_pnl=5_000.0, away_pnl=5_000.0, trades=2)
    prices = {"back_home": 2.5, "lay_home": 2.6, "back_away": 1.6, "lay_away": 1.65}

    # Model has edge on home (p=0.55 vs back 2.5 -> edge > 0.10)
    # Single-shot benchmark stops trading completely:
    assert _run(v_single, 0.55, 0.40, prices, book) == []

    # Multi-cycle specialist continues trading to compound profits:
    intents = _run(v_multi, 0.55, 0.40, prices, book)
    assert len(intents) == 1
    assert intents[0].side == BACK
    assert intents[0].selection == HOME


def test_multi_cycle_pure_house_money_invariant():
    # S116: Once greened up, any re-entry trade is strictly constrained to banked profit.
    # Worst-case across both outcomes CANNOT drop below 0.0.
    v_multi = Variant(
        name="multi",
        w=0.5,
        min_edge=0.03,
        auto_green_up=True,
        multi_cycle_green=True,
        max_lay_odds=3.00,
        max_trade_risk=25_000.0,
        bankroll=2_000_000.0,
    )
    # Greened-up book with +1,500 locked
    book = Book(home_pnl=1_500.0, away_pnl=1_500.0, trades=2)

    # Even with a massive 15% edge, risk is strictly capped at banked profit (1,500):
    prices = {"back_home": 2.5, "lay_home": 2.6, "back_away": 1.6, "lay_away": 1.65}
    intents = _run(v_multi, 0.55, 0.40, prices, book)
    assert len(intents) == 1
    assert intents[0].stake <= 1_500.0 + 1e-6

    # Verify worst-case PnL after trade is >= 0:
    i = intents[0]
    new_home = book.home_pnl + (i.price_limit - 1.0) * i.stake
    new_away = book.away_pnl - i.stake
    assert min(new_home, new_away) >= -1e-6


def test_max_lay_odds_guard_blocks_longshot_lays():
    # S116: Strict max_lay_odds guard prevents laying selections at extreme odds
    v = Variant(name="test_lay", w=0.5, min_edge=0.03, max_lay_odds=3.00)
    book = Book()
    # Market: Away is a massive underdog (Lay 12.0, Back 11.0). Model predicts 25% edge to lay away:
    prices = {"back_home": 1.09, "lay_home": 1.10, "back_away": 11.0, "lay_away": 12.0}
    intents = _run(v, 0.02, 0.08, prices, book)
    # Lay away @ 12.0 is strictly BLOCKED by max_lay_odds=3.00:
    for intent in intents:
        if intent.side == LAY:
            assert intent.price_limit <= 3.00


def test_edge_aware_green_up_holds_when_edge_is_massive():
    v_multi = Variant(
        name="multi",
        w=0.5,
        min_edge=0.03,
        auto_green_up=True,
        multi_cycle_green=True,
        min_green_profit=2_500.0,
    )
    # Backed home @ 2.5 with stake 20,000 -> Home PnL +30,000, Away PnL -20,000
    book = Book(home_pnl=30_000.0, away_pnl=-20_000.0, trades=1)

    # Market moves slightly in favor: Lay home is 2.40.
    # Greening up now would yield only +833 common profit.
    # But our model still has a massive edge (p_model = 0.60 vs p_market = 0.41):
    prices = {"back_home": 2.35, "lay_home": 2.40, "back_away": 1.70, "lay_away": 1.75}
    intents = _run(v_multi, 0.60, 0.41, prices, book)
    # Does NOT sell out prematurely for +833 when edge is massive:
    assert not any(i.reason == "green_up" for i in intents)

    # When odds move to 1.60 and common profit would be +11,250 >= min_green_profit (2,500):
    prices_target = {"back_home": 1.55, "lay_home": 1.60, "back_away": 2.60, "lay_away": 2.70}
    intents_target = _run(v_multi, 0.60, 0.60, prices_target, book)
    assert len(intents_target) == 1
    assert intents_target[0].reason == "green_up"
