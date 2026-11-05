"""S05: liability-based risk limits, reservations, sizing and period breakers."""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import virtual_trading.engine as engine_mod
from shared.constants import (
    MAX_BET_LIABILITY_PCT,
    MAX_MATCH_LIABILITY_PCT,
    MAX_TEAM_LIABILITY_PCT,
)
from shared.schemas import BettingAction, OddsEvent, VirtualBet
from virtual_trading.engine import VirtualBetEngine
from virtual_trading.portfolio import PortfolioManager, worst_loss
from virtual_trading.risk_manager import RiskManager

EPS = 1e-6
ACTIONS = [a for a in BettingAction if a != BettingAction.HOLD]


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(engine_mod, "MIN_BET_INTERVAL_SECONDS", 0)


def _event(match_id="m1", home="India", away="Australia", back=2.0, lay=2.0) -> OddsEvent:
    return OddsEvent(
        match_id=match_id, team_home=home, team_away=away,
        timestamp=datetime.now(timezone.utc),
        back_home=back, lay_home=lay, back_away=back, lay_away=lay, is_live=True,
    )


def _make(balance=100_000.0, clock=None, budget=None):
    pf = PortfolioManager(initial_balance=balance, clock=clock)
    eng = VirtualBetEngine(pf, slippage_pct=0.0, rejection_rate=0.0, per_match_budget=budget)
    return pf, eng, eng.risk


def _loss(pf: PortfolioManager, amount: float, match_id="x") -> None:
    """Open a tiny bet and close it at a given (negative) P&L."""
    bet = VirtualBet(match_id=match_id, placed_at=datetime.now(timezone.utc),
                     action=BettingAction.BACK_HOME_SM, team="India", odds=2.0, stake=100.0)
    assert pf.open_position(bet)
    pf.close_position(bet.id, pnl=-amount)  # type: ignore[arg-type]


def test_single_max_bets_per_hour_definition():
    text = (Path(__file__).resolve().parents[2] / "shared" / "constants.py").read_text(encoding="utf-8")
    assert len(re.findall(r"^MAX_BETS_PER_HOUR\s*=", text, flags=re.M)) == 1


def test_bet_liability_never_exceeds_cap():
    rng = random.Random(7)
    for _ in range(400):
        odds = rng.uniform(1.1, 50.0)
        pf, eng, _ = _make()
        action = rng.choice(ACTIONS)
        wp = rng.choice([None, rng.uniform(0.01, 0.99)])
        p = eng.propose(action, _event(back=odds, lay=odds), rng.random(), win_prob=wp)
        if p is None:
            continue
        assert p.liability <= MAX_BET_LIABILITY_PCT * 100_000.0 + EPS
        bet = eng.commit(p)
        from virtual_trading.portfolio import bet_liability
        assert bet_liability(bet) <= MAX_BET_LIABILITY_PCT * 100_000.0 + EPS


def test_kelly_requires_edge():
    pf, eng, _ = _make()
    ev = _event(back=2.0, lay=2.0)
    assert eng.propose(BettingAction.BACK_HOME_LG, ev, 1.0, win_prob=0.5) is None  # no edge
    assert eng.propose(BettingAction.BACK_HOME_LG, ev, 1.0, win_prob=0.6) is not None
    assert eng.propose(BettingAction.LAY_AWAY_LG, ev, 1.0, win_prob=0.6) is None  # lay needs p<1/o
    assert eng.propose(BettingAction.LAY_AWAY_LG, ev, 1.0, win_prob=0.3) is not None


def test_checked_stake_is_committed_stake(monkeypatch):
    pf, eng, risk = _make()
    seen = {}
    orig_check, orig_commit = risk.check, eng.commit

    def check(p):
        seen["checked"] = p
        seen["stake"] = p.stake
        return orig_check(p)

    def commit(p, fill_odds=None):
        seen["committed"] = p
        return orig_commit(p, fill_odds)

    monkeypatch.setattr(risk, "check", check)
    monkeypatch.setattr(eng, "commit", commit)
    bet = eng.execute_action(BettingAction.BACK_HOME_LG, _event(), 0.9)
    assert bet is not None
    assert seen["checked"] is seen["committed"]
    assert bet.stake == seen["stake"]


def test_match_liability_cap_unless_reducing():
    rng = random.Random(3)
    clock = Clock(datetime(2026, 11, 2, 9, tzinfo=timezone.utc))
    pf, eng, risk = _make(clock=clock)
    committed = reducing = 0
    for _ in range(50):
        clock.advance(minutes=5)
        o = rng.uniform(1.5, 4.0)
        ev = _event(back=o, lay=o)
        p = eng.propose(rng.choice(ACTIONS), ev, rng.random())
        if p is None:
            continue
        if not risk.check(p).allowed:
            eng.release(p)
            continue
        eng.commit(p)
        committed += 1
        reducing += p.reduces_liability
        if not p.reduces_liability:
            book = pf.match_books()["m1"]
            assert worst_loss(book) <= MAX_MATCH_LIABILITY_PCT * pf.state.current_balance + EPS
    assert committed >= 3


