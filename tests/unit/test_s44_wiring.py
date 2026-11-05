"""S44: trust labels and the kill switch wired into the live signal path, outcome log, nightly refresh, CLI.

Everything runs on fakes: no Redis, no database, no bets. Files go to pytest's tmp_path only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from rl.graduation import GraduationEvaluator
from shared.constants import KILL_ALL_SCOPE, TRUST_CRITERIA
from strategy.base import OrderIntent
from strategy.runner import StrategyRunner, count_outcomes, live_execution, outcome_versions
from strategy.signals import SignalBus
from strategy.trust import TrustedSignalBus, TrustRegistry
from virtual_trading.kill_switch import KillSwitch

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- fakes
class FakeRedis:
    """Minimal async Redis stand-in: JSON get/set and pub/sub publish."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(data or {})
        self.published: list[tuple[str, Any]] = []

    async def get_json(self, key: str) -> Any:
        return self.data.get(key)

    async def set_json(self, key: str, data: dict[str, Any], ttl: Any = None) -> None:
        self.data[key] = json.loads(json.dumps(data, default=str))

    async def publish_event(self, channel: str, message: Any) -> None:
        self.published.append((channel, message))


class OneEntry:
    """Strategy that asks for one back-home order on its first tick, then nothing."""

    name = "value"
    version = "v2"

    def __init__(self, price_limit: float = 2.0) -> None:
        self.price_limit = price_limit
        self.last_details: list[dict[str, Any]] = []
        self._fired = False

    def reset(self, meta: dict[str, Any]) -> None:
        self._fired = False
        self.last_details = []

    def on_tick(self, tick: dict[str, Any], position: Any) -> list[OrderIntent]:
        if self._fired:
            self.last_details = []
            return []
        self._fired = True
        self.last_details = [{"kind": "entry", "fair_prob": 0.6, "market_price": 2.10, "edge": 0.03}]
        return [OrderIntent(side="BACK", selection="home", price_limit=self.price_limit, stake=100.0,
                            reason="value_entry")]


class _Portfolio:
    def __init__(self) -> None:
        self.state = SimpleNamespace(current_balance=10_000.0)

    def reserve(self, proposal: Any) -> None:
        pass

    def release(self, reservation: Any) -> None:
        pass


class FakeEngine:
    def __init__(self) -> None:
        self.portfolio = _Portfolio()
        self.commits: list[Any] = []

    def commit(self, proposal: Any, fill_odds: Any = None) -> Any:
        bet = SimpleNamespace(agent_version="", placed_at=proposal.odds)
        self.commits.append(bet)
        return bet

    def release(self, proposal: Any) -> None:
        pass


class FakeRisk:
    def check(self, proposal: Any) -> SimpleNamespace:
        return SimpleNamespace(allowed=True, violations=[])

    def record_bet(self) -> None:
        pass


def _tick(seconds: float, back_home: float = 2.10, match_id: str = "m1") -> dict[str, Any]:
    return {
        "match_id": match_id, "time": (T0 + timedelta(seconds=seconds)).isoformat(),
        "team_home": "India", "team_away": "Australia", "competition": "IPL",
        "family": "t20", "match_format": "t20",
        "back_home": back_home, "lay_home": round(back_home + 0.04, 2),
        "back_away": 1.95, "lay_away": 1.99,
    }


def _registry(tmp_path, kill: KillSwitch | None = None, redis: Any = None) -> TrustRegistry:
    evaluator = GraduationEvaluator(state_path=tmp_path / "trust_state.json", criteria=TRUST_CRITERIA)
    return TrustRegistry(redis=redis, kill_switch=kill, evaluator=evaluator)


def _runner(bus: Any, *, outcome_log: Any = None, price_limit: float = 2.0) -> StrategyRunner:
    return StrategyRunner(
        lambda tick: OneEntry(price_limit=price_limit), FakeEngine(), FakeRisk(), bus,
        execution=live_execution(), bankroll=lambda: 10_000.0, outcome_log_path=outcome_log,
    )


def _async_return(value: Any):
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value
    return _f


# ---------------------------------------------------------------- signal path: trust label and suppression
async def test_published_signal_carries_trust_label(tmp_path) -> None:
    log = tmp_path / "signals.jsonl"
    bus = TrustedSignalBus(SignalBus(log_path=log, webhook_url=""), _registry(tmp_path))
    out = await _runner(bus).on_tick(_tick(0))

    assert len(out.signals) == 1  # the runner's own Signal; the labelled copy is what gets published
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["trust"] == "unproven"
    assert row["strategy_version"] == "value:v2"


