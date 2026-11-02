"""Tests for the graduation evaluation system."""

from datetime import datetime, timedelta, timezone

import pytest
from rl.graduation import GraduationEvaluator

GOOD = dict(
    win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
    max_drawdown=0.05, profitable_days=12, total_bets=150, avg_clv=0.02,
    clv_ci_lower=0.005, n_matches=60,
)


class Clock:
    """Controllable UTC clock; ``advance`` moves forward by whole days."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: int = 1) -> None:
        self.now += timedelta(days=days)


class TestGraduationEvaluator:
    """Test graduation criteria evaluation."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.clock = Clock()
        self.evaluator = GraduationEvaluator(state_path=tmp_path / "grad.json", now_fn=self.clock)

    def test_all_criteria_met(self) -> None:
        """All criteria met should increment consecutive days."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02, clv_ci_lower=0.005, n_matches=60,
        )
        assert status.consecutive_days == 1
        assert not status.ready  # Need 14 consecutive days

    def test_criteria_not_met_resets_counter(self) -> None:
        """Failing any criterion should reset consecutive days to 0."""
        # First day: pass
        self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02, clv_ci_lower=0.005, n_matches=60,
        )
        self.clock.advance()
        # Second day: fail (low win rate)
        status = self.evaluator.evaluate(
            win_rate=0.40, roi=-0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02, clv_ci_lower=0.005, n_matches=60,
        )
        assert status.consecutive_days == 0

    def test_graduation_after_14_days(self) -> None:
        """Should graduate after 14 consecutive passing days."""
        for _ in range(14):
            status = self.evaluator.evaluate(**GOOD)
            self.clock.advance()
        assert status.ready
        assert status.consecutive_days == 14

    def test_max_drawdown_threshold(self) -> None:
        """Max drawdown above threshold should fail."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.25,  # Above 15% threshold
            profitable_days=12, total_bets=150,
            avg_clv=0.02, clv_ci_lower=0.005, n_matches=60,
        )
        assert status.consecutive_days == 0
        # Find the drawdown criterion
        dd_criterion = next(c for c in status.criteria if c.name == "max_drawdown")
        assert not dd_criterion.met

    def test_clv_criterion(self) -> None:
        """Negative CLV should fail the avg_clv criterion."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=-0.05, clv_ci_lower=-0.1, n_matches=60,  # Negative CLV
        )
        clv_criterion = next(c for c in status.criteria if c.name == "avg_clv")
        assert not clv_criterion.met

    def test_bet_volume_too_low(self) -> None:
        """Insufficient bet volume should fail."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=50,
            avg_clv=0.02, clv_ci_lower=0.005, n_matches=60,
        )
        vol_criterion = next(c for c in status.criteria if c.name == "bet_volume")
        assert not vol_criterion.met

    def test_progress_percentage(self) -> None:
        """Progress should reflect consecutive days."""
        for _ in range(7):
            self.evaluator.evaluate(**GOOD)
            self.clock.advance()
        assert self.evaluator.get_progress_pct() == pytest.approx(50.0)

    def test_same_day_evaluations_count_once(self) -> None:
        for _ in range(3):
            status = self.evaluator.evaluate(**GOOD)
        assert status.consecutive_days == 1

    def test_state_survives_reinstantiation(self) -> None:
        self.evaluator.evaluate(**GOOD)
        self.clock.advance()
        self.evaluator.evaluate(**GOOD)
        again = GraduationEvaluator(state_path=self.tmp_path / "grad.json", now_fn=self.clock)
        assert again.get_progress_pct() == pytest.approx(100.0 * 2 / again.required_days)
        self.clock.advance()
        assert again.evaluate(**GOOD).consecutive_days == 3

    def test_day_gap_restarts_streak(self) -> None:
        self.evaluator.evaluate(**GOOD)
        self.clock.advance(3)
        assert self.evaluator.evaluate(**GOOD).consecutive_days == 1

    def test_failed_evaluation_same_day_resets(self) -> None:
        self.evaluator.evaluate(**GOOD)
        bad = dict(GOOD, roi=-0.1)
        assert self.evaluator.evaluate(**bad).consecutive_days == 0
