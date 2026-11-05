"""S61 market-blend model: the market's probability, corrected only where recorded evidence says so.

On a snapshot row the model is a Bayesian logistic regression with the market logit as offset::

    logit p_final = a + b * L_mkt + c * (L_model - L_mkt) + sum_k d_k * segment_k

``L_mkt`` is the logit of the de-vigged market probability (``training.market.market_prob_home``, power method),
``L_model`` the logit of the S60 pre-match probability. The ``c`` term is zero on rows without a model
(``has_model`` False), so those rows never move ``c``. Segment terms are reference-coded against the pre-registered
segments of ``training.segments``: each ``d_k`` is a shift relative to the reference group (for example
``gender_women`` against the men's competitions, ``market_favourite`` against ``market_mid``).

Priors are Gaussian and centred on the market: ``a ~ N(0, 0.1)``, ``b ~ N(1, 0.1)``, ``c ~ N(0, 0.3)``,
``d_k ~ N(0, 0.1)``. The MAP estimate is found by Newton's method and the posterior is the Laplace approximation
(the inverse Hessian at the MAP). With no training rows the model returns the market probability exactly.

Walk-forward (:func:`walk_forward`): for each match date D in ascending order the model is refitted on the labelled
rows dated strictly before D and predicts the rows dated D. No row of day D can reach its own training set.

Outputs are probabilities, never bets. numpy and the existing market, segment and ratings modules only. No I/O, no
database, no network. :func:`prematch_probability` reads a ratings object and a PrematchModel the caller has loaded.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from strategy.live_context import gender_of as ratings_gender_of
from strategy.live_context import ratings_team_name
from strategy.prematch_model import rest_feature
from training import segments as seg
from training.evaluate import flat_book
from training.market import DEFAULT_DEVIG, market_prob_home

BLEND_VERSION = "1.0.0"
DEFAULT_SNAPSHOT = "T-10m"
DEFAULT_SEED = 61
N_DRAWS = 200
Z_95 = 1.959963984540054
DEFAULT_PRIOR_SD: Dict[str, float] = {"a": 0.1, "b": 0.1, "c": 0.3, "d": 0.1}
LOGIT_EPS = 1e-6
NEWTON_MAX_ITER = 100
NEWTON_TOL = 1e-10
# Pre-registered segments that enter as indicator columns. Complementary pairs and the market reference group are
# left out (real = not simulated, gender_men = not women, tier_1 = not tier_2, market_mid = reference).
BLEND_SEGMENTS: Tuple[str, ...] = (
    "family_t20",
    "family_odi",
    "family_test",
    "gender_women",
    "tier_2",
    "market_favourite",
    "market_longshot",
    "simulated",
)
RATED_FAMILIES: Tuple[str, ...] = ("t20", "odi")
_INTERNATIONAL_RE = re.compile(r"\bt20i\b|international|world cup", re.IGNORECASE)


# --------------------------------------------------------------------------- probabilities
def logit(p: Any) -> np.ndarray:
    x = np.clip(np.asarray(p, dtype=float), LOGIT_EPS, 1.0 - LOGIT_EPS)
    return np.log(x / (1.0 - x))


def sigmoid(x: Any) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


# --------------------------------------------------------------------------- rows
@dataclass(frozen=True)
class BlendRow:
    """One labelled match at one snapshot: market and model probabilities, outcome and segments.

    ``p_model`` is None when the match could not be rated; the ``c`` term is then zero for the row.
    """

    match_id: str
    snapshot_id: str
    date: str
    p_market: float
    p_model: Optional[float]
    home_won: Optional[int]
    segments: Tuple[str, ...] = ()

    @property
    def has_model(self) -> bool:
        return self.p_model is not None

    def offset_and_design(self) -> Tuple[float, np.ndarray]:
        """Offset ``L_mkt`` and design row ``[1, L_mkt, delta, segment indicators]`` for the deviation parameters."""
        lm = float(logit(self.p_market))
        delta = float(logit(self.p_model)) - lm if self.has_model else 0.0
        indicators = [1.0 if name in self.segments else 0.0 for name in BLEND_SEGMENTS]
        return lm, np.asarray([1.0, lm, delta] + indicators, dtype=float)


def _design(rows: Sequence[BlendRow]) -> Tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.empty(0), np.empty((0, 3 + len(BLEND_SEGMENTS)))
    pairs = [r.offset_and_design() for r in rows]
    return np.asarray([p[0] for p in pairs], dtype=float), np.asarray([p[1] for p in pairs], dtype=float)


# --------------------------------------------------------------------------- model
class BlendModel:
    """Bayesian logistic blend of the market logit and the model logit, with segment shifts.

    Parameters are stored as deviations ``beta = (a, b - 1, c, d_1 .. d_K)`` from the market (all prior means zero),
    so the linear predictor is ``L_mkt + Z beta``.
    """

    def __init__(self, *, prior_sd: Optional[Mapping[str, float]] = None, n_draws: int = N_DRAWS,
                 seed: int = DEFAULT_SEED) -> None:
        self.prior_sd = {**DEFAULT_PRIOR_SD, **dict(prior_sd or {})}
        for key, value in self.prior_sd.items():
            if key not in DEFAULT_PRIOR_SD or not (math.isfinite(value) and value > 0):
                raise ValueError(f"prior sd {key!r} must be one of {sorted(DEFAULT_PRIOR_SD)} and positive")
        self.n_draws = int(n_draws)
        self.seed = int(seed)
        scale = [self.prior_sd["a"], self.prior_sd["b"], self.prior_sd["c"]] + [self.prior_sd["d"]] * len(BLEND_SEGMENTS)
        self._prior_prec = 1.0 / np.square(np.asarray(scale, dtype=float))
        self.beta = np.zeros(len(scale))
        self.cov = np.diag(1.0 / self._prior_prec)
        self.n_train = 0
        self.fitted = False
        self.train_dates: Tuple[str, ...] = ()

    @property
    def n_params(self) -> int:
        return 3 + len(BLEND_SEGMENTS)

    def fit(self, rows: Sequence[BlendRow]) -> "BlendModel":
        """MAP by Newton's method, then the Laplace covariance. Rows must carry a home_won of 0 or 1."""
        for r in rows:
            if r.home_won not in (0, 1):
                raise ValueError(f"blend row {r.match_id} has no outcome; only settled rows can be fitted")
        self.n_train = len(rows)
        self.train_dates = tuple(sorted({r.date for r in rows}))
        if not rows:
            self.beta = np.zeros(self.n_params)
            self.cov = np.diag(1.0 / self._prior_prec)
            self.fitted = False
            return self
        off, Z = _design(rows)
        y = np.asarray([r.home_won for r in rows], dtype=float)
        beta = np.zeros(self.n_params)
        converged = False
        for _ in range(NEWTON_MAX_ITER):
            mu = sigmoid(off + Z @ beta)
            grad = Z.T @ (y - mu) - self._prior_prec * beta
            hess = Z.T @ (Z * (mu * (1.0 - mu))[:, None]) + np.diag(self._prior_prec)
            step = np.linalg.solve(hess, grad)
            beta = beta + step
            if float(np.max(np.abs(step))) < NEWTON_TOL:
                converged = True
                break
        if not converged:
            raise RuntimeError("blend MAP fit did not converge")
        mu = sigmoid(off + Z @ beta)
        hess = Z.T @ (Z * (mu * (1.0 - mu))[:, None]) + np.diag(self._prior_prec)
        self.beta = beta
        self.cov = np.linalg.inv(hess)
        self.fitted = True
        return self

    def predict(self, rows: Sequence[BlendRow]) -> Tuple[np.ndarray, np.ndarray]:
        """``(p_mean, p_sd)`` per row from ``n_draws`` seeded posterior draws.

        Unfitted (no training rows): returns the market probability exactly, with sd zero.
        """
        off, Z = _design(rows)
        if not rows:
            return np.empty(0), np.empty(0)
        if not self.fitted:
            p_market = np.asarray([r.p_market for r in rows], dtype=float)
            return p_market, np.zeros(len(rows))
        rng = np.random.default_rng(self.seed)
        draws = rng.multivariate_normal(self.beta, self.cov, size=self.n_draws, method="eigh")
        p_draws = sigmoid(off[None, :] + draws @ Z.T)
        return p_draws.mean(axis=0), p_draws.std(axis=0)

    def predict_one(self, row: BlendRow) -> Tuple[float, float]:
        mean, sd = self.predict([row])
        return float(mean[0]), float(sd[0])

    def posterior(self) -> Dict[str, Any]:
        """Posterior mean, sd and 95% interval (Laplace, normal) of a, b, c and each segment shift."""
        def block(name: str, index: int, shift: float = 0.0) -> Dict[str, float]:
            mean = float(self.beta[index] + shift)
            sd = float(math.sqrt(self.cov[index, index]))
            return {"mean": mean, "sd": sd, "lo95": mean - Z_95 * sd, "hi95": mean + Z_95 * sd}

        out: Dict[str, Any] = {
            "a": block("a", 0),
            "b": block("b", 1, shift=1.0),
            "c": block("c", 2),
            "segments": {name: block(name, 3 + i) for i, name in enumerate(BLEND_SEGMENTS)},
        }
        return out


