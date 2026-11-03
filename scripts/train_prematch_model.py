"""S60: pre-match strength model v3, season walk-forward on Cricsheet (labels only, no prices).

    python scripts/train_prematch_model.py --family t20 [--gender male|female|all] [--out-dir models/prematch]

Walk-forward rule: for each season Y with enough data, fit the base learners on seasons < Y-2, calibrate on the
two seasons Y-2 and Y-1, test on season Y. The calibrated candidate (pure Elo + Platt, logistic + Platt, or a stack
of Elo, logistic and GBM logits + Platt) is chosen on the calibration window only, by season-split cross-fit log
loss, and recorded per fold. Features are as of each match's own date, so no result from the match or any later
match reaches its row.

Reports pooled and per-fold log loss, Brier and ECE for v3 (the selected candidate), a pure-Elo baseline (home term
included), the logistic and GBM base models alone (uncalibrated), and each Platt candidate, overall and per gender
and tier, plus the per-fold winners.

Outputs: ``<out-dir>/<family>_v3.joblib`` (fitted through the latest season, base models on seasons < latest,
calibration on the latest season) and ``<family>_v3_walkforward.json``. A gender filter adds ``_<gender>`` to both.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.train_fair_value import FAMILY_DIRS, brier, ece, iter_cricsheet, log_loss  # noqa: E402
from strategy.prematch_model import (  # noqa: E402
    CORE_FEATURES,
    FEATURE_NAMES,
    VERSION,
    MatchFeatures,
    PrematchModel,
    build_feature_rows,
    elo_probability,
    feature_matrix,
)
from strategy.ratings import CricketRatings  # noqa: E402

DEFAULT_OUT = Path("models/prematch")
DEFAULT_DATA_ROOT = Path("data")
FAMILIES = ("t20", "odi")
MODELS = ("v3", "elo", "logistic", "gbm", "elo_platt", "logistic_platt", "stack_platt")
CANDIDATE_KEYS = {"elo": "elo_platt", "logistic": "logistic_platt", "stack": "stack_platt"}
CAVEATS = [
    "Pre-toss only: toss fields are excluded; lineups are the Cricsheet squad lists (assumed known pre-match).",
    "Labels only. No market prices are used or compared here; market evaluation is S61 and S63.",
    "logistic and gbm are base models without calibration. *_platt are the three candidates, each Platt-calibrated.",
    "v3 is the candidate chosen per fold on the calibration window only (season-split cross-fit log loss).",
    "Calibration window = seasons Y-2 and Y-1; base learners see seasons < Y-2; the test season Y is never used to choose.",
    "Ties, no-results and matches without a winner are excluded from labels and from the walk-forward.",
]


def _metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    return {"log_loss": log_loss(p, y), "brier": brier(p, y), "ece": ece(p, y), "n": int(len(y))}


def _predictions(model: PrematchModel, rows: Sequence[MatchFeatures]) -> Dict[str, np.ndarray]:
    X = feature_matrix(rows)
    parts = model.predict_parts(X)
    cands = model.predict_candidates(X)
    out: Dict[str, np.ndarray] = {
        "v3": model.predict_matrix(X),
        "elo": np.asarray([elo_probability(r.features) for r in rows]),
        "logistic": parts["logistic"],
        "gbm": parts["gbm"],
    }
    for name, key in CANDIDATE_KEYS.items():
        out[key] = cands[name]
    return out


def _pooled(chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Pooled metrics for every model, overall and per gender and tier, from per-fold test chunks."""
    if not chunks:
        return {}
    y = np.concatenate([c["y"] for c in chunks])
    genders = np.concatenate([c["gender"] for c in chunks])
    tiers = np.concatenate([c["tier"] for c in chunks])
    preds = {m: np.concatenate([c[m] for c in chunks]) for m in MODELS}
    out: Dict[str, Any] = {"overall": {m: _metrics(preds[m], y) for m in MODELS}, "by_gender": {}, "by_tier": {}}
    for g in ("male", "female"):
        sel = genders == g
        if sel.any():
            out["by_gender"][g] = {m: _metrics(preds[m][sel], y[sel]) for m in MODELS}
    for t in sorted(set(tiers.tolist())):
        sel = tiers == t
        out["by_tier"][str(int(t))] = {m: _metrics(preds[m][sel], y[sel]) for m in MODELS}
    return out


