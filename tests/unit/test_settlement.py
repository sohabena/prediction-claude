"""Tests for settlement engine including CLV computation."""

import pytest
from virtual_trading.settlement import SettlementEngine


class TestCLVComputation:
    """Test Closing Line Value calculations."""

    def test_positive_clv(self) -> None:
        """Getting better odds than closing should give positive CLV."""
        # Placed at 2.5, closed at 2.0 (market moved towards our position)
        clv = SettlementEngine.compute_clv(placement_odds=2.5, closing_odds=2.0)
        assert clv > 0  # Positive = good

    def test_negative_clv(self) -> None:
        """Getting worse odds than closing should give negative CLV."""
        # Placed at 2.0, closed at 2.5 (market moved against us)
        clv = SettlementEngine.compute_clv(placement_odds=2.0, closing_odds=2.5)
        assert clv < 0

    def test_zero_clv(self) -> None:
        """Same odds should give zero CLV."""
        clv = SettlementEngine.compute_clv(placement_odds=2.0, closing_odds=2.0)
        assert clv == pytest.approx(0.0)

    def test_clv_formula_correctness(self) -> None:
        """Verify CLV formula: (closing_ip / placement_ip) - 1."""
        # Placement: 3.0 -> implied prob 0.333
        # Closing: 2.0 -> implied prob 0.500
        # CLV = (0.500 / 0.333) - 1 = 0.5
        clv = SettlementEngine.compute_clv(placement_odds=3.0, closing_odds=2.0)
        assert clv == pytest.approx(0.5, rel=0.01)

    def test_invalid_odds_returns_zero(self) -> None:
        """Invalid odds should return 0 CLV."""
        assert SettlementEngine.compute_clv(1.0, 2.0) == 0.0
        assert SettlementEngine.compute_clv(2.0, 1.0) == 0.0
        assert SettlementEngine.compute_clv(0.5, 2.0) == 0.0
