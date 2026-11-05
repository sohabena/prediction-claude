"""S90: target position, no-trade band, ticket budget and pre-match share (strategy.model_edge). Hand-computed."""
from __future__ import annotations

import inspect
import random

import pytest

from strategy.model_edge import AWAY, BACK, HOME, LAY, NEW_RISK_REASONS, Book, Variant, decide
from strategy.base import MatchedBet

NOW = 1_000.0
P1 = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.2, "lay_away": 2.3}
CAP = 100_000.0

# Default per-trade risk 25,000, so the no-trade band is 40% of it: 10,000 in balance units.
# The budget tests below are hand-computed for a 10-ticket budget; the default is 30 (operator, 2026-11-03).
V = Variant(name="s90", w=1.0, min_edge=0.03, bankroll=1_000_000.0, ticket_budget=10)


def _fill(intent, price):
    """The matched bet for an intent at ``price``: the decision price for a BACK, its limit for a LAY (worst fill)."""
    return MatchedBet(
        side=intent.side, selection=intent.selection, price=price, stake=intent.stake,
        placed_at=0.0, matched_at=0.0, fill_index=0, reason=intent.reason,
    )


def _run(variant, p, prices, book, *, in_play=False):
    return decide(variant, p, p, prices, book, now=NOW, last_trade_at=None, in_play=in_play)


def _shape(intents):
    return [(i.side, i.selection, i.reason) for i in intents]


# ----------------------------------------------------------------------------- settings
def test_defaults_and_validation():
    v = Variant(name="d", w=0.5, min_edge=0.03)
    assert (v.ticket_budget, v.ticket_reserve, v.no_trade_share, v.prematch_budget_share) == (30, 2, 0.4, 0.5)
    assert v.no_trade_band == pytest.approx(10_000)  # 40% of the 25,000 default per-trade risk
    assert v.match_cap(in_play=False) == pytest.approx(50_000)
    assert v.match_cap(in_play=True) == pytest.approx(100_000)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=0.03, ticket_budget=-1)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=0.03, prematch_budget_share=1.5)
    with pytest.raises(ValueError):
        Variant(name="bad", w=0.5, min_edge=0.03, no_trade_share=-0.1)


def test_in_play_defaults_to_pre_match_and_is_keyword_only():
    param = inspect.signature(decide).parameters["in_play"]
    assert param.default is False
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


# ----------------------------------------------------------------------------- no-trade band
def test_no_ticket_inside_the_band():
    # Target at p 0.6: BACK home Kelly stake 35,000 at 1.9, so the target balance is 1.9 * 35,000 = 66,500.
    on_target = Book(home_pnl=31_500, away_pnl=-35_000, trades=1)  # balance 66,500
    assert _run(V, 0.6, P1, on_target) == []
    # 6,500 short of target: inside the 10,000 band, so no ticket.
    near = Book(home_pnl=30_000, away_pnl=-30_000, trades=1)  # balance 60,000
    assert _run(V, 0.6, P1, near) == []


def test_a_gap_outside_the_band_gets_one_ticket():
    # Balance 55,000 is 11,500 short of the 66,500 target: outside the band. One add of 11,500 / 1.9 = 6,052.63,
    # rounded down to a human 6,000 (BACK home moves the balance by 1.9 per unit).
    short = Book(home_pnl=27_500, away_pnl=-27_500, trades=1)  # balance 55,000
    (i,) = _run(V, 0.6, P1, short)
    assert (i.side, i.selection, i.reason) == (BACK, HOME, "add")
    assert i.stake == pytest.approx(6_000)


