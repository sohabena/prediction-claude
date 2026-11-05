"""
Audit a synthetic corpus for a free, exploitable price edge (S13).

Question: from public tick fields alone (score, wickets, overs, required run rate, current
price, last 3 price changes, and the cumulative state-unexplained price move so far), can
the NEXT-tick mid-price change be predicted out of sample? In an efficient market it cannot.
A mean-reverting price (the old Ornstein-Uhlenbeck noise) is predictable.

Prices are measured as logit of the mid implied probability, ``(1/back_home + 1/lay_home)/2``.

Two out-of-sample ridge regressions (split by match) are reported:

* ``raw``: target = next-tick change of the logit mid price.
* ``excess``: target = that change minus the change explained by the public state moving
  (a state-delta model fitted out-of-fold by match). This removes the large, unpredictable
  state jumps (wickets, boundaries) that otherwise swamp a small reversion signal. It uses the
  next tick's public state only to *remove* state-driven change, never as a predictor. Predictors
  include the cumulative unexplained move so far in the match (mean-reversion detector).

Exit code 1 if the ``excess`` test has R^2 > 0.02 or sign hit rate > 0.55, 
(informational: slope/t-stat of mean reversion). The audit detects strong, tradable predictability;
it is NOT sensitive to a very weak reversion (e.g. default OU theta=0.1 on a small corpus), so the
P&L null test remains the final arbiter. The ``raw`` figures are
printed for information only: the synthetic fair value is not a calibrated probability, so its
state-driven drift is mildly predictable even with no noise at all (mode ``none``); that drift is
a property of the fair-value layer, not of the noise process this audit polices.

Usage: python scripts/audit_synthetic_edge.py --corpus data/specialists/corpus_ipl.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

R2_THRESHOLD = 0.02
HIT_RATE_THRESHOLD = 0.55
N_LAGS = 3


def _logit(p: float) -> float:
    p = min(0.995, max(0.005, p))
    return math.log(p / (1.0 - p))


def _mid_logit(t: Dict[str, Any]) -> Optional[float]:
    bh, lh = t.get("back_home"), t.get("lay_home")
    if not bh or bh <= 1.0:
        return None
    if lh and lh > 1.0:
        return _logit((1.0 / bh + 1.0 / lh) / 2.0)
    return _logit(1.0 / bh)


def _state(t: Dict[str, Any]) -> List[float]:
    score = float(t.get("score", 0) or 0)
    wk = float(t.get("wickets", 0) or 0)
    overs = float(t.get("overs", 0) or 0)
    rrr = float(t.get("req_run_rate", 0) or 0)
    balls = float(t.get("balls_remaining", 0) or 0)
    inn = float(t.get("innings", 1) or 1)
    runs_needed = rrr * balls / 6.0
    return [score, wk, overs, rrr, balls, inn, runs_needed, 10.0 - wk, score / max(1.0, overs)]


def _match_arrays(match: Dict[str, Any]) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    S, P = [], []
    for t in match.get("ticks", []):
        m = _mid_logit(t)
        if m is None:
            continue
        S.append(_state(t))
        P.append(m)
    if len(P) < N_LAGS + 3:
        return None
    return np.asarray(S, dtype=float), np.asarray(P, dtype=float)


def _excess_changes(arrs: Sequence[Tuple[np.ndarray, np.ndarray]], folds: int = 4, seed: int = 0) -> List[np.ndarray]:
    """Per match, e[i] = price change i-1 -> i NOT explained by the public state moving.

    A gradient-boosted model of the change given (state_{i-1}, state_i - state_{i-1}) is fitted
    out-of-fold (by match), and e is the residual. The state delta is used only to remove
    state-driven change; the audit's predictors below never see a future tick.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor

    rows = []
    for S, P in arrs:
        rows.append((np.c_[S[:-1], S[1:] - S[:-1]], np.diff(P)))
    rng = np.random.default_rng(seed)
    fold_of = rng.integers(0, folds, size=len(arrs))
    out: List[np.ndarray] = [np.zeros(0)] * len(arrs)
    for f in range(folds):
        tr = [r for r, k in zip(rows, fold_of) if k != f]
        if not tr:
            continue
        mdl = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.1, random_state=0)
        mdl.fit(np.vstack([r[0] for r in tr]), np.concatenate([r[1] for r in tr]))
        for i, (r, k) in enumerate(zip(rows, fold_of)):
            if k == f:
                out[i] = r[1] - mdl.predict(r[0])
    return out


