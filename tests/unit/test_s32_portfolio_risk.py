"""S32: per-competition liability cap and daily breaker that counts open worst-case risk.

The S05 caps (per-bet 3%, per-match 10%, per-team 12%, total 30%) still apply and are
covered by ``test_s05_risk.py``. The story's original numbers (3 matches at 18% each)
cannot hold under those caps, so the scenarios here use 2.9% per match and a lowered
competition cap where needed to isolate the competition check.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import virtual_trading.risk_manager as rm_mod
from shared.constants import MAX_DAILY_LOSS_PERCENT
from shared.schemas import BettingAction, VirtualBet
from virtual_trading.portfolio import BetProposal, PortfolioManager
from virtual_trading.risk_manager import RiskManager

BAL = 100_000.0
STAKE = 2_900.0  # 2.9%: under the 3% per-bet cap and the 10% per-match cap


def _pf() -> tuple[PortfolioManager, RiskManager]:
    pf = PortfolioManager(initial_balance=BAL)
    return pf, RiskManager(pf)


def _prop(match_id: str, stake: float = STAKE, *, reduces: bool = False,
          team: str = "Home") -> BetProposal:
    return BetProposal(
        match_id=match_id, action=BettingAction.BACK_HOME_SM, team=team, side="back",
        odds=2.0, stake=stake, liability=stake, reduces_liability=reduces,
        confidence=None, reservation_id=uuid.uuid4().hex,
    )


def _add_match(pf: PortfolioManager, risk: RiskManager, match_id: str,
               competition: str | None, stake: float = STAKE) -> tuple[BetProposal, bool]:
    """Register a match (own teams, so no team cap applies), risk-check, and reserve if allowed."""
    pf.register_match(match_id, f"H_{match_id}", f"A_{match_id}", competition=competition)
    p = _prop(match_id, stake)
    allowed = risk.check(p).allowed
    if allowed:
        pf.reserve(p)
    return p, allowed


def _open_back(pf: PortfolioManager, match_id: str, stake: float) -> None:
    bet = VirtualBet(match_id=match_id, placed_at=datetime.now(timezone.utc),
                     action=BettingAction.BACK_HOME_SM, team="Home", odds=2.0, stake=stake)
    assert pf.open_position(bet)


def _realised_loss(pf: PortfolioManager, amount: float) -> None:
    bet = VirtualBet(match_id="closed", placed_at=datetime.now(timezone.utc),
                     action=BettingAction.BACK_HOME_SM, team="Home", odds=2.0, stake=100.0)
    assert pf.open_position(bet)
    pf.close_position(bet.id, pnl=-amount)  # type: ignore[arg-type]


# ----------------------------------------------------------- competition cap

def test_competition_cap_default_is_below_total_cap():
    assert rm_mod.MAX_COMPETITION_LIABILITY_PCT == 0.15
    assert rm_mod.MAX_COMPETITION_LIABILITY_PCT < rm_mod.MAX_TOTAL_LIABILITY_PCT


@pytest.fixture
def _isolate_competition(monkeypatch):
    """Keep the daily breaker out of the way so only the liability caps are under test."""
    monkeypatch.setattr(rm_mod, "MAX_DAILY_LOSS_PERCENT", 1.0)


def test_competition_cap_allows_until_limit_then_rejects_and_isolates(monkeypatch, _isolate_competition):
    # Lowered to 20% so the competition check binds before the 30% total cap.
    monkeypatch.setattr(rm_mod, "MAX_COMPETITION_LIABILITY_PCT", 0.20)
    pf, risk = _pf()
    for i in range(6):  # 6 x 2.9% = 17.4% of IPL
        _, ok = _add_match(pf, risk, f"ipl{i}", "IPL")
        assert ok
    assert pf.competition_open_liability("IPL") == pytest.approx(6 * STAKE)

    p = _prop("ipl6")
    pf.register_match("ipl6", "H6", "A6", competition="IPL")
    d = risk.check(p)  # 7th IPL match: 20.3% > 20%
    assert not d.allowed
    assert any("Competition 'IPL'" in v for v in d.violations)
    assert not any("Total liability" in v for v in d.violations)

    # Another competition is unaffected by the full IPL cap.
    _, ok_bbl = _add_match(pf, risk, "bbl0", "BBL")
    assert ok_bbl


def test_default_cap_rejects_sixth_match_on_competition_before_total(_isolate_competition):
    # 5 x 2.9% = 14.5% of the balance: the 6th (17.4%) breaches the 15% competition cap, not the 30% total.
    pf, risk = _pf()
    for i in range(5):
        _, ok = _add_match(pf, risk, f"ipl{i}", "IPL")
        assert ok
    p = _prop("ipl5")
    pf.register_match("ipl5", "H5", "A5", competition="IPL")
    d = risk.check(p)
    assert not d.allowed
    assert any("Competition 'IPL'" in v for v in d.violations)
    assert not any("Total liability" in v for v in d.violations)


def test_unknown_competition_has_no_competition_cap(monkeypatch, _isolate_competition):
    monkeypatch.setattr(rm_mod, "MAX_COMPETITION_LIABILITY_PCT", 0.20)
    pf, risk = _pf()
    for i in range(7):  # 20.3% in total, but no competition registered
        _, ok = _add_match(pf, risk, f"m{i}", None)
        assert ok
    assert pf.competition_open_liability("IPL") == 0.0


def test_reducing_proposal_is_exempt_from_competition_cap(monkeypatch, _isolate_competition):
    monkeypatch.setattr(rm_mod, "MAX_COMPETITION_LIABILITY_PCT", 0.20)
    pf, risk = _pf()
    for i in range(6):
        _add_match(pf, risk, f"ipl{i}", "IPL")
    pf.register_match("ipl0", "H_ipl0", "A_ipl0", competition="IPL")
    hedge = _prop("ipl0", 1_000.0, reduces=True)
    assert risk.check(hedge).allowed


def test_competition_registration_survives_reregistration():
    pf, _ = _pf()
    pf.register_match("m1", "A", "B", competition="IPL")
    pf.register_match("m1", "A", "B")  # the engine re-registers on every proposal
    assert pf.match_competition("m1") == "IPL"
    assert pf.match_competition("unknown") is None


# ------------------------------------------------------------- daily breaker

def test_daily_breaker_ignores_open_worst_case_risk():
    pf, risk = _pf()
    _open_back(pf, "m1", 6_000.0)
    _open_back(pf, "m3", 4_000.0)  # open worst-case 10% of the balance, no realised loss
    d = risk.check(_prop("m2", 1_000.0))
    assert not any("Daily loss" in v for v in d.violations)
    assert pf.daily_loss_fraction() == pytest.approx(0.0)
    assert pf.open_unrealised_loss() == pytest.approx(10_000.0)


def test_daily_breaker_blocks_every_match_after_ten_percent_realised_loss():
    pf, risk = _pf()
    _realised_loss(pf, 10_000.0)  # exactly -10% of the day's start balance
    for m in ("m1", "m2", "m3"):
        d = risk.check(_prop(m))
        assert not d.allowed and any("Daily loss" in v for v in d.violations)
    shim = risk.check_all(100.0, "m1")
    assert not shim["allowed"] and "Daily loss limit" in shim["violations"]


def test_reducing_proposal_passes_daily_breaker():
    pf, risk = _pf()
    _open_back(pf, "m1", 2_000.0)
    _realised_loss(pf, 10_000.0)  # breaker tripped by realised loss
    assert not risk.check(_prop("m2", 1_000.0)).allowed
    hedge = _prop("m1", 1_000.0, reduces=True)
    assert risk.check(hedge).allowed


def test_daily_breaker_clears_after_rollover_with_no_open_risk():
    clock_t = [datetime(2026, 11, 2, 10, tzinfo=timezone.utc)]
    pf = PortfolioManager(initial_balance=BAL, clock=lambda: clock_t[0])
    risk = RiskManager(pf)
    _realised_loss(pf, 10_000.0)
    assert not risk.check(_prop("m1", 1_000.0)).allowed
    clock_t[0] = clock_t[0] + timedelta(days=1)
    assert risk.check(_prop("m1", 1_000.0)).allowed
