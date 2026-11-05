"""S97 market-dynamics model: features, labels, dataset rows and leave-one-match-out evaluation (pure, no I/O).

The model trades the odds' own movement. Its target is the band market probability 10 minutes after a snapshot minus
the probability at the snapshot (``y10``). Features are computed by :class:`MatchHistory` from the price rows and the
live score rows of one match, using only rows at or before the snapshot time ``t`` and only the last ``LOOKBACK_S``
of prices. The live trader (``strategy.market_dynamics``, via ``virtual_trading.paper_trader``) loads the same window,
so the same function gives the same numbers in the dataset, in a live cycle and in a replay.

Labels use prices after ``t`` (``p60``, ``p600``) and are never inputs. A snapshot is dropped when its label window is
not covered by prices (a gap of more than ``GAP_LIMIT_S`` between recorded prices, or the recording ends first), when
it has no fresh price at ``t``, or when it is before the off.

Price rows are dicts with ``time`` (aware datetime), ``back_home``, ``lay_home``, ``back_away``, ``lay_away`` and
optionally ``source``. Score rows are ``match_context`` dicts (``time``, ``innings``, ``overs`` in cricket notation,
``wickets``, ``score``, ``req_run_rate``, ``balls_remaining``, ``is_live``).
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from shared.metrics import MIN_CLUSTERS_FOR_CI, cluster_bootstrap
from strategy.probability import legal_balls
from training.dataset import effective_start
from training.market import TRADING_DEVIG, band_bounds, market_prob_home

VERSION = "s97-1"
SNAPSHOT_EVERY_S = 30  # a snapshot every 30 s from the off
HORIZON_S = 600  # the model predicts the change over 10 minutes
FILL_S = 60  # the bet is taken 60 s after the decision (S71 fill delay)
LOOKBACK_S = 1200  # features read only the last 20 minutes of prices (the live trader loads this window)
GAP_LIMIT_S = 180  # a gap of more than 3 minutes between recorded prices is a stoppage: no fresh price, no label
SCORE_FRESH_S = 300  # the live score is used only when its latest row is at most 5 minutes old (as the trader)
LABEL_STALE_S = 900  # labels use the S58 as-of rule (a field is stale after 15 minutes)
PRICE_FIELDS = ("back_home", "lay_home", "back_away", "lay_away")
MARKET_FEATURES = (
    "p_now", "d1m", "d3m", "d5m", "d10m", "vol10m", "spread_home", "spread_away", "dist_ref", "since_change_s",
)
SCORE_FEATURES = (
    "no_scoreboard", "innings", "overs", "wickets", "score", "req_run_rate", "balls_remaining",
    "runs_1o", "wkts_1o", "runs_3o", "wkts_3o", "runs_5o", "wkts_5o",
)
FEATURES = MARKET_FEATURES + SCORE_FEATURES
THETA = 0.02  # primary simulated trade threshold: |predicted change| above 2 probability points
THETAS = (0.01, 0.02, 0.03, 0.05)
COOLDOWN_S = 120  # simulated trades: at least 2 minutes apart per match (the trader's cooldown)
MAX_TRADES_PER_MATCH = 30  # simulated trades: the ticket budget
N_BOOT = 2000
SEED = 20261104

_NAN = float("nan")


# --------------------------------------------------------------------------- helpers
def _dt(value: Any) -> Optional[datetime]:
    """An aware UTC datetime from a datetime or ISO string (naive values are UTC); None when unusable."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _num(value: Any) -> Optional[float]:
    """A finite float, or None (NULL, NaN and infinities are missing)."""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _spread(back: Optional[float], lay: Optional[float]) -> Optional[float]:
    """Back-lay spread of one runner in probability points: 1/back - 1/lay (positive for a normal book)."""
    if back is None or lay is None or back <= 1.0 or lay <= 1.0:
        return None
    return 1.0 / back - 1.0 / lay


