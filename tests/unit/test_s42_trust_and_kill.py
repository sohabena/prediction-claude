"""S42: trust gate (Betfair CLV, per-version streak), signal kill switch, trust-labelled signals."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from shared.constants import CLV_REFERENCE_SOURCE

from rl.graduation import GraduationEvaluator
from rl.lifecycle import graduation_handler as gh
from shared.constants import KILL_ALL_SCOPE, TRUST_CRITERIA
from shared.schemas import BettingAction
from strategy.signals import Signal, SignalBus
from strategy.trust import (
    LABEL_TRUSTED,
    LABEL_UNPROVEN,
    REASON_NO_BETFAIR,
    TrustedSignalBus,
    TrustRegistry,
    annotate_betfair,
    betfair_clv,
    trust_metrics,
)
from virtual_trading.kill_switch import KillSwitch
from virtual_trading.portfolio import BetProposal, PortfolioManager
from virtual_trading.risk_manager import RiskManager

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
KICKOFF = NOW - timedelta(hours=3)
VERSION = "value:v1"


# ---------------------------------------------------------------- fakes
class FakeRedis:
    """Async get_json/set_json over a dict (the ReliableRedis surface the kill switch and registry use)."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get_json(self, key: str) -> Any:
        value = self.store.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    async def set_json(self, key: str, data: dict, ttl: Any = None) -> None:
        self.store[key] = json.loads(json.dumps(data))


class BrokenRedis:
    async def get_json(self, key: str) -> Any:
        raise ConnectionError("redis down")

    async def set_json(self, key: str, data: dict, ttl: Any = None) -> None:
        raise ConnectionError("redis down")


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: int = 1) -> None:
        self.now += timedelta(days=days)


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class FakeSession:
    def __init__(self, bet_rows: list[dict], tick_rows: list[dict]) -> None:
        self._bet_rows = bet_rows
        self._tick_rows = tick_rows
        self.params: list[dict] = []
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> FakeResult:
        self.sql.append(str(stmt))
        self.params.append(params or {})
        if "agent_version" in (params or {}):
            return FakeResult(self._bet_rows)
        return FakeResult(self._tick_rows)


# ---------------------------------------------------------------- builders
def _bet(match_id: str, *, placed: datetime = KICKOFF - timedelta(minutes=30), action: str = "BACK_HOME_SM",
         odds: float = 2.0, pnl: float | None = None, settled: datetime | None = None,
         lotus_clv: float | None = 0.02, team_home: str = "Alpha", team_away: str = "Beta") -> dict[str, Any]:
    return {
        "bet_id": uuid.uuid4().hex, "match_id": match_id, "placed_at": placed, "settled_at": settled,
        "action": action, "odds": odds, "stake": 10.0, "profit_loss": pnl, "clv": lotus_clv,
        "team_home": team_home, "team_away": team_away,
    }


def _tick(time: datetime, *, live: bool = False, back_home: float | None = None,
          lay_home: float | None = None, competition: str = "Metro League") -> dict[str, Any]:
    return {
        "match_id": "m", "time": time, "is_live": live, "competition": competition,
        "team_home": "Alpha", "team_away": "Beta",
        "back_home": back_home, "lay_home": lay_home, "back_away": None, "lay_away": None,
    }


def _passing_set(n_matches: int = 30, per_match: int = 10, pre_back: float = 1.8, missed: int = 60):
    """300 settled winning bets over 30 real matches, Betfair pre-off price 1.8 vs entry 2.0 (CLV > 0)."""
    bets: list[dict[str, Any]] = []
    ticks: dict[str, list[dict[str, Any]]] = {}
    for m in range(n_matches):
        mid = f"m{m}"
        ticks[mid] = [_tick(KICKOFF - timedelta(minutes=10), back_home=pre_back)]
        for _ in range(per_match):
            bets.append(_bet(mid, pnl=10.0, settled=KICKOFF + timedelta(hours=2)))
    return bets, ticks, missed


def _refresh_kwargs(**over: Any) -> dict[str, Any]:
    return dict(allocated_bankroll=1000.0, missed_count=60, n_bootstrap=200, **over)


