"""S64 in-play price dynamics (research track): price-only features, baselines and walk-forward models.

Question: after a large move in the de-vigged LotusBook home probability, does the price continue or revert over the
next 10 minutes, measured at the price a person could actually take (60 s after the snapshot)?

Definitions (fixed before any run, see ``docs/stories/S64-inplay-price-dynamics.md``):

- Source: the S58 dataset, in-play snapshots with prices (every 2 min). ``p_t`` is ``training.market.market_prob_home``
  of the snapshot's own ``book`` (power de-vig, via ``training.evaluate.flat_book``).
- Features at snapshot t use only ``book`` at t and earlier snapshots of the same match: ``d2`` (last 2 min move),
  ``abs_d2``, ``d6``, ``d10`` (moves over 6 and 10 min), ``vol10`` (sd of the five 2-min moves up to t), ``spread``
  (relative back/lay spread at t) and ``minutes`` (time since the match's first in-play snapshot).
- Label: ``y = p(book_10m) - p(book_60s)`` at the same snapshot. Both are look-ahead books, used for labels only.
- Large move: ``|d6| >= LARGE_MOVE_P``.
- Walk-forward: matches in order of their first in-play snapshot. A test match is predicted by models fitted on rows
  of other matches whose label window has closed by the test match's first snapshot (``t + 10 min <= cutoff``), so
  labels never overlap the test period. Folds with too little training data make no model prediction.

Pure functions, numpy and scikit-learn only. No database, no network, no signals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge

from training.evaluate import flat_book
from training.market import DEFAULT_DEVIG, market_prob_home, wilson_interval

RESEARCH_VERSION = "1.0.0"
STEP_S = 120
LAG_S = 60
HORIZON_S = 600
LARGE_MOVE_P = 0.05
FADE_FRACTION = 0.5
MIN_TRAIN_ROWS = 100
MIN_TRAIN_MATCHES = 3
RIDGE_ALPHA = 1.0
GBM_PARAMS = {"n_estimators": 60, "max_depth": 2, "learning_rate": 0.05, "subsample": 0.8,
              "min_samples_leaf": 10}
N_BOOT = 2000
BOOT_SEED = 20261103
FEATURES = ("d2", "abs_d2", "d6", "d10", "vol10", "spread", "minutes")
MODELS = ("random_walk", "momentum", "reversion", "ridge", "gbm")


# --------------------------------------------------------------------------- rows
def _market_p(book: Mapping[str, Any], method: str) -> Optional[float]:
    return market_prob_home(flat_book(book), method)


def _spread(book: Mapping[str, Any]) -> Optional[float]:
    """Mean relative back/lay spread over both runners, or None when either runner lacks a back or a lay."""
    widths = []
    for runner in ("home", "away"):
        leg = book.get(runner) or {}
        back, lay = leg.get("back"), leg.get("lay")
        if back is None or lay is None or float(back) <= 1.0 or float(lay) <= 1.0:
            return None
        mid = 0.5 * (float(back) + float(lay))
        widths.append((float(lay) - float(back)) / mid)
    return float(np.mean(widths))


def _at(series: Mapping[datetime, float], t: datetime, lag_s: int) -> Optional[float]:
    return series.get(t - timedelta(seconds=lag_s))


def build_rows(dataset: Mapping[str, Any], method: str = DEFAULT_DEVIG) -> list[dict[str, Any]]:
    """One row per in-play snapshot with a usable price, features and label. Incomplete rows are dropped.

    Each row keeps ``match_id``, ``snapshot_id``, ``t``, ``is_simulated``, ``home_won``, ``match_start_time``, the
    features (``FEATURES``), the label ``y`` and ``p_now`` and ``d6`` for policy use.
    """
    matches = {str(m["match_id"]): m for m in dataset["matches"]}
    by_match: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for snap in dataset["snapshots"]:
        if snap.get("in_play"):
            by_match[str(snap["match_id"])].append(snap)

    rows: list[dict[str, Any]] = []
    for match_id, snaps in by_match.items():
        snaps.sort(key=lambda s: s["t"])
        first_t = datetime.fromisoformat(snaps[0]["t"])
        series: dict[datetime, float] = {}
        for snap in snaps:
            p = _market_p(snap["book"], method)
            if p is not None:
                series[datetime.fromisoformat(snap["t"])] = p
        match = matches.get(match_id, {})
        for snap in snaps:
            t = datetime.fromisoformat(snap["t"])
            p_now = series.get(t)
            if p_now is None:
                continue
            p_60 = _market_p(snap["book_60s"], method)
            p_10m = _market_p(snap["book_10m"], method)
            spread = _spread(snap["book"])
            p_m2, p_m6, p_m10 = (_at(series, t, k) for k in (STEP_S, 3 * STEP_S, 5 * STEP_S))
            if None in (p_60, p_10m, spread, p_m2, p_m6, p_m10):
                continue
            # Volatility: sd of the five 2-minute moves ending at t, all six points needed.
            grid = [_at(series, t, k * STEP_S) for k in range(0, 6)]
            if any(g is None for g in grid):
                continue
            moves = [grid[k] - grid[k + 1] for k in range(5)]
            d2 = p_now - p_m2
            feats = {
                "d2": d2,
                "abs_d2": abs(d2),
                "d6": p_now - p_m6,
                "d10": p_now - p_m10,
                "vol10": float(np.std(moves, ddof=1)),
                "spread": spread,
                "minutes": (t - first_t).total_seconds() / 60.0,
            }
            rows.append({
                "match_id": match_id,
                "snapshot_id": snap["snapshot_id"],
                "t": t,
                "match_start_time": match.get("match_start_time"),
                "is_simulated": bool(match.get("is_simulated")),
                "home_won": match.get("home_won"),
                "p_now": p_now,
                "features": feats,
                "y": p_10m - p_60,
            })
    rows.sort(key=lambda r: (r["t"], r["match_id"]))
    return rows


def large_move(row: Mapping[str, Any]) -> bool:
    return abs(row["features"]["d6"]) >= LARGE_MOVE_P


def _matrix(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.array([[r["features"][f] for f in FEATURES] for r in rows], dtype=float)


# --------------------------------------------------------------------------- predictors
def fit_predictor(name: str, train: Sequence[Mapping[str, Any]]) -> Callable[[Sequence[Mapping[str, Any]]], np.ndarray]:
    """Fit one predictor of the 10-minute price change on training rows only; returns ``predict(rows) -> changes``."""
    if name == "random_walk":
        return lambda rows: np.zeros(len(rows))
    if name in ("momentum", "reversion"):
        magnitude = float(np.mean([abs(r["y"]) for r in train])) if train else 0.0
        sign = 1.0 if name == "momentum" else -1.0
        return lambda rows: np.array([sign * magnitude * np.sign(r["features"]["d6"]) for r in rows])
    x = _matrix(train)
    y = np.array([r["y"] for r in train], dtype=float)
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    z = (x - mu) / sd
    if name == "ridge":
        model = Ridge(alpha=RIDGE_ALPHA).fit(z, y)
    elif name == "gbm":
        model = GradientBoostingRegressor(random_state=BOOT_SEED, **GBM_PARAMS).fit(z, y)
    else:
        raise ValueError(f"unknown model {name!r}; expected one of {MODELS}")
    return lambda rows: model.predict((_matrix(rows) - mu) / sd)


def walk_forward(rows: Sequence[Mapping[str, Any]], models: Sequence[str] = MODELS) -> list[dict[str, Any]]:
    """Out-of-sample predictions per row and model, from matches' expanding windows with label purging.

    Returns one dict per evaluable row: ``match_id``, ``t``, ``y``, ``d6``, ``large``, and ``pred[model]``. A row is
    evaluable only when its fold had enough training data (``MIN_TRAIN_ROWS`` rows from ``MIN_TRAIN_MATCHES`` matches).
    """
    first_t: dict[str, datetime] = {}
    for r in rows:
        first_t[r["match_id"]] = min(first_t.get(r["match_id"], r["t"]), r["t"])
    out: list[dict[str, Any]] = []
    for match_id in sorted(first_t, key=lambda m: (first_t[m], m)):
        cutoff = first_t[match_id]
        test = [r for r in rows if r["match_id"] == match_id]
        train = [r for r in rows
                 if r["match_id"] != match_id and r["t"] + timedelta(seconds=HORIZON_S) <= cutoff]
        if len(train) < MIN_TRAIN_ROWS or len({r["match_id"] for r in train}) < MIN_TRAIN_MATCHES:
            continue
        predictors = {name: fit_predictor(name, train) for name in models}
        preds = {name: predictor(test) for name, predictor in predictors.items()}
        for i, r in enumerate(test):
            out.append({
                "match_id": match_id,
                "snapshot_id": r["snapshot_id"],
                "t": r["t"],
                "y": r["y"],
                "d6": r["features"]["d6"],
                "large": large_move(r),
                "pred": {name: float(preds[name][i]) for name in models},
            })
    return out


# --------------------------------------------------------------------------- metrics
def _bootstrap_mean(values: np.ndarray, groups: np.ndarray, seed: int = BOOT_SEED,
                    n_boot: int = N_BOOT) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Mean of per-row values with a percentile CI from resampling matches (paired by construction)."""
    if values.size == 0:
        return None, None, None
    ids = np.unique(groups)
    per = {g: values[groups == g] for g in ids}
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.choice(ids, size=len(ids), replace=True)
        chunk = np.concatenate([per[g] for g in pick])
        means[b] = chunk.mean()
    return float(values.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def score_models(evaluated: Sequence[Mapping[str, Any]], models: Sequence[str] = MODELS) -> dict[str, Any]:
    """Out-of-sample scores for each model against the random walk, on the same evaluable rows.

    - ``r2_oos``: 1 - SSE / SST with SST around the mean of the test-row labels (negative means worse than the mean).
    - ``mse_gain_vs_rw``: mean of (squared error of the random walk - squared error of the model) per row, with a
      bootstrap CI over matches. Positive and CI above 0 means the model beats "no change".
    - ``hit_large``: share of large moves where the predicted sign equals the label sign (labels of exactly 0 are
      excluded), with a Wilson 95% CI. ``fade_hit_large``: share where the label moves against the 6-minute move.
    """
    n = len(evaluated)
    if n == 0:
        return {"rows": 0, "matches": 0, "models": {}}
    y = np.array([r["y"] for r in evaluated])
    groups = np.array([r["match_id"] for r in evaluated])
    large = np.array([r["large"] for r in evaluated])
    d6 = np.array([r["d6"] for r in evaluated])
    sst = float(((y - y.mean()) ** 2).sum())
    rw_err = y ** 2
    summary: dict[str, Any] = {"rows": n, "matches": int(len(np.unique(groups))),
                               "large_rows": int(large.sum()), "models": {}}
    fade_ok = (np.sign(y) == -np.sign(d6)) & (y != 0) & large
    fade_n = int(((y != 0) & large).sum())
    lo, hi = wilson_interval(int(fade_ok.sum()), fade_n)
    summary["fade_hit_large"] = {"hits": int(fade_ok.sum()), "n": fade_n,
                                 "rate": (fade_ok.sum() / fade_n) if fade_n else None, "lo": lo, "hi": hi}
    for name in models:
        pred = np.array([r["pred"][name] for r in evaluated])
        err = (y - pred) ** 2
        gain = rw_err - err
        mean, glo, ghi = _bootstrap_mean(gain, groups)
        valid = (np.sign(pred) != 0) & (y != 0) & large
        hits = int((np.sign(pred) == np.sign(y))[valid].sum())
        n_hit = int(valid.sum())
        hlo, hhi = wilson_interval(hits, n_hit)
        summary["models"][name] = {
            "r2_oos": float(1.0 - err.sum() / sst) if sst > 0 else None,
            "mse_gain_vs_rw": {"point": mean, "lo": glo, "hi": ghi},
            "hit_large": {"hits": hits, "n": n_hit, "rate": (hits / n_hit) if n_hit else None,
                          "lo": hlo, "hi": hhi},
        }
    return summary


def detected(summary: Mapping[str, Any], name: str) -> bool:
    """True when a predictor beats 'no change' with a CI above zero and fades large moves significantly (CI > 0.5)."""
    block = summary.get("models", {}).get(name)
    if not block:
        return False
    gain = block["mse_gain_vs_rw"]
    hit = block["hit_large"]
    return bool(gain["lo"] is not None and gain["lo"] > 0 and hit["lo"] is not None and hit["lo"] > 0.5)


# --------------------------------------------------------------------------- policy
def fade_predictions(rows: Sequence[Mapping[str, Any]], fraction: float = FADE_FRACTION) -> list[dict[str, Any]]:
    """S63 probability-source rows for the 'fade large moves' policy.

    After a large 6-minute move, the forecast is the current price moved back by ``fraction`` of that move; otherwise
    the forecast is the current price itself, which the policy does not bet on. Used with
    ``training.evaluate.build_eval_rows`` and ``simulate_policy``; never with live prices.
    """
    out = []
    for r in rows:
        p = r["p_now"]
        if large_move(r):
            p = p - fraction * r["features"]["d6"]
        p = min(1.0, max(0.0, p))
        out.append({"match_id": r["match_id"], "snapshot": r["snapshot_id"], "p_home": p, "p_sd": 0.0})
    return out


def bet_summary(bets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count, mean CLV and mean simulated P&L per bet, with match-clustered bootstrap CIs."""
    if not bets:
        return {"bets": 0, "matches": 0, "clv_mean": None, "clv_ci": [None, None], "clv_n": 0,
                "pnl_total": 0.0, "pnl_mean": None, "pnl_ci": [None, None]}
    groups = np.array([b["match_id"] for b in bets])
    pnl = np.array([b["pnl"] for b in bets], dtype=float)
    clv_rows = [b for b in bets if b.get("clv") is not None]
    clv = np.array([b["clv"] for b in clv_rows], dtype=float)
    clv_g = np.array([b["match_id"] for b in clv_rows])
    clv_mean, clv_lo, clv_hi = _bootstrap_mean(clv, clv_g) if clv.size else (None, None, None)
    pnl_mean, pnl_lo, pnl_hi = _bootstrap_mean(pnl, groups)
    return {"bets": len(bets), "matches": int(len(np.unique(groups))),
            "clv_mean": clv_mean, "clv_ci": [clv_lo, clv_hi], "clv_n": int(clv.size),
            "pnl_total": float(pnl.sum()), "pnl_mean": pnl_mean, "pnl_ci": [pnl_lo, pnl_hi]}


def fade_policy_summary(dataset: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                   fraction: float = FADE_FRACTION) -> dict[str, Any]:
    """Run the S63 policy simulation for the fade rule on the in-play rows of the dataset."""
    from training.evaluate import build_eval_rows, simulate_policy

    snapshots_by_id = {s["snapshot_id"]: s for s in dataset["snapshots"]}
    preds = fade_predictions(rows, fraction)
    eval_rows = [r for r in build_eval_rows(dataset, preds) if r["arena"] == "inplay"]
    bets = simulate_policy(eval_rows, snapshots_by_id)
    return {"rule": f"fade {fraction:g} x d6 when |d6| >= {LARGE_MOVE_P}", "rows": len(eval_rows),
            "settled_matches": len({r["match_id"] for r in eval_rows}), **bet_summary(bets)}