def _band(flat: Mapping[str, Optional[float]]) -> Optional[float]:
    """Band market probability of P(home) from a flat book, or None when a price is missing or the book contradicts
    itself (the same rule as the trader, ``training.market.market_prob_home`` with ``TRADING_DEVIG``)."""
    if any(flat.get(f) is None for f in PRICE_FIELDS):
        return None
    return market_prob_home({f: flat[f] for f in PRICE_FIELDS}, TRADING_DEVIG)


def _nan(value: Optional[float]) -> float:
    return _NAN if value is None else float(value)


class _Score:
    """One score row, the fields the features read."""

    __slots__ = ("time", "innings", "legal", "wickets", "score", "req", "balls_left", "live")

    def __init__(self, time: datetime, row: Mapping[str, Any]) -> None:
        self.time = time
        self.innings = int(row.get("innings") or 0)
        self.legal = legal_balls(row.get("overs"))
        self.wickets = _num(row.get("wickets"))
        self.score = _num(row.get("score"))
        self.req = _num(row.get("req_run_rate"))
        self.balls_left = _num(row.get("balls_remaining"))
        self.live = row.get("is_live") is not False


# --------------------------------------------------------------------------- one match's history
class MatchHistory:
    """The price and score history of one match, read as of any time ``t`` without looking past it.

    ``features(t)`` and ``label(t)`` are the two entry points. ``off`` is the start the snapshots are measured from
    (the actual off, else the scheduled start); it sets the reference price of ``dist_ref``.
    """

    def __init__(
        self,
        price_rows: Iterable[Mapping[str, Any]],
        score_rows: Iterable[Mapping[str, Any]] = (),
        off: Any = None,
    ) -> None:
        points = []
        for row in price_rows:
            ts = _dt(row.get("time"))
            if ts is None:
                continue
            points.append((ts, tuple(_num(row.get(f)) for f in PRICE_FIELDS)))
        points.sort(key=lambda p: p[0])
        self._times = [p[0] for p in points]
        self._vals = [p[1] for p in points]
        scores = []
        for row in score_rows:
            ts = _dt(row.get("time"))
            if ts is not None:
                scores.append(_Score(ts, row))
        scores.sort(key=lambda s: s.time)
        self._score_times = [s.time for s in scores]
        self._scores = scores
        self.off = _dt(off)

    # ------------------------------------------------------------------ as-of reads
    def last_time(self) -> Optional[datetime]:
        return self._times[-1] if self._times else None

    def fresh(self, t: datetime) -> bool:
        """True when a price row was recorded at or before ``t`` and no more than ``GAP_LIMIT_S`` before it."""
        hi = bisect.bisect_right(self._times, t)
        return hi > 0 and t - self._times[hi - 1] <= timedelta(seconds=GAP_LIMIT_S)

    def flat_book(self, t: datetime) -> dict[str, Optional[float]]:
        """The flat book at ``t`` read from the live window (the prices the features use)."""
        return self._flat(t, t - timedelta(seconds=LOOKBACK_S))

    def _flat(self, t: datetime, lo: datetime) -> dict[str, Optional[float]]:
        """Per field, the last non-missing value at or before ``t`` among the rows at or after ``lo``."""
        out: dict[str, Optional[float]] = {f: None for f in PRICE_FIELDS}
        hi = bisect.bisect_right(self._times, t)
        start = bisect.bisect_left(self._times, lo)
        for j in range(hi - 1, start - 1, -1):
            vals = self._vals[j]
            for k, f in enumerate(PRICE_FIELDS):
                if out[f] is None and vals[k] is not None:
                    out[f] = vals[k]
            if all(v is not None for v in out.values()):
                break
        return out

    # ------------------------------------------------------------------ features at t (inputs only)
    def features(self, t: datetime) -> Optional[dict[str, float]]:
        """Every feature in ``FEATURES`` at time ``t``, or None when there is no fresh band price at ``t``.

        Uses rows at or before ``t`` only, and prices no older than ``LOOKBACK_S`` before ``t``.
        """
        lo = t - timedelta(seconds=LOOKBACK_S)
        hi = bisect.bisect_right(self._times, t)
        if not self.fresh(t):
            return None  # no price in the last 3 minutes: the market is not quoting
        flat = self._flat(t, lo)
        p_now = _band(flat)
        if p_now is None:
            return None
        out: dict[str, float] = {"p_now": p_now}
        for name, seconds in (("d1m", 60), ("d3m", 180), ("d5m", 300), ("d10m", 600)):
            past = _band(self._flat(t - timedelta(seconds=seconds), lo))
            out[name] = _nan(None if past is None else p_now - past)
        grid = []
        for j in range(21):  # t-10min, t-9.5min, ..., t: the 30 s path over the last 10 minutes
            value = _band(self._flat(t - timedelta(seconds=HORIZON_S - SNAPSHOT_EVERY_S * j), lo))
            if value is not None:
                grid.append(value)
        steps = np.diff(np.asarray(grid, dtype=float)) if len(grid) >= 3 else np.asarray([])
        out["vol10m"] = float(np.std(steps)) if steps.size >= 2 else _NAN
        out["spread_home"] = _nan(_spread(flat["back_home"], flat["lay_home"]))
        out["spread_away"] = _nan(_spread(flat["back_away"], flat["lay_away"]))
        ref_lo = lo if self.off is None else max(self.off, lo)  # the off, or the window start when it is earlier
        j_ref = bisect.bisect_left(self._times, ref_lo)
        ref_p = None
        if j_ref < hi:  # the first recorded row at or after the reference time, and no later than t
            ref_p = _band(dict(zip(PRICE_FIELDS, self._vals[j_ref])))
        out["dist_ref"] = _nan(None if ref_p is None else p_now - ref_p)
        last_change = None
        prev = None
        for j in range(bisect.bisect_left(self._times, lo), hi):
            if prev is None or self._vals[j] != prev:
                last_change = self._times[j]
            prev = self._vals[j]
        out["since_change_s"] = _NAN if last_change is None else (t - last_change).total_seconds()
        out.update(self._score_features(t))
        return {f: out[f] for f in FEATURES}

    def _score_features(self, t: datetime) -> dict[str, float]:
        """Scoreboard features from the latest score row at or before ``t`` (fresh and live), else the no-scoreboard
        flag with every score feature missing."""
        none = {f: _NAN for f in SCORE_FEATURES}
        none["no_scoreboard"] = 1.0
        hi = bisect.bisect_right(self._score_times, t)
        if hi == 0:
            return none
        latest = self._scores[hi - 1]
        if t - latest.time > timedelta(seconds=SCORE_FRESH_S) or not latest.live or latest.score is None:
            return none
        out = {
            "no_scoreboard": 0.0,
            "innings": float(latest.innings),
            "overs": latest.legal / 6.0,
            "wickets": _nan(latest.wickets),
            "score": float(latest.score),
            "req_run_rate": _nan(latest.req),
            "balls_remaining": _nan(latest.balls_left),
        }
        for k in (1, 3, 5):
            base_score, base_wkts = 0.0, 0.0  # the innings started at 0/0: no earlier row in it means so
            for j in range(hi - 2, -1, -1):
                row = self._scores[j]
                if row.innings != latest.innings:
                    break
                if row.legal <= latest.legal - 6 * k:
                    base_score = row.score if row.score is not None else _NAN
                    base_wkts = row.wickets if row.wickets is not None else _NAN
                    break
            out[f"runs_{k}o"] = latest.score - base_score
            out[f"wkts_{k}o"] = _NAN if latest.wickets is None else latest.wickets - base_wkts
        return out

    # ------------------------------------------------------------------ labels after t (targets only)
    def label(self, t: datetime) -> Optional[dict[str, float]]:
        """The band price 60 s and 10 minutes after ``t``, and the home half-spread at the fill (60 s), or None when
        the label window is not covered: a gap over ``GAP_LIMIT_S`` between recorded prices from ``t`` to ``t+10min``
        (a stoppage, a suspension or the end of the recording)."""
        t60 = t + timedelta(seconds=FILL_S)
        t600 = t + timedelta(seconds=HORIZON_S)
        i_lo = bisect.bisect_right(self._times, t)
        i_hi = bisect.bisect_right(self._times, t600)
        points = [t] + self._times[i_lo:i_hi] + [t600]
        gap = max(b - a for a, b in zip(points, points[1:]))
        if gap > timedelta(seconds=GAP_LIMIT_S):
            return None
        flat60 = self._flat(t60, t60 - timedelta(seconds=LABEL_STALE_S))
        flat600 = self._flat(t600, t600 - timedelta(seconds=LABEL_STALE_S))
        p60, p600 = _band(flat60), _band(flat600)
        spread = _spread(flat60["back_home"], flat60["lay_home"])
        if p60 is None or p600 is None or spread is None:
            return None
        return {"p60": p60, "p600": p600, "spread_fill": spread}