def _registry(tmp_path: Path, clock: Clock, *, kill: KillSwitch | None = None, redis: Any = None) -> TrustRegistry:
    ev = GraduationEvaluator(state_path=tmp_path / "trust.json", now_fn=clock, criteria=TRUST_CRITERIA)
    return TrustRegistry(redis=redis, kill_switch=kill, evaluator=ev, now_fn=clock)


def _signal(version: str = VERSION, match: str = "m0") -> Signal:
    return Signal(
        id=Signal.new_id(), created_at=NOW, valid_until=NOW + timedelta(seconds=120), match=match,
        selection="home", side="BACK", price_limit=1.9, suggested_stake=20.0, fair_prob=0.56,
        market_price=2.0, edge=0.03, reason="entry edge", strategy_version=version, kind="entry",
    )


# ---------------------------------------------------------------- Betfair CLV definition
class TestBetfairClv:
    def test_pre_match_uses_last_price_before_off(self) -> None:
        ticks = [
            _tick(KICKOFF - timedelta(minutes=10), back_home=1.8),
            _tick(KICKOFF - timedelta(seconds=30), back_home=1.7),
        ]
        bet = _bet("m", placed=KICKOFF - timedelta(minutes=5), odds=2.0)
        row = annotate_betfair([bet], {"m": ticks})[0]
        assert row["in_play"] is False
        assert row["betfair_clv"] == pytest.approx(1 / 1.7 - 1 / 2.0)

    def test_in_play_uses_same_side_price_ten_minutes_after_placement(self) -> None:
        placed = KICKOFF + timedelta(minutes=60)
        ticks = [
            _tick(KICKOFF - timedelta(minutes=5), back_home=1.8),
            _tick(placed - timedelta(seconds=20), live=True, back_home=1.5),
            _tick(placed + timedelta(minutes=5), live=True, back_home=1.2),   # not the horizon price
            _tick(placed + timedelta(minutes=10), live=True, back_home=1.9),  # horizon price
        ]
        bet = _bet("m", placed=placed, odds=2.0)
        row = annotate_betfair([bet], {"m": ticks})[0]
        assert row["in_play"] is True
        assert row["betfair_clv"] == pytest.approx(1 / 1.9 - 1 / 2.0)

    def test_lay_side_sign_is_reversed(self) -> None:
        ticks = [_tick(KICKOFF - timedelta(minutes=5), lay_home=1.9)]
        bet = _bet("m", placed=KICKOFF - timedelta(minutes=1), action="LAY_HOME_SM", odds=2.0)
        assert betfair_clv(dict(bet, in_play=False), sorted(ticks, key=lambda t: t["time"])) == pytest.approx(
            1 / 2.0 - 1 / 1.9
        )

    def test_no_betfair_price_gives_none(self) -> None:
        bet = _bet("m", placed=KICKOFF - timedelta(minutes=1))
        assert annotate_betfair([bet], {})[0]["betfair_clv"] is None

    def test_simulated_league_is_not_real(self) -> None:
        ticks = [_tick(KICKOFF - timedelta(minutes=5), back_home=1.8, competition="Virtual T20 League")]
        row = annotate_betfair([_bet("m", placed=KICKOFF - timedelta(minutes=1))], {"m": ticks})[0]
        assert row["is_real"] is False