def _build(arrs, excess) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Features, raw next-tick change, state-adjusted next-tick change (one row per tick with a successor)."""
    X, y_raw, y_exc = [], [], []
    for (S, P), e in zip(arrs, excess):
        n = len(P)
        c = np.concatenate([[0.0], np.cumsum(e)])  # c[i]: cumulative unexplained move up to tick i (causal)
        for i in range(n - 1):
            lags = [P[i - k] - P[i - k - 1] if i - k - 1 >= 0 else 0.0 for k in range(N_LAGS)]
            elags = [e[i - 1 - k] if i - 1 - k >= 0 else 0.0 for k in range(N_LAGS)]
            X.append([*S[i], P[i], *lags, *elags, c[i], c[i] - c[: i + 1].mean()])
            y_raw.append(P[i + 1] - P[i])
            y_exc.append(e[i])
    return np.asarray(X, dtype=float), np.asarray(y_raw, dtype=float), np.asarray(y_exc, dtype=float)


def _reversion_t(Xte: np.ndarray, yte_exc: np.ndarray) -> Tuple[float, float]:
    """Informational only. OLS slope and t-stat of next unexplained move on the (centred) cumulative unexplained move.

    The ridge R^2 of a genuinely mean-reverting price can be tiny (R^2 ~ theta^2 * var(x) / var(change)),
    but the slope is estimated very precisely; a strongly negative t-stat is the mean-reversion signature.
    """
    c = Xte[:, -1]
    c = c - c.mean()
    denom = float(np.sum(c * c))
    if denom <= 0 or len(c) < 10:
        return 0.0, 0.0
    slope = float(np.sum(c * (yte_exc - yte_exc.mean())) / denom)
    resid = (yte_exc - yte_exc.mean()) - slope * c
    se = math.sqrt(float(np.sum(resid ** 2)) / max(1, len(c) - 2) / denom)
    return slope, (slope / se if se > 0 else 0.0)


def _score(Xtr, ytr, Xte, yte) -> Tuple[float, float]:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler().fit(Xtr)
    reg = Ridge(alpha=10.0).fit(sc.transform(Xtr), ytr)
    pred = reg.predict(sc.transform(Xte))
    ss_res = float(np.sum((yte - pred) ** 2))
    ss_tot = float(np.sum((yte - yte.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    nz = (yte != 0) & (pred != 0)
    hit = float(np.mean(np.sign(pred[nz]) == np.sign(yte[nz]))) if nz.any() else 0.5
    return r2, hit


def audit_matches(matches: Sequence[Dict[str, Any]], seed: int = 0) -> Tuple[bool, Dict[str, Any]]:
    """Returns (passed, report). Ridge fitted on ~70% of matches, scored on the held-out ~30%."""
    arrs = [a for a in (_match_arrays(m) for m in matches) if a is not None]
    if len(arrs) < 10:
        raise ValueError("not enough matches to audit (need >= 10)")
    excess = _excess_changes(arrs, seed=seed)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(arrs))
    n_test = max(2, int(0.3 * len(arrs)))
    te_idx, tr_idx = order[:n_test], order[n_test:]
    Xtr, ytr_raw, ytr_exc = _build([arrs[i] for i in tr_idx], [excess[i] for i in tr_idx])
    Xte, yte_raw, yte_exc = _build([arrs[i] for i in te_idx], [excess[i] for i in te_idx])

    r2_raw, hit_raw = _score(Xtr, ytr_raw, Xte, yte_raw)
    r2_exc, hit_exc = _score(Xtr, ytr_exc, Xte, yte_exc)
    slope, t_stat = _reversion_t(Xte, yte_exc)
    report = {
        "n_matches": len(arrs),
        "n_test_ticks": int(len(yte_raw)),
        "raw": {"r2": r2_raw, "hit_rate": hit_raw},
        "excess": {"r2": r2_exc, "hit_rate": hit_exc},
        "reversion": {"slope": slope, "t_stat": t_stat},
        # Gate on the state-adjusted test; "raw" is informational (see module docstring).
        "r2": r2_exc,
        "hit_rate": hit_exc,
    }
    passed = r2_exc <= R2_THRESHOLD and hit_exc <= HIT_RATE_THRESHOLD
    return passed, report


def _load_corpus(path: Path) -> List[Dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a synthetic corpus for a predictable next-tick price change.")
    parser.add_argument("--corpus", required=True, help="Path to corpus_<key>.json.gz (or .json)")
    args = parser.parse_args(argv)

    matches = _load_corpus(Path(args.corpus))
    passed, rep = audit_matches(matches)
    tags = sorted({str(m.get("market_model", "unknown")) for m in matches})
    print(f"Synthetic-edge audit: {args.corpus} ({rep['n_matches']} matches, market_model={tags})")
    for name in ("raw", "excess"):
        print(f"  {name:<7} out-of-sample R2={rep[name]['r2']:+.4f}  sign hit rate={rep[name]['hit_rate']:.3f}")
    print(f"  mean-reversion slope={rep['reversion']['slope']:+.4f} t={rep['reversion']['t_stat']:+.1f}")
    print("PASS" if passed else
          f"FAIL: next-tick change is predictable (R2>{R2_THRESHOLD} or hit rate>{HIT_RATE_THRESHOLD})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