def test_green_up_needs_a_balance_outside_the_band():
    # Balance 5,000 with locked profit 5,000: BACK home at 2.0 is at edge 0 (inside the hedge band), but the balance
    # is inside the 10,000 band, so nothing is greened up. Balance 40,000 does green up (16,000 BACK away at 2.5).
    prices = {"back_home": 2.0, "lay_home": 2.5, "back_away": 2.5}
    assert _run(V, 0.5, prices, Book(home_pnl=10_000, away_pnl=5_000, trades=2)) == []
    (i,) = _run(V, 0.5, prices, Book(home_pnl=60_000, away_pnl=20_000, trades=2))
    assert (i.side, i.selection, i.reason) == (BACK, AWAY, "green_up")
    assert i.stake == pytest.approx(16_000)


# ----------------------------------------------------------------------------- one ticket closes the gap
def test_one_ticket_closes_a_large_gap_and_the_next_decision_is_quiet():
    v = Variant(name="gap", w=1.0, min_edge=0.03, bankroll=1_000_000.0, max_trade_risk=100_000.0)  # band 40,000
    # p 0.7: BACK home Kelly stake 90,000 (edge 0.33, kelly 0.3667 * 0.25 * 1,000,000 = 91,667 -> 90,000), target
    # balance 1.9 * 90,000 = 171,000. From flat the gap is 171,000: one ticket of 171,000 / 1.9 = 90,000.
    book = Book()
    (i,) = _run(v, 0.7, P1, book, in_play=True)
    assert (i.side, i.selection, i.reason) == (BACK, HOME, "enter")
    assert i.stake == pytest.approx(90_000)
    book.apply(_fill(i, price=1.9))
    assert book.balance == pytest.approx(171_000)
    assert _run(v, 0.7, P1, book, in_play=True) == []

    # Probability falls to 0.6: the target is back to 66,500, so the gap is -104,500. One reduce on the other side,
    # the best-edge sell (BACK away at 2.2, edge -0.12): 104,500 / 2.2 = 47,500, rounded down to 45,000. Balance
    # after: 171,000 - 2.2 * 45,000 = 72,000, which is 5,500 from target, inside the band.
    (sell,) = _run(v, 0.6, P1, book, in_play=True)
    assert (sell.side, sell.selection, sell.reason) == (BACK, AWAY, "reduce")
    assert sell.stake == pytest.approx(45_000)
    book.apply(_fill(sell, price=2.2))
    assert book.balance == pytest.approx(72_000)
    assert _run(v, 0.6, P1, book, in_play=True) == []


# ----------------------------------------------------------------------------- ticket budget
def test_budget_exhaustion_keeps_reducing():
    # Ten tickets used. The favoured position is overpriced (p 0.4), so the close-out reduce still goes through.
    spent = Book(home_pnl=90_000, away_pnl=-100_000, trades=10)
    assert _shape(_run(V, 0.4, P1, spent, in_play=True)) == [(LAY, HOME, "reduce")]
    # No new risk: entering from flat is refused.
    assert _run(V, 0.6, P1, Book(trades=10)) == []


def test_when_two_are_left_only_reducing_tickets_are_allowed():
    # Eight used, two left: a flat book may not enter.
    assert _run(V, 0.6, P1, Book(trades=8)) == []
    # Seven used, three left: entering is allowed. The Kelly target is 35,000 at 1.9, but one enter is capped at the
    # 25,000 max_trade_risk, so the ticket is 25,000 and the rest of the gap is for later tickets.
    (i,) = _run(V, 0.6, P1, Book(trades=7))
    assert (i.side, i.selection, i.stake, i.reason) == (BACK, HOME, pytest.approx(25_000), "enter")
    # Eight used with a favoured position that must reverse: the flip is cut to its close-out reduce to flat.
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=8)
    assert _shape(_run(V, 0.4, P1, held, in_play=True)) == [(LAY, HOME, "reduce")]
    (close,) = _run(V, 0.4, P1, held, in_play=True)
    assert close.stake == pytest.approx(95_000)  # 190,000 balance / 2.0 decision price, rounded down