# --------------------------------------------------------------------------- walk-forward
def walk_forward(rows: Sequence[BlendRow], *, prior_sd: Optional[Mapping[str, float]] = None,
                 n_draws: int = N_DRAWS, seed: int = DEFAULT_SEED) -> List[Dict[str, Any]]:
    """Out-of-sample predictions for every labelled row, refitting before each match date.

    Returns one record per labelled row: ``match_id``, ``snapshot_id``, ``date``, ``p_home``, ``p_sd``, ``p_market``,
    ``has_model``, ``n_train`` (training rows) and ``train_max_date`` (latest training date, None when empty).
    """
    labelled = [r for r in rows if r.home_won in (0, 1)]
    out: List[Dict[str, Any]] = []
    for day in sorted({r.date for r in labelled}):
        train = [r for r in labelled if r.date < day]
        test = [r for r in labelled if r.date == day]
        model = BlendModel(prior_sd=prior_sd, n_draws=n_draws, seed=seed).fit(train)
        means, sds = model.predict(test)
        for row, p, sd in zip(test, means, sds):
            out.append({
                "match_id": row.match_id,
                "snapshot_id": row.snapshot_id,
                "date": day,
                "p_home": float(p),
                "p_sd": float(sd),
                "p_market": row.p_market,
                "has_model": row.has_model,
                "n_train": len(train),
                "train_max_date": max((r.date for r in train), default=None),
            })
    return out


