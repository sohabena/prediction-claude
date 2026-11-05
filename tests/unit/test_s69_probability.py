"""S69: probability service for paper trading, with stub ratings and stub models (no data, no database)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from strategy.probability import (
    PREMATCH_FEATURES,
    STATE_FEATURES,
    ProbabilityService,
    derive_target,
    legal_balls,
    required_run_rate,
)
from training.evaluate import flat_book
from training.market import market_prob_home

PM_FIELDS: Dict[str, float] = {
    "pm_elo_home": 1600.0, "pm_elo_away": 1500.0, "pm_xi_home": 0.1, "pm_xi_away": -0.1,
    "pm_toss_home": 0.0, "pm_toss_bat": 0.5, "pm_venue_chase_pct": 55.0, "pm_venue_n": 20.0, "pm_tier": 2.0,
}


class StubRatings:
    """Ratings stand-in: the teams in ``known`` (name, gender) are rated; ``pre_match_live`` returns fixed pm fields."""

    def __init__(self, known: Set[Tuple[str, str]]) -> None:
        self.rows: Dict[str, Any] = {}
        self.known = known
        self.calls: List[Dict[str, Any]] = []

    def _team_known(self, name: str, family: str, gender: str) -> bool:
        return (name, gender) in self.known

    def pre_match_live(self, team_home: str, team_away: str, family: str, gender: str, as_of: str, *,
                       venue: Optional[str] = None, city: Optional[str] = None, players: Any = None,
                       toss_winner: Optional[str] = None, toss_decision: Optional[str] = None,
                       tier: Optional[int] = None, normalize_team: Any = None) -> Dict[str, float]:
        self.calls.append({"home": team_home, "away": team_away, "family": family, "gender": gender,
                           "as_of": as_of, "venue": venue, "toss_winner": toss_winner,
                           "toss_decision": toss_decision, "tier": tier})
        return dict(PM_FIELDS)


class StubPrematch:
    """Pre-match model stand-in: P = 0.5 + 0.1 * elo_diff, and records the feature mappings it was given."""

    def __init__(self) -> None:
        self.seen: List[Dict[str, Any]] = []

    def predict_home(self, features: Dict[str, Any]) -> float:
        self.seen.append(dict(features))
        return 0.5 + 0.1 * float(features["elo_diff"])


class StubInplay:
    """In-play model stand-in: P = 0.6 when home bats, 0.4 when away bats; records every tick it is asked about."""

    def __init__(self) -> None:
        self.ticks: List[Dict[str, Any]] = []

    def predict_home(self, tick: Dict[str, Any]) -> float:
        self.ticks.append(dict(tick))
        return 0.6 if tick["batting_team"] == tick["team_home"] else 0.4


MATCH = {"match_id": "lb_1", "team_home": "Alpha", "team_away": "Bravo", "match_format": "T20",
         "competition": "Test League", "venue": "Ground One"}
KNOWN_MEN = {("Alpha", "male"), ("Bravo", "male")}
BOOK = {"home": {"back": 1.9, "lay": 2.0}, "away": {"back": 2.05, "lay": 2.2}}
AS_OF = "2026-11-03"


def _row(**kw: Any) -> Dict[str, Any]:
    base = {"time": "2026-11-03 10:00:00+00", "innings": 1, "score": 80, "wickets": 3, "overs": 10.2,
            "balls_remaining": 58, "batting_team": "Alpha", "bowling_team": "Bravo", "run_rate": 7.7,
            "req_run_rate": 0.0, "match_format": "T20"}
    base.update(kw)
    return base


def _service(ratings: StubRatings, pre: Any = None, inplay: Any = None) -> ProbabilityService:
    return ProbabilityService(ratings, {"t20": pre or StubPrematch()}, {"t20": inplay or StubInplay()})


# --------------------------------------------------------------------------- pure helpers
def test_legal_balls_reads_cricket_notation() -> None:
    assert legal_balls(17.5) == 107
    assert legal_balls(0.0) == 0
    assert legal_balls(19.0) == 114
    assert legal_balls(19.2) == 116


def test_legal_balls_reads_decimal_overs_when_fraction_is_not_a_ball_count() -> None:
    assert legal_balls(17.83) == 107


def test_derive_target_uses_last_innings_one_row() -> None:
    history = [
        {"innings": 1, "score": 90}, {"innings": 1, "score": 146}, {"innings": 2, "score": 5},
    ]
    assert derive_target(history) == 147
    assert derive_target([{"innings": 2, "score": 5}]) is None


def test_required_run_rate_matches_training_rule() -> None:
    assert required_run_rate(147, 100, 60) == pytest.approx(4.7)
    assert required_run_rate(147, 150, 60) == 0.0
    assert required_run_rate(147, 100, 0) == 60.0


# --------------------------------------------------------------------------- market
def test_p_market_is_the_band_midpoint_of_the_book() -> None:
    # Trading uses the band: the midpoint of the range both runners' prices allow (never outside the book)
    svc = _service(StubRatings(set()))
    est = svc.estimate(MATCH, BOOK, as_of=AS_OF)
    assert est.p_market == pytest.approx(market_prob_home(flat_book(BOOK), "band"))


def test_p_market_accepts_flat_book_and_rejects_unusable_book() -> None:
    svc = _service(StubRatings(set()))
    flat = {"back_home": 1.9, "lay_home": 2.0, "back_away": 2.05, "lay_away": 2.2}
    assert svc.estimate(MATCH, flat, as_of=AS_OF).p_market == pytest.approx(market_prob_home(flat, "band"))
    # Away prices alone still pin P(home) = 1 - P(away): between 1 - 1/2.0 and 1 - 1/2.2
    away_only = {"home": {"back": None, "lay": None}, "away": {"back": 2.0, "lay": 2.2}}
    assert svc.estimate(MATCH, away_only, as_of=AS_OF).p_market == pytest.approx((0.5 + (1 - 1 / 2.2)) / 2)
    empty = {"home": {"back": None, "lay": None}, "away": {"back": None, "lay": None}}
    assert svc.estimate(MATCH, empty, as_of=AS_OF).p_market is None
    assert svc.estimate(MATCH, None, as_of=AS_OF).p_market is None


# --------------------------------------------------------------------------- pre-match path
def test_prematch_path_maps_features_and_caches_per_match() -> None:
    ratings, pre = StubRatings(KNOWN_MEN), StubPrematch()
    svc = _service(ratings, pre=pre)
    est = svc.estimate(MATCH, BOOK, None, as_of=AS_OF)
    assert est.source == "prematch"
    assert est.has_model is True
    assert est.p_model == pytest.approx(0.5 + 0.1 * 0.25)
    assert pre.seen[0]["elo_diff"] == pytest.approx(0.25)
    assert ratings.calls[0]["as_of"] == AS_OF and ratings.calls[0]["gender"] == "male"
    svc.estimate(MATCH, BOOK, None, as_of=AS_OF)
    assert len(ratings.calls) == 1, "pre-match fields must be cached per match"


def test_prematch_path_women_names_are_stripped_for_ratings() -> None:
    ratings = StubRatings({("Sydney Thunder", "female"), ("Sydney Sixers", "female")})
    svc = _service(ratings)
    match = {"match_id": "w_1", "team_home": "Sydney Thunder W", "team_away": "Sydney Sixers W",
             "match_format": "T20", "competition": "Women's Big Bash League"}
    est = svc.estimate(match, BOOK, None, as_of=AS_OF)
    assert est.source == "prematch" and est.has_model
    assert ratings.calls[0]["home"] == "Sydney Thunder"
    assert ratings.calls[0]["away"] == "Sydney Sixers"
    assert ratings.calls[0]["gender"] == "female"


def test_unrated_teams_give_no_model_and_market_remains() -> None:
    svc = _service(StubRatings(set()))
    est = svc.estimate(MATCH, BOOK, None, as_of=AS_OF)
    assert est.has_model is False
    assert est.p_model is None
    assert est.source == "market_only"
    assert est.p_market is not None
    assert "not_rated" in est.reason


def test_multiday_family_has_no_model() -> None:
    svc = _service(StubRatings(KNOWN_MEN))
    est = svc.estimate({**MATCH, "match_format": "Test"}, BOOK, None, as_of=AS_OF)
    assert est.has_model is False and est.source == "market_only"


# --------------------------------------------------------------------------- in-play path
def test_inplay_away_side_batting_is_oriented_to_away() -> None:
    ratings, inplay, pre = StubRatings(KNOWN_MEN), StubInplay(), StubPrematch()
    svc = _service(ratings, pre=pre, inplay=inplay)
    row = _row(batting_team="Bravo", bowling_team="Alpha", overs=10.2, balls_remaining=58)
    est = svc.estimate(MATCH, BOOK, row, as_of=AS_OF)
    assert est.source == "inplay" and est.has_model
    assert est.p_model == pytest.approx(0.4)
    tick = inplay.ticks[0]
    assert tick["batting_team"] == "Bravo" and tick["bowling_team"] == "Alpha"
    assert tick["overs"] == pytest.approx(62 / 6)
    assert tick["run_rate"] == pytest.approx(80 * 6 / 62)
    assert tick["balls_remaining"] == 58.0
    assert tick["req_run_rate"] == 0.0 and tick["innings"] == 1
    assert est.features["batting_home"] == 0.0
    assert est.features["overs"] == pytest.approx(62 / 6)
    assert set(STATE_FEATURES) <= set(est.features) and set(PREMATCH_FEATURES) <= set(est.features)
    assert est.features["pm_elo_diff"] == pytest.approx(0.25)
    assert pre.seen == [], "the pre-match model is not used when the in-play path answers"


def test_inplay_home_side_batting_has_batting_home_flag() -> None:
    inplay = StubInplay()
    svc = _service(StubRatings(KNOWN_MEN), inplay=inplay)
    est = svc.estimate(MATCH, BOOK, _row(batting_team="Alpha"), as_of=AS_OF)
    assert est.p_model == pytest.approx(0.6)
    assert est.features["batting_home"] == 1.0
    assert inplay.ticks[0]["bowling_team"] == "Bravo"


def test_target_and_required_rate_come_from_first_innings_history() -> None:
    inplay = StubInplay()
    svc = _service(StubRatings(KNOWN_MEN), inplay=inplay)
    history = [
        _row(innings=1, score=150, overs=19.6, balls_remaining=0, batting_team="Alpha", time="2026-11-03 09:00:00+00"),
        _row(innings=2, score=40, overs=6.0, balls_remaining=84, batting_team="Bravo", wickets=1,
             req_run_rate=0.0, time="2026-11-03 10:00:00+00"),
    ]
    est = svc.estimate(MATCH, BOOK, history, as_of=AS_OF)
    tick = inplay.ticks[0]
    assert tick["target"] == 151
    assert tick["req_run_rate"] == pytest.approx((151 - 40) * 6 / 84)
    assert est.features["req_run_rate"] == pytest.approx((151 - 40) * 6 / 84)
    assert est.features["needed"] == pytest.approx((151 - 40) * 6 / 84 * 84 / 6)
    assert est.features["batting_home"] == 0.0


def test_innings_two_without_history_uses_feed_required_rate() -> None:
    inplay = StubInplay()
    svc = _service(StubRatings(KNOWN_MEN), inplay=inplay)
    est = svc.estimate(MATCH, BOOK, _row(innings=2, score=40, batting_team="Bravo", req_run_rate=7.25), as_of=AS_OF)
    assert est.source == "inplay"
    assert inplay.ticks[0]["target"] is None
    assert inplay.ticks[0]["req_run_rate"] == pytest.approx(7.25)


def test_state_row_order_uses_latest_time_as_the_state() -> None:
    inplay = StubInplay()
    svc = _service(StubRatings(KNOWN_MEN), inplay=inplay)
    late = _row(score=95, overs=12.0, balls_remaining=48, time="2026-11-03 10:05:00+00")
    early = _row(score=60, overs=8.0, balls_remaining=72, time="2026-11-03 09:55:00+00")
    svc.estimate(MATCH, BOOK, [late, early], as_of=AS_OF)
    assert inplay.ticks[0]["score"] == 95.0


def test_batting_team_matching_neither_side_falls_back_to_prematch() -> None:
    inplay, pre = StubInplay(), StubPrematch()
    svc = _service(StubRatings(KNOWN_MEN), pre=pre, inplay=inplay)
    est = svc.estimate(MATCH, BOOK, _row(batting_team="Zulu"), as_of=AS_OF)
    assert inplay.ticks == []
    assert est.source == "prematch" and est.has_model
    assert est.reason == ""


def test_inplay_pm_fields_are_cached_per_match() -> None:
    ratings = StubRatings(KNOWN_MEN)
    svc = _service(ratings)
    svc.estimate(MATCH, BOOK, _row(), as_of=AS_OF)
    svc.estimate(MATCH, BOOK, _row(score=90, overs=11.0, balls_remaining=54), as_of=AS_OF)
    inplay_calls = [c for c in ratings.calls if c["home"] == "Alpha" and c["away"] == "Bravo"]
    assert len(inplay_calls) == 1


# --------------------------------------------------------------------------- degradation
def test_missing_models_and_ratings_never_raise() -> None:
    svc = ProbabilityService(None, {}, {})
    est = svc.estimate(MATCH, BOOK, _row(), as_of=AS_OF)
    assert est.has_model is False and est.p_model is None
    assert est.source == "market_only"
    assert est.p_market is not None


def test_missing_teams_never_raise() -> None:
    svc = _service(StubRatings(KNOWN_MEN))
    est = svc.estimate({"match_id": "x"}, None, _row(), as_of=AS_OF)
    assert est.has_model is False
    assert est.p_market is None
    assert est.source == "market_only"
