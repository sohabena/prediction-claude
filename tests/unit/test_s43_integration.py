"""S43: one manual execution model, the live strategy object in the backtest, pre-match ratings wired into live.

No Redis, DB, network or real model file is used. Ratings come from the S38 synthetic Cricsheet fixture or fakes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backtesting.execution import ExecutionModel
from backtesting.real_backtester import run_match
from scripts import run_real_backtest as rrb
from scripts.cricsheet_harmonizer import canonicalize_team
from shared.schemas import OddsEvent
from strategy import live_context as lc
from strategy.base import OrderIntent
from strategy.ratings import CricketRatings
from strategy.runner import StrategyRunner, live_execution
from strategy.signals import SignalBus
from strategy.value_policy import ValueStrategy
from tests.unit.test_s38_fair_value_v2 import _make_matches
from virtual_trading import live_trading_loop as live
from virtual_trading.decision_gate import DecisionGate

BANKROLL = 100_000.0
E0 = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)
STEP = 5.0
N_TICKS = 40
PRICE_MOVE_INDEX = 16  # the price moves at E0 + 80 s, outside the fill window (E0 + 60 s .. E0 + 65 s)


# ================================================================ builders
class OneEntry:
    """Strategy that places one BACK home order on its first tick (both paths call it once per tick)."""

    name = "value"
    version = "v2"

    def __init__(self) -> None:
        self.last_details: list[dict[str, Any]] = []
        self._calls = 0

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        self._calls = 0
        self.last_details = []

    def on_tick(self, tick: dict[str, Any], position: Any) -> list[OrderIntent]:
        self._calls += 1
        self.last_details = []
        if self._calls > 1:
            return []
        self.last_details = [{"kind": "entry", "fair_prob": 0.6, "market_price": 2.10, "edge": 0.03}]
        return [OrderIntent(side="BACK", selection="home", price_limit=None, stake=100.0, reason="value_entry")]


class _Portfolio:
    def __init__(self) -> None:
        self.state = SimpleNamespace(current_balance=BANKROLL)

    def reserve(self, proposal: Any) -> None:
        return None

    def release(self, reservation_id: Any) -> None:
        return None


class FakeEngine:
    def __init__(self) -> None:
        self.portfolio = _Portfolio()
        self.committed: list[Any] = []

    def commit(self, proposal: Any, fill_odds: float | None = None) -> Any:
        bet = SimpleNamespace(match_id=proposal.match_id, side=proposal.side, odds=fill_odds, stake=proposal.stake,
                              action=proposal.action, agent_version="")
        self.committed.append(bet)
        return bet

    def release(self, proposal: Any) -> None:
        return None


class FakeRisk:
    def check(self, proposal: Any) -> SimpleNamespace:
        return SimpleNamespace(allowed=True, violations=[])

    def record_bet(self) -> None:
        return None


class FakeRatings:
    """Stand-in for CricketRatings.pre_match_live: Elo for India 1510, every other team 1490; records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def pre_match_live(self, team_home: str, team_away: str, family: str, gender: str, as_of: str,
                       *, venue: Any = None, toss_winner: Any = None, toss_decision: Any = None,
                       normalize_team: Any = None) -> dict[str, float]:
        self.calls.append((team_home, team_away, family, gender, as_of))
        return {
            "pm_elo_home": 1510.0 if team_home == "India" else 1490.0,
            "pm_elo_away": 1510.0 if team_away == "India" else 1490.0,
            "pm_xi_home": 0.0, "pm_xi_away": 0.0, "pm_toss_home": 0.0, "pm_toss_bat": 0.5,
            "pm_venue_chase_pct": 50.0, "pm_venue_n": 0.0, "pm_tier": 2.0,
        }


