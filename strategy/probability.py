"""S69 probability service for paper trading: one call gives P(home wins) for a match at a moment.

``ProbabilityService.estimate(match, book, state=None, *, as_of)`` returns an :class:`Estimate` with:

* ``p_market``: the de-vigged (power method) home probability of the book, or None when the book is unusable;
* ``p_model``: the model's P(home wins), from one of two paths:

  - no live score (``state`` is None), or no in-play model, or the in-play path cannot be fed: the S60 pre-match
    model (``PrematchModel`` v3) through :func:`training.blend.prematch_probability`, source ``prematch``;
  - a live score row (``state``): the S38 v2 in-play model (``FairValueModel``), fed with the state of the latest row
    and the ``pm_*`` pre-match block from :func:`strategy.live_context.prematch_fields`, source ``inplay``.

  When neither can be computed, ``p_model`` is None, ``has_model`` is False and the source is ``market_only``.

Live score rows are the ``match_context`` columns written by S68 (``score``, ``wickets``, ``overs``, ``innings``,
``balls_remaining``, ``batting_team``, ``run_rate``, ``req_run_rate``, ``match_format``). Two conversions are needed
to match how the in-play model was trained (``strategy.ratings.state_ticks``):

* ``overs`` is cricket notation in the feed (17.5 = 17 overs and 5 balls) and decimal overs in training
  (``legal_balls / 6``), so the feed value is converted to legal balls first;
* the target is not a column: on innings 2 it is the first-innings total + 1, taken from the last innings-1 row of
  the history passed in. The required run rate is then recomputed exactly as the training ticks do. Without a
  target, the feed's ``req_run_rate`` is used.

The batting side fixes the orientation: the score is the batting side's, ``batting_home`` is set from the batting
team's name matched against ``team_home`` / ``team_away``. A batting team that matches neither side is a missing
piece (``has_model`` False).

Never raises on missing data. No I/O, no database, no network: the ratings and the models are loaded by the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from shared.logging import setup_logging
from strategy.fair_value import family_of, prematch_features, state_features
from strategy.live_context import gender_of, prematch_fields, ratings_team_name
from training.blend import RestIndex, prematch_probability
from training.evaluate import flat_book
from training.market import TRADING_DEVIG, market_prob_home

logger = setup_logging("probability")

SOURCES = ("prematch", "inplay", "market_only")
STATE_FEATURES: Tuple[str, ...] = (
    "innings", "balls_frac", "wickets_left", "run_rate", "req_run_rate", "score", "needed", "batting_home", "overs",
)
PREMATCH_FEATURES: Tuple[str, ...] = (
    "pm_elo_diff", "pm_xi_diff", "pm_toss_home", "pm_toss_bat", "pm_venue_chase", "pm_log_venue_n", "pm_tier",
)
MAX_REQUIRED_RATE = 60.0  # the cap used by strategy.ratings._state_tick
MAX_CACHE_ENTRIES = 5000


@dataclass(frozen=True)
class Estimate:
    """P(home wins) from the model and from the market, with the source of the model probability."""

    p_model: Optional[float]
    p_market: Optional[float]
    source: str
    has_model: bool
    features: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


# --------------------------------------------------------------------------- pure helpers
def legal_balls(overs: Any) -> int:
    """Legal balls bowled from a feed ``overs`` value in cricket notation (17.5 = 17 overs and 5 balls).

    A fraction that is not a valid ball count (0 to 5 after the point) is read as decimal overs.
    """
    x = float(overs or 0.0)
    if not math.isfinite(x) or x < 0.0:
        return 0
    whole = int(math.floor(x + 1e-9))
    balls = int(round((x - whole) * 10))
    if 0 <= balls <= 5:
        return whole * 6 + balls
    return int(round(x * 6))


def derive_target(history: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """First-innings total + 1, from the last innings-1 row of ``history`` (rows in time order)."""
    total: Optional[float] = None
    for row in history:
        if int(row.get("innings") or 0) == 1 and row.get("score") is not None:
            total = float(row["score"])
    return None if total is None else int(total) + 1


def required_run_rate(target: int, score: float, balls_left: float) -> float:
    """Required run rate, with the same rule as the training ticks (``strategy.ratings._state_tick``)."""
    if target - score <= 0:
        return 0.0
    if balls_left <= 0:
        return MAX_REQUIRED_RATE
    return min(MAX_REQUIRED_RATE, (target - score) * 6.0 / balls_left)


def _key(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _orient(batting: Any, home: str, away: str, gender: str, normalize: Optional[Callable[[str], Optional[str]]]
            ) -> Optional[str]:
    """``"home"`` or ``"away"`` for the batting team's name, or None when it matches neither side."""
    if not _key(batting):
        return None
    sides = (("home", home), ("away", away))
    for side, name in sides:
        if _key(name) == _key(batting):
            return side
    for side, name in sides:
        if _key(ratings_team_name(name, gender)) == _key(ratings_team_name(batting, gender)):
            return side
    if normalize is not None:
        canon_batting = normalize(str(batting))
        if canon_batting:
            for side, name in sides:
                canon = normalize(name)
                if canon and canon == canon_batting:
                    return side
    return None


