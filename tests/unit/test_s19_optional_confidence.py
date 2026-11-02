"""S19: engine accepts no confidence; NULL is persisted instead of 0.5."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import virtual_trading.engine as engine_mod
from shared.schemas import BettingAction, OddsEvent
from virtual_trading.engine import VirtualBetEngine
from virtual_trading.portfolio import PortfolioManager


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(engine_mod, "MIN_BET_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(engine_mod, "STAKE_NOISE_PERCENT", 0.0)
    monkeypatch.setattr(engine_mod, "humanize_stake", lambda x: round(x, 2))


def _event() -> OddsEvent:
    return OddsEvent(
        match_id="m1", team_home="India", team_away="Australia",
        timestamp=datetime.now(timezone.utc),
        back_home=2.0, lay_home=2.0, back_away=2.0, lay_away=2.0, is_live=True,
    )


def _engine() -> VirtualBetEngine:
    pf = PortfolioManager(initial_balance=100_000.0)
    return VirtualBetEngine(pf, slippage_pct=0.0, rejection_rate=0.0)


def test_none_confidence_unscaled_and_null():
    p = _engine().propose(BettingAction.BACK_HOME_SM, _event())
    full = _engine().propose(BettingAction.BACK_HOME_SM, _event(), confidence=1.0)
    assert p is not None and full is not None
    assert p.confidence is None
    assert p.stake == pytest.approx(full.stake)


def test_explicit_confidence_scales_as_before():
    eng = _engine()
    p = eng.propose(BettingAction.BACK_HOME_SM, _event(), confidence=0.8)
    full = _engine().propose(BettingAction.BACK_HOME_SM, _event(), confidence=1.0)
    assert p.confidence == 0.8
    assert p.stake == pytest.approx(full.stake * (0.25 + 0.75 * 0.8), rel=1e-6)


async def test_none_confidence_persisted_as_null():
    from virtual_trading.bet_persister import persist_bet

    eng = _engine()
    proposal = eng.propose(BettingAction.BACK_HOME_SM, _event())
    bet = eng.commit(proposal)
    assert bet is not None and bet.confidence is None

    session = MagicMock()
    cm = MagicMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = False
    with patch("shared.db.get_session", return_value=cm):
        await persist_bet(bet, "v1")
    record = session.add.call_args[0][0]
    assert record.confidence is None
