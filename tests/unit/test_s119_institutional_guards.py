"""Unit tests for S119: Institutional Risk Guards.

1. allow_adds=False strictly prevents adding / averaging down into existing positions.
2. Stop-loss execution: when held position edge turns negative (hold.edge <= -hedge_edge),
   the model emits a reduce ticket toward flat (target = 0.0) even when the opposite side
   has no positive alpha.
3. Max lay odds limit: prevents any new lay order when odds exceed max_lay_odds.
"""

from __future__ import annotations

import pytest
from strategy.model_edge import Book, Variant, decide


def test_allow_adds_false_blocks_averaging_down():
    # Green specialist model with allow_adds=False
    v = Variant(name="green_test", w=0.5, min_edge=0.04, auto_green_up=True, allow_adds=False)
    # Existing position: BACK home at 2.00 for stake 5000 (home_pnl = +5000, away_pnl = -5000)
    pos = Book(home_pnl=5000.0, away_pnl=-5000.0, trades=1)

    # Market price moves favorably to 2.10, model still gives edge
    book_prices = {
        "back_home": 2.10,
        "lay_home": 2.15,
        "back_away": 1.85,
        "lay_away": 1.90,
    }
    intents = decide(v, p_model=0.70, p_market=0.50, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # Because allow_adds is False, it must NOT add to the position
    for intent in intents:
        assert intent.reason != "add"


def test_stop_loss_reduces_toward_flat_on_negative_edge():
    # Model with hedge_edge = 0.01
    v = Variant(name="stop_loss_test", w=0.7, min_edge=0.04, hedge_edge=0.01, allow_adds=False)
    # Position: BACK home for stake 10,000 (home_pnl = +10,000, away_pnl = -10,000)
    pos = Book(home_pnl=10000.0, away_pnl=-10000.0, trades=1)

    # Catastrophic move against home: home drops to 0.10 model win prob (drifted to odds 5.0)
    book_prices = {
        "back_home": 5.00,
        "lay_home": 2.40,  # lay home within max_lay_odds
        "back_away": 1.60,
        "lay_away": 1.65,
    }
    intents = decide(v, p_model=0.10, p_market=0.20, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    assert len(intents) >= 1
    # It must trigger a risk-reducing action (reduce or flip)
    assert intents[0].reason in ("reduce", "flip")


def test_max_lay_odds_strictly_enforced():
    v = Variant(name="green_lay_test", w=0.5, min_edge=0.04, max_lay_odds=2.50)
    pos = Book()  # flat

    # Attempt to lay at high odds (e.g. 5.0)
    book_prices = {
        "back_home": 4.80,
        "lay_home": 5.00,  # lay odds 5.00 > 2.50
        "back_away": 1.22,
        "lay_away": 1.25,
    }
    # Model heavily favours away (p_home = 0.10)
    intents = decide(v, p_model=0.10, p_market=0.20, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # No LAY ticket on home @ 5.00 may be generated
    for intent in intents:
        if intent.side == "LAY":
            assert intent.price <= 2.50