def test_flip_splits_into_close_out_and_capped_new_risk():
    # Balance 66,500 (BACK home 35,000 at 1.9) and p 0.4: the target is LAY home 50,000 = balance -100,000. The gap
    # is -166,500. The close-out 66,500 / 2.0 = 33,250, rounded down to 30,000, is uncapped. The new side is capped
    # at max_trade_risk: 25,000 of liability at 2.4625 (limit) is 25,000 / 1.4625 = 17,094.02 stake.
    book = Book(home_pnl=31_500, away_pnl=-35_000, trades=1)
    (i,) = _run(V, 0.4, P1, book, in_play=True)
    assert (i.side, i.selection, i.reason) == (LAY, HOME, "flip")
    assert i.stake == pytest.approx(30_000 + 25_000 / 1.4625)


def test_budget_never_blocks_a_reduce_that_the_book_needs():
    # Ten used, balance 190,000 and p 0.4: the reduce to zero goes through even at exhaustion (cap room is 129,914
    # in play, so the 95,000 close-out fits).
    held = Book(home_pnl=90_000, away_pnl=-100_000, trades=10)
    (i,) = _run(V, 0.4, P1, held, in_play=True)
    assert (i.side, i.reason, i.stake) == (LAY, "reduce", pytest.approx(95_000))


# ----------------------------------------------------------------------------- pre-match share
def test_pre_match_share_caps_the_worst_case_before_the_off():
    v = Variant(name="pm", w=1.0, min_edge=0.03, bankroll=1_000_000.0, max_trade_risk=100_000.0)
    # Pre-match: the target is 171,000 but BACK home may not put the away outcome below -50,000. Stake 50,000.
    book = Book()
    (first,) = _run(v, 0.7, P1, book)
    assert (first.side, first.selection, first.reason) == (BACK, HOME, "enter")
    assert first.stake == pytest.approx(50_000)
    book.apply(_fill(first, price=1.9))
    assert book.worst() == pytest.approx(50_000)
    # Nothing more before the off: the pre-match share is used up.
    assert _run(v, 0.7, P1, book) == []
    # In play the full cap applies: the gap to target is 171,000 - 95,000 = 76,000, a 40,000 BACK home add.
    (later,) = _run(v, 0.7, P1, book, in_play=True)
    assert (later.side, later.reason) == (BACK, "add")
    assert later.stake == pytest.approx(40_000)
    book.apply(_fill(later, price=1.9))
    assert book.worst() == pytest.approx(90_000)
    assert book.worst() <= CAP


# ----------------------------------------------------------------------------- caps and invariants
@pytest.mark.parametrize("in_play", [False, True])
def test_random_walk_holds_the_cap_and_the_budget(in_play):
    """300 decisions per regime on random prices and probabilities. Every fill keeps the worst case within the regime's
    cap (LAY filled at its limit), and every new-risk ticket is taken with fewer than eight tickets already filled."""
    rng = random.Random(90)
    cap = V.match_cap(in_play)
    book = Book()
    new_risk_tickets = 0
    for step in range(300):
        p = rng.uniform(0.3, 0.7)
        back_home = round(rng.uniform(1.5, 4.0), 2)
        back_away = round(rng.uniform(1.5, 4.0), 2)
        prices = {
            "back_home": back_home, "lay_home": round(back_home + 0.05, 2),
            "back_away": back_away, "lay_away": round(back_away + 0.05, 2),
        }
        for i in decide(V, p, p, prices, book, now=float(step * 15), last_trade_at=None, in_play=in_play):
            if i.reason in NEW_RISK_REASONS:
                assert book.trades < V.ticket_budget - V.ticket_reserve, (step, book.trades)
                new_risk_tickets += 1
            decision = prices[f"{'back' if i.side == BACK else 'lay'}_{i.selection}"]
            book.apply(_fill(i, i.price_limit if i.side == LAY else decision))
            assert book.worst() <= cap + 1e-6, (step, book.home_pnl, book.away_pnl)
    assert book.trades >= 3
    assert new_risk_tickets >= 1
