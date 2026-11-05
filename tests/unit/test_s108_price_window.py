"""S108: the trader reads prices only as far back as open bets still need them. Pure function, no database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from virtual_trading import paper_trader as pt

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
LOOK = pt.PENDING_LOOKBACK


def _bet(status, decided_min_ago, *, in_play=True, clv=None, filled_min_ago=None, match_id="m1"):
    return SimpleNamespace(status=status, match_id=match_id, in_play=in_play, clv=clv,
                           decided_at=NOW - timedelta(minutes=decided_min_ago),
                           filled_at=None if filled_min_ago is None else NOW - timedelta(minutes=filled_min_ago))


def test_no_open_bets_reads_the_default_twenty_minutes():
    assert pt.price_window_start([], {}, NOW) == NOW - LOOK


def test_an_old_prematch_bet_before_its_off_does_not_widen_the_window():
    """2026-11-05: a pre-match bet decided 12 h before the start made every cycle read 12 h of prices."""
    bet = _bet("filled", 720, in_play=False, filled_min_ago=719)
    assert pt.price_window_start([bet], {"m1": NOW + timedelta(hours=1)}, NOW) == NOW - LOOK


def test_a_bet_with_clv_recorded_needs_nothing_old():
    bet = _bet("filled", 600, clv=0.01, filled_min_ago=599)
    assert pt.price_window_start([bet], {}, NOW) == NOW - LOOK


def test_a_pending_bet_reaches_back_to_its_decision():
    bet = _bet("pending", 30)
    assert pt.price_window_start([bet], {}, NOW) == NOW - timedelta(minutes=30) - LOOK


def test_an_in_play_fill_waiting_for_clv_reaches_its_reference():
    bet = _bet("filled", 40, filled_min_ago=39)
    reference = NOW - timedelta(minutes=39) + pt.CLV_DELAY
    assert pt.price_window_start([bet], {}, NOW) == reference - LOOK


def test_a_prematch_fill_waiting_for_clv_after_the_off_reaches_the_off():
    bet = _bet("filled", 720, in_play=False, filled_min_ago=719)
    off = NOW - timedelta(minutes=50)
    assert pt.price_window_start([bet], {"m1": off}, NOW) == off - LOOK