async def test_kill_on_version_means_signal_is_not_published(tmp_path) -> None:
    redis = FakeRedis()
    kill = KillSwitch(redis)
    await kill.trip("value:v2", "daily paper loss -600.00 <= -5% of allocated bankroll")
    registry = _registry(tmp_path, kill=kill)
    log = tmp_path / "signals.jsonl"
    bus = TrustedSignalBus(SignalBus(redis=redis, log_path=log, webhook_url=""), registry)

    runner = _runner(bus)
    out = await runner.on_tick(_tick(0))

    assert out.signals == []
    assert not log.exists()
    assert redis.published == []
    assert registry.trust_label("value:v2") == "suspended"
    # A suppressed signal is not paper-tracked: no pending order, so it can neither fill nor count as missed
    assert all(not st.pending for st in runner._matches.values())


# ---------------------------------------------------------------- live loop: kill switch, trust and feed check
async def _loop_with_fakes(monkeypatch, redis: FakeRedis):
    from virtual_trading import live_trading_loop as lt

    monkeypatch.setattr(lt, "get_redis", _async_return(redis))
    monkeypatch.setattr(lt, "_load_fair_models", lambda: {"t20": SimpleNamespace(version="v2")})
    monkeypatch.setattr(lt, "_load_ratings", lambda: None)
    return lt, lt.LiveVirtualTradingLoop(agent=None)


async def test_live_loop_risk_manager_and_runner_carry_the_kill_switch(monkeypatch) -> None:
    from strategy.trust import TrustedSignalBus as Bus

    lt, loop = await _loop_with_fakes(monkeypatch, FakeRedis())
    runner = await loop._get_runner()

    assert runner is loop._runner
    assert isinstance(loop._risk_manager.kill_switch, KillSwitch)
    assert loop._risk_manager.kill_switch is loop._kill_switch
    assert isinstance(runner._bus, Bus)
    assert loop.signal_guard() is not None


async def test_persisted_kill_is_loaded_at_boot(monkeypatch) -> None:
    redis = FakeRedis({"kill:value:v2": {"active": True, "reason": "rolling CLV"}})
    lt, loop = await _loop_with_fakes(monkeypatch, redis)
    await loop._get_runner()

    assert loop._kill_switch.blocked("value:v2") == "rolling CLV"
    assert loop._trust_registry.trust_label("value:v2") == "suspended"


async def test_feed_check_trips_global_kill_for_stale_live_match() -> None:
    from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

    now = datetime.now(timezone.utc)
    kill = KillSwitch(FakeRedis(), now_fn=lambda: now, stale_seconds=300)
    loop = LiveVirtualTradingLoop(agent=None)
    loop._kill_switch = kill
    loop._open_bets = {"m1": [(object(), now)]}
    loop._latest_events = {"m1": SimpleNamespace(timestamp=now - timedelta(seconds=600), is_live=True)}

    reason = await loop._check_feed()

    assert reason is not None and "m1" in reason
    assert kill.blocked() is not None


async def test_feed_check_ignores_finished_match_waiting_to_settle() -> None:
    from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

    now = datetime.now(timezone.utc)
    kill = KillSwitch(FakeRedis(), now_fn=lambda: now, stale_seconds=300)
    loop = LiveVirtualTradingLoop(agent=None)
    loop._kill_switch = kill
    loop._open_bets = {"m1": [(object(), now)]}
    loop._latest_events = {"m1": SimpleNamespace(timestamp=now - timedelta(hours=3), is_live=False)}

    assert await loop._check_feed() is None
    assert kill.blocked() is None


async def test_feed_check_is_a_no_op_before_the_runner_exists() -> None:
    from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

    loop = LiveVirtualTradingLoop(agent=None)
    assert loop.signal_guard() is None
    assert await loop._check_feed() is None


# ---------------------------------------------------------------- outcome log
async def test_missed_fill_writes_one_outcome_line(tmp_path) -> None:
    outcome_log = tmp_path / "signal_outcomes.jsonl"
    bus = SignalBus(log_path=tmp_path / "signals.jsonl", webhook_url="")
    runner = _runner(bus, outcome_log=outcome_log, price_limit=2.0)

    first = await runner.on_tick(_tick(0, back_home=2.10))
    assert len(first.signals) == 1
    out = await runner.on_tick(_tick(65, back_home=1.80))  # check tick: 1.80 is through the 2.00 limit

    assert [p.status for p in out.paper] == ["missed"]
    lines = outcome_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["outcome"] == "missed"
    assert rec["strategy_version"] == "value:v2"
    assert rec["match_id"] == "m1"
    assert rec["signal_id"] == first.signals[0].id
    assert count_outcomes("value:v2", None, outcome_log) == {"missed": 1, "lapsed": 0, "rejected": 0}