def walk_forward(rows: Sequence[MatchFeatures], *, seed: int = 0, min_train: int = 200,
                 min_test: int = 30) -> Dict[str, Any]:
    """Season-by-season walk-forward: base learners on seasons < Y-2, calibration on Y-2 and Y-1, test on Y."""
    folds: List[Dict[str, Any]] = []
    chunks: List[Dict[str, Any]] = []
    for year in sorted({r.season for r in rows}):
        train = [r for r in rows if r.season < year - 2]
        calib = [r for r in rows if r.season in (year - 2, year - 1)]
        test = [r for r in rows if r.season == year]
        if len(train) < min_train or len({r.season for r in calib}) < 2 or len(test) < min_test:
            continue
        if {r.match_id for r in test} & {r.match_id for r in train + calib}:
            raise RuntimeError("test season leaked into training")
        model = PrematchModel(seed=seed).fit(train, calib)
        preds = _predictions(model, test)
        y = np.asarray([r.label for r in test], dtype=int)
        fold = {
            "season": year,
            "trained_through": max(r.date for r in train),
            "calibration_seasons": [year - 2, year - 1],
            "selected": model.selected,
            "selection_scores": {k: round(v, 6) for k, v in model.selection_scores.items()},
            "n_train_matches": len(train),
            "n_calib_matches": len(calib),
            "n_test_matches": len(test),
            "tier_mix_test": dict(Counter(int(r.tier) for r in test)),
            "metrics": {m: _metrics(preds[m], y) for m in MODELS},
        }
        folds.append(fold)
        chunks.append({"y": y, "gender": np.asarray([r.gender for r in test]),
                       "tier": np.asarray([r.tier for r in test]), **preds})
    winners = Counter(f["selected"] for f in folds)
    return {"folds": folds, "pooled": _pooled(chunks), "selected_winners": dict(winners),
            "min_train_matches": min_train, "min_test_matches": min_test}


def fit_final(rows: Sequence[MatchFeatures], seed: int = 0) -> PrematchModel:
    """Deployment model: base learners on seasons < latest-1, calibration on the latest two seasons."""
    last = max(r.season for r in rows)
    train = [r for r in rows if r.season < last - 1]
    calib = [r for r in rows if r.season in (last - 1, last)]
    if not train or len({r.season for r in calib}) < 2:
        raise ValueError("need at least three seasons for the final fit")
    model = PrematchModel(seed=seed).fit(train, calib)
    model.trained_through = max(r.date for r in rows)
    return model


def _summary(report: Dict[str, Any]) -> List[str]:
    lines = [f"{report['family']} v3 gender={report['gender']} labelled_matches={report['counts']['labelled']}"]
    for f in report["folds"]:
        m = f["metrics"]
        lines.append(
            f"  season {f['season']}: n_test={f['n_test_matches']} winner={f['selected']}  "
            + "  ".join(f"{k} ll={m[k]['log_loss']:.4f}" for k in ("v3", "elo", "logistic", "gbm"))
        )
    lines.append(f"  selected winners: {report.get('selected_winners', {})}")
    overall = report["pooled"].get("overall", {})
    for k in MODELS:
        if k in overall:
            m = overall[k]
            lines.append(f"  pooled {k:13s}: logloss={m['log_loss']:.4f} brier={m['brier']:.4f} "
                         f"ece={m['ece']:.4f} n={m['n']}")
    return lines


def run(args: argparse.Namespace) -> Dict[str, Any]:
    family = args.family
    stats: Counter = Counter()
    dirs = [Path(args.data_root) / d for d in FAMILY_DIRS[family]]
    ratings = CricketRatings(lambda: iter_cricsheet(dirs, family, stats))
    gender = None if args.gender == "all" else args.gender
    rows = build_feature_rows(ratings, gender=gender)
    stats["labelled"] = len(rows)
    stats["ratings_rejected"] = sum(ratings.rejected.values())
    result = walk_forward(rows, seed=args.seed, min_train=args.min_train, min_test=args.min_test)
    suffix = "" if gender is None else f"_{gender}"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {
        "family": family,
        "version": VERSION,
        "gender": args.gender,
        "data_root": str(args.data_root),
        "counts": dict(stats),
        "ratings_rejected": dict(ratings.rejected),
        "features": list(FEATURE_NAMES),
        "core_features": list(CORE_FEATURES),
        "caveats": CAVEATS,
        **result,
    }
    if rows:
        model = fit_final(rows, seed=args.seed)
        model_file = out_dir / f"{family}_{VERSION}{suffix}.joblib"
        model.save(model_file)
        report["final_model"] = str(model_file)
        report["final_model_trained_through"] = model.trained_through
    report_path = out_dir / f"{family}_{VERSION}{suffix}_walkforward.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", choices=FAMILIES, required=True)
    ap.add_argument("--gender", choices=("male", "female", "all"), default="all")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--min-train", type=int, default=200)
    ap.add_argument("--min-test", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    report = run(args)
    print("\n".join(_summary(report)))
    print(f"report: {report['report_path']}")


if __name__ == "__main__":
    main()