class FakeFair:
    """Stand-in for FairValueModel: records the constructor arguments and the fit ticks; P(home) = 0.6."""

    def __init__(self, family: str = "t20", stride: int = 3, calib_frac: float = 0.2, seed: int = 0,
                 version: str = "v1") -> None:
        self.family, self.seed, self.version = family, seed, version
        self.fit_ticks: list[dict[str, Any]] = []
        self.train_match_ids: list[str] = []

    def fit(self, episodes: Any, calib_episodes: Any = None) -> "FakeFair":
        eps = [e for e in episodes if e]
        self.fit_ticks = [t for ep in eps for t in ep]
        self.train_match_ids = [str(ep[0].get("match_id", "")) for ep in eps]
        return self

    def predict_home(self, tick: dict[str, Any]) -> float:
        return 0.6


def _exchange_ticks() -> list[dict[str, Any]]:
    """Exchange-stamped ticks every 5 s. The back price steps at PRICE_MOVE_INDEX."""
    ticks = []
    for i in range(N_TICKS):
        back = 2.10 if i < PRICE_MOVE_INDEX else 2.30
        ticks.append({
            "match_id": "bf_1_200", "time": (E0 + timedelta(seconds=i * STEP)).isoformat(),
            "team_home": "India", "team_away": "Australia", "competition": "IPL",
            "family": "t20", "match_format": "t20",
            "back_home": back, "lay_home": round(back + 0.04, 2), "back_away": 1.95, "lay_away": 1.99,
        })
    return ticks


def _arrival_stamped(ticks: list[dict[str, Any]], lag_s: float) -> list[dict[str, Any]]:
    """The same ticks as the live scraper stamps them: each one arrives ``lag_s`` after its exchange time."""
    out = []
    for t in ticks:
        u = dict(t)
        u["time"] = (datetime.fromisoformat(t["time"]) + timedelta(seconds=lag_s)).isoformat()
        out.append(u)
    return out


async def _drive(runner: StrategyRunner, ticks: list[dict[str, Any]]) -> list[Any]:
    paper: list[Any] = []
    for tick in ticks:
        paper.extend((await runner.on_tick(dict(tick))).paper)
    paper.extend((await runner.finish(str(ticks[0]["match_id"]))).paper)
    return paper


def _ep(match_id: str, season: int, idx: int, home: str = "India", away: str = "Australia",
        winner_is_home: bool = True) -> dict[str, Any]:
    """Real-corpus style episode: ticks carry the match-state-free price fields, the result sits on tick 0."""
    start = datetime(season, 6, 1 + idx, 10, 0, tzinfo=timezone.utc)
    ticks = []
    for i in range(12):
        ticks.append({
            "match_id": match_id, "time": (start + timedelta(seconds=60 * i)).isoformat(),
            "team_home": home, "team_away": away, "competition": "IPL", "family": "t20", "match_format": "t20",
            "back_home": 2.10, "lay_home": 2.14, "back_away": 1.95, "lay_away": 1.99, "in_play": False,
        })
    ticks[0]["_meta"] = {"winner": home if winner_is_home else away, "loser": away if winner_is_home else home,
                         "winner_is_home": winner_is_home, "result_type": "win"}
    return {
        "match_id": match_id, "market_id": f"1.{idx}", "family": "t20", "match_format": "t20",
        "competition": "IPL", "team_home": home, "team_away": away, "data_tier": "real",
        "start": ticks[0]["time"], "ticks": ticks, "competition_tier": "other",
    }


def _seasons() -> list[dict[str, Any]]:
    return [
        _ep("bf_2023_1", 2023, 0), _ep("bf_2023_2", 2023, 1), _ep("bf_2023_3", 2023, 2, winner_is_home=False),
        _ep("bf_2024_1", 2024, 0), _ep("bf_2024_2", 2024, 1, "Sydney Sixers W", "Perth Scorchers W"),
    ]


def _live_tick(time_iso: str = "2024-01-05T10:00:00+00:00", **over: Any) -> dict[str, Any]:
    tick = {"match_id": "bf_1_300", "time": time_iso, "team_home": "India", "team_away": "Australia",
            "competition": "IPL", "family": "t20", "match_format": "t20", "back_home": 2.1, "lay_home": 2.14}
    tick.update(over)
    return tick