# --------------------------------------------------------------------------- dataset rows
def build_match_rows(
    match_row: Mapping[str, Any],
    price_rows: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]] = (),
    *,
    every_s: int = SNAPSHOT_EVERY_S,
) -> tuple[list[dict[str, Any]], Counter]:
    """Dataset rows for one match: a snapshot every ``every_s`` seconds from the off, with features and labels.

    Returns the rows and a counter of the snapshots dropped, by reason. A row keeps the match id, the snapshot time,
    the minutes since the off, every feature, the labels (``p60``, ``p600``), the targets ``y10`` (= p600 - p_now) and
    ``y60`` (= p60 - p_now), ``fill_to_ref`` (= p600 - p60) and ``half_spread_fill`` (half the home spread at the fill).
    """
    stats: Counter = Counter()
    start, _basis = effective_start(match_row)
    if start is None:
        stats["no_start"] += 1
        return [], stats
    hist = MatchHistory(price_rows, score_rows, off=start)
    last = hist.last_time()
    if last is None:
        stats["no_prices"] += 1
        return [], stats
    match_id = str(match_row["match_id"])
    rows: list[dict[str, Any]] = []
    t = start
    while t <= last:
        feats = hist.features(t)
        if feats is None:
            stats["no_fresh_price" if not hist.fresh(t) else "no_band"] += 1
        else:
            label = hist.label(t)
            if label is None:
                stats["label_window_gap"] += 1
            else:
                p_now = feats["p_now"]
                spread_now = feats["spread_home"]
                rows.append({
                    "match_id": match_id,
                    "t": t.isoformat(),
                    "in_play_min": (t - start).total_seconds() / 60.0,
                    **feats,
                    "p60": label["p60"],
                    "p600": label["p600"],
                    "y10": label["p600"] - p_now,
                    "y60": label["p60"] - p_now,
                    "fill_to_ref": label["p600"] - label["p60"],
                    "half_spread_fill": label["spread_fill"] / 2.0,
                    "half_spread_now": spread_now / 2.0 if math.isfinite(spread_now) else _NAN,
                })
                stats["rows"] += 1
        t = t + timedelta(seconds=every_s)
    return rows, stats


