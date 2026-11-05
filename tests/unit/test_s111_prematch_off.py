"""Unit tests for S111: Enforce in-play only, permanently disable pre-match new risk."""

from __future__ import annotations

from virtual_trading import paper_trader as pt


def test_prematch_new_risk_is_permanently_false():
    """PREMATCH_NEW_RISK must be False so no strategy variant opens new risk before the off."""
    assert pt.PREMATCH_NEW_RISK is False