# ================================================================ one manual execution model
def test_manual_preset_is_five_second_lag_then_sixty_seconds_to_place() -> None:
    model = ExecutionModel.preset("manual")
    assert model.latency_s == 5.0
    assert model.bet_delay_s == 60.0
    assert model.commission == 0.05  # unchanged from S39


def test_live_execution_keeps_the_manual_bet_delay_without_decision_latency() -> None:
    model = live_execution()
    assert model.latency_s == 0.0
    assert model.bet_delay_s == ExecutionModel.preset("manual").bet_delay_s


def test_backtest_and_live_fill_the_same_signal_at_the_same_time_and_price(tmp_path: Path) -> None:
    exchange = _exchange_ticks()
    episode = {"match_id": "bf_1_200", "family": "t20", "match_format": "t20", "competition": "IPL",
               "team_home": "India", "team_away": "Australia", "start": exchange[0]["time"], "ticks": exchange}
    bt = run_match(OneEntry(), episode, ExecutionModel.preset("manual"), bankroll=BANKROLL)
    assert len(bt["bets"]) == 1
    bet = bt["bets"][0]

    engine = FakeEngine()
    bus = SignalBus(log_path=tmp_path / "signals.jsonl", post=lambda url, text: None)
    runner = StrategyRunner(lambda tick: OneEntry(), engine, FakeRisk(), bus,
                            execution=live_execution(), bankroll=lambda: BANKROLL)
    paper = asyncio.run(_drive(runner, _arrival_stamped(exchange, lag_s=5.0)))
    fills = [p for p in paper if p.status == "filled"]
    assert len(fills) == 1
    fill = fills[0]

    assert fill.placed_at == pytest.approx(bet["placed_at"])     # same decision time: E0 + 5 s
    assert fill.matched_at == pytest.approx(bet["matched_at"])   # same fill time: E0 + 65 s
    assert fill.matched_at - fill.placed_at == pytest.approx(60.0)
    assert bet["matched_at"] - bet["placed_at"] == pytest.approx(60.0)
    assert fill.price == bet["price"] == 2.10
    assert fill.stake == pytest.approx(bet["stake"])


# ================================================================ strategy object and pre-match fields
def test_build_strategy_value_is_the_live_object_with_the_requested_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rrb, "FairValueModel", FakeFair)
    episodes = _seasons()[:3]
    strategy, fitted, skip = rrb.build_strategy("value", episodes, "t20", ExecutionModel.preset("manual"), seed=0)
    assert skip is None
    assert isinstance(strategy, ValueStrategy)
    assert strategy.version == "v2"
    assert strategy.fair.version == "v2"
    assert set(fitted) == {ep["match_id"] for ep in episodes}


def test_build_strategy_v1_stays_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rrb, "FairValueModel", FakeFair)
    strategy, _fitted, _skip = rrb.build_strategy("value", _seasons()[:3], "t20", ExecutionModel(), 0,
                                                  model_version="v1")
    assert isinstance(strategy, ValueStrategy) and strategy.version == "v1"
    with pytest.raises(ValueError):
        rrb.build_strategy("value", _seasons()[:3], "t20", ExecutionModel(), 0, model_version="v9")


def test_v2_value_walk_forward_ticks_carry_prematch_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[FakeFair] = []

    class RecordingFair(FakeFair):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(rrb, "FairValueModel", RecordingFair)
    ratings = FakeRatings()
    report = rrb.run_walk_forward(_seasons(), "value", "t20", ExecutionModel.preset("manual"),
                                  n_bootstrap=200, seed=0, model_version="v2", ratings=ratings)

    assert report["model_version"] == "v2" and report["prematch_fields"] is True
    assert [f["test_season"] for f in report["folds"]] == [2024]  # 2023 has no earlier seasons to train on
    assert made and all(m.version == "v2" for m in made)
    assert all("pm_elo_home" in t for m in made for t in m.fit_ticks)
    # as of the match date, with the caller's gender rule (women's sides and competitions)
    assert ("India", "Australia", "t20", "male", "2023-06-01") in ratings.calls
    assert ("Sydney Sixers", "Perth Scorchers", "t20", "female", "2024-06-02") in ratings.calls  # " W" stripped for rating lookups (S67)


