"""Tests for backtesting report and bootstrap confidence intervals."""

import pytest
from backtesting.report import bootstrap_ci, generate_report


class TestBootstrapCI:
    """Test bootstrap confidence interval computation."""

    def test_positive_data_has_positive_ci(self) -> None:
        """Clearly positive data should have CI above 0."""
        data = [1.0, 2.0, 1.5, 3.0, 2.5, 1.8, 2.2] * 20
        result = bootstrap_ci(data, n_bootstrap=5000, statistic="mean")
        assert result["ci_lower"] > 0
        assert result["significant"]

    def test_negative_data_has_negative_ci(self) -> None:
        """Clearly negative data should have CI below 0."""
        data = [-1.0, -2.0, -1.5, -3.0, -2.5] * 20
        result = bootstrap_ci(data, n_bootstrap=5000, statistic="mean")
        assert result["ci_upper"] < 0
        assert result["significant"]

    def test_mixed_data_not_significant(self) -> None:
        """Mixed positive/negative data with mean near 0 should not be significant."""
        data = [1.0, -1.0, 0.5, -0.5, 0.2, -0.2] * 20
        result = bootstrap_ci(data, n_bootstrap=5000, statistic="mean")
        # CI should span 0
        assert result["ci_lower"] < 0 < result["ci_upper"]
        assert not result["significant"]

    def test_insufficient_data(self) -> None:
        """Too few data points should return non-significant."""
        result = bootstrap_ci([1.0, 2.0], n_bootstrap=1000, statistic="mean")
        assert not result["significant"]
        assert result["estimate"] == 0.0

    def test_sharpe_statistic(self) -> None:
        """Sharpe ratio CI should work correctly."""
        # Strongly positive daily returns
        data = [100.0, 150.0, 120.0, 200.0, 180.0] * 10
        result = bootstrap_ci(data, n_bootstrap=5000, statistic="sharpe")
        assert result["estimate"] > 0


class TestGenerateReport:
    """Test full report generation."""

    def test_report_structure(self) -> None:
        """Report should contain expected keys."""
        summary = {"total_bets": 100, "win_rate": 0.55, "total_pnl": 5000.0}
        bet_pnls = [50.0, -30.0, 80.0, -20.0, 60.0] * 20
        daily_pnls = [200.0, -100.0, 300.0, -50.0, 150.0] * 6

        report = generate_report(summary, bet_pnls, daily_pnls)

        assert "summary" in report
        assert "confidence_intervals" in report
        assert "assessment" in report
        assert "mean_pnl" in report["confidence_intervals"]
        assert "sharpe_ratio" in report["confidence_intervals"]
        assert "win_rate" in report["confidence_intervals"]

    def test_profitable_assessment(self) -> None:
        """Clearly profitable results should be marked as profitable."""
        summary = {"total_bets": 200, "win_rate": 0.60, "total_pnl": 10000.0}
        bet_pnls = [100.0, 80.0, 120.0, 90.0, 110.0] * 40
        daily_pnls = [500.0, 400.0, 600.0, 450.0, 550.0] * 6

        report = generate_report(summary, bet_pnls, daily_pnls)
        assert report["assessment"]["profitable"]
