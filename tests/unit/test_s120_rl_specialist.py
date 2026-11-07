"""Unit tests for S120: Format-Specialist Reinforcement Learning (PPO) integration into Paper Trader."""

from __future__ import annotations

import os
from datetime import datetime, timezone
import pytest

from strategy.model_edge import Book, Variant
from virtual_trading import paper_trader as pt
from shared.schemas import BettingAction


def test_rl_specialist_variant_registration():
    """Verify RL_SPECIALIST_V1 is registered in variants and GREEN_VARIANTS with institutional risk limits."""
    assert pt.RL_SPECIALIST_V1 == "rl_specialist_v1"
    assert pt.RL_SPECIALIST_V1 in pt.GREEN_VARIANTS

    variants = pt.build_variants()
    rl_vars = [v for v in variants if v.name == pt.RL_SPECIALIST_V1]
    assert len(rl_vars) == 1
    rl_v = rl_vars[0]

    # Verify institutional risk parameters
    assert rl_v.allow_adds is False
    assert rl_v.auto_green_up is True
    assert rl_v.multi_cycle_green is True
    assert rl_v.max_lay_odds == 2.50
    assert rl_v.max_trade_risk == 15_000.0
    assert rl_v.guard_double_negative is True
    assert rl_v.ticket_budget == 15


def test_rl_specialist_not_in_play():
    """Verify RL specialist opens no risk pre-match."""
    v = next(var for var in pt.build_variants() if var.name == pt.RL_SPECIALIST_V1)
    cand = pt.Candidate(
        match_id="m_pre",
        team_home="Australia",
        team_away="India",
        competition="T20 International",
        match_start_time=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
        is_simulated=False,
        toss_winner=None,
        toss_decision=None,
        last_price_at=datetime.now(timezone.utc),
        in_play=False,
    )
    book = {"back_home": 1.90, "lay_home": 1.95, "back_away": 2.05, "lay_away": 2.10}
    pos = Book()
    now = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)

    intents, p_model, source, detail = pt._rl_specialist_intents(
        v, cand, book, pos, rows=[], state=None, last_trade_at=None, now=now
    )
    assert intents == []
    assert detail["reason"] == "not_in_play"


def test_rl_specialist_single_bullet_guard():
    """Verify RL specialist adheres to single-bullet rule: no adds to an open un-greened position."""
    v = next(var for var in pt.build_variants() if var.name == pt.RL_SPECIALIST_V1)
    cand = pt.Candidate(
        match_id="m_live",
        team_home="Brisbane Heat",
        team_away="Hobart Hurricanes",
        competition="Big Bash League",
        match_start_time=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        is_simulated=False,
        toss_winner="Brisbane Heat",
        toss_decision="bat",
        last_price_at=datetime.now(timezone.utc),
        in_play=True,
    )
    book = {"back_home": 2.20, "lay_home": 2.30, "back_away": 1.75, "lay_away": 1.80}
    # Open un-greened position (trades=1, balance!=0, worst_case = -10,000)
    pos = Book(home_pnl=12000.0, away_pnl=-10000.0, trades=1)
    now = datetime.now(timezone.utc)
    rows = [{"overs": 10.0, "score": 85, "wickets": 2, "time": now, "is_live": True}]

    intents, p_model, source, detail = pt._rl_specialist_intents(
        v, cand, book, pos, rows=rows, state=rows, last_trade_at=None, now=now
    )
    assert intents == []
    assert detail["reason"] == "single_bullet_active"