async def test_lapsed_order_is_logged_on_finish(tmp_path) -> None:
    outcome_log = tmp_path / "signal_outcomes.jsonl"
    runner = _runner(SignalBus(log_path=tmp_path / "s.jsonl", webhook_url=""), outcome_log=outcome_log)
    await runner.on_tick(_tick(0))
    out = await runner.finish("m1")

    assert [p.status for p in out.paper] == ["lapsed"]
    assert count_outcomes("value:v2", None, outcome_log)["lapsed"] == 1


async def test_filled_order_writes_no_outcome_line(tmp_path) -> None:
    outcome_log = tmp_path / "signal_outcomes.jsonl"
    runner = _runner(SignalBus(log_path=tmp_path / "s.jsonl", webhook_url=""), outcome_log=outcome_log)
    await runner.on_tick(_tick(0, back_home=2.10))
    out = await runner.on_tick(_tick(65, back_home=2.10))  # 2.10 clears the 2.00 limit

    assert [p.status for p in out.paper] == ["filled"]
    assert not outcome_log.exists()


async def test_runner_without_outcome_log_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = _runner(SignalBus(log_path=tmp_path / "s.jsonl", webhook_url=""), outcome_log=None)
    await runner.on_tick(_tick(0, back_home=2.10))
    await runner.on_tick(_tick(65, back_home=1.80))
    assert not any(tmp_path.rglob("signal_outcomes.jsonl"))


