"""Unit tests for S109: Odds validity corridor for new-risk entries.

Rules:
1. New-risk LAY orders below 1.35 odds are rejected with "lay_odds_too_low".
2. New-risk BACK orders at or above 4.00 odds are rejected with "back_odds_too_high",
   except for tail bets (reason="tail") which are designed for long shots.
3. Risk-reducing orders (reduce, green_up) are exempt and may trade at any odds.
4. Variants enforce min_tradable_odds and max_tradable_odds in decision generation.
"""

from __future__ import annotations

import pytest

from strategy.base import OrderIntent
from strategy.model_edge import Book, Variant, decide, placebo_ticket
from strategy.trade_guards import GuardConfig, intent_refusal


def _mock_book(back_home=2.00, lay_home=2.04, back_away=1.90, lay_away=1.95):
    return {
        "back_home": back_home,
        "lay_home": lay_home,
        "back_away": back_away,
        "lay_away": lay_away,
    }


def _mock_ages():
    return {
        "back_home": 1.0,
        "lay_home": 1.0,
        "back_away": 1.0,
        "lay_away": 1.0,
    }


def test_intent_refusal_blocks_low_lay_odds():
    cfg = GuardConfig()
    book = _mock_book(back_home=1.10, lay_home=1.12)
    ages = _mock_ages()
    intent = OrderIntent(side="LAY", selection="home", price_limit=1.12, stake=500.0, reason="enter")
    refusal = intent_refusal(intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg)
    assert refusal == "lay_odds_too_low"


def test_intent_refusal_accepts_valid_lay_odds():
    cfg = GuardConfig()
    book = _mock_book(back_home=1.50, lay_home=1.55)
    ages = _mock_ages()
    intent = OrderIntent(side="LAY", selection="home", price_limit=1.55, stake=500.0, reason="enter")
    refusal = intent_refusal(intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg)
    assert refusal is None


def test_intent_refusal_blocks_high_back_odds():
    cfg = GuardConfig()
    book = _mock_book(back_home=5.50, lay_home=5.80)
    ages = _mock_ages()
    intent = OrderIntent(side="BACK", selection="home", price_limit=5.50, stake=500.0, reason="enter")
    refusal = intent_refusal(intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg)
    assert refusal == "back_odds_too_high"


def test_intent_refusal_accepts_valid_back_odds():
    cfg = GuardConfig()
    book = _mock_book(back_home=2.50, lay_home=2.60)
    ages = _mock_ages()
    intent = OrderIntent(side="BACK", selection="home", price_limit=2.50, stake=500.0, reason="enter")
    refusal = intent_refusal(intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg)
    assert refusal is None


def test_intent_refusal_allows_tail_bets_at_high_odds():
    cfg = GuardConfig()
    book = _mock_book(back_home=15.0, lay_home=16.0)
    ages = _mock_ages()
    intent = OrderIntent(side="BACK", selection="home", price_limit=15.0, stake=100.0, reason="tail")
    refusal = intent_refusal(intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg)
    assert refusal is None


def test_intent_refusal_exempts_reduce_and_green_up():
    cfg = GuardConfig()
    # Severe low lay odds
    book = _mock_book(back_home=1.05, lay_home=1.06)
    ages = _mock_ages()
    reduce_intent = OrderIntent(side="LAY", selection="home", price_limit=1.06, stake=1000.0, reason="reduce")
    assert intent_refusal(reduce_intent, book=book, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg) is None

    # Severe high back odds
    book2 = _mock_book(back_home=25.0, lay_home=28.0)
    green_intent = OrderIntent(side="BACK", selection="home", price_limit=25.0, stake=500.0, reason="green_up")
    assert intent_refusal(green_intent, book=book2, ages=ages, in_play=True, recent=(), now=1000.0, cfg=cfg) is None


def test_decide_skips_out_of_corridor_candidates():
    v = Variant(name="test", w=0.7, min_edge=0.05, min_tradable_odds=1.35, max_tradable_odds=4.00)
    book_prices = {
        "back_home": 5.0,
        "lay_home": 5.2,
        "back_away": 1.20,
        "lay_away": 1.22,
    }
    pos = Book()
    intents = decide(v, p_model=0.40, p_market=0.20, book_prices=book_prices, position=pos, now=1000.0, last_trade_at=None, in_play=True)
    # Home back is out of corridor (>= 4.00), away lay is out of corridor (< 1.35) -> no trades
    assert intents == []


def test_placebo_ticket_respects_corridor():
    v = Variant(name="test", w=0.7, min_edge=0.04, placebo="favourite", min_tradable_odds=1.35, max_tradable_odds=4.00)
    book_low = {"back_home": 1.20, "lay_home": 1.22, "back_away": 4.5, "lay_away": 4.8}
    pos = Book()

    # Attempt LAY at 1.22 (outside corridor < 1.35)
    ticket_lay = placebo_ticket(v, 0.80, book_low, pos, side="LAY", selection="home", stake=500.0, in_play=True, new_risk=True)
    assert ticket_lay is None

    # Attempt BACK at 4.50 (outside corridor >= 4.00)
    ticket_back = placebo_ticket(v, 0.20, book_low, pos, side="BACK", selection="away", stake=500.0, in_play=True, new_risk=True)
    assert ticket_back is None

    # Valid BACK inside corridor [1.35, 4.00)
    book_valid = {"back_home": 2.00, "lay_home": 2.04, "back_away": 1.90, "lay_away": 1.94}
    ticket_valid = placebo_ticket(v, 0.50, book_valid, pos, side="BACK", selection="home", stake=1500.0, in_play=True, new_risk=True)
    assert ticket_valid is not None