def test_back_to_back_proposals_cannot_jointly_breach():
    pf, eng, risk = _make(budget=1e9)  # engine budget off; the risk manager must cap
    ev = _event(back=2.0, lay=2.0)
    kept, denied = [], 0
    for _ in range(6):  # none is committed before the next is proposed
        p = eng.propose(BettingAction.BACK_HOME_LG, ev, 1.0)
        assert p is not None
        if risk.check(p).allowed:
            kept.append(p)  # stays reserved
        else:
            denied += 1
            eng.release(p)
    assert kept and denied > 0
    cap = MAX_MATCH_LIABILITY_PCT * 100_000.0
    assert sum(p.liability for p in kept) <= cap + EPS
    for p in kept:
        eng.commit(p)
    assert worst_loss(pf.match_books()["m1"]) <= cap + EPS


def test_released_reservation_frees_capacity():
    pf, eng, risk = _make(budget=1e9)
    ev = _event()
    ps = [eng.propose(BettingAction.BACK_HOME_LG, ev, 1.0) for _ in range(5)]
    last = ps[-1]
    assert not risk.check(last).allowed  # the 4 other reservations count
    for p in ps[:3]:
        eng.release(p)
    assert risk.check(last).allowed


def test_same_team_cap_across_concurrent_matches():
    pf, eng, risk = _make(budget=1e9)
    e1 = _event("m1", "India", "Australia")
    e2 = _event("m2", "India", "England")
    for _ in range(3):  # ~9k on m1, below the 10% match cap
        p = eng.propose(BettingAction.BACK_HOME_LG, e1, 1.0)
        assert p is not None and risk.check(p).allowed
        eng.commit(p)
    denied = None
    for _ in range(4):
        p = eng.propose(BettingAction.BACK_HOME_LG, e2, 1.0)
        assert p is not None
        d = risk.check(p)
        if not d.allowed:
            denied = d
            eng.release(p)
            break
        eng.commit(p)
    assert denied is not None and any("Team 'India'" in v for v in denied.violations)
    assert not any("Match liability" in v for v in denied.violations)
    india = sum(
        worst_loss(b) for m, b in pf.match_books().items() if "India" in pf.match_teams(m)
    )
    assert india <= MAX_TEAM_LIABILITY_PCT * 100_000.0 + EPS


def test_daily_loss_halts_day1_allows_day2():
    clock = Clock(datetime(2026, 11, 2, 10, tzinfo=timezone.utc))  # Monday
    pf, eng, risk = _make(clock=clock)
    _loss(pf, 10_000.0)  # exactly 10% of the day's start balance
    p = eng.propose(BettingAction.BACK_HOME_SM, _event(), 0.8)
    assert p is not None
    d = risk.check(p)
    assert not d.allowed and any("Daily loss" in v for v in d.violations)
    eng.release(p)

    clock.advance(days=1)  # Tuesday, same ISO week
    p2 = eng.propose(BettingAction.BACK_HOME_SM, _event(), 0.8)
    assert p2 is not None
    assert risk.check(p2).allowed


def test_weekly_loss_halts_until_next_iso_week():
    clock = Clock(datetime(2026, 11, 2, 10, tzinfo=timezone.utc))  # Monday
    pf, eng, risk = _make(clock=clock)
    for _ in range(3):  # Mon, Tue, Wed: 9.5k each = 28.5k lost this week
        _loss(pf, 9_500.0)
        clock.advance(days=1)
    p = eng.propose(BettingAction.BACK_HOME_SM, _event(), 0.8)
    assert p is not None
    assert any("Weekly loss" in v for v in risk.check(p).violations)
    eng.release(p)

    clock.advance(days=5)  # next Monday: new ISO week and new day
    p2 = eng.propose(BettingAction.BACK_HOME_SM, _event(), 0.8)
    assert p2 is not None
    v = risk.check(p2).violations
    assert not any("Weekly loss" in x or "Daily loss" in x for x in v)
    assert pf.weekly_pnl == 0.0 and pf.state.daily_pnl == 0.0


def test_circuit_breaker_clears_after_cooldown():
    clock = Clock(datetime(2026, 11, 2, 10, tzinfo=timezone.utc))
    pf, eng, risk = _make(clock=clock)
    for _ in range(5):
        _loss(pf, 100.0)
        clock.advance(minutes=1)
    assert pf.is_circuit_breaker_active
    clock.advance(minutes=30)
    assert pf.is_circuit_breaker_active
    clock.advance(minutes=31)  # > 60 min since the last loss
    assert not pf.is_circuit_breaker_active


def test_reducing_bet_passes_breakers():
    pf, eng, risk = _make()
    back = VirtualBet(match_id="m1", placed_at=datetime.now(timezone.utc),
                      action=BettingAction.BACK_HOME_SM, team="India", odds=2.0, stake=3000.0)
    assert pf.open_position(back)
    pf.register_match("m1", "India", "Australia")
    _loss(pf, 10_000.0, match_id="other")  # daily loss breaker now active
    p = eng.propose(BettingAction.LAY_HOME_SM, _event(back=2.0, lay=2.0), 0.9)
    assert p is not None and p.reduces_liability
    assert risk.check(p).allowed
    assert eng.commit(p).id is not None
    # ... while a fresh risk-adding bet is blocked
    q = eng.propose(BettingAction.BACK_AWAY_SM, _event("m9", back=2.0, lay=2.0), 0.9)
    assert q is not None and not q.reduces_liability
    assert not risk.check(q).allowed
