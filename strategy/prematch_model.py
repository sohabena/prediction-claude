"""S60 pre-match strength model v3: one row per match, pre-toss, no state features.

Inputs are as of the match date (only matches dated strictly before it are used):

* team Elo by format and gender with the home term, from ``strategy.ratings.CricketRatings.pre_match`` (``pm_elo_*``);
* expected-XI strength over each player's last ``APP_WINDOW`` appearances in that format and gender, shrunk to the
  league average (``AppearanceLedger``, computed here; the decayed ratings field is also used, as ``xi_decayed_diff``);
* rest, in days since each team's previous match, log-compressed and capped;
* venue chase bias and venue sample size (``pm_venue_*``), competition tier and gender.

Toss fields are deliberately absent: the arena is before the toss.

The learner is a stack. Two base models are fitted on the training seasons: an L2 logistic regression on the core
features and a histogram GBM on all features. Three calibrated candidates are fitted on the two-season calibration
window: pure Elo with Platt scaling, the logistic with Platt scaling, and a logistic stacker on the Elo, logistic and
GBM logits with Platt scaling. The candidate is chosen on the calibration window by season-split cross-fit log loss
(never on the test season). ``PrematchModel.predict_home`` reads feature values only.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from strategy.ratings import WICKET_RUN_VALUE, CricketRatings, MatchRow

VERSION = "v3"
CORE_FEATURES: Tuple[str, ...] = ("elo_diff", "xi_recent_diff", "xi_decayed_diff", "chase_bias", "rest_diff", "tier")
EXTRA_FEATURES: Tuple[str, ...] = ("venue_log_n", "male")
FEATURE_NAMES: Tuple[str, ...] = CORE_FEATURES + EXTRA_FEATURES
_CORE_IDX = [FEATURE_NAMES.index(n) for n in CORE_FEATURES]
_DEFAULTS: Dict[str, float] = {"tier": 2.0}
APP_WINDOW = 10
APP_SHRINK = 2.0
REST_CAP_DAYS = 60.0
PROB_CLIP = (0.01, 0.99)
ELO_LOGIT_SCALE = math.log(10.0)  # elo_diff is (Elo_home - Elo_away) / 400, so the Elo logit is ln(10) * elo_diff
CANDIDATES: Tuple[str, ...] = ("elo", "logistic", "stack")
_ZERO5: Tuple[float, ...] = (0.0,) * 5
_NOT_LABELLED_RESULTS = frozenset({"tie", "draw", "no result"})


def rest_feature(days: Optional[float]) -> float:
    """Log-compressed rest in days, capped at ``REST_CAP_DAYS``. No earlier match counts as the cap."""
    d = REST_CAP_DAYS if days is None else min(max(float(days), 0.0), REST_CAP_DAYS)
    return math.log1p(d)


def appearance_value(totals: Sequence[float], league: Sequence[float]) -> float:
    """Runs above the league-average rate in one match, from one player's totals in that match.

    ``totals`` and ``league`` use the layout of ``strategy.ratings`` (bat runs, bat balls, conceded, bowl balls,
    bowler wickets). Batting counts runs above the match's per-ball rate; bowling counts runs saved plus wickets at
    ``WICKET_RUN_VALUE`` each, both relative to the same match's league rates.
    """
    bat_runs, bat_balls, conceded, bowl_balls, wickets = totals
    lg_bat = league[0] / league[1] if league[1] else 0.0
    lg_conc = league[2] / league[3] if league[3] else 0.0
    lg_wkt = league[4] / league[3] if league[3] else 0.0
    bat = bat_runs - lg_bat * bat_balls
    bowl = (lg_conc * bowl_balls - conceded) + WICKET_RUN_VALUE * (wickets - lg_wkt * bowl_balls)
    return float(bat + bowl)


class AppearanceLedger:
    """Per player (family, gender, name): the value of each past appearance, queryable strictly before a date."""

    def __init__(self) -> None:
        self._pending: Dict[tuple, List[Tuple[str, float]]] = {}
        self._dates: Dict[tuple, List[str]] = {}
        self._vals: Dict[tuple, List[float]] = {}

    def add(self, key: tuple, iso_date: str, value: float) -> None:
        self._pending.setdefault(key, []).append((iso_date, value))

    def freeze(self) -> None:
        for key, entries in self._pending.items():
            entries.sort(key=lambda e: e[0])
            self._dates[key] = [d for d, _ in entries]
            self._vals[key] = [v for _, v in entries]
        self._pending = {}

    def strength(self, key: tuple, as_of: str) -> float:
        """Mean value of the last ``APP_WINDOW`` appearances dated before ``as_of``, shrunk to zero (league average)."""
        dates = self._dates.get(key)
        if not dates:
            return 0.0
        idx = bisect.bisect_left(dates, as_of)
        if idx == 0:
            return 0.0
        recent = self._vals[key][max(0, idx - APP_WINDOW):idx]
        return float(sum(recent) / (len(recent) + APP_SHRINK))


@dataclass(frozen=True)
class MatchFeatures:
    """One labelled match: as-of features, the label (1 if home won) and the segment fields."""

    match_id: str
    date: str
    season: int
    gender: str
    tier: int
    home: str
    away: str
    label: int
    features: Mapping[str, float] = field(default_factory=dict)


def _excluded(row: MatchRow) -> bool:
    """Ties, no-results and matches without a winner among the two teams carry no label."""
    result = str(row.outcome.get("result") or "").strip().lower()
    if result in _NOT_LABELLED_RESULTS or not row.winner:
        return True
    return row.winner not in row.teams


def build_feature_rows(ratings: CricketRatings, *, gender: Optional[str] = None) -> List[MatchFeatures]:
    """Labelled pre-toss feature rows for every match in ``ratings``, each as of its own date.

    ``gender`` (``male`` or ``female``) keeps only that gender's matches; ``None`` keeps both. The ledgers are built
    from all matches (appearances and rest are keyed by family and gender, so the filter does not mix them).
    """
    rows = sorted(ratings.rows.values(), key=lambda r: (r.date, r.match_id))
    apps = AppearanceLedger()
    team_dates: Dict[tuple, List[str]] = {}
    for r in rows:
        lg = r.league_totals
        for team in r.teams:
            team_dates.setdefault((r.family, r.gender, team), []).append(r.date)
            for name in r.players.get(team, ()):
                value = appearance_value(r.player_totals.get(name, _ZERO5), lg)
                apps.add((r.family, r.gender, name), r.date, value)
    apps.freeze()

    def rest_days(team: str, r: MatchRow) -> Optional[float]:
        dates = team_dates.get((r.family, r.gender, team), [])
        idx = bisect.bisect_left(dates, r.date)  # strictly earlier matches only
        if idx == 0:
            return None
        return float((date.fromisoformat(r.date) - date.fromisoformat(dates[idx - 1])).days)

    def xi(team: str, r: MatchRow) -> float:
        names = r.players.get(team, ())
        if not names:
            return 0.0
        return sum(apps.strength((r.family, r.gender, n), r.date) for n in names) / len(names)

    out: List[MatchFeatures] = []
    for r in rows:
        if _excluded(r):
            continue
        if gender is not None and r.gender != gender:
            continue
        pm = ratings.pre_match(r.match_id, r.date)
        feats = {
            "elo_diff": (pm["pm_elo_home"] - pm["pm_elo_away"]) / 400.0,
            "xi_recent_diff": xi(r.home, r) - xi(r.away, r),
            "xi_decayed_diff": pm["pm_xi_home"] - pm["pm_xi_away"],
            "chase_bias": pm["pm_venue_chase_pct"] / 100.0 - 0.5,
            "rest_diff": rest_feature(rest_days(r.home, r)) - rest_feature(rest_days(r.away, r)),
            "tier": float(r.tier),
            "venue_log_n": math.log1p(max(0.0, pm["pm_venue_n"])),
            "male": 1.0 if r.gender == "male" else 0.0,
        }
        out.append(MatchFeatures(
            match_id=r.match_id, date=r.date, season=int(r.date[:4]), gender=r.gender, tier=r.tier,
            home=r.home, away=r.away, label=int(r.winner == r.home), features=feats,
        ))
    return out


def feature_vector(features: Mapping[str, Any]) -> List[float]:
    """Feature values in ``FEATURE_NAMES`` order. Missing values are neutral (0, or the tier default 2)."""
    return [float(features[n]) if features.get(n) is not None else _DEFAULTS.get(n, 0.0) for n in FEATURE_NAMES]


def feature_matrix(rows: Sequence[MatchFeatures]) -> np.ndarray:
    if not rows:
        return np.empty((0, len(FEATURE_NAMES)), dtype=float)
    return np.asarray([feature_vector(r.features) for r in rows], dtype=float)


def elo_probability(features: Mapping[str, Any]) -> float:
    """Pure-Elo baseline ``1 / (1 + 10^(-(Elo_home - Elo_away) / 400))``, home term included."""
    return float(1.0 / (1.0 + 10.0 ** (-float(features["elo_diff"]))))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1.0 - 1e-4)
    return np.log(p / (1.0 - p))


class _Platt:
    """Platt scaling: a one-dimensional logistic regression on a logit (one column of the base-logit matrix)."""

    def __init__(self) -> None:
        self._lr: Optional[LogisticRegression] = None

    def fit(self, Z: np.ndarray, y: np.ndarray) -> "_Platt":
        self._lr = LogisticRegression(C=1.0, max_iter=1000).fit(np.asarray(Z, dtype=float).reshape(len(Z), 1), y)
        return self

    def predict(self, Z: np.ndarray) -> np.ndarray:
        return self._lr.predict_proba(np.asarray(Z, dtype=float).reshape(len(Z), 1))[:, 1]


class _StackPlatt:
    """Logistic stacker on several logits, then Platt scaling of the stacked logit."""

    def __init__(self) -> None:
        self._stacker: Optional[LogisticRegression] = None
        self._platt: Optional[_Platt] = None

    def fit(self, Z: np.ndarray, y: np.ndarray) -> "_StackPlatt":
        self._stacker = LogisticRegression(C=1.0, max_iter=1000).fit(Z, y)
        self._platt = _Platt().fit(self._stacker.decision_function(Z), y)
        return self

    def predict(self, Z: np.ndarray) -> np.ndarray:
        return self._platt.predict(self._stacker.decision_function(Z))


_CANDIDATE_COLS: Dict[str, List[int]] = {"elo": [0], "logistic": [1], "stack": [0, 1, 2]}


def _fit_candidate(name: str, Z: np.ndarray, y: np.ndarray) -> Any:
    """Calibrator for one candidate on base-logit columns ``Z`` (columns: elo, logistic, gbm)."""
    cols = _CANDIDATE_COLS[name]
    if name == "stack":
        return _StackPlatt().fit(Z[:, cols], y)
    return _Platt().fit(Z[:, cols], y)


def _predict_candidate(name: str, model: Any, Z: np.ndarray) -> np.ndarray:
    return np.clip(model.predict(Z[:, _CANDIDATE_COLS[name]]), *PROB_CLIP)


def _cross_fit_log_loss(name: str, Z: np.ndarray, y: np.ndarray, seasons: np.ndarray) -> float:
    """Leave-one-calibration-season-out log loss of a candidate, pooled over the calibration window.

    Each calibration season is scored by a calibrator fitted on the other calibration seasons only, so the
    selection score is out-of-sample within the window and never sees the test season.
    """
    preds = np.empty(len(y), dtype=float)
    for s in np.unique(seasons):
        held = seasons == s
        cal = _fit_candidate(name, Z[~held], y[~held])
        preds[held] = _predict_candidate(name, cal, Z[held])
    p = np.clip(preds, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


class PrematchModel:
    """Pre-match win probability: base logistic (core features) and GBM (all features), then one calibrated candidate.

    Candidates are ``elo`` (Platt on the Elo logit), ``logistic`` (Platt on the base logistic logit) and ``stack``
    (logistic stacker on the Elo, logistic and GBM logits, then Platt). The base learners are fitted on the training
    seasons. The candidate is chosen on the calibration window only, by season-split cross-fit log loss, and then
    refitted on the whole window. ``selected`` and ``selection_scores`` record the choice.
    """

    version: str = VERSION

    def __init__(self, family: str = "t20", seed: int = 0) -> None:
        self.family = family
        self.seed = seed
        self.trained_through: Optional[str] = None
        self.selected: Optional[str] = None
        self.selection_scores: Dict[str, float] = {}
        self._lr: Any = None
        self._gbm: Optional[HistGradientBoostingClassifier] = None
        self._cal: Dict[str, Any] = {}

    def fit_arrays(self, X_train: np.ndarray, y_train: np.ndarray, X_calib: np.ndarray, y_calib: np.ndarray,
                   calib_seasons: np.ndarray) -> "PrematchModel":
        """Base learners on the training seasons; candidate selection and calibration on the calibration window."""
        y_train = np.asarray(y_train, dtype=int)
        y_calib = np.asarray(y_calib, dtype=int)
        seasons = np.asarray(calib_seasons)
        if len(set(y_train.tolist())) < 2:
            raise ValueError("pre-match training needs both outcomes in the training seasons")
        for s in np.unique(seasons):
            if len(set(y_calib[seasons == s].tolist())) < 2:
                raise ValueError(f"calibration season {s} needs both outcomes")
        self._lr = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000)).fit(
            X_train[:, _CORE_IDX], y_train)
        self._gbm = HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=50, l2_regularization=1.0,
            random_state=self.seed,
        ).fit(X_train, y_train)
        Z = self._base_logits(X_calib)
        self.selection_scores = {n: _cross_fit_log_loss(n, Z, y_calib, seasons) for n in CANDIDATES}
        self.selected = min(CANDIDATES, key=lambda n: (self.selection_scores[n], CANDIDATES.index(n)))
        self._cal = {n: _fit_candidate(n, Z, y_calib) for n in CANDIDATES}
        return self

    def fit(self, train: Sequence[MatchFeatures], calib: Sequence[MatchFeatures]) -> "PrematchModel":
        """Fit from feature rows: ``train`` for the base learners, ``calib`` (two or more seasons) for calibration.

        Every training season must be earlier than every calibration season.
        """
        cal_seasons = sorted({r.season for r in calib})
        if len(cal_seasons) < 2:
            raise ValueError("the calibration window needs at least two seasons")
        if train and max(r.season for r in train) >= cal_seasons[0]:
            raise ValueError("training seasons must all precede the calibration window")
        self.trained_through = max((r.date for r in train), default=None)
        return self.fit_arrays(
            feature_matrix(train), np.asarray([r.label for r in train], dtype=int),
            feature_matrix(calib), np.asarray([r.label for r in calib], dtype=int),
            np.asarray([r.season for r in calib], dtype=int),
        )

    def _base_logits(self, X: np.ndarray) -> np.ndarray:
        """Columns: Elo logit, base logistic logit, GBM logit."""
        elo = ELO_LOGIT_SCALE * X[:, FEATURE_NAMES.index("elo_diff")]
        lr_p = self._lr.predict_proba(X[:, _CORE_IDX])[:, 1]
        gbm_p = self._gbm.predict_proba(X)[:, 1]
        return np.column_stack([elo, _logit(lr_p), _logit(gbm_p)])

    def predict_parts(self, X: np.ndarray) -> Dict[str, np.ndarray]:
        """Uncalibrated base predictions (logistic on core features, GBM on all) for comparison."""
        if self._gbm is None:
            raise RuntimeError("PrematchModel is not fitted")
        X = np.asarray(X, dtype=float)
        return {
            "logistic": self._lr.predict_proba(X[:, _CORE_IDX])[:, 1],
            "gbm": self._gbm.predict_proba(X)[:, 1],
        }

    def predict_candidates(self, X: np.ndarray) -> Dict[str, np.ndarray]:
        """Calibrated P(home wins) from every candidate (diagnostic; ``predict_matrix`` uses the selected one)."""
        if self.selected is None:
            raise RuntimeError("PrematchModel is not fitted")
        Z = self._base_logits(np.asarray(X, dtype=float))
        return {n: _predict_candidate(n, self._cal[n], Z) for n in CANDIDATES}

    def predict_matrix(self, X: np.ndarray) -> np.ndarray:
        """Calibrated P(home wins) for a matrix in ``FEATURE_NAMES`` order, from the selected candidate."""
        return self.predict_candidates(X)[self.selected]

    def predict_home(self, features: Mapping[str, Any]) -> float:
        """Calibrated P(home wins) from a named feature mapping (see ``FEATURE_NAMES``)."""
        return float(self.predict_matrix(np.asarray([feature_vector(features)], dtype=float))[0])

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, p)

    @classmethod
    def load(cls, path: str | Path) -> "PrematchModel":
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls):
            raise TypeError(f"{path} does not contain a PrematchModel")
        return obj
