"""S97 live market-dynamics probability: the band market probability now, plus the model's predicted 10-minute change.

``MarketDynamicsModel.estimate(price_history, score_history, now, off=...)`` returns ``p_market_now + predicted change``
clipped to the band the book allows, or None when the inputs are insufficient (no fresh price, no band). The features
come from ``training.market_dynamics.MatchHistory``, the same code the dataset and the replay use. Nothing here uses team
ratings or Cricsheet.

``load_current()`` reads ``models/market_dynamics/current.json`` and the model it names; a missing or unreadable model
gives None (the variants then open no new risk) and is logged once. ``PerMatchDynamics`` holds one model per match
for the replay's leave-one-match-out run. No I/O beyond the model files; no database, no network.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import joblib
import numpy as np

from shared.logging import setup_logging
from training.market import band_bounds
from training.market_dynamics import FEATURES, VERSION, MatchHistory

logger = setup_logging("market_dynamics")

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "market_dynamics"
CURRENT_PATH = MODEL_DIR / "current.json"

_warned: set[str] = set()
P_CLIP_LOW = 0.01  # the model's probability stays inside these bounds (a sanity clip, not the band; see detail())
P_CLIP_HIGH = 0.99


def _warn_once(key: str, message: str, **fields: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, **fields)


class MarketDynamicsModel:
    """One fitted regressor of the 10-minute change in the band probability, with its feature order."""

    def __init__(self, estimator: Any, *, features: Sequence[str] = FEATURES, meta: Optional[Mapping[str, Any]] = None):
        if list(features) != list(FEATURES):
            raise ValueError("market-dynamics model was fitted on a different feature list")
        self.estimator = estimator
        self.features = tuple(features)
        self.meta = dict(meta or {})

    @classmethod
    def load(cls, path: Path) -> "MarketDynamicsModel":
        """Load a model file written by ``scripts/train_market_dynamics.py``. Raises on a missing or invalid file."""
        payload = joblib.load(Path(path))
        if not isinstance(payload, dict) or "estimator" not in payload:
            raise ValueError(f"{path}: not a market-dynamics model file")
        return cls(payload["estimator"], features=payload.get("features", FEATURES),
                   meta={k: v for k, v in payload.items() if k != "estimator"})

    def model_for(self, match_id: str) -> "MarketDynamicsModel":
        """The model for one match: this one, for every match."""
        return self

    def predict_change(self, features: Mapping[str, float]) -> float:
        x = np.asarray([[float(features.get(f, math.nan)) for f in self.features]], dtype=float)
        return float(self.estimator.predict(x)[0])

    def detail(
        self,
        price_history: Sequence[Mapping[str, Any]],
        score_history: Sequence[Mapping[str, Any]],
        now: datetime,
        *,
        off: Optional[datetime] = None,
    ) -> Optional[dict[str, Any]]:
        """Probability ``p_market now + predicted change`` (clipped to ``P_CLIP_LOW``..``P_CLIP_HIGH``), the predicted change
    and the book's band (for the record); None when the inputs are insufficient.

        ``price_history``: this match's price rows (``time`` and the four price fields), ``score_history``: its
        ``match_context`` rows. Rows after ``now`` are ignored by the features. ``off``: the start of the match
        (sets the reference price of the ``dist_ref`` feature).
        """
        hist = MatchHistory(price_history, score_history, off=off)
        feats = hist.features(now)
        if feats is None:
            return None
        delta = self.predict_change(feats)
        if not math.isfinite(delta):
            return None
        p_now = feats["p_now"]  # the band midpoint: None (no feature row) when the book has no band
        p = min(P_CLIP_HIGH, max(P_CLIP_LOW, p_now + delta))
        # Clipped to fixed bounds, not to the book's band. Inside the band no takeable price has a positive edge (a BACK
        # home at the back price pays only when p >= 1/back_home, the band's top), so a band clip would stop every trade.
        # The band itself is checked by the trade guards on the market probability (S75).
        flat = hist.flat_book(now)
        bounds = band_bounds(flat) if flat is not None else None
        low, high = bounds if bounds is not None else (None, None)
        return {"p": p, "p_now": p_now, "delta": delta, "low": low, "high": high}

    def estimate(
        self,
        price_history: Sequence[Mapping[str, Any]],
        score_history: Sequence[Mapping[str, Any]],
        now: datetime,
        *,
        off: Optional[datetime] = None,
    ) -> Optional[float]:
        """``p_market_now + predicted change`` clipped to the sanity bounds; None when the model or the inputs are missing."""
        found = self.detail(price_history, score_history, now, off=off)
        return None if found is None else float(found["p"])


class PerMatchDynamics:
    """One model per match (replay leave-one-match-out); ``default`` serves matches with no model of their own."""

    def __init__(self, models: Mapping[str, MarketDynamicsModel], default: Optional[MarketDynamicsModel] = None):
        self.models = {str(k): v for k, v in models.items()}
        self.default = default

    def model_for(self, match_id: str) -> Optional[MarketDynamicsModel]:
        return self.models.get(str(match_id), self.default)


_cache: dict[str, tuple[tuple[int, int], Optional[MarketDynamicsModel]]] = {}


def load_current(root: Path = ROOT) -> Optional[MarketDynamicsModel]:
    """The active model named by ``models/market_dynamics/current.json``, or None (logged once) when there is none or
    it cannot be read. The pointer and the model are re-read only when either file changes (the trader calls this
    every cycle)."""
    path = Path(root) / "models" / "market_dynamics" / "current.json"
    if not path.exists():
        _warn_once("missing", "market_dynamics_no_current_model", path=str(path))
        return None
    try:
        pointer = json.loads(path.read_text(encoding="utf-8"))
        model_path = Path(root) / pointer["model"]
        stamp = (path.stat().st_mtime_ns, model_path.stat().st_mtime_ns if model_path.exists() else -1)
    except Exception as exc:  # a missing, corrupt or other-version file must not stop the trader: no new risk instead
        _warn_once(f"bad:{path}", "market_dynamics_pointer_unreadable", path=str(path),
                   error=f"{type(exc).__name__}: {exc}")
        return None
    key = str(path)
    if key in _cache and _cache[key][0] == stamp:
        return _cache[key][1]
    try:
        model: Optional[MarketDynamicsModel] = MarketDynamicsModel.load(model_path)
    except Exception as exc:
        _warn_once(f"bad:{model_path}", "market_dynamics_model_unreadable", path=str(model_path),
                   error=f"{type(exc).__name__}: {exc}")
        model = None
    else:
        if model.meta.get("version") != VERSION:
            _warn_once(f"version:{model_path}", "market_dynamics_version_mismatch", path=str(model_path),
                       version=str(model.meta.get("version")), expected=VERSION)
    _cache[key] = (stamp, model)
    return model


__all__ = [
    "CURRENT_PATH",
    "MODEL_DIR",
    "MarketDynamicsModel",
    "PerMatchDynamics",
    "load_current",
]