# ---------------------------------------------------------------- trust gate
class TestTrustGate:
    def test_lotusbook_only_clv_stays_unproven_for_14_days(self, tmp_path: Path) -> None:
        import asyncio

        bets, _, missed = _passing_set()  # positive lotus CLV, no Betfair ticks at all
        clock = Clock()
        reg = _registry(tmp_path, clock)

        async def run() -> dict:
            entry = {}
            for _ in range(14):
                entry = await reg.refresh(VERSION, bets, {}, **_refresh_kwargs())
                clock.advance()
            return entry

        entry = asyncio.run(run())
        assert entry["metrics"]["lotusbook_clv_mean"] > 0
        assert entry["metrics"]["clv_ci_lower"] is None
        assert entry["ready"] is False  # an unmeasured Betfair CLV never passes
        assert reg.trust_label(VERSION) == LABEL_UNPROVEN
        assert REASON_NO_BETFAIR in reg.trust_reason(VERSION)

    def test_passing_evidence_becomes_trusted_after_streak(self, tmp_path: Path) -> None:
        import asyncio

        bets, ticks, missed = _passing_set()
        clock = Clock()
        reg = _registry(tmp_path, clock)

        async def run() -> None:
            for _ in range(13):
                await reg.refresh(VERSION, bets, ticks, **_refresh_kwargs())
                clock.advance()
            assert reg.trust_label(VERSION) == LABEL_UNPROVEN  # 13 days is not enough
            await reg.refresh(VERSION, bets, ticks, **_refresh_kwargs())

        asyncio.run(run())
        assert reg.trust_label(VERSION) == LABEL_TRUSTED
        assert reg.trust_reason(VERSION) == ""

    def test_metrics_respect_criteria_thresholds(self) -> None:
        bets, ticks, _ = _passing_set()
        m = trust_metrics(annotate_betfair(bets, ticks), allocated_bankroll=1000.0, missed_count=60,
                          n_bootstrap=200)
        assert m["bet_volume"] == 300 and m["min_matches"] == 30
        assert m["clv_ci_lower"] > 0
        assert m["pnl_per_match_ci_upper"] >= 0
        assert m["max_drawdown"] == 0.0
        assert m["missed_rate"] == pytest.approx(60 / 360)
        assert set(TRUST_CRITERIA) <= set(m)

    def test_unmeasured_missed_rate_fails(self) -> None:
        bets, ticks, _ = _passing_set()
        m = trust_metrics(annotate_betfair(bets, ticks), allocated_bankroll=1000.0, missed_count=None,
                          n_bootstrap=200)
        assert m["missed_rate"] is None

    def test_drawdown_fails_when_over_fifteen_percent(self) -> None:
        bets, ticks, _ = _passing_set()
        losing = [_bet("m0", placed=KICKOFF, pnl=-200.0, settled=KICKOFF + timedelta(hours=1))]
        m = trust_metrics(annotate_betfair(bets + losing, ticks), allocated_bankroll=1000.0,
                          missed_count=60, n_bootstrap=200)
        assert m["max_drawdown"] >= 0.15

    def test_reason_names_failed_criteria(self, tmp_path: Path) -> None:
        import asyncio

        bets, ticks, _ = _passing_set(n_matches=3, per_match=2)  # far too few bets
        clock = Clock()
        reg = _registry(tmp_path, clock)
        entry = asyncio.run(reg.refresh(VERSION, bets, ticks, **_refresh_kwargs()))
        assert "bet_volume" in entry["reason"]
        assert "min_matches" in entry["reason"]


class TestPerVersionStreak:
    def test_same_day_counts_once_and_versions_are_independent(self, tmp_path: Path) -> None:
        clock = Clock()
        ev = GraduationEvaluator(state_path=tmp_path / "g.json", now_fn=clock, criteria=TRUST_CRITERIA)
        ok = {"bet_volume": 999.0, "min_matches": 99.0, "clv_ci_lower": 0.1, "pnl_per_match_ci_upper": 1.0,
              "max_drawdown": 0.0, "missed_rate": 0.0}
        assert ev.evaluate_metrics(ok, version="value:v1").consecutive_days == 1
        assert ev.evaluate_metrics(ok, version="value:v1").consecutive_days == 1  # same day, counted once
        clock.advance()
        assert ev.evaluate_metrics(ok, version="value:v1").consecutive_days == 2
        assert ev.evaluate_metrics(ok, version="value:v2").consecutive_days == 1  # new version starts fresh
        assert ev.get_progress_pct("value:v1") == pytest.approx(100.0 * 2 / 14)

    def test_legacy_single_streak_file_loads_as_unversioned(self, tmp_path: Path) -> None:
        state = tmp_path / "legacy.json"
        state.write_text(json.dumps({"consecutive_days": 5, "last_pass_date": "2026-01-09", "daily_results": []}))
        ev = GraduationEvaluator(state_path=state, now_fn=Clock())
        assert ev.get_progress_pct() == pytest.approx(100.0 * 5 / 14)
        assert ev.get_progress_pct("value:v1") == 0.0