def test_v2_value_walk_forward_needs_ratings() -> None:
    with pytest.raises(ValueError):
        rrb.run_walk_forward(_seasons(), "value", "t20", ExecutionModel.preset("manual"), n_bootstrap=50)


def test_enrich_leaves_multiday_episodes_unchanged() -> None:
    ep = _ep("bf_x", 2024, 0)
    ep["family"] = "multiday"
    rrb.enrich_episodes([ep], FakeRatings())
    assert all("pm_elo_home" not in t for t in ep["ticks"])


def test_live_prematch_gives_an_elo_difference_for_known_teams_and_neutral_for_unknown() -> None:
    raws = _make_matches()
    ratings = CricketRatings(raws)
    row = ratings.rows[raws[-1]["match_id"]]
    day = raws[-1]["info"]["dates"][0]
    known = lc.attach_prematch(_live_tick(f"{day}T10:00:00+00:00", team_home=row.home, team_away=row.away),
                               ratings, normalize_team=canonicalize_team)
    assert known["pm_elo_home"] - known["pm_elo_away"] != 0.0

    unknown = lc.attach_prematch(_live_tick(team_home="Zeta Kings", team_away="Omega Ducks"), ratings)
    assert unknown["pm_elo_home"] == ratings._elo.base
    assert unknown["pm_elo_away"] == ratings._elo.base


def test_attach_prematch_is_once_per_match_and_day_and_never_mutates_its_input() -> None:
    ratings = FakeRatings()
    tick = _live_tick()
    out = lc.attach_prematch(tick, ratings)
    again = lc.attach_prematch(_live_tick("2024-01-05T11:30:00+00:00"), ratings)
    assert len(ratings.calls) == 1
    assert out["pm_elo_home"] == 1510.0 and again["pm_elo_home"] == 1510.0
    assert "pm_elo_home" not in tick

    lc.attach_prematch(_live_tick("2024-01-06T10:00:00+00:00"), ratings)
    assert len(ratings.calls) == 2  # a new day is a new lookup


def test_prematch_is_empty_without_ratings_teams_or_a_rated_family() -> None:
    assert lc.prematch_fields(_live_tick(), None) == {}
    assert lc.prematch_fields(_live_tick(team_home=""), FakeRatings()) == {}
    assert lc.prematch_fields(_live_tick(family="multiday"), FakeRatings()) == {}


def test_gender_rule_for_live_and_backtest_names() -> None:
    assert lc.gender_of("India", "Australia") == "male"
    assert lc.gender_of("Sydney Sixers W", "Perth Scorchers W") == "female"
    assert lc.gender_of("Delhi Capitals Women", "Mumbai Indians Women") == "female"
    assert lc.gender_of("India", "Australia", "Women's T20 Challenge") == "female"


# ================================================================ ratings cache
def test_load_ratings_reuses_the_cache_until_the_cricsheet_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Path] = []

    def fake_build(root: Path) -> CricketRatings:
        built.append(root)
        return CricketRatings(_make_matches())

    monkeypatch.setattr(lc, "build_ratings", fake_build)
    data = tmp_path / "data"
    (data / "raw" / "ipl").mkdir(parents=True)
    (data / "raw" / "ipl" / "1.json").write_text("{}", encoding="utf-8")
    cache = tmp_path / "ratings.joblib"

    first = lc.load_ratings(data, cache=cache)
    second = lc.load_ratings(data, cache=cache)
    assert len(built) == 1
    assert len(second.rows) == len(first.rows) > 0
    assert second.pre_match_live("India", "Australia", "t20", "male", "2030-01-01") == \
        first.pre_match_live("India", "Australia", "t20", "male", "2030-01-01")

    (data / "raw" / "ipl" / "2.json").write_text("{}", encoding="utf-8")
    lc.load_ratings(data, cache=cache)
    assert len(built) == 2  # new Cricsheet file: fingerprint changed


