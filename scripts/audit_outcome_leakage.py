"""
Outcome-leakage audit for PHOENIX training corpora.

Fits two classifiers predicting ``home_won``:
  1. state-only features (score, wickets, overs, innings, target, required rate, balls left)
  2. state + market odds (log-odds of the home back price)
and reports held-out AUC for both, per match-progress bucket. If odds add more than
``--threshold`` AUC on top of state in any bucket, the odds encode information about the
result that the state does not contain (leakage) and the script exits non-zero.

Usage:
    python scripts/audit_outcome_leakage.py data/specialists/corpus_ipl.json.gz
    python scripts/audit_outcome_leakage.py --synthetic-sample 500
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import os

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np

STATE_FEATURES = [
    "score", "wickets", "overs", "innings", "target", "rrr",
    "balls_remaining", "runs_needed", "wickets_left",
]
N_BUCKETS = 4


def _auc(y: np.ndarray, s: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney); 0.5 when only one class is present."""
    pos = y == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _make_model():
    """Flexible classifier so non-linear state->price maps are not mistaken for leakage."""
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(max_iter=120, max_depth=4, learning_rate=0.08, random_state=0)
    except Exception:  # pragma: no cover - sklearn is installed in this repo
        return None


def _fit_predict(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray) -> np.ndarray:
    model = _make_model()
    if model is not None and len(np.unique(ytr)) > 1:
        model.fit(Xtr, ytr)
        return model.predict_proba(Xte)[:, 1]
    # numpy-only logistic regression fallback
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A = np.c_[np.ones(len(Xtr)), (Xtr - mu) / sd]
    B = np.c_[np.ones(len(Xte)), (Xte - mu) / sd]
    w = np.zeros(A.shape[1])
    for _ in range(300):
        p = 1.0 / (1.0 + np.exp(-A @ w))
        w -= 0.1 * (A.T @ (p - ytr) / len(ytr) + 1e-3 * w)
    return B @ w


def _tick_rows(match: Dict[str, Any]) -> List[Tuple[List[float], float, float]]:
    """Return [(state_features, odds_logit, progress)] for one match."""
    ticks = match.get("ticks", [])
    n = len(ticks)
    rows = []
    for i, t in enumerate(ticks):
        bh = t.get("back_home")
        if not bh or bh <= 1.0:
            continue
        score = float(t.get("score", 0) or 0)
        wk = float(t.get("wickets", 0) or 0)
        rrr = float(t.get("req_run_rate", 0) or 0)
        balls = float(t.get("balls_remaining", 0) or 0)
        target = score + rrr * balls / 6.0 if rrr > 0 else 0.0
        p = min(0.98, max(0.02, 1.0 / bh))
        rows.append((
            [score, wk, float(t.get("overs", 0) or 0), float(t.get("innings", 1) or 1), target, rrr,
             balls, max(0.0, target - score), 10.0 - wk],
            math.log(p / (1.0 - p)),
            (i + 1) / max(1, n),
        ))
    return rows


def audit_matches(matches: Sequence[Dict[str, Any]], threshold: float = 0.03, seed: int = 0) -> Tuple[bool, List[Dict[str, float]]]:
    """Run the audit; returns (passed, per-bucket report)."""
    usable = [m for m in matches if m.get("winner") and m.get("result_type", "win") != "draw"]
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(usable))
    n_test = max(1, int(0.3 * len(usable)))
    test_ids = set(idx[:n_test].tolist())

    data = {"tr": ([], [], [], []), "te": ([], [], [], [])}
    for k, m in enumerate(usable):
        bucket = data["te" if k in test_ids else "tr"]
        y = 1.0 if m["winner"] == m["team_home"] else 0.0
        for state, odds, prog in _tick_rows(m):
            bucket[0].append(state)
            bucket[1].append(odds)
            bucket[2].append(prog)
            bucket[3].append(y)

    Str, Otr, Ptr, Ytr = (np.asarray(a, dtype=float) for a in data["tr"])
    Ste, Ote, Pte, Yte = (np.asarray(a, dtype=float) for a in data["te"])
    if len(Ytr) == 0 or len(Yte) == 0:
        raise ValueError("not enough data to audit")

    p_state = _fit_predict(Str, Ytr, Ste)
    p_both = _fit_predict(np.c_[Str, Otr], Ytr, np.c_[Ste, Ote])

    report: List[Dict[str, float]] = []
    passed = True
    for b in range(N_BUCKETS):
        lo, hi = b / N_BUCKETS, (b + 1) / N_BUCKETS
        mask = (Pte > lo) & (Pte <= hi)
        if mask.sum() < 20:
            continue
        a_state = _auc(Yte[mask], p_state[mask])
        a_both = _auc(Yte[mask], p_both[mask])
        delta = a_both - a_state
        report.append({"bucket_lo": lo, "bucket_hi": hi, "n": int(mask.sum()),
                       "auc_state": a_state, "auc_state_odds": a_both, "delta": delta})
        if delta > threshold:
            passed = False
    return passed, report


def _load_corpus(path: Path) -> List[Dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _synthetic_sample(count: int) -> List[Dict[str, Any]]:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.generate_realistic_historical_corpus import RealisticCorpusGenerator

    sims = RealisticCorpusGenerator(seed=42).generate_corpus(match_count=count)
    return [
        {"match_id": s.match_id, "team_home": s.team_home, "team_away": s.team_away,
         "winner": s.winner, "result_type": "win", "ticks": s.ticks}
        for s in sims
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a corpus for outcome leakage in odds.")
    parser.add_argument("corpus", nargs="?", help="Path to corpus_<key>.json.gz")
    parser.add_argument("--synthetic-sample", type=int, default=0, help="Generate N archetype matches in memory")
    parser.add_argument("--threshold", type=float, default=0.03)
    args = parser.parse_args(argv)

    if args.corpus and Path(args.corpus).exists():
        matches = _load_corpus(Path(args.corpus))
        label = args.corpus
    elif args.synthetic_sample > 0:
        matches = _synthetic_sample(args.synthetic_sample)
        label = f"synthetic sample ({args.synthetic_sample} matches)"
    else:
        parser.error("provide an existing corpus path or --synthetic-sample N")
        return 2

    passed, report = audit_matches(matches, threshold=args.threshold)
    print(f"Outcome-leakage audit: {label} ({len(matches)} matches)")
    for r in report:
        print(f"  progress ({r['bucket_lo']:.2f},{r['bucket_hi']:.2f}] n={r['n']:>6} "
              f"AUC(state)={r['auc_state']:.3f} AUC(state+odds)={r['auc_state_odds']:.3f} delta={r['delta']:+.3f}")
    print("PASS" if passed else f"FAIL: odds add > {args.threshold} AUC over state in at least one bucket")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