# --------------------------------------------------------------------------- building rows from a dataset
def build_blend_rows(
    dataset: Mapping[str, Any],
    p_model_by_match: Mapping[str, Optional[float]],
    *,
    snapshot_label: str = DEFAULT_SNAPSHOT,
    include_simulated: bool = False,
) -> Tuple[List[BlendRow], Counter]:
    """One :class:`BlendRow` per settled match with a priced snapshot at ``snapshot_label``.

    Excluded matches are counted by reason in the returned Counter: ``not_settled``, ``simulated``, ``no_start_date``,
    ``no_snapshot``, ``no_prices``. The market probability is the power de-vig of the snapshot book.
    """
    snapshots: Dict[str, Dict[str, Any]] = {}
    for snap in dataset["snapshots"]:
        if snap.get("label") == snapshot_label:
            snapshots[str(snap["match_id"])] = snap
    rows: List[BlendRow] = []
    reasons: Counter = Counter()
    for match in dataset["matches"]:
        match_id = str(match["match_id"])
        if match.get("home_won") not in (0, 1):
            reasons["not_settled"] += 1
            continue
        if match.get("is_simulated") and not include_simulated:
            reasons["simulated"] += 1
            continue
        start = match.get("match_start_time")
        if not start:
            reasons["no_start_date"] += 1
            continue
        snap = snapshots.get(match_id)
        if snap is None:
            reasons["no_snapshot"] += 1
            continue
        p_mkt = market_prob_home(flat_book(snap["book"]), DEFAULT_DEVIG)
        if p_mkt is None:
            reasons["no_prices"] += 1
            continue
        segs = seg.segments_for({
            "family": match.get("family"),
            "competition": match.get("competition"),
            "is_simulated": bool(match.get("is_simulated")),
            "p_market": p_mkt,
        })
        rows.append(BlendRow(
            match_id=match_id,
            snapshot_id=str(snap["snapshot_id"]),
            date=str(start)[:10],
            p_market=float(p_mkt),
            p_model=p_model_by_match.get(match_id),
            home_won=int(match["home_won"]),
            segments=segs,
        ))
    return rows, reasons



def recorded_tier(competition: Any) -> int:
    """Ratings tier for a recorded (non-Cricsheet) match: 0 international, otherwise 1 (club or league)."""
    return 0 if _INTERNATIONAL_RE.search(str(competition or "")) else 1