# ---------------------------------------------------------------- kill switch
class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_daily_loss_suspends_version_only(self) -> None:
        redis = FakeRedis()
        ks = KillSwitch(redis, now_fn=lambda: NOW)
        bets = [_bet("m0", pnl=-60.0, settled=NOW - timedelta(hours=1))]  # -6% of 1000
        reason = await ks.evaluate_version(VERSION, bets, {}, allocated_bankroll=1000.0)
        assert reason and "daily paper loss" in reason
        assert ks.blocked(VERSION) is not None
        assert ks.blocked("value:v2") is None
        assert ks.blocked() is None  # not global

    @pytest.mark.asyncio
    async def test_yesterdays_loss_does_not_count_today(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW)
        bets = [_bet("m0", pnl=-60.0, settled=NOW - timedelta(days=1, hours=2))]
        assert await ks.evaluate_version(VERSION, bets, {}, allocated_bankroll=1000.0) is None

    @pytest.mark.asyncio
    async def test_rolling_negative_clv_suspends_after_full_window(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW, clv_window=100, n_bootstrap=200)
        # S45: the reference tick must be strictly after the fill (pre-off price at KICKOFF - 30 s)
        ticks = {"m": [_tick(KICKOFF - timedelta(seconds=30), back_home=2.0)]}
        bets = [_bet("m", odds=1.8, placed=KICKOFF - timedelta(minutes=1)) for _ in range(99)]
        assert await ks.evaluate_version(VERSION, bets, ticks, allocated_bankroll=1000.0) is None  # window not full
        bets.append(_bet("m", odds=1.8, placed=KICKOFF - timedelta(minutes=1)))
        reason = await ks.evaluate_version(VERSION, bets, ticks, allocated_bankroll=1000.0)
        assert reason and "rolling reference CLV" in reason
        assert ks.blocked(VERSION) is not None

    @pytest.mark.asyncio
    async def test_stale_feed_suspends_globally(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW, stale_seconds=300.0)
        assert await ks.evaluate_feed({"m1": NOW - timedelta(seconds=10)}, ["m1"]) is None
        reason = await ks.evaluate_feed({"m1": NOW - timedelta(seconds=400)}, ["m1"])
        assert reason and "no live tick" in reason
        assert ks.blocked("any:version") is not None

    @pytest.mark.asyncio
    async def test_suspension_survives_restart_until_manual_reset(self) -> None:
        redis = FakeRedis()
        first = KillSwitch(redis, now_fn=lambda: NOW)
        await first.trip(VERSION, "daily paper loss test")

        restarted = KillSwitch(redis, now_fn=lambda: NOW)
        await restarted.load([VERSION])
        assert restarted.blocked(VERSION) == "daily paper loss test"

        await restarted.reset(VERSION, operator="operator")
        after_reset = KillSwitch(redis, now_fn=lambda: NOW)
        await after_reset.load([VERSION])
        assert after_reset.blocked(VERSION) is None

    @pytest.mark.asyncio
    async def test_reset_needs_an_operator(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW)
        await ks.trip(KILL_ALL_SCOPE, "x")
        with pytest.raises(ValueError):
            await ks.reset(KILL_ALL_SCOPE, operator="  ")
        assert ks.blocked() is not None

    @pytest.mark.asyncio
    async def test_unreadable_state_fails_closed(self) -> None:
        ks = KillSwitch(BrokenRedis(), now_fn=lambda: NOW)
        await ks.load([VERSION])
        assert ks.blocked() is not None
        assert "global" in ks.blocked(VERSION)


