"""Unit tests for MarketBlend model integration into ProbabilityService (S103 / OddsPapi dataset)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from strategy.probability import ProbabilityService
from training.blend import BLEND_SEGMENTS, BlendModel, BlendRow

PM_FIELDS: Dict[str, float] = {
    "pm_elo_home": 1600.0,
    "pm_elo_away": 1500.0,
    "pm_xi_home": 0.1,
    "pm_xi_away": -0.1,
    "pm_toss_home": 0.0,
    "pm_toss_bat": 0.5,
    "pm_venue_chase_pct": 55.0,
    "pm_venue_n": 20.0,
    "pm_tier": 2.0,
}


class DummyRatings:
    def __init__(self) -> None:
        self.rows: Dict[str, Any] = {}

    def _team_known(self, name: str, family: str, gender: str) -> bool:
        return True

    def pre_match_live(
        self,
        team_home: str,
        team_away: str,
        family: str,
        gender: str,
        as_of: str,
        *,
        venue: Optional[str] = None,
        city: Optional[str] = None,
        players: Any = None,
        toss_winner: Optional[str] = None,
        toss_decision: Optional[str] = None,
        tier: Optional[int] = None,
        normalize_team: Any = None,
    ) -> Dict[str, float]:
        return dict(PM_FIELDS)


class DummyPrematch:
    def predict_home(self, features: Dict[str, Any]) -> float:
        return 0.65


def test_blend_model_from_dict_and_predict():
    data = {
        "version": "1.0.0",
        "fitted": True,
        "usable_rows": 100,
        "posterior": {
            "a": {"mean": 0.05, "sd": 0.08},
            "b": {"mean": 0.80, "sd": 0.07},
            "c": {"mean": 0.60, "sd": 0.07},
            "segments": {name: {"mean": 0.01, "sd": 0.05} for name in BLEND_SEGMENTS},
        },
    }
    model = BlendModel.from_dict(data)
    assert model.fitted is True
    assert model.n_train == 100

    row = BlendRow(
        match_id="m1",
        snapshot_id="close",
        date="2026-11-06",
        p_market=0.50,
        p_model=0.60,
        home_won=None,
        segments=("family_t20", "market_favourite"),
    )
    p_mean, p_sd = model.predict_one(row)
    assert 0.50 < p_mean < 0.65
    assert p_sd > 0.0


def test_probability_service_with_blend_model():
    blend_data = {
        "version": "1.0.0",
        "fitted": True,
        "usable_rows": 500,
        "posterior": {
            "a": {"mean": 0.0, "sd": 0.08},
            "b": {"mean": 0.75, "sd": 0.07},
            "c": {"mean": 0.65, "sd": 0.07},
            "segments": {name: {"mean": 0.0, "sd": 0.05} for name in BLEND_SEGMENTS},
        },
    }
    blend_model = BlendModel.from_dict(blend_data)
    service = ProbabilityService(
        ratings=DummyRatings(),
        prematch_models={"t20": DummyPrematch()},
        inplay_models={},
        blend_model=blend_model,
    )

    match = {
        "match_id": "test_m1",
        "team_home": "Alpha",
        "team_away": "Bravo",
        "family": "t20",
        "competition": "IPL",
    }
    book = {"home": {"back": 1.90, "lay": 1.92}, "away": {"back": 2.05, "lay": 2.10}}

    # Pre-match estimation should blend model and market
    est = service.estimate(match, book=book, state=None, as_of="2026-11-06T10:00:00Z")
    assert est.p_market is not None
    assert est.p_model is not None
    assert est.source == "prematch"
    assert est.has_model is True
    assert abs(est.p_model - est.p_market) > 0.0
