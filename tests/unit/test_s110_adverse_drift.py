"""Unit tests for S110: Freeze adverse position averaging (add) when price drifts adversely.

Rules:
1. When adding to a position (reason="add"), if the market price has drifted adversely
   by more than max_adverse_drift (default 30%) relative to the existing entry odds,
   the add order is blocked.
2. Moderate adds within the drift allowance (<= 30%) are permitted.
3. Risk-reducing actions (reduce, green_up, flip) are never blocked by price drift.
4. Initial entry (reason="enter") from flat is never blocked by price drift.
"""

from __future__ import annotations

import pytest

from strategy.model_edge import Book, Variant, decide


def test_variant_drift_default_and_validation():
    v = Variant(name="test", w=0.7, min_edge=0.04)
    assert v.max_adverse_drift == 0.30

    with pytest.raises(ValueError):
        Variant(name="test", w=0.7, min_edge=0.04, max_adverse_drift=-0.05)


def test_add_allowed_within_drift_corridor():
    v = Variant(name="test", w=0.7, min_edge=0.04, max_adverse_drift=0.30)
    # Existing position: BACK home at 2.00 for stake 500
    # home_pnl = +500, away_pnl = -500
    # Entry odds = 1.0 + (500 / 500) = 2.00
    pos = Book(home_pnl=500.0, away_pnl=-500.0, trades=1)

    # Current market price: 2.20 (drift is (2.20 - 2.00) / 2.00 = +10% <= 30%)
    # Model gives home a strong 0.70 probability -> edge = 0.70 - (1/2.20) = 0.70 - 0.4545 = 0.2455
    book_prices = {
        "back_home": 2.20,
        "lay_home": 2.24,
        "back_away": 1.75,
        "lay_away": 1.78,
    }
    intents = decide(v, p_model=0.70, p_market=0.45, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # Should generate an 'add' order
    assert len(intents) == 1
    assert intents[0].reason == "add"
    assert intents[0].side == "BACK"
    assert intents[0].selection == "home"


def test_add_blocked_when_adverse_drift_exceeds_threshold():
    v = Variant(name="test", w=0.7, min_edge=0.04, max_adverse_drift=0.30)
    # Existing position: BACK home at 2.00 for stake 500 (entry odds = 2.00)
    pos = Book(home_pnl=500.0, away_pnl=-500.0, trades=1)

    # Current market price: 2.80 (drift is (2.80 - 2.00) / 2.00 = +40% > 30%)
    # Model still thinks home is 0.65 -> positive edge
    book_prices = {
        "back_home": 2.80,
        "lay_home": 2.86,
        "back_away": 1.50,
        "lay_away": 1.53,
    }
    intents = decide(v, p_model=0.65, p_market=0.35, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # Should NOT add to the losing position
    assert len(intents) == 0


def test_hedging_not_blocked_by_drift():
    v = Variant(name="test", w=0.7, min_edge=0.04, max_adverse_drift=0.30)
    # Existing position: BACK home at 2.00 for stake 500 (entry odds = 2.00)
    pos = Book(home_pnl=500.0, away_pnl=-500.0, trades=1)

    # Market has moved drastically against home (home back 3.50, lay 3.60)
    # Model probability drops to 0.20 -> edge is negative -> trigger hedge/reduce/flip
    book_prices = {
        "back_home": 3.50,
        "lay_home": 3.60,
        "back_away": 1.35,
        "lay_away": 1.38,
    }
    intents = decide(v, p_model=0.20, p_market=0.28, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # Hedging/flipping is allowed
    assert len(intents) == 1
    assert intents[0].reason in ("reduce", "green_up", "flip")