def test_load_ratings_rebuilds_when_the_cache_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "build_ratings", lambda root: CricketRatings(_make_matches()))
    cache = tmp_path / "ratings.joblib"
    cache.write_bytes(b"not a joblib file")
    ratings = lc.load_ratings(tmp_path / "data", cache=cache)
    assert len(ratings.rows) > 0


# ================================================================ live loop wiring
class _RecordingRunner:
    def __init__(self) -> None:
        self.ticks: list[dict[str, Any]] = []

    async def on_tick(self, tick: dict[str, Any], *, wall_now: float | None = None) -> Any:
        self.ticks.append(tick)
        return SimpleNamespace(signals=[], paper=[])


class _FinishRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.finished: list[str] = []
        self.error = error

    async def finish(self, match_id: str) -> Any:
        self.finished.append(match_id)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(signals=[], paper=[])


def _bare_loop() -> Any:
    loop = object.__new__(live.LiveVirtualTradingLoop)
    loop._strategy_mode = "value"
    loop._agent = None
    loop._get_agent = None
    loop._open_bets = {}
    loop._completed_match_ids = set()
    loop._decision_gate = DecisionGate()
    loop._on_match_settled = None
    return loop


def test_value_decision_passes_prematch_fields_to_the_runner() -> None:
    loop = _bare_loop()
    loop._ratings = FakeRatings()
    loop._normalize_team = canonicalize_team
    loop._runner = _RecordingRunner()
    ctx = SimpleNamespace(match_format="T20", innings=1, wickets=0, overs=0.0, score=0, run_rate=0.0,
                          required_run_rate=None, balls_remaining=120, batting_team="India", bowling_team="Australia")
    event = OddsEvent(match_id="bf_1_300", timestamp=E0, team_home="India", team_away="Australia",
                      back_home=2.1, lay_home=2.14, back_away=1.95, lay_away=1.99, is_live=True)
    asyncio.run(loop._value_decision(event, ctx, obs=None))
    tick = loop._runner.ticks[0]
    assert tick["family"] == "t20"
    assert tick["pm_elo_home"] == 1510.0 and tick["pm_elo_away"] == 1490.0


def test_get_runner_loads_ratings_alongside_the_fair_models(monkeypatch: pytest.MonkeyPatch) -> None:
    ratings = FakeRatings()
    monkeypatch.setattr(live, "_load_fair_models", lambda: {"t20": object()})
    monkeypatch.setattr(live, "_load_ratings", lambda: ratings)

    async def no_redis() -> None:
        return None

    monkeypatch.setattr(live, "get_redis", no_redis)
    import strategy.signals as signals

    monkeypatch.setattr(signals, "SignalBus", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    loop = _bare_loop()
    loop._runner = None
    loop._engine = object()
    loop._risk_manager = object()
    runner = asyncio.run(loop._get_runner())
    assert loop._ratings is ratings
    assert loop._normalize_team is canonicalize_team
    assert runner is loop._runner


def test_load_ratings_failure_degrades_to_no_prematch(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no cricsheet")

    monkeypatch.setattr(lc, "load_ratings", broken)
    assert live._load_ratings() is None


def test_match_result_finishes_the_runner_and_marks_the_match_completed() -> None:
    loop = _bare_loop()
    runner = _FinishRunner()
    loop._runner = runner
    asyncio.run(loop._handle_match_result({"match_id": "bf_1_300", "winner": "India",
                                           "team_home": "India", "team_away": "Australia"}))
    assert runner.finished == ["bf_1_300"]
    assert "bf_1_300" in loop._completed_match_ids


def test_match_result_still_completes_when_the_runner_finish_fails() -> None:
    loop = _bare_loop()
    loop._runner = _FinishRunner(error=RuntimeError("boom"))
    asyncio.run(loop._handle_match_result({"match_id": "bf_1_300", "winner": "India"}))
    assert "bf_1_300" in loop._completed_match_ids