class RestIndex:
    """Dates of each team's rated matches per (family, gender), for days since the previous match before a date."""

    def __init__(self, ratings: Any) -> None:
        self._dates: Dict[tuple, List[str]] = defaultdict(list)
        for r in ratings.rows.values():
            for team in r.teams:
                self._dates[(r.family, r.gender, team)].append(r.date)
        for dates in self._dates.values():
            dates.sort()

    def days_since(self, family: str, gender: str, team: str, as_of: str) -> Optional[float]:
        dates = self._dates.get((family, gender, team), [])
        idx = bisect_left(dates, as_of)  # strictly earlier matches only, as in build_feature_rows
        if idx == 0:
            return None
        return float((date.fromisoformat(as_of) - date.fromisoformat(dates[idx - 1])).days)


def prematch_probability(
    *,
    home: str,
    away: str,
    family: str,
    competition: Any,
    as_of: str,
    ratings: Any,
    model: Any,
    rest: RestIndex,
    normalize_team: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[float]:
    """S60 P(home wins) for a recorded match, or None when it cannot be rated.

    Rated only when the family is t20 or odi and both teams are known to the ratings in that family and gender.
    Features that need the match's venue or squads (chase bias, venue size, expected XI) are neutral: the recorded
    matches carry no venue or squad lists, so the model sees Elo, rest, tier and gender only.
    """
    if family not in RATED_FAMILIES or model is None:
        return None
    gender = ratings_gender_of(home, away, competition or "")
    # Women's sides are recorded as "Sydney Thunder W"; the ratings know them as "Sydney Thunder" (gender female)
    home, away = ratings_team_name(home, gender), ratings_team_name(away, gender)
    canon_home = (normalize_team(home) if normalize_team else None) or str(home).strip()
    canon_away = (normalize_team(away) if normalize_team else None) or str(away).strip()
    if not (ratings._team_known(canon_home, family, gender) and ratings._team_known(canon_away, family, gender)):
        return None
    tier = recorded_tier(competition)
    pm = ratings.pre_match_live(home, away, family, gender, as_of, tier=tier, normalize_team=normalize_team)
    rest_home = rest.days_since(family, gender, canon_home, as_of)
    rest_away = rest.days_since(family, gender, canon_away, as_of)
    features = {
        "elo_diff": (pm["pm_elo_home"] - pm["pm_elo_away"]) / 400.0,
        "xi_recent_diff": 0.0,
        "xi_decayed_diff": pm["pm_xi_home"] - pm["pm_xi_away"],
        "chase_bias": pm["pm_venue_chase_pct"] / 100.0 - 0.5,
        "rest_diff": rest_feature(rest_home) - rest_feature(rest_away),
        "tier": float(tier),
        "venue_log_n": math.log1p(max(0.0, pm["pm_venue_n"])),
        "male": 1.0 if gender == "male" else 0.0,
    }
    return float(model.predict_home(features))


def prematch_probabilities(
    matches: Iterable[Mapping[str, Any]],
    *,
    ratings: Any,
    models: Mapping[str, Any],
    normalize_team: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Optional[float]]:
    """S60 P(home wins) per recorded match, keyed by ``match_id``. Matches without a start date get None."""
    rest = RestIndex(ratings)
    out: Dict[str, Optional[float]] = {}
    for m in matches:
        match_id = str(m["match_id"])
        start = m.get("match_start_time")
        if not start:
            out[match_id] = None
            continue
        out[match_id] = prematch_probability(
            home=str(m.get("team_home") or ""), away=str(m.get("team_away") or ""),
            family=str(m.get("family") or ""), competition=m.get("competition"),
            as_of=str(start)[:10], ratings=ratings, model=models.get(str(m.get("family") or "")),
            rest=rest, normalize_team=normalize_team,
        )
    return out


__all__ = [
    "BLEND_VERSION",
    "BLEND_SEGMENTS",
    "DEFAULT_PRIOR_SD",
    "DEFAULT_SNAPSHOT",
    "N_DRAWS",
    "BlendRow",
    "BlendModel",
    "walk_forward",
    "build_blend_rows",
    "recorded_tier",
    "RestIndex",
    "prematch_probability",
    "prematch_probabilities",
    "logit",
    "sigmoid",
]
