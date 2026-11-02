"""S45: trust and rolling-CLV kill measured against a configurable reference source (LotusBook by default)."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rl.graduation import GraduationEvaluator
from rl.lifecycle import graduation_handler as gh
from shared.constants import CLV_REFERENCE_SOURCE, CLV_REFERENCE_SOURCES, KILL_ALL_SCOPE, TRUST_CRITERIA
from strategy.trust import (
    LABEL_TRUSTED,
    LABEL_UNPROVEN,
    REASON_NO_BETFAIR,
    REASON_NO_REFERENCE,
    TrustRegistry,
    annotate_betfair,
    annotate_reference,
    betfair_clv,
    reference_clv,
)
from virtual_trading.kill_switch import KillSwitch

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
KICKOFF = NOW - timedelta(hours=3)
VERSION = "value:v1"


# ---------------------------------------------------------------- fakes
class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get_json(self, key: str) -> Any:
        value = self.store.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    async def set_json(self, key: str, data: dict, ttl: Any = None) -> None:
        self.store[key] = json.loads(json.dumps(data))


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: int = 1) -> None:
        self.now += timedelta(days=days)


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict]:
        return self._rows


class FakeSession:
    def __init__(self, bet_rows: list[dict], tick_rows: list[dict]) -> None:
        self._bet_rows = bet_rows
        self._tick_rows = tick_rows
        self.params: list[dict] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> _Result:
        self.params.append(params or {})
        if "agent_version" in (params or {}):
            return _Result(self._bet_rows)
        return _Result(self._tick_rows)


# ---------------------------------------------------------------- builders
def _bet(match_id: str = "m", *, placed: datetime, action: str = "BACK_HOME_SM", odds: float = 2.0,
         pnl: float | None = None, settled: datetime | None = None) -> dict[str, Any]:
    return {
        "bet_id": uuid.uuid4().hex, "match_id": match_id, "placed_at": placed, "settled_at": settled,
        "action": action, "odds": odds, "stake": 10.0, "profit_loss": pnl, "clv": 0.02,
        "team_home": "Alpha", "team_away": "Beta",
    }


def _tick(time: datetime, *, source: str = "lotusbook", live: bool = False, back_home: float | None = None,
          match_id: str = "m") -> dict[str, Any]:
    return {
        "match_id": match_id, "time": time, "source": source, "is_live": live,
        "competition": "Metro League", "team_home": "Alpha", "team_away": "Beta",
        "back_home": back_home, "lay_home": None, "back_away": None, "lay_away": None,
    }


def _passing_lotus_set(n_matches: int = 30, per_match: int = 10, pre_back: float = 1.8):
    """300 settled winning bets over 30 real matches; LotusBook pre-off price 1.8 vs entry 2.0 (CLV > 0)."""
    bets: list[dict[str, Any]] = []
    ticks: dict[str, list[dict[str, Any]]] = {}
    for m in range(n_matches):
        mid = f"m{m}"
        ticks[mid] = [_tick(KICKOFF - timedelta(minutes=10), back_home=pre_back, match_id=mid)]
        for _ in range(per_match):
            bets.append(_bet(mid, placed=KICKOFF - timedelta(minutes=30), pnl=10.0,
                             settled=KICKOFF + timedelta(hours=2)))
    return bets, ticks


def _registry(tmp_path: Path, clock: Clock, *, source: str) -> TrustRegistry:
    ev = GraduationEvaluator(state_path=tmp_path / "trust.json", now_fn=clock, criteria=TRUST_CRITERIA)
    return TrustRegistry(evaluator=ev, now_fn=clock, reference_source=source)


# ---------------------------------------------------------------- configuration
class TestConfiguration:
    def test_default_source_is_lotusbook_when_env_unset(self) -> None:
        assert set(CLV_REFERENCE_SOURCES) == {"lotusbook", "betfair"}
        assert CLV_REFERENCE_SOURCE in CLV_REFERENCE_SOURCES
        if "CLV_REFERENCE_SOURCE" not in os.environ:
            assert CLV_REFERENCE_SOURCE == "lotusbook"

    def test_old_names_still_import_and_write_betfair_field(self) -> None:
        ticks = [_tick(KICKOFF - timedelta(minutes=10), source="betfair", back_home=1.8)]
        bet = _bet("m", placed=KICKOFF - timedelta(minutes=30))
        row = annotate_betfair([bet], {"m": ticks})[0]
        assert row["betfair_clv"] == pytest.approx(1 / 1.8 - 1 / 2.0)
        assert row["reference_clv"] == row["betfair_clv"]
        assert betfair_clv(dict(bet, in_play=False), ticks) == pytest.approx(1 / 1.8 - 1 / 2.0)
        assert REASON_NO_BETFAIR == REASON_NO_REFERENCE == "insufficient reference-price evidence"


# ---------------------------------------------------------------- source selection in trust
class TestReferenceTrust:
    def test_lotusbook_evidence_becomes_trusted_under_default_source(self, tmp_path: Path) -> None:
        bets, ticks = _passing_lotus_set()
        clock = Clock()
        reg = _registry(tmp_path, clock, source="lotusbook")

        async def run() -> dict:
            entry: dict = {}
            for _ in range(14):
                entry = await reg.refresh(VERSION, bets, ticks, allocated_bankroll=1000.0,
                                          missed_count=60, n_bootstrap=200)
                clock.advance()
            return entry

        entry = asyncio.run(run())
        assert entry["ready"] is True
        assert entry["reference_source"] == "lotusbook"
        assert entry["metrics"]["clv_ci_lower"] > 0
        assert reg.trust_label(VERSION) == LABEL_TRUSTED

    def test_betfair_source_without_betfair_ticks_stays_unproven(self, tmp_path: Path) -> None:
        bets, ticks = _passing_lotus_set()  # LotusBook ticks only
        clock = Clock()
        reg = _registry(tmp_path, clock, source="betfair")

        entry = asyncio.run(reg.refresh(VERSION, bets, ticks, allocated_bankroll=1000.0,
                                        missed_count=60, n_bootstrap=200))
        assert entry["ready"] is False
        assert entry["metrics"]["clv_ci_lower"] is None
        assert reg.trust_label(VERSION) == LABEL_UNPROVEN
        assert REASON_NO_REFERENCE in reg.trust_reason(VERSION)
        assert "(reference: betfair)" in reg.trust_reason(VERSION)

    def test_only_configured_source_ticks_are_used(self) -> None:
        bet = _bet("m", placed=KICKOFF - timedelta(minutes=30))
        ticks = {"m": [_tick(KICKOFF - timedelta(minutes=10), source="betfair", back_home=1.8)]}
        assert annotate_reference([bet], ticks, source="lotusbook")[0]["reference_clv"] is None
        assert annotate_reference([bet], ticks, source="betfair")[0]["reference_clv"] == pytest.approx(
            1 / 1.8 - 1 / 2.0
        )

    def test_unknown_source_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            annotate_reference([], {}, source="pinnacle")


# ---------------------------------------------------------------- self-reference guard
class TestSelfReferenceGuard:
    def test_pre_match_reference_is_strictly_after_fill(self) -> None:
        placed = KICKOFF - timedelta(minutes=5)
        ticks = {"m": [
            _tick(placed, back_home=1.5),                          # the fill's own price: not a reference
            _tick(placed + timedelta(minutes=1), back_home=1.8),   # strictly later: the reference
        ]}
        row = annotate_reference([_bet("m", placed=placed, odds=2.0)], ticks)[0]
        assert row["in_play"] is False
        assert row["reference_clv"] == pytest.approx(1 / 1.8 - 1 / 2.0)

    def test_fill_tick_alone_gives_no_reference(self) -> None:
        placed = KICKOFF - timedelta(minutes=5)
        ticks = {"m": [_tick(placed, back_home=1.5)]}
        row = annotate_reference([_bet("m", placed=placed, odds=2.0)], ticks)[0]
        assert row["reference_clv"] is None

    def test_in_play_horizon_is_strictly_after_fill(self) -> None:
        placed = KICKOFF + timedelta(minutes=60)
        ticks = {"m": [
            _tick(placed, live=True, back_home=1.5),                          # fill tick: excluded
            _tick(placed + timedelta(minutes=5), live=True, back_home=1.2),   # before horizon
            _tick(placed + timedelta(minutes=10), live=True, back_home=1.9),  # horizon price
        ]}
        row = annotate_reference([_bet("m", placed=placed, odds=2.0)], ticks)[0]
        assert row["in_play"] is True
        assert row["reference_clv"] == pytest.approx(1 / 1.9 - 1 / 2.0)

    def test_in_play_fill_tick_alone_gives_no_reference(self) -> None:
        placed = KICKOFF + timedelta(minutes=60)
        ticks = {"m": [_tick(placed, live=True, back_home=1.5)]}
        row = annotate_reference([_bet("m", placed=placed, odds=2.0)], ticks)[0]
        assert row["reference_clv"] is None


# ---------------------------------------------------------------- rolling-CLV kill source
class TestKillSwitchSource:
    @pytest.mark.asyncio
    async def test_rolling_clv_kill_uses_configured_source(self) -> None:
        ticks = {"m": [_tick(KICKOFF - timedelta(minutes=10), back_home=2.0)]}  # LotusBook, against the bets
        bets = [_bet("m", placed=KICKOFF - timedelta(minutes=30), odds=1.8) for _ in range(100)]

        lotus = KillSwitch(FakeRedis(), now_fn=lambda: NOW, clv_window=100, n_bootstrap=200,
                           reference_source="lotusbook")
        reason = await lotus.evaluate_version(VERSION, bets, ticks, allocated_bankroll=1000.0)
        assert reason and "lotusbook" in reason
        assert lotus.blocked(VERSION) is not None

        betfair = KillSwitch(FakeRedis(), now_fn=lambda: NOW, clv_window=100, n_bootstrap=200,
                             reference_source="betfair")
        assert await betfair.evaluate_version(VERSION, bets, ticks, allocated_bankroll=1000.0) is None
        assert betfair.blocked(VERSION) is None

    @pytest.mark.asyncio
    async def test_kill_switch_default_source_is_configured_constant(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW)
        assert ks._reference_source == CLV_REFERENCE_SOURCE
        assert ks.blocked() is None and ks.blocked(KILL_ALL_SCOPE) is None


# ---------------------------------------------------------------- tick SQL source parameter
class TestTickQuery:
    def test_signal_ticks_sql_filters_on_source_parameter(self) -> None:
        assert "source = :source" in gh.SIGNAL_TICKS_SQL
        assert "'betfair'" not in gh.SIGNAL_TICKS_SQL

    def test_load_signal_evidence_passes_configured_source(self) -> None:
        bet_rows = [{"bet_id": "b1", "match_id": "m", "placed_at": KICKOFF, "settled_at": None,
                     "action": "BACK_HOME_SM", "odds": 2.0, "stake": 10.0, "profit_loss": 0.0,
                     "clv": None, "team_home": "Alpha", "team_away": "Beta"}]
        tick_rows = [_tick(KICKOFF, back_home=1.8)]
        session = FakeSession(bet_rows, tick_rows)
        bets, ticks = asyncio.run(gh.load_signal_evidence(session, "v1"))
        assert bets and "m" in ticks
        tick_params = session.params[-1]
        assert tick_params["source"] == CLV_REFERENCE_SOURCE
        assert tick_params["match_ids"] == ["m"]