def _market(book: Any) -> Optional[float]:
    if not book:
        return None
    try:
        flat = flat_book(book) if isinstance(book.get("home"), Mapping) else dict(book)
        p = market_prob_home(flat, TRADING_DEVIG)  # band: never outside the book (trading)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return None if p is None or not math.isfinite(p) else float(p)


# --------------------------------------------------------------------------- service
class ProbabilityService:
    """Pre-match and in-play P(home wins) per match, with the market alongside.

    ``prematch_models`` maps family to a :class:`strategy.prematch_model.PrematchModel`; ``inplay_models`` maps family
    to a v2 :class:`strategy.fair_value.FairValueModel`. Families without a model fall back as described above.
    Pre-match fields are cached per match (ratings are slow); the rest index is built once per service.
    """

    def __init__(
        self,
        ratings: Any,
        prematch_models: Optional[Mapping[str, Any]],
        inplay_models: Optional[Mapping[str, Any]],
        *,
        normalize_team: Optional[Callable[[str], Optional[str]]] = None,
        blend_model: Optional[Any] = None,
    ) -> None:
        self.ratings = ratings
        self.prematch_models: Dict[str, Any] = dict(prematch_models or {})
        self.inplay_models: Dict[str, Any] = dict(inplay_models or {})
        self.normalize_team = normalize_team
        self.blend_model = blend_model
        self._rest: Optional[RestIndex] = None
        self._pm_cache: Dict[tuple, Dict[str, float]] = {}
        self._pre_cache: Dict[tuple, Optional[float]] = {}

    def estimate(
        self,
        match: Mapping[str, Any],
        book: Optional[Mapping[str, Any]],
        state: Any = None,
        *,
        as_of: Any,
    ) -> Estimate:
        """Model and market P(home wins) for ``match`` at ``as_of``.

        ``match``: ``match_id``, ``team_home``, ``team_away`` and, when known, ``family`` or ``match_format``,
        ``competition``, ``venue``, ``toss_winner``, ``toss_decision``. ``book``: a nested S58 snapshot book
        (``home``/``away`` with ``back``/``lay``) or a flat one (``back_home`` and so on). ``state``: one
        ``match_context`` row (a mapping) or the rows of the match so far, in time order (the last row is the state).
        """
        day = str(as_of)[:10]
        family = str(match.get("family") or "").strip().lower() or family_of(match.get("match_format"))
        p_market = _market(book)

        p_model: Optional[float] = None
        source = "market_only"
        features: Dict[str, float] = {}
        reasons: List[str] = []
        if state is not None:
            p_model, features, reason = self._inplay(match, state, family, day)
            if p_model is not None:
                source = "inplay"
            else:
                reasons.append(f"inplay:{reason}")
        if p_model is None:
            p_model, reason = self._prematch(match, family, day, p_market=p_market)
            if p_model is not None:
                source = "prematch"
            else:
                reasons.append(f"prematch:{reason}")
        return Estimate(
            p_model=p_model,
            p_market=p_market,
            source=source,
            has_model=p_model is not None,
            features=features,
            reason="" if p_model is not None else ";".join(reasons),
        )

    # ----------------------------------------------------------------- pre-match path
    def _prematch(self, match: Mapping[str, Any], family: str, day: str, p_market: Optional[float] = None) -> Tuple[Optional[float], str]:
        if self.ratings is None:
            return None, "no_ratings"
        model = self.prematch_models.get(family)
        if model is None:
            return None, "no_prematch_model"
        home, away = str(match.get("team_home") or "").strip(), str(match.get("team_away") or "").strip()
        if not home or not away:
            return None, "no_teams"
        competition = str(match.get("competition") or "")
        key = (str(match.get("match_id") or ""), family, day, home, away, competition)
        if key not in self._pre_cache:
            if len(self._pre_cache) >= MAX_CACHE_ENTRIES:
                self._pre_cache.clear()
            try:
                p = prematch_probability(
                    home=home, away=away, family=family, competition=competition, as_of=day,
                    ratings=self.ratings, model=model, rest=self._rest_index(),
                    normalize_team=self.normalize_team,
                )
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning("prematch_probability_failed", match_id=match.get("match_id"),
                               error=type(exc).__name__)
                p = None
            self._pre_cache[key] = None if p is None or not math.isfinite(p) else float(p)
        p = self._pre_cache[key]
        if p is None:
            return (None, "not_rated")

        # S61 / S103 Market Blend: if blend_model is active and market probability is known, apply the calibrated blend
        if self.blend_model is not None and p_market is not None and 0.01 < p_market < 0.99:
            try:
                from training.blend import BlendRow
                segs = []
                if family == "t20":
                    segs.append("family_t20")
                elif family == "odi":
                    segs.append("family_odi")
                elif family == "test":
                    segs.append("family_test")
                gen = gender_of(home, away, competition)
                if gen == "female":
                    segs.append("gender_women")
                tier_val = match.get("pm_tier", match.get("tier", 1))
                if int(tier_val or 1) == 1:
                    segs.append("tier_2")
                if p_market >= 0.5:
                    segs.append("market_favourite")
                if p_market < 0.25:
                    segs.append("market_longshot")
                row = BlendRow(
                    match_id=str(match.get("match_id") or ""),
                    snapshot_id="live",
                    date=day,
                    p_market=float(p_market),
                    p_model=float(p),
                    home_won=None,
                    segments=tuple(segs),
                )
                p_blend, _ = self.blend_model.predict_one(row)
                if math.isfinite(p_blend):
                    p = float(p_blend)
            except Exception as exc:
                logger.warning("blend_predict_failed", error=type(exc).__name__)

        return (p, "")

    def _rest_index(self) -> RestIndex:
        if self._rest is None:
            self._rest = RestIndex(self.ratings)
        return self._rest

    # ----------------------------------------------------------------- in-play path
    def _inplay(self, match: Mapping[str, Any], state: Any, family: str, day: str
                ) -> Tuple[Optional[float], Dict[str, float], str]:
        model = self.inplay_models.get(family)
        if model is None:
            return None, {}, "no_inplay_model"
        if self.ratings is None:
            return None, {}, "no_ratings"
        if isinstance(state, Mapping):
            history: List[Mapping[str, Any]] = [state]
        else:
            history = [r for r in state if r is not None]
        if not history:
            return None, {}, "no_state"
        history = sorted(history, key=lambda r: str(r.get("time") or ""))
        tick, reason = self._tick(match, history, family, day)
        if tick is None:
            return None, {}, reason
        pm = self._pm_fields(match, tick, family, day)
        if not pm:
            return None, {}, "no_prematch_fields"
        tick.update(pm)
        p = float(model.predict_home(tick))
        if not math.isfinite(p):
            return None, {}, "non_finite"
        feats = dict(zip(STATE_FEATURES, state_features(tick, family)))
        feats.update(zip(PREMATCH_FEATURES, prematch_features(tick)))
        return p, feats, ""

    def _tick(self, match: Mapping[str, Any], history: Sequence[Mapping[str, Any]], family: str, day: str
              ) -> Tuple[Optional[Dict[str, Any]], str]:
        """The v2 tick fields for the latest row (see the module docstring for the mapping)."""
        home, away = str(match.get("team_home") or "").strip(), str(match.get("team_away") or "").strip()
        if not home or not away:
            return None, "no_teams"
        row = history[-1]
        gender = gender_of(home, away, str(match.get("competition") or ""))
        side = _orient(row.get("batting_team"), home, away, gender, self.normalize_team)
        if side is None:
            return None, "batting_team_unmatched"
        innings = int(row.get("innings") or 1)
        legal = legal_balls(row.get("overs"))
        score = float(row.get("score") or 0.0)
        balls_left = float(row.get("balls_remaining") or 0.0)
        target = derive_target(history) if innings == 2 else None
        if innings == 1:
            req = 0.0
        elif target is not None:
            req = required_run_rate(target, score, balls_left)
        else:
            req = float(row.get("req_run_rate") or 0.0)
        batting = home if side == "home" else away
        bowling = away if side == "home" else home
        tick: Dict[str, Any] = {
            "match_id": str(match.get("match_id") or ""),
            "time": day,
            "team_home": home,
            "team_away": away,
            "competition": str(match.get("competition") or ""),
            "match_format": str(match.get("match_format") or row.get("match_format") or ""),
            "family": family,
            "venue": match.get("venue"),
            "toss_winner": match.get("toss_winner"),
            "toss_decision": match.get("toss_decision"),
            "in_play": True,
            "innings": innings,
            "score": score,
            "wickets": float(row.get("wickets") or 0.0),
            "overs": legal / 6.0,
            "balls_remaining": balls_left,
            "run_rate": score * 6.0 / legal if legal else 0.0,
            "req_run_rate": req,
            "batting_team": batting,
            "bowling_team": bowling,
            "target": target,
        }
        return tick, ""

    def _pm_fields(self, match: Mapping[str, Any], tick: Mapping[str, Any], family: str, day: str
                   ) -> Dict[str, float]:
        key = (tick["match_id"], family, day, tick["team_home"], tick["team_away"], tick["competition"],
               str(match.get("venue") or ""), str(match.get("toss_winner") or ""),
               str(match.get("toss_decision") or ""))
        if key not in self._pm_cache:
            if len(self._pm_cache) >= MAX_CACHE_ENTRIES:
                self._pm_cache.clear()
            self._pm_cache[key] = prematch_fields(
                dict(tick), self.ratings, normalize_team=self.normalize_team, as_of=day,
            )
        return dict(self._pm_cache[key])


__all__ = [
    "SOURCES",
    "STATE_FEATURES",
    "PREMATCH_FEATURES",
    "Estimate",
    "ProbabilityService",
    "derive_target",
    "legal_balls",
    "required_run_rate",
]
