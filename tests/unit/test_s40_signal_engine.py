"""S40: manual-bet signal engine. Parity with the S39 backtester, signal fields, paper fills, no-signal windows,
webhook gating, PPO shadow-only mode, and the live pre-match ratings lookup. No Redis, DB or webhook is used."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backtesting.execution import ExecutionModel
from backtesting.real_backtester import run_match
from shared.constants import MANUAL_LATENCY_SECONDS, SIGNAL_VALID_SECONDS
from strategy.base import MarketFeed, MatchedBet, OrderIntent, Position
from strategy.runner import StrategyRunner, epoch_of, live_execution
from strategy.signals import Signal, SignalBus
from strategy.value_policy import ValueStrategy, round_stake
from tests.unit.test_s38_fair_value_v2 import _make_matches
from strategy.ratings import HOME_ADVANTAGE_ELO, CricketRatings

BASE = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)
BANKROLL = 100_000.0


# ================================================================ builders
class FixedFair:
    """Stand-in fair model: P(home) is carried on the tick as ``fair_p`` (no sklearn needed)."""

    version = "v2"

    def predict_home(self, tick: dict[str, Any]) -> float:
        return float(tick.get("fair_p", 0.5))


class Recorder:
    """Wraps a strategy and records every intent with its tick index (for parity)."""

    def __init__(self, inner: Any, log: list[tuple[int, OrderIntent]], index_of: Any) -> None:
        self.inner = inner
        self.log = log
        self.index_of = index_of
        self.name = inner.name
        self.version = inner.version

    @property
    def last_details(self) -> list[dict[str, Any]]:
        return self.inner.last_details

    def reset(self, meta: dict[str, Any]) -> None:
        self.inner.reset(meta)

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        intents = self.inner.on_tick(tick, position)
        for it in intents:
            self.log.append((self.index_of(tick), it))
        return intents


def _tick(i: int, step: float = 20.0, **over: Any) -> dict[str, Any]:
    t = BASE + timedelta(seconds=i * step)
    tick: dict[str, Any] = {
        "match_id": "bf_1_100",
        "time": t.isoformat(),
        "team_home": "India",
        "team_away": "Australia",
        "competition": "IPL",
        "match_format": "t20",
        "family": "t20",
        "innings": 1,
        "wickets": 0,
        "overs": 0.0,
        "balls_remaining": 120,
        "back_home": 2.10,
        "lay_home": 2.14,
        "back_away": 1.95,
        "lay_away": 1.99,
        "fair_p": 0.6,
    }
    tick.update(over)
    return tick


def _scenario() -> list[dict[str, Any]]:
    """Entry at tick 0 (back home at 2.10), price drifts to a green-up level from tick 12 on."""
    ticks = []
    for i in range(40):
        lay_home = 1.85 if i >= 12 else 2.14
        ticks.append(_tick(i, lay_home=lay_home))
    return ticks


def _position(ticks: list[dict[str, Any]], k: int, matched=(), open_orders=()) -> Position:
    times = [epoch_of(t["time"]) for t in ticks[: k + 1]]
    pos = Position(match_id="bf_1_100", bankroll=BANKROLL, matched=list(matched), open_orders=list(open_orders))
    pos.feed = MarketFeed(ticks[: k + 1], times, k)
    return pos


def _call(strategy: ValueStrategy, tick: dict[str, Any], **kw: Any) -> list[OrderIntent]:
    return strategy.on_tick(dict(tick), _position([tick], 0, **kw))


class FakePortfolio:
    def __init__(self) -> None:
        self.state = SimpleNamespace(current_balance=BANKROLL)
        self.reservations: dict[str, Any] = {}

    def reserve(self, proposal: Any) -> None:
        self.reservations[proposal.reservation_id] = proposal

    def release(self, reservation_id: str) -> None:
        self.reservations.pop(reservation_id, None)


class FakeEngine:
    def __init__(self) -> None:
        self.portfolio = FakePortfolio()
        self.committed: list[Any] = []

    def commit(self, proposal: Any, fill_odds: float | None = None) -> Any:
        self.release(proposal)
        bet = SimpleNamespace(match_id=proposal.match_id, side=proposal.side, odds=fill_odds, stake=proposal.stake,
                              action=proposal.action, agent_version="")
        self.committed.append(bet)
        return bet

    def release(self, proposal: Any) -> None:
        self.portfolio.release(proposal.reservation_id)


class FakeRisk:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.recorded = 0

    def check(self, proposal: Any) -> SimpleNamespace:
        return SimpleNamespace(allowed=self.allowed, violations=[] if self.allowed else ["limit"])

    def record_bet(self) -> None:
        self.recorded += 1


class CollectingPost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, text: str) -> None:
        self.calls.append((url, text))


def _runner(tmp_path: Path, *, post: Any = None, webhook: str | None = None, allowed: bool = True,
            ) -> tuple[StrategyRunner, FakeEngine, FakeRisk, SignalBus]:
    engine = FakeEngine()
    risk = FakeRisk(allowed)
    bus = SignalBus(log_path=tmp_path / "signals.jsonl", post=post, webhook_url=webhook)
    runner = StrategyRunner(lambda tick: ValueStrategy(FixedFair()), engine, risk, bus,
                            bankroll=lambda: BANKROLL)
    return runner, engine, risk, bus


def _episode(ticks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"match_id": "bf_1_100", "family": "t20", "team_home": "India", "team_away": "Australia",
            "match_format": "t20", "competition": "IPL", "ticks": ticks}


# ================================================================ strategy maths
def test_round_stake_rounds_down_to_human_amounts() -> None:
    assert round_stake(3000.0) == 3000.0
    assert round_stake(199.0) == 150.0
    assert round_stake(1234.0) == 1000.0
    assert round_stake(5.0) == 0.0


def test_back_entry_price_limit_clears_min_edge_and_below_is_no_signal() -> None:
    strat = ValueStrategy(FixedFair(), min_edge=0.02, commission=0.02)
    intents = _call(strat, _tick(0))
    assert [(i.side, i.selection) for i in intents] == [("BACK", "home")]
    limit = intents[0].price_limit
    assert limit is not None and 1.5 < limit <= 2.10
    # Net edge at the limit clears min_edge: p(1 + (limit - h - 1)(1 - c)) - 1 >= 0.02 with h = 0.02.
    edge_at_limit = 0.6 * (1.0 + (limit - 0.02 - 1.0) * 0.98) - 1.0
    assert edge_at_limit >= 0.02 - 1e-9
    # A quote just under the limit is not signalled (a LAY on the away side may still be; that is a different bet).
    strat2 = ValueStrategy(FixedFair(), min_edge=0.02, commission=0.02)
    intents = _call(strat2, _tick(0, back_home=round(limit - 0.01, 2), lay_home=round(limit + 0.03, 2)))
    assert not any(i.side == "BACK" and i.selection == "home" for i in intents)


def test_commission_and_half_spread_reduce_the_edge() -> None:
    strat = ValueStrategy(FixedFair(), commission=0.0)
    no_comm = strat._ev("BACK", 0.6, 2.10, 0.0)[0]
    strat = ValueStrategy(FixedFair(), commission=0.02)
    with_comm = strat._ev("BACK", 0.6, 2.10, 0.0)[0]
    with_spread = strat._ev("BACK", 0.6, 2.10, 0.02)[0]
    assert no_comm == pytest.approx(0.26)
    assert with_comm < no_comm and with_spread < with_comm


def test_entry_stake_is_capped_by_liability_and_rounded() -> None:
    strat = ValueStrategy(FixedFair())
    intents = _call(strat, _tick(0, fair_p=0.95))  # huge edge: the 3 % liability cap binds
    assert intents[0].stake == pytest.approx(3000.0)


def test_entry_is_not_repeated_while_a_bet_is_pending() -> None:
    strat = ValueStrategy(FixedFair())
    first = _call(strat, _tick(0))
    assert first
    pending = [first[0]]
    assert _call(strat, _tick(1), open_orders=pending) == []


def test_green_up_exit_locks_profit_at_a_rounded_limit() -> None:
    strat = ValueStrategy(FixedFair(), quiet_after_change_s=0.0, commission=0.02)  # expected limit assumes 2%
    entry_tick = _tick(0)
    first = _call(strat, entry_tick)
    assert first[0].side == "BACK"
    matched = [MatchedBet("BACK", "home", 2.10, 3000.0, 0.0, 60.0, 3, "value_entry")]
    tick = _tick(12, lay_home=1.85)
    out = strat.on_tick(dict(tick), _position([entry_tick, tick], 1, matched=matched))
    assert len(out) == 1
    exit_ = out[0]
    assert exit_.side == "LAY" and exit_.selection == "home"
    assert exit_.reason == "exit_green_up"
    assert exit_.price_limit == pytest.approx(1.87)
    assert exit_.stake == pytest.approx(3000.0 * 2.10 / 1.85, abs=0.01)
    # The home selection is not re-entered after it was closed out (the away lay may still be offered).
    later = strat.on_tick(dict(_tick(13)), _position([tick], 0))
    assert not any(i.selection == "home" for i in later)


def test_no_signal_in_the_quiet_window_after_a_wicket() -> None:
    strat = ValueStrategy(FixedFair(), quiet_after_change_s=30.0)
    assert _call(strat, _tick(0, wickets=0)) != []          # edge present, state unchanged: signalled
    assert _call(strat, _tick(1, wickets=1)) == []          # wicket at t=20: quiet until t=50
    assert _call(strat, _tick(2, wickets=1)) == []          # t=40: still quiet
    assert _call(strat, _tick(3, wickets=1)) != []          # t=60: window over


# ================================================================ signal object and bus
def test_signal_carries_every_field_and_validity_is_after_creation() -> None:
    t = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)
    sig = Signal(id="abc", created_at=t, valid_until=t + timedelta(seconds=SIGNAL_VALID_SECONDS),
                 match="m", selection="home", side="BACK", price_limit=1.8, suggested_stake=500.0,
                 fair_prob=0.6, market_price=2.1, edge=0.05, reason="value_entry",
                 strategy_version="value:v2", kind="entry")
    d = sig.to_dict()
    for field in ("id", "created_at", "valid_until", "match", "selection", "side", "price_limit",
                  "suggested_stake", "fair_prob", "market_price", "edge", "reason", "strategy_version", "kind"):
        assert field in d
    assert len(sig.text_lines()) == 12
    with pytest.raises(ValueError):
        Signal(id="x", created_at=t, valid_until=t, match="m", selection="home", side="BACK", price_limit=None,
               suggested_stake=10.0, fair_prob=0.5, market_price=2.0, edge=0.0, reason="r",
               strategy_version="v", kind="entry")


async def test_duplicates_are_suppressed_while_valid(tmp_path: Path) -> None:
    bus = SignalBus(log_path=tmp_path / "s.jsonl", post=None, webhook_url=None)
    t = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)

    def make(created: datetime, selection: str = "home") -> Signal:
        return Signal(id=Signal.new_id(), created_at=created, valid_until=created + timedelta(seconds=120),
                      match="m", selection=selection, side="BACK", price_limit=1.8, suggested_stake=500.0,
                      fair_prob=0.6, market_price=2.1, edge=0.05, reason="value_entry",
                      strategy_version="value:v2", kind="entry")

    assert await bus.publish(make(t)) is True
    assert await bus.publish(make(t + timedelta(seconds=20))) is False  # same idea, still valid
    assert await bus.publish(make(t + timedelta(seconds=20), selection="away")) is True
    assert await bus.publish(make(t + timedelta(seconds=130))) is True  # first one has expired
    lines = (tmp_path / "s.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["kind"] == "entry"


async def test_webhook_called_only_when_env_var_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)
    sig = Signal(id="x", created_at=t, valid_until=t + timedelta(seconds=60), match="m", selection="home",
                 side="LAY", price_limit=2.0, suggested_stake=100.0, fair_prob=0.4, market_price=2.1,
                 edge=0.03, reason="value_entry", strategy_version="value:v2", kind="entry")
    monkeypatch.delenv("SIGNAL_WEBHOOK_URL", raising=False)
    post = CollectingPost()
    await SignalBus(log_path=tmp_path / "a.jsonl", post=post).publish(sig)
    assert post.calls == []

    monkeypatch.setenv("SIGNAL_WEBHOOK_URL", "https://hooks.example.invalid/fake")
    post = CollectingPost()
    await SignalBus(log_path=tmp_path / "b.jsonl", post=post).publish(sig)
    assert len(post.calls) == 1
    url, text = post.calls[0]
    assert url == "https://hooks.example.invalid/fake"
    assert "bet: LAY home" in text and "\n" in text


async def test_redis_and_webhook_failures_do_not_raise(tmp_path: Path) -> None:
    class BrokenRedis:
        async def publish_event(self, channel: str, data: dict) -> bool:
            raise ConnectionError("down")

    def broken_post(url: str, text: str) -> None:
        raise OSError("unreachable")

    t = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)
    sig = Signal(id="x", created_at=t, valid_until=t + timedelta(seconds=60), match="m", selection="home",
                 side="BACK", price_limit=None, suggested_stake=100.0, fair_prob=0.6, market_price=2.1,
                 edge=0.03, reason="value_entry", strategy_version="value:v2", kind="entry")
    bus = SignalBus(redis=BrokenRedis(), log_path=tmp_path / "c.jsonl", post=broken_post, webhook_url="https://x.invalid")
    assert await bus.publish(sig) is True
    assert (tmp_path / "c.jsonl").exists()


# ================================================================ parity with the S39 backtester
def test_live_runner_matches_backtest_intents_and_fills(tmp_path: Path) -> None:
    ticks = _scenario()
    episode = _episode(ticks)
    bt_log: list[tuple[int, OrderIntent]] = []
    bt_result = run_match(
        Recorder(ValueStrategy(FixedFair()), bt_log, lambda tick: tick_index(tick, ticks)),
        episode, live_execution(), bankroll=BANKROLL,
    )

    live_log: list[tuple[int, OrderIntent]] = []
    runner, engine, risk, _bus = _runner(tmp_path)
    runner._factory = lambda tick: Recorder(ValueStrategy(FixedFair()), live_log, lambda t: tick_index(t, ticks))

    async def drive() -> list[Any]:
        paper: list[Any] = []
        for tick in ticks:
            out = await runner.on_tick(dict(tick))
            paper.extend(out.paper)
        paper.extend((await runner.finish("bf_1_100")).paper)
        return paper

    paper = asyncio.run(drive())

    assert bt_log, "scenario must produce at least one intent"
    assert [(i.side, i.selection, i.price_limit, i.stake, i.reason) for _, i in live_log] == \
        [(i.side, i.selection, i.price_limit, i.stake, i.reason) for _, i in bt_log]
    assert [k for k, _ in live_log] == [k for k, _ in bt_log]

    bt_fills = [(b["side"], b["selection"], b["price"], round(b["stake"], 6), b["placed_at"], b["matched_at"])
                for b in bt_result["bets"]]
    live_fills = [(p.side, p.selection, p.price, round(p.stake, 6), p.placed_at, p.matched_at)
                  for p in paper if p.status == "filled"]
    assert bt_fills and live_fills == bt_fills
    assert len(engine.committed) == len(live_fills)


def tick_index(tick: dict[str, Any], ticks: list[dict[str, Any]]) -> int:
    return next(i for i, t in enumerate(ticks) if t["time"] == tick["time"])


def test_paper_fill_uses_the_price_one_latency_later(tmp_path: Path) -> None:
    ticks = _scenario()
    runner, engine, _risk, _bus = _runner(tmp_path)

    async def drive() -> list[Any]:
        paper: list[Any] = []
        for tick in ticks:
            paper.extend((await runner.on_tick(dict(tick))).paper)
        return paper

    paper = asyncio.run(drive())
    entry = next(p for p in paper if p.side == "BACK")
    assert entry.status == "filled"
    assert entry.matched_at - entry.placed_at >= MANUAL_LATENCY_SECONDS
    assert entry.matched_at - entry.placed_at < MANUAL_LATENCY_SECONDS + 20.0  # first tick after the latency


async def test_price_through_the_limit_is_recorded_as_missed(tmp_path: Path) -> None:
    ticks = [_tick(i, back_home=(1.50 if i >= 3 else 2.10)) for i in range(6)]
    runner, engine, _risk, _bus = _runner(tmp_path)
    paper: list[Any] = []
    for tick in ticks:
        paper.extend((await runner.on_tick(dict(tick))).paper)
    entry = next(p for p in paper if p.side == "BACK")
    assert entry.status == "missed"
    assert entry.price_limit is not None and entry.price_limit > 1.5
    assert engine.committed == []


async def test_paper_bet_is_tagged_as_signal_and_risk_checked(tmp_path: Path) -> None:
    ticks = _scenario()
    runner, engine, risk, _bus = _runner(tmp_path, allowed=False)
    paper: list[Any] = []
    for tick in ticks:
        paper.extend((await runner.on_tick(dict(tick))).paper)
    assert engine.committed == []
    assert any(p.status == "rejected" for p in paper)

    runner2, engine2, risk2, _ = _runner(tmp_path)
    paper2: list[Any] = []
    for tick in ticks:
        paper2.extend((await runner2.on_tick(dict(tick))).paper)
    filled = [p for p in paper2 if p.status == "filled"]
    assert filled and all(p.agent_version == "signal:value:v2" for p in filled)
    assert risk2.recorded == len(filled)


async def test_stale_tick_gives_no_intents(tmp_path: Path) -> None:
    runner, engine, _risk, _bus = _runner(tmp_path)
    tick = _tick(0)
    wall = epoch_of(tick["time"]) + 60.0
    out = await runner.on_tick(tick, wall_now=wall)
    assert out.signals == [] and out.paper == []


async def test_signal_outcome_flows_into_runner_signals(tmp_path: Path) -> None:
    runner, _engine, _risk, bus = _runner(tmp_path)
    out = await runner.on_tick(_tick(0))
    assert len(out.signals) == 1
    sig = out.signals[0]
    assert sig.kind == "entry" and sig.side == "BACK" and sig.selection == "home"
    assert sig.valid_until > sig.created_at
    assert sig.strategy_version == "value:v2"


def test_execution_model_is_manual_latency() -> None:
    model = live_execution()
    assert isinstance(model, ExecutionModel)
    assert model.bet_delay_s == MANUAL_LATENCY_SECONDS
    assert model.latency_s == 0.0


# ================================================================ live loop: PPO shadow and env switches
class _ShadowAgent:
    def __init__(self) -> None:
        self.calls = 0

    def predict(self, obs: Any, deterministic: bool = True) -> tuple[int, None]:
        self.calls += 1
        return 1, None  # BACK_HOME_SM: must only be logged


class _CountingEngine:
    def __init__(self) -> None:
        self.propose_calls = 0
        self.execute_calls = 0

    def propose(self, *a: Any, **k: Any) -> None:
        self.propose_calls += 1

    def execute_action(self, *a: Any, **k: Any) -> None:
        self.execute_calls += 1


class _FakeRunner:
    def __init__(self) -> None:
        self.ticks: list[dict[str, Any]] = []

    async def on_tick(self, tick: dict[str, Any], *, wall_now: float | None = None) -> Any:
        self.ticks.append(tick)
        return SimpleNamespace(signals=[], paper=[])


def test_value_mode_logs_ppo_action_and_never_places(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.schemas import OddsEvent
    from virtual_trading.live_trading_loop import LiveVirtualTradingLoop

    loop = object.__new__(LiveVirtualTradingLoop)
    loop._strategy_mode = "value"
    loop._agent = _ShadowAgent()
    loop._get_agent = None
    loop._engine = _CountingEngine()
    loop._runner = _FakeRunner()
    loop._open_bets = {}

    event = OddsEvent(match_id="bf_1_100", timestamp=BASE, team_home="India", team_away="Australia",
                      back_home=2.1, lay_home=2.14, back_away=1.95, lay_away=1.99, is_live=True)
    asyncio.run(loop._value_decision(event, None, obs=[0.0] * 50))

    assert loop._agent.calls == 1          # PPO was consulted for the shadow log
    assert loop._engine.propose_calls == 0  # ...and nothing was proposed or placed
    assert loop._engine.execute_calls == 0
    assert loop._open_bets == {}


def test_strategy_mode_and_placement_delay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from virtual_trading.live_trading_loop import _placement_delay, _strategy_mode

    monkeypatch.delenv("PHOENIX_STRATEGY", raising=False)
    assert _strategy_mode() == "value"
    monkeypatch.setenv("PHOENIX_STRATEGY", "bogus")
    assert _strategy_mode() == "value"
    monkeypatch.setenv("PHOENIX_STRATEGY", "ppo")
    assert _strategy_mode() == "ppo"

    monkeypatch.delenv("PLACEMENT_DELAY_MODE", raising=False)
    assert _placement_delay() == 0.0
    monkeypatch.setenv("PLACEMENT_DELAY_MODE", "random")
    assert 5.0 <= _placement_delay() <= 15.0


# ================================================================ live pre-match ratings (S38 integration note)
@pytest.fixture(scope="module")
def s38_ratings() -> CricketRatings:
    return CricketRatings(_make_matches())


def test_live_prematch_matches_historical_elo_difference(s38_ratings: CricketRatings) -> None:
    raws = _make_matches()
    raw = raws[0]  # first 2019 fixture: no prior matches, so no home-ground term for either side
    row_home, row_away = s38_ratings.rows[raw["match_id"]].home, s38_ratings.rows[raw["match_id"]].away
    day = raw["info"]["dates"][0]
    hist = s38_ratings.pre_match(raw["match_id"], day)
    live = s38_ratings.pre_match_live(row_home, row_away, "t20", "male", day, venue=None)
    assert live["pm_elo_home"] - live["pm_elo_away"] == pytest.approx(hist["pm_elo_home"] - hist["pm_elo_away"])
    assert live["pm_xi_home"] == 0.0 and live["pm_xi_away"] == 0.0  # XIs unknown: neutral difference
    assert live["pm_venue_chase_pct"] == 50.0 and live["pm_venue_n"] == 0.0


def test_unknown_team_is_neutral_and_logged_once(s38_ratings: CricketRatings) -> None:
    before = len(s38_ratings._unknown_logged)
    a = s38_ratings.pre_match_live("Zeta Kings", "Alpha", "t20", "male", "2030-01-01")
    b = s38_ratings.pre_match_live("Zeta Kings", "Alpha", "t20", "male", "2030-01-01")
    assert a == b
    assert a["pm_elo_home"] == s38_ratings._elo.base
    assert len(s38_ratings._unknown_logged) == before + 1


def test_live_prematch_is_cached_and_uses_the_caller_normaliser(s38_ratings: CricketRatings) -> None:
    calls: list[str] = []

    def canon(name: str) -> str | None:
        calls.append(name)
        return {"ALPHA XI": "Alpha"}.get(name.strip())

    first = s38_ratings.pre_match_live("ALPHA XI", "Bravo", "t20", "male", "2030-01-02", normalize_team=canon)
    size = len(s38_ratings._live_cache)
    second = s38_ratings.pre_match_live("ALPHA XI", "Bravo", "t20", "male", "2030-01-02", normalize_team=canon)
    assert first == second
    assert len(s38_ratings._live_cache) == size  # served from the cache
    assert "ALPHA XI" in calls
    assert first["pm_elo_home"] != s38_ratings._elo.base  # resolved to the known team "Alpha"


def test_home_advantage_constant_unchanged() -> None:
    assert HOME_ADVANTAGE_ELO == 35.0