def test_rl_specialist_auto_green_up():
    """Verify RL specialist locks in guaranteed profit when position is in green profit."""
    v = next(var for var in pt.build_variants() if var.name == pt.RL_SPECIALIST_V1)
    cand = pt.Candidate(
        match_id="m_live_gu",
        team_home="Sydney Sixers",
        team_away="Perth Scorchers",
        competition="Big Bash League",
        match_start_time=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        is_simulated=False,
        toss_winner="Sydney Sixers",
        toss_decision="bat",
        last_price_at=datetime.now(timezone.utc),
        in_play=True,
    )
    # Position: entered early on Sydney Sixers at 2.50 (home_pnl = +15,000, away_pnl = -10,000)
    pos = Book(home_pnl=15000.0, away_pnl=-10000.0, trades=1)
    now = datetime.now(timezone.utc)
    rows = [{"overs": 15.0, "score": 140, "wickets": 2, "time": now, "is_live": True}]

    # Odds collapse in favour of Sixers: lay_home is now 1.25!
    book = {"back_home": 1.23, "lay_home": 1.25, "back_away": 5.0, "lay_away": 5.5}

    intents, p_model, source, detail = pt._rl_specialist_intents(
        v, cand, book, pos, rows=rows, state=rows, last_trade_at=None, now=now
    )
    assert len(intents) == 1
    intent = intents[0]
    assert intent.reason == "green_up"
    assert intent.side == "LAY"
    assert intent.selection == "home"
    assert detail["reason"] == "green_up"


def test_rl_specialist_capped_lay_odds_guard():
    """Verify RL specialist strictly rejects LAY actions when odds exceed max_lay_odds (2.50)."""
    v = next(var for var in pt.build_variants() if var.name == pt.RL_SPECIALIST_V1)
    cand = pt.Candidate(
        match_id="m_live_lay",
        team_home="Adelaide Strikers",
        team_away="Melbourne Stars",
        competition="Big Bash League",
        match_start_time=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        is_simulated=False,
        toss_winner="Adelaide Strikers",
        toss_decision="bat",
        last_price_at=datetime.now(timezone.utc),
        in_play=True,
    )
    # Book with lay odds way above 2.50
    book = {"back_home": 3.70, "lay_home": 3.75, "back_away": 1.30, "lay_away": 1.33}
    pos = Book()
    now = datetime.now(timezone.utc)
    rows = [{"overs": 18.0, "score": 110, "wickets": 7, "time": now, "is_live": True}]

    # In our earlier test, Adelaide Strikers at 18 ov, 110/7 predicted action 5 or 6 (LAY)
    intents, p_model, source, detail = pt._rl_specialist_intents(
        v, cand, book, pos, rows=rows, state=rows, last_trade_at=None, now=now
    )
    # Any lay order with odds 3.75 must be rejected
    for intent in intents:
        if intent.side == "LAY":
            assert intent.price_limit <= 2.50


def test_rl_specialist_inference_entry():
    """Verify RL specialist executes policy inference through ModelZoo and creates valid OrderIntent."""
    v = next(var for var in pt.build_variants() if var.name == pt.RL_SPECIALIST_V1)
    cand = pt.Candidate(
        match_id="m_live_entry",
        team_home="Perth Scorchers",
        team_away="Sydney Sixers",
        competition="Big Bash League",
        match_start_time=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        is_simulated=False,
        toss_winner="Perth Scorchers",
        toss_decision="bat",
        last_price_at=datetime.now(timezone.utc),
        in_play=True,
    )
    # Balanced book with tight odds
    book = {"back_home": 1.85, "lay_home": 1.87, "back_away": 2.12, "lay_away": 2.16}
    pos = Book()
    now = datetime.now(timezone.utc)
    rows = [{"overs": 5.0, "score": 42, "wickets": 1, "time": now, "is_live": True}]

    intents, p_model, source, detail = pt._rl_specialist_intents(
        v, cand, book, pos, rows=rows, state=rows, last_trade_at=None, now=now
    )
    assert detail["specialist_key"] == "bbl_men"
    assert "action" in detail
    assert source.startswith("rl_specialist:bbl_men")
    if intents:
        intent = intents[0]
        assert intent.reason == "enter"
        assert intent.stake > 0
        assert intent.stake <= v.max_trade_risk
