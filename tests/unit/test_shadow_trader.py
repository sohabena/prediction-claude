"""Unit tests for the shadow trader and drift detection system."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from virtual_trading.shadow_trader import ShadowTrader, ShadowBet, DriftStatus


class TestShadowBet:
    """Tests for ShadowBet data class."""

    def test_create_shadow_bet(self) -> None:
        """Shadow bet is created with correct defaults."""
        bet = ShadowBet(
            match_id="m1",
            action="BACK_HOME_SM",
            team="India",
            odds=1.85,
            stake=1000.0,
            confidence=0.72,
        )
        assert bet.match_id == "m1"
        assert bet.outcome == "pending"
        assert bet.profit_loss == 0.0
        assert bet.settled_at is None

    def test_settle_win(self) -> None:
        """Settling a winning bet sets outcome and P&L."""
        bet = ShadowBet("m1", "BACK_HOME_SM", "India", 1.85, 1000.0, 0.7)
        bet.settle("win", 850.0)
        assert bet.outcome == "win"
        assert bet.profit_loss == 850.0
        assert bet.settled_at is not None

    def test_settle_loss(self) -> None:
        """Settling a losing bet sets negative P&L."""
        bet = ShadowBet("m1", "BACK_HOME_SM", "India", 1.85, 1000.0, 0.7)
        bet.settle("loss", -1000.0)
        assert bet.outcome == "loss"
        assert bet.profit_loss == -1000.0

    def test_to_dict(self) -> None:
        """Serialization includes all fields."""
        bet = ShadowBet("m1", "BACK_HOME_SM", "India", 1.85, 1000.0, 0.7)
        d = bet.to_dict()
        assert d["match_id"] == "m1"
        assert d["action"] == "BACK_HOME_SM"
        assert d["confidence"] == 0.7
        assert d["outcome"] == "pending"


class TestShadowTrader:
    """Tests for the ShadowTrader portfolio management."""

    def setup_method(self) -> None:
        self.trader = ShadowTrader(initial_balance=100_000.0)

    def test_initial_state(self) -> None:
        """Initial portfolio has correct balance."""
        perf = self.trader.get_performance()
        assert perf["balance"] == 100_000.0
        assert perf["total_bets"] == 0
        assert perf["win_rate"] == 0.0

    def test_place_shadow_bet(self) -> None:
        """Shadow bet is placed from advisor signal."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_HOME_SM",
            "confidence": 0.72,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 1.85,
        }
        bet = self.trader.place_shadow_bet(signal)
        assert bet is not None
        assert bet.match_id == "m1"
        assert bet.action == "BACK_HOME_SM"
        assert bet.confidence == 0.72
        assert self.trader._total_bets == 1

    def test_skip_hold_signal(self) -> None:
        """HOLD signals are not traded."""
        signal = {
            "match_id": "m1",
            "recommended_action": "HOLD",
            "confidence": 0.90,
            "team_home": "India",
            "team_away": "Australia",
        }
        bet = self.trader.place_shadow_bet(signal)
        assert bet is None
        assert self.trader._total_bets == 0

    def test_skip_duplicate_match(self) -> None:
        """Only one shadow bet per match."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_HOME_SM",
            "confidence": 0.72,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 1.85,
        }
        self.trader.place_shadow_bet(signal)
        bet2 = self.trader.place_shadow_bet(signal)
        assert bet2 is None
        assert self.trader._total_bets == 1

    def test_settle_winning_bet(self) -> None:
        """Settling a winning shadow bet increases balance."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_HOME_SM",
            "confidence": 0.72,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 2.00,
        }
        self.trader.place_shadow_bet(signal)
        bet = self.trader.settle_shadow_bet("m1", won=True)

        assert bet is not None
        assert bet.outcome == "win"
        assert bet.profit_loss > 0
        assert self.trader._balance > 100_000.0
        assert self.trader._total_wins == 1

    def test_settle_losing_bet(self) -> None:
        """Settling a losing shadow bet decreases balance."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_HOME_SM",
            "confidence": 0.72,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 2.00,
        }
        self.trader.place_shadow_bet(signal)
        bet = self.trader.settle_shadow_bet("m1", won=False)

        assert bet is not None
        assert bet.outcome == "loss"
        assert bet.profit_loss < 0
        assert self.trader._balance < 100_000.0

    def test_settle_nonexistent_match(self) -> None:
        """Settling a bet for unknown match returns None."""
        bet = self.trader.settle_shadow_bet("nonexistent", won=True)
        assert bet is None

    def test_large_stake_for_lg_actions(self) -> None:
        """Large actions use 3% stake instead of 1%."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_HOME_LG",
            "confidence": 0.80,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 1.85,
        }
        bet = self.trader.place_shadow_bet(signal)
        assert bet is not None
        assert bet.stake == 100_000.0 * 0.03  # 3% = 3000

    def test_small_stake_for_sm_actions(self) -> None:
        """Small actions use 1% stake."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_AWAY_SM",
            "confidence": 0.65,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 2.10,
        }
        bet = self.trader.place_shadow_bet(signal)
        assert bet is not None
        assert bet.stake == 100_000.0 * 0.01  # 1% = 1000

    def test_away_team_selected(self) -> None:
        """AWAY actions select the away team."""
        signal = {
            "match_id": "m1",
            "recommended_action": "BACK_AWAY_SM",
            "confidence": 0.65,
            "team_home": "India",
            "team_away": "Australia",
            "odds": 2.10,
        }
        bet = self.trader.place_shadow_bet(signal)
        assert bet is not None
        assert bet.team == "Australia"

    def test_performance_after_multiple_bets(self) -> None:
        """Performance tracks correctly over multiple bets."""
        # Place and settle 5 winning bets
        for i in range(5):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.70,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=True)

        # Place and settle 3 losing bets
        for i in range(5, 8):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.55,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=False)

        perf = self.trader.get_performance()
        assert perf["total_bets"] == 8
        assert perf["total_wins"] == 5
        assert perf["win_rate"] == pytest.approx(5 / 8, abs=0.01)
        assert perf["total_pnl"] > 0  # 5 wins @ +1000 each, 3 losses @ -1000 each = +2000


class TestDriftDetection:
    """Tests for drift detection logic."""

    def setup_method(self) -> None:
        self.trader = ShadowTrader(initial_balance=100_000.0)

    def test_no_drift_with_insufficient_data(self) -> None:
        """No drift detected with fewer than 20 bets."""
        for i in range(10):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.70,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=False)

        drift = self.trader.check_drift()
        assert not drift.is_drifting

    def test_drift_detected_when_losing(self) -> None:
        """Drift is detected when win rate drops below floor."""
        # Place 25 bets, all losses (well below 50% win rate)
        for i in range(25):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.55,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=False)

        drift = self.trader.check_drift()
        assert drift.is_drifting
        assert len(drift.violations) > 0
        assert drift.consecutive_drift_days == 1

    def test_no_drift_when_profitable(self) -> None:
        """No drift when agent is winning above thresholds."""
        from datetime import timedelta

        for i in range(30):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.70,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            # 70% win rate (above 50% floor)
            self.trader.settle_shadow_bet(f"m{i}", won=(i % 10 < 7))

        # Spread daily P&L across multiple days with varying positive values
        # so Sharpe can be properly calculated (needs non-zero std)
        today = datetime.now(timezone.utc).date()
        daily_values = [400.0, 600.0, 500.0, 700.0, 450.0, 550.0, 650.0]
        self.trader._daily_pnl = {
            (today - timedelta(days=d)).isoformat(): daily_values[d]
            for d in range(7)
        }

        drift = self.trader.check_drift()
        assert not drift.is_drifting

    def test_demotion_not_triggered_on_single_day(self) -> None:
        """Demotion requires consecutive days, not just one check."""
        for i in range(25):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.55,
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=False)

        drift = self.trader.check_drift()
        assert drift.is_drifting
        assert not drift.should_demote  # Only 1 day, need 5

    def test_drift_status_to_dict(self) -> None:
        """DriftStatus serializes correctly."""
        ds = DriftStatus()
        d = ds.to_dict()
        assert d["is_drifting"] is False
        assert d["consecutive_drift_days"] == 0
        assert d["should_demote"] is False
        assert d["violations"] == []


class TestConfidenceCalibration:
    """Tests for confidence calibration tracking."""

    def setup_method(self) -> None:
        self.trader = ShadowTrader(initial_balance=100_000.0)

    def test_calibration_buckets(self) -> None:
        """Calibration groups bets into confidence buckets."""
        # Place bets with varying confidence
        for i in range(20):
            sig = {
                "match_id": f"m{i}",
                "recommended_action": "BACK_HOME_SM",
                "confidence": 0.35 + (i * 0.025),  # Range from 0.35 to 0.825
                "team_home": "India",
                "team_away": "Australia",
                "odds": 2.00,
            }
            self.trader.place_shadow_bet(sig)
            self.trader.settle_shadow_bet(f"m{i}", won=(i % 2 == 0))

        perf = self.trader.get_performance()
        calibration = perf["calibration"]
        assert len(calibration) > 0
        for bucket in calibration:
            assert "bucket" in bucket
            assert "count" in bucket
            assert "actual_win_rate" in bucket
            assert 0 <= bucket["actual_win_rate"] <= 1.0

    def test_empty_calibration(self) -> None:
        """Calibration with no data returns empty list."""
        perf = self.trader.get_performance()
        assert perf["calibration"] == []