# ---------------------------------------------------------------- signals and risk
class TestTrustedSignals:
    @pytest.mark.asyncio
    async def test_unproven_signal_is_published_with_label(self, tmp_path: Path) -> None:
        log = tmp_path / "signals.jsonl"
        bus = TrustedSignalBus(SignalBus(log_path=log, webhook_url=""), _registry(tmp_path, Clock()))
        assert await bus.publish(_signal()) is True
        record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert record["trust"] == LABEL_UNPROVEN
        assert record["trust_reason"] == "not yet evaluated"
        assert record["strategy_version"] == VERSION

    @pytest.mark.asyncio
    async def test_suspended_version_emits_nothing(self, tmp_path: Path) -> None:
        log = tmp_path / "signals.jsonl"
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW)
        await ks.trip(VERSION, "test trip")
        bus = TrustedSignalBus(SignalBus(log_path=log, webhook_url=""), _registry(tmp_path, Clock(), kill=ks))
        assert await bus.publish(_signal()) is False
        assert not log.exists()

    @pytest.mark.asyncio
    async def test_trusted_signal_carries_trusted_label(self, tmp_path: Path) -> None:
        bets, ticks, _ = _passing_set()
        clock = Clock()
        reg = _registry(tmp_path, clock)
        for _ in range(14):
            await reg.refresh(VERSION, bets, ticks, **_refresh_kwargs())
            clock.advance()
        log = tmp_path / "signals.jsonl"
        bus = TrustedSignalBus(SignalBus(log_path=log, webhook_url=""), reg)
        assert await bus.publish(_signal()) is True
        record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert record["trust"] == LABEL_TRUSTED

    def test_text_lines_show_trust(self) -> None:
        from strategy.trust import with_trust

        line = with_trust(_signal(), LABEL_UNPROVEN, "insufficient Betfair evidence").text_lines()[-1]
        assert line.startswith("trust: unproven") and "insufficient Betfair evidence" in line


class TestRiskManagerKillSwitch:
    def _proposal(self, reduces: bool) -> BetProposal:
        return BetProposal(
            match_id="m", action=BettingAction.BACK_HOME_SM, team="Alpha", side="back", odds=2.0,
            stake=10.0, liability=10.0, reduces_liability=reduces, confidence=None,
            reservation_id=uuid.uuid4().hex,
        )

    @pytest.mark.asyncio
    async def test_global_suspension_blocks_new_risk_but_not_exits(self) -> None:
        ks = KillSwitch(FakeRedis(), now_fn=lambda: NOW)
        await ks.trip(KILL_ALL_SCOPE, "stale feed test")
        risk = RiskManager(PortfolioManager(initial_balance=1000.0), kill_switch=ks)
        blocked = risk.check(self._proposal(reduces=False))
        assert blocked.allowed is False
        assert any("kill switch" in v for v in blocked.violations)
        exit_decision = risk.check(self._proposal(reduces=True))
        assert not any("kill switch" in v for v in exit_decision.violations)

    def test_no_kill_switch_keeps_existing_behaviour(self) -> None:
        risk = RiskManager(PortfolioManager(initial_balance=1000.0))
        assert risk.check(self._proposal(reduces=False)).allowed is True


# ---------------------------------------------------------------- evidence loader
class TestSignalEvidenceLoader:
    @pytest.mark.asyncio
    async def test_unsettled_bets_have_no_profit_and_only_betfair_ticks_load(self) -> None:
        session = FakeSession(
            bet_rows=[
                _bet("m1", pnl=0.0, settled=None),
                _bet("m1", pnl=12.0, settled=NOW),
            ],
            tick_rows=[{"match_id": "m1", "time": KICKOFF, "competition": "Metro", "team_home": "A",
                        "team_away": "B", "is_live": False, "back_home": 1.8, "lay_home": None,
                        "back_away": None, "lay_away": None}],
        )
        bets, ticks = await gh.load_signal_evidence(session, VERSION)
        assert bets[0]["profit_loss"] is None
        assert bets[1]["profit_loss"] == 12.0
        assert session.params[0] == {"agent_version": "signal:value:v1"}
        # S45: the reference source is configurable (default lotusbook) and passed as a parameter
        assert session.params[1] == {"match_ids": ["m1"], "source": CLV_REFERENCE_SOURCE}
        assert "source = :source" in session.sql[1]
        assert list(ticks) == ["m1"]
