"""S39: real-price event-driven backtester, conservative execution, walk-forward, R0 sanity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backtesting.baselines import HoldPolicy, RandomPolicy
from backtesting.execution import ExecutionModel, limit_ok, plan_fill
from backtesting.real_backtester import (
    DriftMomentum,
    FadeTheJump,
    PolicyStrategy,
    WalkForwardLeakError,
    _epoch,
    assert_walk_forward,
    fold_metadata,
    r0_check,
    run,
    run_match,
    summarise,
    walk_forward_folds,
)
from rl.environment import shift_price
from scripts import build_real_price_corpus as corpus
from scripts.run_real_backtest import build_strategy, run_walk_forward
from shared.metrics import clv_prob
from shared.schemas import BettingAction
from strategy.base import (
    LookAheadError,
    MarketFeed,
    OrderIntent,
    Position,
    bet_gross_pnl,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "betfair"
BASE = datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)


# ================================================================
# Builders
# ================================================================


def _tick(t_s: float, base: datetime = BASE, **fields: Any) -> dict[str, Any]:
    """One real tick (README contract) at ``t_s`` seconds after ``base``."""
    tick: dict[str, Any] = {
        "time": (base + timedelta(seconds=t_s)).isoformat(),
        "market_id": "1.100",
        "match_id": "bf_1_100",
        "team_home": "India",
        "team_away": "Australia",
        "competition": "T20 International",
        "match_format": "t20",
        "family": "t20",
        "in_play": True,
        "ltp_home": None,
        "ltp_away": None,
        "ltp_draw": None,
        "back_home": None,
        "lay_home": None,
        "back_away": None,
        "lay_away": None,
        "traded_volume_home": None,
        "traded_volume_away": None,
        "source": "betfair_historical",
        "data_tier": "basic",
        "market_model": "real",
    }
    tick.update(fields)
    return tick


def _episode(
    ticks: list[dict[str, Any]],
    *,
    winner_is_home: bool | None,
    result_type: str = "win",
    match_id: str = "bf_1_100",
    family: str = "t20",
    tier: str = "other",
) -> dict[str, Any]:
    """Episode dict as ``load_real_corpus`` returns it. The result sits on the first tick only."""
    ticks[0]["_meta"] = {
        "winner": "India" if winner_is_home else ("Australia" if winner_is_home is False else ""),
        "loser": "Australia" if winner_is_home else ("India" if winner_is_home is False else ""),
        "winner_is_home": winner_is_home,
        "result_type": result_type,
    }
    return {
        "match_id": match_id,
        "market_id": "1.100",
        "family": family,
        "match_format": family,
        "competition": "T20 International",
        "team_home": "India",
        "team_away": "Australia",
        "data_tier": ticks[0]["data_tier"],
        "start": ticks[0]["time"],
        "ticks": ticks,
        "competition_tier": tier,
    }


class _Scripted:
    """Places one order at its first decision and records what the decision could see."""

    name = "scripted"

    def __init__(self, intent: OrderIntent) -> None:
        self.intent = intent
        self.calls: list[dict[str, Any]] = []
        self.metas: list[dict[str, Any]] = []
        self._placed = False

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        self.metas.append(dict(match_meta_without_result))
        self._placed = False

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        self.calls.append({
            "tick": dict(tick),
            "visible": len(position.feed),
            "now": position.feed.now,
            "history_last_time": position.feed.history()[-1]["time"],
        })
        if self._placed:
            return []
        self._placed = True
        return [self.intent]


def _fair_market(rng: np.random.Generator, match_id: str, start_year: int, n_ticks: int = 60) -> dict[str, Any]:
    """Market with no edge: quotes at +/-1% around the true probability, one tick a minute."""
    base = datetime(start_year, 1, 5, 10, 0, tzinfo=timezone.utc)
    p_home = float(rng.uniform(0.35, 0.65))
    fair_home, fair_away = 1.0 / p_home, 1.0 / (1.0 - p_home)
    ticks = [
        _tick(
            i * 60.0, base=base, match_id=match_id, market_id="1.200", in_play=i > 0,
            ltp_home=fair_home, ltp_away=fair_away,
            back_home=fair_home * 0.99, lay_home=fair_home * 1.01,
            back_away=fair_away * 0.99, lay_away=fair_away * 1.01,
        )
        for i in range(n_ticks)
    ]
    home_wins = bool(rng.random() < p_home)
    return _episode(ticks, winner_is_home=home_wins, match_id=match_id)


# ================================================================
# Exchange maths and execution presets
# ================================================================


class TestExchangeMaths:
    def test_back_pays_on_win_and_costs_stake_otherwise(self) -> None:
        assert bet_gross_pnl("BACK", "home", 2.0, 10.0, "home") == pytest.approx(10.0)
        assert bet_gross_pnl("BACK", "home", 2.0, 10.0, "away") == pytest.approx(-10.0)
        assert bet_gross_pnl("BACK", "home", 2.0, 10.0, "draw") == pytest.approx(-10.0)

    def test_lay_liability_and_draw_settles_both_selections_as_losers(self) -> None:
        assert bet_gross_pnl("LAY", "home", 2.0, 10.0, "away") == pytest.approx(10.0)
        assert bet_gross_pnl("LAY", "home", 2.0, 10.0, "home") == pytest.approx(-10.0)
        assert bet_gross_pnl("LAY", "home", 2.0, 10.0, "draw") == pytest.approx(10.0)
        assert bet_gross_pnl("LAY", "away", 3.0, 10.0, "home") == pytest.approx(10.0)

    def test_execution_presets(self) -> None:
        assert ExecutionModel.preset("manual").latency_s == 60.0
        assert ExecutionModel.preset("automated").latency_s == 8.0
        assert ExecutionModel.preset("manual").commission == 0.05
        with pytest.raises(ValueError):
            ExecutionModel.preset("unknown")

    def test_basic_fill_uses_ltp_shifted_against_us(self) -> None:
        model = ExecutionModel(spread_ticks_basic=1)
        tick = {"ltp_home": 2.0, "back_home": None, "lay_home": None}
        assert model.fill_price(tick, "BACK", "home") == pytest.approx(shift_price(2.0, -1))
        assert model.fill_price(tick, "LAY", "home") == pytest.approx(shift_price(2.0, 1))
        assert model.fill_price({"ltp_home": None}, "BACK", "home") is None

    def test_limit_semantics(self) -> None:
        assert limit_ok(1.9, "BACK", 1.85)
        assert not limit_ok(1.8, "BACK", 1.85)
        assert limit_ok(2.1, "LAY", 2.2)
        assert not limit_ok(2.3, "LAY", 2.2)
        assert limit_ok(1.5, "BACK", None)

    def test_orderintent_validates_fields(self) -> None:
        with pytest.raises(ValueError):
            OrderIntent("BACK", "draw", None, 10.0)
        with pytest.raises(ValueError):
            OrderIntent("BACK", "home", None, 0.0)
        with pytest.raises(ValueError):
            OrderIntent("BACK", "home", 1.0, 10.0)


# ================================================================
# Hand-computed fill and settlement
# ================================================================


class TestHandComputedFill:
    """Ticks every 5 s; the first decision is at t=0. Latency 8 s, bet delay 5 s.

    Decision time 8 s, so the order reaches the market at 13 s and fills at the first tick at
    or after that: t=15 (index 3), where back_home is 1.90. BACK 10 at 1.90, limit 1.85.
    """

    def _ticks(self, back_at_fill: float = 1.90) -> list[dict[str, Any]]:
        prices = [2.00, 1.95, 1.93, back_at_fill, 1.88]
        return [_tick(5.0 * i, back_home=p, lay_home=p + 0.02, ltp_home=p) for i, p in enumerate(prices)]

    def test_back_fill_price_stake_and_net_pnl(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0, commission=0.05)
        strategy = _Scripted(OrderIntent("BACK", "home", 1.85, 10.0, "test"))
        episode = _episode(self._ticks(), winner_is_home=True)

        result = run_match(strategy, episode, execution, bankroll=1000.0)

        assert len(result["bets"]) == 1
        bet = result["bets"][0]
        assert bet["price"] == pytest.approx(1.90)
        assert bet["stake"] == pytest.approx(10.0)
        assert bet["matched_at"] == pytest.approx(_epoch(BASE + timedelta(seconds=15)))
        assert bet["gross_pnl"] == pytest.approx(9.0)          # 10 * (1.90 - 1)
        assert result["commission"] == pytest.approx(0.45)     # 5% of net market winnings
        assert result["pnl"] == pytest.approx(8.55)

    def test_back_loses_stake_and_pays_no_commission(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0, commission=0.05)
        strategy = _Scripted(OrderIntent("BACK", "home", 1.85, 10.0))
        episode = _episode(self._ticks(), winner_is_home=False)

        result = run_match(strategy, episode, execution, bankroll=1000.0)

        assert result["pnl"] == pytest.approx(-10.0)
        assert result["commission"] == 0.0

    def test_no_fill_when_price_moved_through_limit(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0)
        strategy = _Scripted(OrderIntent("BACK", "home", 1.95, 10.0))
        episode = _episode(self._ticks(back_at_fill=1.90), winner_is_home=True)

        result = run_match(strategy, episode, execution, bankroll=1000.0)

        assert result["bets"] == []
        assert result["pnl"] == 0.0

    def test_decision_sees_only_ticks_up_to_latency_lag(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0)
        strategy = _Scripted(OrderIntent("BACK", "home", None, 10.0))
        episode = _episode(self._ticks(), winner_is_home=True)

        run_match(strategy, episode, execution, bankroll=1000.0)

        assert strategy.calls[0]["now"] == pytest.approx(_epoch(BASE))
        for index, call in enumerate(strategy.calls):
            assert call["visible"] == index + 1
            assert call["history_last_time"] == (BASE + timedelta(seconds=5 * index)).isoformat()

    def test_lay_fill_is_capped_by_liability_and_pays_draw_commission(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0, commission=0.05)
        ticks = [_tick(5.0 * i, back_home=2.0, lay_home=2.0, ltp_home=2.0) for i in range(6)]
        strategy = _Scripted(OrderIntent("LAY", "home", None, 100.0))
        episode = _episode(ticks, winner_is_home=None, result_type="draw")

        result = run_match(strategy, episode, execution, bankroll=1000.0)

        bet = result["bets"][0]
        # Liability cap: 3% of 1000 = 30 at odds 2.0 caps the lay stake at 30.
        assert bet["stake"] == pytest.approx(30.0)
        assert bet["gross_pnl"] == pytest.approx(30.0)     # draw: the lay wins
        assert result["commission"] == pytest.approx(1.5)  # 5% of 30
        assert result["pnl"] == pytest.approx(28.5)

    def test_volume_cap_limits_stake_to_window_traded_volume(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0, max_volume_frac=0.05)
        # traded_volume is cumulative: 100 at the decision (t=0), 110 at the fill (t=15). Window = 10.
        ticks = [_tick(5.0 * i, back_home=2.0, lay_home=2.1, ltp_home=2.0, traded_volume_home=100.0)
                 for i in range(6)]
        ticks[3]["traded_volume_home"] = 110.0
        strategy = _Scripted(OrderIntent("BACK", "home", None, 10.0))
        episode = _episode(ticks, winner_is_home=True)

        result = run_match(strategy, episode, execution, bankroll=1000.0)

        assert result["bets"][0]["stake"] == pytest.approx(0.5)  # 5% of window volume 10

    def test_plan_fill_has_no_fill_without_a_future_tick(self) -> None:
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0)
        ticks = [_tick(0.0, back_home=2.0), _tick(5.0, back_home=2.0)]
        times = [_epoch(t["time"]) for t in ticks]
        check, fill = plan_fill(
            execution, ticks, times, 0, times[0] + 8.0,
            OrderIntent("BACK", "home", None, 10.0), bankroll=1000.0,
        )
        assert check is None and fill is None


class TestBinding_CLVReference:
    """Integration note (2026-11-02): in-play CLV uses the same-side price 10 min after placement."""

    def test_in_play_reference_is_same_side_price_ten_minutes_after_placement(self) -> None:
        # Decision at t=0 -> placed at t=8 -> fills at t=60 (back 2.0). The reference is the first
        # tick at or after 8 + 600 = 608 s, i.e. t=660, where back_home is 1.8.
        prices = {t: (2.0 if t <= 600 else 1.8) for t in range(0, 1201, 60)}
        ticks = [_tick(float(t), back_home=prices[t], lay_home=prices[t] + 0.02, ltp_home=prices[t])
                 for t in range(0, 1201, 60)]
        strategy = _Scripted(OrderIntent("BACK", "home", None, 10.0))
        episode = _episode(ticks, winner_is_home=True)

        result = run_match(strategy, episode, ExecutionModel(latency_s=8.0, bet_delay_s=5.0), bankroll=1000.0)

        bet = result["bets"][0]
        assert bet["phase"] == "in_play"
        assert bet["reference_price"] == pytest.approx(1.8)
        assert bet["clv"] == pytest.approx(clv_prob("BACK", 2.0, 1.8))
        # 60 s execution check: first tick at or after 8 + 60 = 68 s is t=120, back 2.0.
        assert bet["clv_60s"] == pytest.approx(0.0)

    def test_basic_tier_clv_falls_back_to_ltp(self) -> None:
        # BASIC files have ltp only: CLV must still be measured, against the later ltp.
        prices = {t: (2.0 if t <= 600 else 1.8) for t in range(0, 1201, 60)}
        ticks = [_tick(float(t), ltp_home=prices[t], ltp_away=1.0 / (1.0 - 1.0 / prices[t]))
                 for t in range(0, 1201, 60)]
        strategy = _Scripted(OrderIntent("BACK", "home", None, 10.0))
        result = run_match(strategy, _episode(ticks, winner_is_home=True),
                           ExecutionModel(latency_s=8.0, bet_delay_s=5.0), bankroll=1000.0)
        bet = result["bets"][0]
        assert bet["reference_price"] == pytest.approx(1.8)
        assert bet["clv"] is not None and bet["clv"] > 0


# ================================================================
# Look-ahead and leakage
# ================================================================


class TestNoLookAhead:
    def test_strategy_peeking_at_future_tick_raises(self) -> None:
        class _Peeker:
            name = "peeker"

            def reset(self, meta: dict[str, Any]) -> None:
                pass

            def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
                position.feed.peek(1)
                return []

        ticks = [_tick(5.0 * i, back_home=2.0, ltp_home=2.0) for i in range(4)]
        episode = _episode(ticks, winner_is_home=True)

        with pytest.raises(LookAheadError):
            run(_Peeker(), [episode], ExecutionModel())

    def test_visible_history_and_metadata_never_expose_the_result(self) -> None:
        ticks = [_tick(5.0 * i, back_home=2.0, lay_home=2.1, ltp_home=2.0) for i in range(5)]
        episode = _episode(ticks, winner_is_home=True)
        strategy = _Scripted(OrderIntent("BACK", "home", None, 10.0))

        run_match(strategy, episode, ExecutionModel(latency_s=8.0), bankroll=1000.0)

        for call in strategy.calls:
            assert "_meta" not in call["tick"]
            assert call["history_last_time"] == (BASE + timedelta(seconds=5 * (call["visible"] - 1))).isoformat()
        for meta in strategy.metas:
            assert not {"winner", "winner_is_home", "result_type", "_meta"} & set(meta)

    def test_market_feed_peek_and_lagged(self) -> None:
        ticks = [{"time": str(i), "ltp_home": 2.0 + i * 0.1} for i in range(5)]
        times = [10.0, 20.0, 40.0, 70.0, 100.0]
        feed = MarketFeed(ticks, times, 3)

        assert feed.now == 70.0
        assert feed.peek(0) is ticks[3]
        assert feed.peek(-1) is ticks[2]
        assert feed.lagged(30.0) is ticks[2]  # latest at or before 40
        assert feed.lagged(1000.0) is None
        with pytest.raises(LookAheadError):
            feed.peek(2)


# ================================================================
# R0 sanity and policy adapters
# ================================================================


class TestR0AndPolicies:
    def test_random_loses_costs_on_fair_market_and_r0_is_not_flagged(self) -> None:
        rng = np.random.default_rng(7)
        episodes = [_fair_market(rng, f"bf_1_{i}", 2024) for i in range(150)]
        execution = ExecutionModel(latency_s=8.0, bet_delay_s=5.0, commission=0.05)

        result = run(PolicyStrategy(RandomPolicy(seed=3), name="random"), episodes, execution)
        summary = summarise(result, n_bootstrap=2000)

        assert summary["n_bets"] > 0
        assert summary["mean_pnl_per_match"] < 0.0
        assert r0_check(summary)["flagged"] is False

    def test_r0_flag_raised_only_when_random_ci_is_above_zero(self) -> None:
        assert r0_check({"pnl_per_match_ci": {"ci_lower": 1.5}})["flagged"] is True
        assert r0_check({"pnl_per_match_ci": {"ci_lower": -0.5}})["flagged"] is False

    def test_hold_places_no_bets(self) -> None:
        rng = np.random.default_rng(1)
        episodes = [_fair_market(rng, f"bf_2_{i}", 2024) for i in range(5)]
        result = run(PolicyStrategy(HoldPolicy(), name="hold"), episodes, ExecutionModel())
        assert result["bets"] == []
        assert summarise(result, n_bootstrap=100)["total_pnl"] == 0.0

    def test_policy_stake_is_capped_by_liability_limit(self) -> None:
        class _FixedLarge:
            name = "fixed_large"

            def reset(self) -> None:
                pass

            def predict(self, obs: Any, tick: dict[str, Any], env: Any = None) -> int:
                return int(BettingAction.BACK_HOME_LG)

        ticks = [_tick(5.0 * i, back_home=2.0, lay_home=2.1, ltp_home=2.0) for i in range(5)]
        episode = _episode(ticks, winner_is_home=True)

        result = run(PolicyStrategy(_FixedLarge()), [episode], ExecutionModel(latency_s=8.0), bankroll=1000.0)

        # 4% of bankroll (40) exceeds the 3% per-bet liability cap (30) for a back at odds 2.0.
        assert result["bets"][0]["stake"] == pytest.approx(30.0)

    def test_fixture_corpus_runs_through_the_s35_loader(self, tmp_path: Path) -> None:
        out = tmp_path / "betfair"
        corpus.build_corpus(FIXTURES, out)
        episodes = corpus.load_real_corpus(out=out)
        assert episodes
        hold = run(PolicyStrategy(HoldPolicy(), name="hold"), episodes, ExecutionModel())
        assert hold["bets"] == []
        random_run = run(PolicyStrategy(RandomPolicy(seed=0), name="random"), episodes,
                         ExecutionModel(latency_s=8.0), bankroll=1000.0)
        for bet in random_run["bets"]:
            assert bet["price"] > 1.0 and bet["stake"] > 0


# ================================================================
# Walk-forward
# ================================================================


class TestWalkForward:
    def _multi_season(self) -> list[dict[str, Any]]:
        rng = np.random.default_rng(11)
        return [_fair_market(rng, f"bf_{year}_{i}", year) for year in (2022, 2023, 2024) for i in range(10)]

    def test_folds_are_by_season_and_train_only_on_earlier_seasons(self) -> None:
        folds = walk_forward_folds(self._multi_season())
        assert [f.test_season for f in folds] == [2023, 2024]
        for fold in folds:
            assert all(s < fold.test_season for s in fold.train_seasons)
            assert_walk_forward(fold_metadata(fold))

    def test_leak_into_training_is_detected(self) -> None:
        meta = fold_metadata(walk_forward_folds(self._multi_season())[-1])
        leaked = {**meta, "train_match_ids": meta["train_match_ids"] + [meta["test_match_ids"][0]]}
        with pytest.raises(WalkForwardLeakError):
            assert_walk_forward(leaked)

    def test_model_fitted_on_test_matches_is_detected(self) -> None:
        meta = fold_metadata(walk_forward_folds(self._multi_season())[-1])
        with pytest.raises(WalkForwardLeakError):
            assert_walk_forward(meta, fitted_match_ids=meta["train_match_ids"] + meta["test_match_ids"][:1])

    def test_value_fold_fits_only_training_matches(self) -> None:
        fold = walk_forward_folds(self._multi_season())[-1]
        _strategy, fitted, skip = build_strategy("value", fold.train, "t20", ExecutionModel(), seed=0)
        assert skip is None
        assert set(fitted) == {ep["match_id"] for ep in fold.train}
        assert_walk_forward(fold_metadata(fold), fitted)

    def test_end_to_end_report_shape(self) -> None:
        report = run_walk_forward(
            self._multi_season(), "random", "t20", ExecutionModel.preset("manual"),
            n_bootstrap=200, seed=0,
        )
        assert report["execution"]["name"] == "manual"
        assert report["execution"]["latency_s"] == 60.0
        assert [f["test_season"] for f in report["folds"]] == [2023, 2024]
        assert set(report["r0"]["summaries"]) == {"random", "hold"}
        assert "flagged" in report["r0"]
        assert set(report["pooled"]["segments"]) == {"phase", "odds_band", "competition_tier"}
        assert report["warnings"]  # synthetic ticks carry no match-state fields


# ================================================================
# Market-behaviour probes
# ================================================================


class TestProbes:
    def test_fade_the_jump_enters_against_a_one_minute_move_and_exits(self) -> None:
        times = list(range(0, 601, 5))
        ticks = [_tick(float(t), ltp_home=(2.0 if t < 120 else 2.6), ltp_away=1.7) for t in times]
        episode = _episode(ticks, winner_is_home=False)
        strategy = FadeTheJump(k_logit=0.15, exit_after_s=300.0, stake_fraction=0.02)

        result = run_match(strategy, episode, ExecutionModel(latency_s=8.0, bet_delay_s=5.0), bankroll=1000.0)

        assert [b["reason"] for b in result["bets"]] == ["fade_entry", "fade_exit"]
        entry, exit_ = result["bets"]
        assert entry["side"] == "BACK"  # the home price lengthened, so back home to revert
        assert exit_["side"] == "LAY"
        # The exit is capped like any other order (conservative: hedges are capped too).
        assert 0.0 < exit_["stake"] <= entry["stake"] + 1e-9

    def test_drift_backs_the_side_that_gained_once_per_match(self) -> None:
        times = list(range(0, 3721, 60))
        ticks = [_tick(float(t), ltp_home=3.0 - min(t, 3600) / 3600.0, ltp_away=1.5, family="odi")
                 for t in times]
        episode = _episode(ticks, winner_is_home=True, family="odi")

        result = run_match(DriftMomentum(), episode, ExecutionModel(latency_s=8.0, bet_delay_s=5.0),
                           bankroll=1000.0)

        assert len(result["bets"]) == 1
        assert result["bets"][0]["side"] == "BACK"
        assert result["bets"][0]["selection"] == "home"

    def test_drift_ignores_t20_markets(self) -> None:
        times = list(range(0, 3721, 60))
        ticks = [_tick(float(t), ltp_home=3.0 - min(t, 3600) / 3600.0, ltp_away=1.5) for t in times]
        episode = _episode(ticks, winner_is_home=True, family="t20")
        result = run_match(DriftMomentum(), episode, ExecutionModel(), bankroll=1000.0)
        assert result["bets"] == []