def build_dataset(
    registry_rows: Iterable[Mapping[str, Any]],
    prices_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
    scores_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows for every real, canonical match with recorded prices (SRL and twin event ids are skipped).

    Returns ``(rows, stats)``; ``stats`` counts matches by status and snapshots dropped by reason.
    """
    rows: list[dict[str, Any]] = []
    stats: Counter = Counter()
    for reg in registry_rows:
        mid = str(reg["match_id"])
        if reg.get("is_simulated"):
            stats["skipped_simulated"] += 1
            continue
        if reg.get("canonical_match_id") is not None:
            stats["skipped_twin"] += 1
            continue
        prices = prices_by_match.get(mid) or []
        if not prices:
            stats["skipped_no_prices"] += 1
            continue
        scores = scores_by_match.get(mid) or []
        mrows, mstats = build_match_rows(reg, prices, scores)
        for key, value in mstats.items():
            stats[f"snapshots_{key}"] += value
        if mrows:
            stats["matches"] += 1
            stats["matches_with_score_rows"] += 1 if scores else 0
            rows.extend(mrows)
        else:
            stats["matches_without_rows"] += 1
    rows.sort(key=lambda r: (r["match_id"], r["t"]))
    return rows, dict(sorted(stats.items()))


# --------------------------------------------------------------------------- model
def make_estimator() -> HistGradientBoostingRegressor:
    """The small gradient-boosted regressor (scikit-learn 1.6.1, pinned). Few matches: shallow, regularised. The loss is
    absolute error, the metric the story names (MAE); squared error lets a few ghost-price jumps dominate the fit."""
    return HistGradientBoostingRegressor(
        loss="absolute_error", max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40,
        l2_regularization=1.0, random_state=0,
    )


def design(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """Feature matrix in ``FEATURES`` order; missing values are NaN (the regressor handles them)."""
    if not rows:
        return np.empty((0, len(FEATURES)), dtype=float)
    return np.asarray([[_nan(_num(r.get(f))) for f in FEATURES] for r in rows], dtype=float)


def targets(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.asarray([float(r["y10"]) for r in rows], dtype=float)


def fit(rows: Sequence[Mapping[str, Any]]) -> HistGradientBoostingRegressor:
    if not rows:
        raise ValueError("cannot fit the market-dynamics model on zero rows")
    return make_estimator().fit(design(rows), targets(rows))


def leave_one_match_out(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """Out-of-fold predictions of ``y10``: each match is predicted by a model fitted on every other match."""
    X = design(rows)
    y = targets(rows)
    groups = np.asarray([str(r["match_id"]) for r in rows])
    pred = np.full(len(rows), np.nan)
    for mid in sorted(set(groups.tolist())):
        held = groups == mid
        if not np.any(~held):
            continue  # one match only: nothing to train on, no prediction
        est = make_estimator().fit(X[~held], y[~held])
        pred[held] = est.predict(X[held])
    return pred


def fit_leave_one_match_out_models(rows: Sequence[Mapping[str, Any]]) -> dict[str, HistGradientBoostingRegressor]:
    """One model per match, fitted without that match (the replay's per-match models)."""
    X = design(rows)
    y = targets(rows)
    groups = np.asarray([str(r["match_id"]) for r in rows])
    out: dict[str, HistGradientBoostingRegressor] = {}
    for mid in sorted(set(groups.tolist())):
        held = groups == mid
        if np.any(~held):
            out[mid] = make_estimator().fit(X[~held], y[~held])
    return out


# --------------------------------------------------------------------------- simulated trades and CLV
def simulate_trades(
    rows: Sequence[Mapping[str, Any]],
    pred: Sequence[float],
    theta: float,
    *,
    seed: int = SEED,
) -> list[dict[str, Any]]:
    """Trades the model would have taken, one decision per snapshot where ``|pred| > theta``.

    Side: BACK home when the predicted change is positive, LAY home when negative. A trade is taken at most every
    ``COOLDOWN_S`` per match and at most ``MAX_TRADES_PER_MATCH`` per match. Simulated CLV (probability points) is the
    realised move in the bet's direction from the fill (60 s after the decision) to 10 minutes after the decision,
    minus half the home spread at the fill. The placebo takes a seeded random side at the same times, with the same
    cost: the excess CLV over it is the part the model adds.
    """
    order = sorted(range(len(rows)), key=lambda i: (str(rows[i]["match_id"]), str(rows[i]["t"])))
    coins = np.random.default_rng(seed).choice(np.array([-1.0, 1.0]), size=len(rows))
    trades: list[dict[str, Any]] = []
    last_t: dict[str, datetime] = {}
    count: Counter = Counter()
    for i in order:
        row = rows[i]
        p = float(pred[i])
        if not math.isfinite(p) or abs(p) <= theta:
            continue
        mid = str(row["match_id"])
        t = _dt(row["t"])
        if count[mid] >= MAX_TRADES_PER_MATCH:
            continue
        if mid in last_t and (t - last_t[mid]).total_seconds() < COOLDOWN_S:
            continue
        direction = 1.0 if p > 0 else -1.0
        cost = float(row["half_spread_fill"])
        move = float(row["fill_to_ref"])
        trades.append({
            "match_id": mid,
            "t": row["t"],
            "side": "BACK" if direction > 0 else "LAY",
            "pred": p,
            "clv": direction * move - cost,
            "placebo_clv": float(coins[i]) * move - cost,
            "has_score": float(row.get("no_scoreboard", 1.0)) < 0.5,
        })
        last_t[mid] = t
        count[mid] += 1
    return trades


def _clustered(values: Sequence[float], clusters: Sequence[str]) -> dict[str, Any]:
    boot = cluster_bootstrap(values, clusters, statistic="mean", n_boot=N_BOOT, seed=SEED)
    return {
        "estimate": boot["estimate"],
        "ci_lower": boot["ci_lower"],
        "ci_upper": boot["ci_upper"],
        "matches": boot["n_clusters"],
        "trades": boot["n_items"],
        "too_few_matches": boot["n_clusters"] < MIN_CLUSTERS_FOR_CI,
    }


def trade_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Match-clustered simulated CLV of the model and of the placebo, their excess, and the trade counts."""
    matches = [str(t["match_id"]) for t in trades]
    model = _clustered([float(t["clv"]) for t in trades], matches)
    placebo = _clustered([float(t["placebo_clv"]) for t in trades], matches)
    per_match: dict[str, list[float]] = {}
    per_match_placebo: dict[str, list[float]] = {}
    for t in trades:
        per_match.setdefault(str(t["match_id"]), []).append(float(t["clv"]))
        per_match_placebo.setdefault(str(t["match_id"]), []).append(float(t["placebo_clv"]))
    common = sorted(per_match)
    diffs = [float(np.mean(per_match[m])) - float(np.mean(per_match_placebo[m])) for m in common]
    excess_boot = cluster_bootstrap(diffs, common, statistic="mean", n_boot=N_BOOT, seed=SEED) if common else {}
    excess = {
        "estimate": excess_boot.get("estimate", _NAN),
        "ci_lower": excess_boot.get("ci_lower", _NAN),
        "ci_upper": excess_boot.get("ci_upper", _NAN),
        "matches": excess_boot.get("n_clusters", 0),
        "too_few_matches": excess_boot.get("n_clusters", 0) < MIN_CLUSTERS_FOR_CI,
    }
    return {
        "trades": len(trades),
        "matches_with_trades": len(per_match),
        "trades_per_match": (len(trades) / len(per_match)) if per_match else _NAN,
        "back_share": (sum(1 for t in trades if t["side"] == "BACK") / len(trades)) if trades else _NAN,
        "clv": model,
        "placebo_clv": placebo,
        "excess_over_placebo": excess,
    }


def evaluate(rows: Sequence[Mapping[str, Any]], pred: Sequence[float]) -> dict[str, Any]:
    """Leave-one-match-out evaluation: accuracy against the zero baseline, direction accuracy on moves larger than the
    half-spread, and the simulated CLV at each threshold, overall and split by scoreboard."""
    y = targets(rows)
    p = np.asarray(pred, dtype=float)
    ok = np.isfinite(p)
    n = int(ok.sum())
    if n == 0:
        raise ValueError("no out-of-fold predictions to evaluate")
    yy, pp = y[ok], p[ok]
    sub = [r for r, k in zip(rows, ok) if k]
    half = np.asarray([_nan(_num(r.get("half_spread_now"))) for r in sub], dtype=float)
    half = np.where(np.isfinite(half), half, 0.0)
    big = np.abs(yy) > half
    direction_ok = np.sign(pp[big]) == np.sign(yy[big])
    out: dict[str, Any] = {
        "rows": n,
        "matches": len({str(r["match_id"]) for r in sub}),
        "mae_model": float(np.mean(np.abs(pp - yy))),
        "mae_zero": float(np.mean(np.abs(yy))),
        "direction_rows_over_spread": int(big.sum()),
        "direction_accuracy_over_spread": float(direction_ok.mean()) if big.any() else _NAN,
        "by_theta": {},
        "by_scoreboard": {},
    }
    out["mae_ratio"] = out["mae_model"] / out["mae_zero"] if out["mae_zero"] > 0 else _NAN
    full_pred = np.where(ok, p, np.nan)
    for theta in THETAS:
        out["by_theta"][f"{theta:.2f}"] = trade_summary(simulate_trades(rows, full_pred, theta))
    has_score = np.asarray([float(r.get("no_scoreboard", 1.0)) < 0.5 for r in rows], dtype=bool)
    for label, mask in (("with_scoreboard", has_score), ("no_scoreboard", ~has_score)):
        idx = [i for i in range(len(rows)) if mask[i]]
        sub_rows = [rows[i] for i in idx]
        sub_pred = [float(full_pred[i]) for i in idx]
        out["by_scoreboard"][label] = {
            "rows": int(np.sum(mask & ok)),
            "matches": len({str(rows[i]["match_id"]) for i in idx if ok[i]}),
            "trades": trade_summary(simulate_trades(sub_rows, sub_pred, THETA)),
        }
    return out


def clv_of_model(rows: Sequence[Mapping[str, Any]], pred: Sequence[float], theta: float = THETA) -> dict[str, Any]:
    """Simulated trade summary of one prediction set on ``rows`` (used for the weekly comparison)."""
    return trade_summary(simulate_trades(rows, pred, theta))


def decide_activation(
    candidate_clv: Optional[float],
    current_clv: Optional[float],
    *,
    current_exists: bool,
    compared_matches: int,
) -> tuple[bool, str]:
    """Whether the candidate replaces the active model (S97 weekly rule).

    No active model: activate. Otherwise activate only when the candidate's leave-one-match-out simulated CLV on the
    matches the active model did not train on is not worse than the active model's on the same matches. A tie counts
    as not worse. No common held-out matches: keep the active model.
    """
    if not current_exists:
        return True, "no active model: the first model is activated"
    if compared_matches <= 0:
        return False, "no held-out matches the active model did not train on: kept the active model"
    cand = 0.0 if candidate_clv is None or not math.isfinite(candidate_clv) else float(candidate_clv)
    cur = 0.0 if current_clv is None or not math.isfinite(current_clv) else float(current_clv)
    if cand >= cur:
        return True, (f"candidate simulated CLV {cand:+.4f} is not worse than the active model's {cur:+.4f} on "
                      f"{compared_matches} held-out matches")
    return False, (f"candidate simulated CLV {cand:+.4f} is worse than the active model's {cur:+.4f} on "
                   f"{compared_matches} held-out matches: kept the active model")


def predict_rows(estimator: Any, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.asarray(estimator.predict(design(rows)), dtype=float) if rows else np.zeros(0)


__all__ = [
    "FEATURES",
    "MARKET_FEATURES",
    "SCORE_FEATURES",
    "THETA",
    "THETAS",
    "VERSION",
    "MatchHistory",
    "build_dataset",
    "build_match_rows",
    "clv_of_model",
    "decide_activation",
    "design",
    "evaluate",
    "fit",
    "fit_leave_one_match_out_models",
    "leave_one_match_out",
    "predict_rows",
    "simulate_trades",
    "trade_summary",
]
