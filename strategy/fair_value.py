"""Calibrated in-play home-win probability model (S14).

Trained per format family (T20 / ODI / multi-day) on match-state features only. The
match result is used strictly as a *label* in ``fit``; ``predict_home`` reads state
fields and never ``winner*`` / ``_meta``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from shared.logging import setup_logging

logger = setup_logging("fair_value")

FAMILIES = ("t20", "odi", "multiday")
_MAX_BALLS = {"t20": 120.0, "odi": 300.0, "multiday": 540.0}


def family_of(match_format: Optional[str]) -> str:
    """Map a corpus ``match_format`` string to a model family."""
    f = (match_format or "").lower()
    if "t20" in f or "twenty" in f or "hundred" in f:
        return "t20"
    if "odi" in f or "list" in f or "50" in f:
        return "odi"
    return "multiday"


def tick_features(tick: Dict[str, Any], family: str) -> List[float]:
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


def _label(episode: Sequence[Dict[str, Any]]) -> Optional[int]:
    """1 if home won, 0 if away won, None if unknown (label use only)."""
    for t in (episode[-1], episode[0]):
        meta = t.get("_meta") or {}
        if meta.get("winner_is_home") is not None:
            return int(bool(meta["winner_is_home"]))
        if meta.get("winner"):
            return int(meta["winner"] == t.get("team_home"))
    return None


class FairValueModel:
    """Gradient-boosted win-probability model with isotonic calibration."""

    def __init__(self, family: str = "t20", stride: int = 3, calib_frac: float = 0.2, seed: int = 0) -> None:
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
        self.family = family
        self.stride = max(1, stride)
        self.calib_frac = calib_frac
        self.seed = seed
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
                xs.append(tick_features(t, self.family))
                ys.append(y)
        return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)

    def fit(self, episodes: Sequence[Sequence[Dict[str, Any]]]) -> "FairValueModel":
        """Fit on chronologically ordered train episodes; the last ``calib_frac`` calibrate."""
        eps = [e for e in episodes if e]
        self.train_match_ids = [str(e[0].get("match_id", "")) for e in eps]
        n_cal = int(len(eps) * self.calib_frac)
        fit_eps, cal_eps = (eps[:-n_cal], eps[-n_cal:]) if n_cal >= 2 else (eps, [])
        X, y = self._xy(fit_eps)
        if len(set(y.tolist())) < 2:
            raise ValueError("fair value training needs both outcomes")
        self._gbm = HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.08, max_iter=150, random_state=self.seed
        ).fit(X, y)
        self._iso = None
        if cal_eps:
            Xc, yc = self._xy(cal_eps)
            if len(set(yc.tolist())) == 2:
                raw = self._gbm.predict_proba(Xc)[:, 1]
                self._iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, yc)
        logger.info("fair_value_fitted", family=self.family, rows=len(y), calibrated=self._iso is not None)
        return self

    def predict_batch(self, ticks: Sequence[Dict[str, Any]]) -> np.ndarray:
        if self._gbm is None:
            raise RuntimeError("FairValueModel is not fitted")
        X = np.asarray([tick_features(t, self.family) for t in ticks], dtype=float)
        p = self._gbm.predict_proba(X)[:, 1]
        if self._iso is not None:
            p = self._iso.predict(p)
        return np.clip(p, 0.01, 0.99)

    def predict_home(self, tick: Dict[str, Any]) -> float:
        """Calibrated P(home wins) from match state only."""
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
