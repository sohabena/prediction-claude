"""Calibrated win-probability model (S14 v1, S38 v2).

Trained per format family (T20 / ODI / multi-day). The match result is used strictly as a *label* in ``fit``;
``predict_home`` reads feature fields only, never ``winner*`` / ``_meta``.

Versions:

- ``v1`` (S14): match-state features only (9 values).
- ``v2`` (S38): the v1 state features plus pre-match strength and context (7 values, ``pm_*`` tick fields
  produced by ``strategy.ratings.CricketRatings.pre_match``). ``v2`` columns are ``v1`` columns followed by the
  pre-match block, so a v2 matrix sliced to its first 9 columns is the v1 matrix.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from shared.logging import setup_logging

logger = setup_logging("fair_value")

FAMILIES = ("t20", "odi", "multiday")
VERSIONS = ("v1", "v2")
V1_WIDTH = 9
V2_WIDTH = V1_WIDTH + 7
# v2 pre-match columns fed to the stage-1 prior (offsets in the 7-value block): elo_diff, xi_diff, toss_home,
# venue_chase, log_venue_n, tier. toss_bat is left out (no signal in the S38 ablation).
_PRIOR_COLS = [0, 1, 2, 4, 5, 6]
DEFAULT_DIR = Path("models/fair_value")
_MAX_BALLS = {"t20": 120.0, "odi": 300.0, "multiday": 540.0}


def family_of(match_format: Optional[str]) -> str:
    """Map a corpus ``match_format`` string to a model family.

    ``ODM`` (Cricsheet one-day match) is an ODI, not a multi-day match.
    """
    f = (match_format or "").lower()
    if "t20" in f or "twenty" in f or "hundred" in f:
        return "t20"
    if "odi" in f or "odm" in f or "list" in f or "50" in f:
        return "odi"
    return "multiday"


def _num(value: Any, default: float) -> float:
    return default if value is None else float(value)


def state_features(tick: Dict[str, Any], family: str) -> List[float]:
    """State-only feature vector for one tick (no result fields are read)."""
    innings = float(tick.get("innings") or 1)
    balls_left = float(tick.get("balls_remaining") or 0.0)
    wickets = float(tick.get("wickets") or 0.0)
    rr = float(tick.get("run_rate") or 0.0)
    rrr = float(tick.get("req_run_rate") or 0.0)
    score = float(tick.get("score") or 0.0)
    needed = rrr * balls_left / 6.0 if innings >= 2 else 0.0
    batting = tick.get("batting_team")
    batting_home = 1.0 if batting and batting == tick.get("team_home") else 0.0
    return [
        innings, min(1.0, balls_left / _MAX_BALLS[family]), 10.0 - wickets, rr, rrr,
        score, needed, batting_home, float(tick.get("overs") or 0.0),
    ]


def prematch_features(tick: Dict[str, Any]) -> List[float]:
    """Pre-match block (v2). Missing fields fall back to neutral values (no information)."""
    elo_home = _num(tick.get("pm_elo_home"), 1500.0)
    elo_away = _num(tick.get("pm_elo_away"), 1500.0)
    xi_home = _num(tick.get("pm_xi_home"), 0.0)
    xi_away = _num(tick.get("pm_xi_away"), 0.0)
    toss_home = _num(tick.get("pm_toss_home"), 0.0)
    toss_bat = _num(tick.get("pm_toss_bat"), 0.5)
    chase = _num(tick.get("pm_venue_chase_pct"), 50.0)
    venue_n = _num(tick.get("pm_venue_n"), 0.0)
    tier = _num(tick.get("pm_tier"), 2.0)
    return [
        (elo_home - elo_away) / 400.0,
        xi_home - xi_away,
        toss_home,
        toss_bat,
        chase / 100.0,
        math.log1p(max(0.0, venue_n)),
        tier,
    ]


def tick_features(tick: Dict[str, Any], family: str, version: str = "v1") -> List[float]:
    """Feature vector for one tick. ``v1`` is state only; ``v2`` appends the pre-match block."""
    state = state_features(tick, family)
    if version == "v1":
        return state
    if version == "v2":
        return state + prematch_features(tick)
    raise ValueError(f"unknown feature version {version!r}; expected one of {VERSIONS}")


def _label(episode: Sequence[Dict[str, Any]]) -> Optional[int]:
    """1 if home won, 0 if away won, None if unknown (label use only)."""
    for t in (episode[-1], episode[0]):
        meta = t.get("_meta") or {}
        if meta.get("winner_is_home") is not None:
            return int(bool(meta["winner_is_home"]))
        if meta.get("winner"):
            return int(meta["winner"] == t.get("team_home"))
    return None


def model_path(family: str, key: Optional[str] = None, version: str = "v1", root: Optional[Path] = None) -> Path:
    """Canonical model file for a family / key / version.

    v1 keeps the S14 names (``<family>.joblib``, ``<family>_<key>.joblib``) so that the value evaluator
    dispatch in ``scripts/train_format_specialists.py`` keeps working. v2 uses
    ``<family>_v2_<key or all>.joblib``.
    """
    base = Path(root) if root is not None else DEFAULT_DIR
    if version == "v1":
        return base / (f"{family}.joblib" if key is None else f"{family}_{key}.joblib")
    if version == "v2":
        return base / f"{family}_v2_{key or 'all'}.joblib"
    raise ValueError(f"unknown feature version {version!r}")


class FairValueModel:
    """Gradient-boosted win-probability model with isotonic calibration."""

    version: str = "v1"          # class default so models pickled before S38 still load as v1
    trained_through: Optional[str] = None  # ISO date of the last match used for fitting (set by callers)

    def __init__(
        self, family: str = "t20", stride: int = 3, calib_frac: float = 0.2, seed: int = 0, version: str = "v1"
    ) -> None:
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
        if version not in VERSIONS:
            raise ValueError(f"unknown version {version!r}; expected one of {VERSIONS}")
        self.family = family
        self.stride = max(1, stride)
        self.calib_frac = calib_frac
        self.seed = seed
        self.version = version
        self._gbm: Optional[HistGradientBoostingClassifier] = None
        self._iso: Optional[IsotonicRegression] = None
        self.train_match_ids: List[str] = []

    def _xy(self, episodes: Sequence[Sequence[Dict[str, Any]]]) -> tuple[np.ndarray, np.ndarray]:
        xs: List[List[float]] = []
        ys: List[int] = []
        for ep in episodes:
            if not ep:
                continue
            y = _label(ep)
            if y is None:
                continue
            for t in ep[:: self.stride]:
                xs.append(tick_features(t, self.family, self.version))
                ys.append(y)
        return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)

    def fit(
        self,
        episodes: Sequence[Sequence[Dict[str, Any]]],
        calib_episodes: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
    ) -> "FairValueModel":
        """Fit on chronologically ordered train episodes.

        Without ``calib_episodes`` the last ``calib_frac`` of ``episodes`` calibrate (S14 behaviour). With them,
        the isotonic calibrator is fitted on ``calib_episodes`` (walk-forward uses the season before the test).
        """
        eps = [e for e in episodes if e]
        self.train_match_ids = [str(e[0].get("match_id", "")) for e in eps]
        if calib_episodes is not None:
            fit_eps = eps
            cal_eps = [e for e in calib_episodes if e]
        else:
            n_cal = int(len(eps) * self.calib_frac)
            fit_eps, cal_eps = (eps[:-n_cal], eps[-n_cal:]) if n_cal >= 2 else (eps, [])
        X, y = self._xy(fit_eps)
        if cal_eps:
            Xc, yc = self._xy(cal_eps)
        else:
            Xc, yc = None, None
        return self.fit_arrays(X, y, Xc, yc)

    def fit_arrays(
        self,
        X: np.ndarray,
        y: np.ndarray,
        Xc: Optional[np.ndarray] = None,
        yc: Optional[np.ndarray] = None,
    ) -> "FairValueModel":
        """Fit on a precomputed feature matrix (columns must match ``self.version``)."""
        y = np.asarray(y, dtype=int)
        if len(set(y.tolist())) < 2:
            raise ValueError("fair value training needs both outcomes")
        self._prior = None
        if self.version == "v2":
            # Two stages. The pre-match block is constant within a match and nearly unique to it, so trees given
            # it directly memorise training matches (S38 ablation: an uninformative match-constant feature raised
            # test Brier 0.166 -> 0.184). Stage 1 fits a regularised logistic prior on one row per distinct
            # pre-match block; stage 2 sees state + that single prior logit, monotonic, with large leaves.
            pm = np.asarray(X, dtype=float)[:, V1_WIDTH:][:, _PRIOR_COLS]
            _, first = np.unique(pm, axis=0, return_index=True)
            if len(set(y[first].tolist())) == 2:
                self._prior = LogisticRegression(C=0.5, max_iter=1000).fit(pm[first], y[first])
            X = self._stage2(X)
            Xc = self._stage2(Xc) if Xc is not None else None
            gbm = HistGradientBoostingClassifier(
                max_depth=4, learning_rate=0.08, max_iter=150, random_state=self.seed,
                min_samples_leaf=500, monotonic_cst=[0] * V1_WIDTH + [1],
            )
        else:
            gbm = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=150, random_state=self.seed)
        self._gbm = gbm.fit(X, y)
        self._iso = None
        if Xc is not None and yc is not None and len(yc) and len(set(np.asarray(yc).tolist())) == 2:
            raw = self._gbm.predict_proba(Xc)[:, 1]
            self._iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, yc)
        logger.info("fair_value_fitted", family=self.family, version=self.version, rows=int(len(y)),
                    calibrated=self._iso is not None)
        return self

    def _stage2(self, X: np.ndarray) -> np.ndarray:
        """v2 stage-2 matrix: state columns plus the stage-1 prior logit (0 when no prior could be fitted)."""
        X = np.asarray(X, dtype=float)
        prior = getattr(self, "_prior", None)
        logit = (prior.decision_function(X[:, V1_WIDTH:][:, _PRIOR_COLS]) if prior is not None
                 else np.zeros(len(X)))
        return np.column_stack([X[:, :V1_WIDTH], logit])

    def predict_X(self, X: np.ndarray) -> np.ndarray:
        """Calibrated P(home wins) for a feature matrix (columns must match ``self.version``)."""
        if self._gbm is None:
            raise RuntimeError("FairValueModel is not fitted")
        X = np.asarray(X, dtype=float)
        if self.version == "v2":
            X = self._stage2(X)
        p = self._gbm.predict_proba(X)[:, 1]
        if self._iso is not None:
            p = self._iso.predict(p)
        return np.clip(p, 0.01, 0.99)

    def predict_batch(self, ticks: Sequence[Dict[str, Any]]) -> np.ndarray:
        if not ticks:
            return np.empty(0, dtype=float)
        X = np.asarray([tick_features(t, self.family, self.version) for t in ticks], dtype=float)
        return self.predict_X(X)

    def predict_home(self, tick: Dict[str, Any]) -> float:
        """Calibrated P(home wins) from match state (and, for v2, pre-match fields only)."""
        return float(self.predict_batch([tick])[0])

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, p)

    @classmethod
    def load(cls, path: str | Path) -> "FairValueModel":
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls):
            raise TypeError(f"{path} does not contain a FairValueModel")
        return obj