def test_count_outcomes_filters_version_and_window(tmp_path) -> None:
    path = tmp_path / "o.jsonl"
    rows = [
        {"time": "2026-10-01T10:00:00+00:00", "strategy_version": "value:v2", "outcome": "missed"},
        {"time": "2026-11-01T10:00:00+00:00", "strategy_version": "value:v2", "outcome": "lapsed"},
        {"time": "2026-11-01T11:00:00+00:00", "strategy_version": "value:v2", "outcome": "rejected"},
        {"time": "2026-11-01T11:00:00+00:00", "strategy_version": "value:v1", "outcome": "missed"},
        {"time": "2026-11-01T12:00:00+00:00", "strategy_version": "value:v2", "outcome": "filled"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    since = datetime(2026, 10, 15, tzinfo=timezone.utc)

    assert count_outcomes("value:v2", since, path) == {"missed": 0, "lapsed": 1, "rejected": 1}
    assert count_outcomes("value:v2", None, path) == {"missed": 1, "lapsed": 1, "rejected": 1}
    assert outcome_versions(since, path) == {"value:v1", "value:v2"}


def test_count_outcomes_on_missing_file_is_all_zero(tmp_path) -> None:
    assert count_outcomes("value:v2", None, tmp_path / "none.jsonl") == {"missed": 0, "lapsed": 0, "rejected": 0}
    assert outcome_versions(None, tmp_path / "none.jsonl") == set()


# ---------------------------------------------------------------- nightly trust refresh
class _FakeLoop:
    def __init__(self, guard: Any) -> None:
        self._guard = guard

    def signal_guard(self) -> Any:
        return self._guard


def _orch(guard: Any, db_versions: set[str] | None = None):
    from rl.orchestrator import Orchestrator

    orch = object.__new__(Orchestrator)
    orch._live_trading_loop = _FakeLoop(guard)

    async def _db(since: datetime) -> set[str]:
        return set(db_versions or ())

    orch._signal_versions_from_db = _db
    return orch


def _patch_outcomes(monkeypatch, versions: set[str], counts: dict[str, dict[str, int]]) -> None:
    import strategy.runner as sr

    monkeypatch.setattr(sr, "outcome_versions", lambda since=None, path=None: set(versions))
    monkeypatch.setattr(
        sr, "count_outcomes",
        lambda version, since=None, path=None: counts.get(version, {"missed": 0, "lapsed": 0, "rejected": 0}),
    )


async def test_nightly_refresh_calls_check_signal_version_once_per_version(monkeypatch) -> None:
    import rl.lifecycle.graduation_handler as gh

    calls: list[tuple] = []

    async def fake_check(version, *, registry, allocated_bankroll, kill_switch, missed_count=None):
        calls.append((version, registry, allocated_bankroll, kill_switch, missed_count))
        return {"ready": False}

    monkeypatch.setattr(gh, "check_signal_version", fake_check)
    _patch_outcomes(monkeypatch, {"value:v2"}, {"value:v2": {"missed": 3, "lapsed": 2, "rejected": 9}})
    registry, kill = object(), object()
    orch = _orch((registry, kill, 9500.0), db_versions={"value:v2", "value:v3"})
    before = dict(vars(orch))

    await orch._refresh_signal_trust(datetime(2026, 11, 2, 4, tzinfo=timezone.utc))

    assert [c[0] for c in calls] == ["value:v2", "value:v3"]  # one call per version, no duplicates
    v2 = calls[0]
    assert v2[4] == 5                                          # missed + lapsed; rejected is not counted
    assert all(c[1] is registry and c[2] == 9500.0 and c[3] is kill for c in calls)
    assert dict(vars(orch)) == before                          # orchestrator state is untouched


async def test_nightly_refresh_continues_after_one_version_fails(monkeypatch) -> None:
    import rl.lifecycle.graduation_handler as gh

    seen: list[str] = []

    async def fake_check(version, **kwargs):
        seen.append(version)
        if version == "value:v2":
            raise RuntimeError("db down")
        return {"ready": False}

    monkeypatch.setattr(gh, "check_signal_version", fake_check)
    _patch_outcomes(monkeypatch, {"value:v2", "value:v3"}, {})
    orch = _orch((object(), object(), 1000.0))
    await orch._refresh_signal_trust(datetime(2026, 11, 2, 4, tzinfo=timezone.utc))

    assert seen == ["value:v2", "value:v3"]


async def test_nightly_refresh_is_skipped_before_the_runner_exists(monkeypatch) -> None:
    import rl.lifecycle.graduation_handler as gh

    async def boom(*args, **kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(gh, "check_signal_version", boom)
    orch = _orch(None, db_versions={"value:v2"})
    await orch._refresh_signal_trust(datetime(2026, 11, 2, 4, tzinfo=timezone.utc))


async def test_nightly_schedule_runs_the_trust_refresh(monkeypatch) -> None:
    import rl.lifecycle.graduation_handler as gh
    from rl.orchestrator import OrchestratorState

    calls: list[str] = []

    async def fake_check(version, **kwargs):
        calls.append(version)
        return {"ready": False}

    class _Training:
        async def run_nightly_retrain(self, *args: Any) -> None:
            pass

        async def run_evaluation(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(gh, "check_signal_version", fake_check)
    _patch_outcomes(monkeypatch, {"value:v2"}, {})
    orch = _orch((object(), object(), 1000.0))
    orch._last_nightly_date = None
    orch._state = OrchestratorState.VIRTUAL_TRADING
    orch._trainer = None
    orch._stats = {}
    orch._model_version = 1
    orch._training_handler = _Training()

    async def no_candidate():
        return None

    orch._graduation_candidate_version = no_candidate
    await orch._check_nightly_schedule(datetime(2026, 11, 4, 4, tzinfo=timezone.utc))

    assert calls == ["value:v2"]


# ---------------------------------------------------------------- audit_clv --source
class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class _SourceSession:
    """Answers the audit's SELECTs. The tick query's source predicate is applied as written in its SQL text."""

    def __init__(self, bets: list[dict], ticks: list[dict]) -> None:
        self.bets, self.ticks = bets, ticks
        self.tick_sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Rows:
        sql = str(stmt)
        if "SET TRANSACTION READ ONLY" in sql:
            return _Rows([])
        if "FROM virtual_bets" in sql:
            return _Rows(self.bets)
        if "FROM odds_ticks" in sql:
            self.tick_sql.append(sql)
            allowed = {s for s in ("betfair", "lotusbook") if f"'{s}'" in sql}
            ids = set(params["match_ids"])
            return _Rows([t for t in self.ticks if t["match_id"] in ids and t["source"] in allowed])
        raise AssertionError(f"unexpected statement: {sql}")


def _audit_fixture() -> tuple[list[dict], list[dict]]:
    placed = T0
    bet = {
        "bet_id": "b1", "match_id": "m1", "placed_at": placed, "action": "BACK_HOME_SM", "team": "India",
        "odds": 2.0, "stake": 100.0, "closing_odds": 1.9, "clv": None, "agent_version": "signal:value:v2",
        "closing_source": "betfair", "team_home": "India", "team_away": "Australia",
    }
    # Only a LotusBook price exists 60 s after placement; the Betfair feed has nothing there.
    tick = {
        "match_id": "m1", "time": placed + timedelta(seconds=60), "competition": "IPL",
        "team_home": "India", "team_away": "Australia", "back_home": 1.9, "lay_home": 1.92,
        "back_away": None, "lay_away": None, "is_live": True, "source": "lotusbook",
    }
    return [bet], [tick]


async def test_audit_source_betfair_excludes_lotusbook_ticks() -> None:
    from scripts import audit_clv

    bets, ticks = _audit_fixture()
    betfair = _SourceSession(bets, ticks)
    report = await audit_clv.run_audit(betfair, limit=5, source="betfair")
    assert "source = 'betfair'" in betfair.tick_sql[0]
    assert "lotusbook" not in betfair.tick_sql[0].split("WHERE", 1)[1]
    assert report["overall"]["post_60s"]["n"] == 0

    lotus = await audit_clv.run_audit(_SourceSession(bets, ticks), limit=5, source="lotusbook")
    assert lotus["overall"]["post_60s"]["n"] == 1

    everything = await audit_clv.run_audit(_SourceSession(bets, ticks), limit=5, source="all")
    assert everything["overall"]["post_60s"]["n"] == 1


def test_audit_cli_defaults_to_reference_source_and_rejects_unknown_source(monkeypatch) -> None:
    from scripts import audit_clv

    seen: list[tuple[int, str]] = []

    async def fake_run_cli(limit: int, source: str) -> str:
        seen.append((limit, source))
        return "ok"

    monkeypatch.setattr(audit_clv, "_run_cli", fake_run_cli)
    assert audit_clv.main([]) == 0
    from shared.constants import CLV_REFERENCE_SOURCE
    assert seen == [(100, CLV_REFERENCE_SOURCE)]  # S45: default follows the reference source
    with pytest.raises(SystemExit):
        audit_clv.main(["--source", "bogus"])


# ---------------------------------------------------------------- signals_admin
def test_reset_without_operator_fails(monkeypatch) -> None:
    from scripts import signals_admin

    with pytest.raises(SystemExit) as exc:
        signals_admin.main(["reset", "--scope", "all"])
    assert exc.value.code == 2


def test_reset_with_operator_calls_kill_switch_reset(monkeypatch) -> None:
    from scripts import signals_admin

    redis = FakeRedis({"kill:__all__": {"active": True, "reason": "stale feed"}})
    monkeypatch.setattr(signals_admin, "get_redis", _async_return(redis))
    calls: list[tuple[str, str]] = []
    original = KillSwitch.reset

    async def spy(self, scope, *, operator):
        calls.append((scope, operator))
        await original(self, scope, operator=operator)

    monkeypatch.setattr(KillSwitch, "reset", spy)

    assert signals_admin.main(["reset", "--scope", "all", "--operator", "sohail"]) == 0
    assert calls == [(KILL_ALL_SCOPE, "sohail")]
    assert redis.data["kill:__all__"]["active"] is False
    assert redis.data["kill:__all__"]["reset_by"] == "sohail"


def test_reset_with_blank_operator_is_refused(monkeypatch, capsys) -> None:
    from scripts import signals_admin

    redis = FakeRedis({"kill:value:v2": {"active": True, "reason": "x"}})
    monkeypatch.setattr(signals_admin, "get_redis", _async_return(redis))

    assert signals_admin.main(["reset", "--scope", "value:v2", "--operator", "   "]) == 1
    assert redis.data["kill:value:v2"]["active"] is True
    assert "operator" in capsys.readouterr().err


async def test_status_reports_label_reason_and_active_kills() -> None:
    from scripts import signals_admin

    redis = FakeRedis({"kill:value:v2": {"active": True, "reason": "rolling CLV"}})
    report = await signals_admin.status(redis, ["value:v2", "value:v3"])
    text = signals_admin.format_status(report)

    labels = {row["version"]: row["label"] for row in report["trust"]}
    assert labels == {"value:v2": "suspended", "value:v3": "unproven"}
    assert report["kills"] == [{"scope": "value:v2", "reason": "rolling CLV"}]
    assert "rolling CLV" in text and "value:v3" in text


def test_show_prints_trust_labels_and_handles_pre_s44_lines(tmp_path) -> None:
    from scripts import signals_admin

    path = tmp_path / "signals.jsonl"
    old = {"created_at": "2026-11-02T10:00:00+00:00", "match": "m0", "side": "BACK", "selection": "home",
           "price_limit": 2.0, "suggested_stake": 50.0, "edge": 0.03, "strategy_version": "value:v1"}
    new = dict(old, match="m1", trust="unproven", trust_reason="not yet evaluated", strategy_version="value:v2")
    path.write_text("\n".join(json.dumps(r) for r in (old, new)) + "\n", encoding="utf-8")

    text = signals_admin.format_signals(signals_admin.recent_signals(2, path))
    assert "trust -" in text
    assert "trust unproven (not yet evaluated)" in text
    assert signals_admin.recent_signals(1, path)[0]["match"] == "m1"
