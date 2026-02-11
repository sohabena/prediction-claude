"""Tests for the graduation evaluation system."""

import pytest
from rl.graduation import GraduationEvaluator


class TestGraduationEvaluator:
    """Test graduation criteria evaluation."""

    def setup_method(self) -> None:
        self.evaluator = GraduationEvaluator()

    def test_all_criteria_met(self) -> None:
        """All criteria met should increment consecutive days."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02,
        )
        assert status.consecutive_days == 1
        assert not status.ready  # Need 14 consecutive days

    def test_criteria_not_met_resets_counter(self) -> None:
        """Failing any criterion should reset consecutive days to 0."""
        # First day: pass
        self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02,
        )
        # Second day: fail (low win rate)
        status = self.evaluator.evaluate(
            win_rate=0.40, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=150,
            avg_clv=0.02,
        )
        assert status.consecutive_days == 0

    def test_graduation_after_14_days(self) -> None:
        """Should graduate after 14 consecutive passing days."""
        for _ in range(14):
            status = self.evaluator.evaluate(
                win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
                max_drawdown=0.05, profitable_days=12, total_bets=150,
                avg_clv=0.02,
            )
        assert status.ready
        assert status.consecutive_days == 14

    def test_max_drawdown_threshold(self) -> None:
        """Max drawdown above threshold should fail."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.25,  # Above 15% threshold
            profitable_days=12, total_bets=150,
            avg_clv=0.02,
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
            avg_clv=-0.05,  # Negative CLV
        )
        clv_criterion = next(c for c in status.criteria if c.name == "avg_clv")
        assert not clv_criterion.met

    def test_bet_volume_too_low(self) -> None:
        """Insufficient bet volume should fail."""
        status = self.evaluator.evaluate(
            win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
            max_drawdown=0.05, profitable_days=12, total_bets=50,
            avg_clv=0.02,
        )
        vol_criterion = next(c for c in status.criteria if c.name == "bet_volume")
        assert not vol_criterion.met

    def test_progress_percentage(self) -> None:
        """Progress should reflect consecutive days."""
        for _ in range(7):
            self.evaluator.evaluate(
                win_rate=0.60, roi=0.12, sharpe_ratio=2.0,
                max_drawdown=0.05, profitable_days=12, total_bets=150,
                avg_clv=0.02,
            )
        assert self.evaluator.get_progress_pct() == pytest.approx(50.0)
