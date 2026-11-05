"""Train and report the calibrated fair-value model (S14 v1, S38 v2 walk-forward).

Legacy v1 (per specialist, synthetic corpus, unchanged in behaviour):
    python scripts/train_fair_value.py --key t20i_men [--out-dir models/fair_value]

S38 walk-forward on Cricsheet alone (v2 uses pre-match strength; v1 is the state-only baseline):
    python scripts/train_fair_value.py --family t20 --version v2 --out-dir <dir> [--key t20i_men]

Walk-forward rule: for each season Y with enough data, fit on seasons < Y-1, calibrate on season Y-1, test on
season Y. ``--timing season`` (default) builds every pre-match feature as of 1 January of the match's own season,
so no result from the season being predicted (or later) can reach its features. ``--timing match`` uses the
information dated before each match instead.

Note: on synthetic corpora the market price is itself a hand-built model, so "edge" versus the market measures
model disagreement, not real alpha. Validate on recorded odds (S16 / S35) before trusting any edge.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.fair_value import (  # noqa: E402
    FAMILIES,
    V1_WIDTH,
    V2_WIDTH,
    VERSIONS,
    FairValueModel,
    _label,
    family_of,
    model_path,
    tick_features,
)
from shared.elo import family_for_match_type  # noqa: E402
from strategy.ratings import CricketRatings, state_ticks  # noqa: E402

DEFAULT_OUT = Path("models/fair_value")
DEFAULT_DATA_ROOT = Path("data")
MAX_OVERS = {"t20": 20, "odi": 50}
# ``raw/all`` is Cricsheet's all_json.zip (every format and league). Files there are classified by info.match_type,
# and a match id present in both ``raw/all`` and a per-format folder is read once (iter_cricsheet dedupes by stem).
FAMILY_DIRS: Dict[str, Tuple[str, ...]] = {
    "t20": ("raw/all", "raw/ipl", "raw/t20s", "incoming_cricsheet"),
    "odi": ("raw/all", "raw/odis", "incoming_cricsheet"),
    "multiday": ("raw/all", "raw/tests", "incoming_cricsheet"),
}
CAVEATS = [
    "Cricsheet has no true home side: 'home' is a deterministic label (match-id parity); the home-ground term "
    "in the ratings uses venue history only.",
    "Lineups are the Cricsheet player lists, assumed known before the match (a lineup feed is out of scope).",
    "Market comparison is not part of this report; see scripts/eval_fair_value_vs_market.py (S38 rung R1).",
]


# ---------------------------------------------------------------- metrics (legacy names kept)
def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def market_prob_home(tick: Dict[str, Any]) -> float:
    """De-vigged mid probability that home wins (legacy synthetic-corpus helper)."""
    def mid(b: Any, l: Any) -> float:
        b, l = float(b or 0), float(l or 0)
        if b > 1 and l > 1:
            return 1.0 / ((b + l) / 2.0)
        return 1.0 / b if b > 1 else 0.5

    h = mid(tick.get("back_home"), tick.get("lay_home"))
    a = mid(tick.get("back_away"), tick.get("lay_away"))
    return h / (h + a) if h + a > 0 else 0.5


def _metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    return {"brier": brier(p, y), "log_loss": log_loss(p, y), "ece": ece(p, y), "n": int(len(y))}


# ---------------------------------------------------------------- legacy v1 (S14)
def evaluate(model: FairValueModel, holdout: Sequence[List[Dict[str, Any]]], stride: int = 3) -> Dict[str, Any]:
    """Brier / log loss / ECE of the fair model and the de-vigged market on holdout episodes."""
    ticks: List[Dict[str, Any]] = []
    ys: List[int] = []
    for ep in holdout:
        y = _label(ep) if ep else None
        if y is None:
            continue
        for t in ep[::stride]:
            ticks.append(t)
            ys.append(y)
    y_arr = np.asarray(ys)
    p_fair = model.predict_batch(ticks)
    p_mkt = np.asarray([market_prob_home(t) for t in ticks])
    return {"n_ticks": len(ys), "fair": _metrics(p_fair, y_arr), "market_implied": _metrics(p_mkt, y_arr)}


def train(key: str, out_dir: Path = DEFAULT_OUT, corpus: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Fit on the train split, report on the holdout, save model + JSON report (S14 v1, unchanged)."""
    from scripts.train_format_specialists import _code_version, _ids_hash, load_corpus, split_matches

    matches = corpus if corpus is not None else load_corpus(key)
    train_m, _val, hold_m = split_matches(matches, key)
    family = family_of(matches[0].get("match_format"))
    model = FairValueModel(family).fit([m["ticks"] for m in train_m])
    hold_ids = {str(m["match_id"]) for m in hold_m}
    if hold_ids & set(model.train_match_ids):
        raise RuntimeError("holdout leaked into fair-value training")
    report = evaluate(model, [m["ticks"] for m in hold_m])
    report.update(
        key=key, family=family, code_version=_code_version(),
        train_split_hash=_ids_hash(train_m), holdout_split_hash=_ids_hash(hold_m),
        n_train_matches=len(train_m), n_holdout_matches=len(hold_m),
        caveat="synthetic corpus: market price is a hand model; validate on recorded odds (S16)",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save(out_dir / f"{family}.joblib")
    (out_dir / f"{family}_{key}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------- S38: Cricsheet walk-forward
def iter_cricsheet(dirs: Sequence[Path], family: str, stats: Optional[Counter] = None) -> Iterator[Dict[str, Any]]:
    """Yield raw Cricsheet match dicts of one family (``match_id`` = file stem), each file once."""
    seen: set[str] = set()
    for root in dirs:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            if path.stem in seen:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if stats is not None:
                    stats["unreadable_file"] += 1
                continue
            info = raw.get("info") or {}
            if family_for_match_type(info.get("match_type")) != family:
                continue
            seen.add(path.stem)
            raw["match_id"] = path.stem
            yield raw


def season_of(iso_date: str) -> int:
    return int(iso_date[:4])


def season_cutoff(year: int) -> str:
    """Feature cutoff for ``--timing season``: nothing dated in ``year`` or later is visible."""
    return f"{year}-01-01"


def gender_for_key(key: Optional[str]) -> Optional[str]:
    """Specialist key suffix to gender filter (``t20i_men`` -> male). Competition is not filtered."""
    if key and key.endswith("_men"):
        return "male"
    if key and key.endswith("_women"):
        return "female"
    return None


@dataclass
class MatchData:
    match_id: str
    season: int
    date: str
    tier: int
    y: int
    X: np.ndarray  # v2 feature rows (width V2_WIDTH), float32


def build_dataset(
    data_root: Path,
    family: str,
    *,
    key: Optional[str] = None,
    timing: str = "season",
    stride: int = 3,
    stats: Optional[Counter] = None,
) -> Tuple[CricketRatings, List[MatchData]]:
    """Ratings plus one feature block per labelled match, streamed from Cricsheet (no full corpus in memory)."""
    if family not in MAX_OVERS:
        raise ValueError("walk-forward supports t20 and odi; multi-day is out of S38 scope")
    if timing not in ("season", "match"):
        raise ValueError(f"unknown timing {timing!r}")
    dirs = [data_root / d for d in FAMILY_DIRS[family]]
    stats = stats if stats is not None else Counter()
    ratings = CricketRatings(lambda: iter_cricsheet(dirs, family, stats), k_by_family=None)
    gender = gender_for_key(key)
    matches: List[MatchData] = []
    for raw in iter_cricsheet(dirs, family, stats):
        row = ratings.rows.get(raw["match_id"])
        if row is None:
            stats["no_row"] += 1
            continue
        if not row.winner:
            stats["unlabelled"] += 1
            continue
        if gender is not None and row.gender != gender:
            stats["other_gender"] += 1
            continue
        season = season_of(row.date)
        cutoff = row.date if timing == "match" else season_cutoff(season)
        pre = ratings.pre_match(row.match_id, cutoff)
        max_balls = (row.overs or MAX_OVERS[family]) * 6
        ticks = state_ticks(raw, row, pre, max_balls)
        X = np.asarray([tick_features(t, family, "v2") for t in ticks[::stride]], dtype=np.float32)
        matches.append(MatchData(row.match_id, season, row.date, row.tier, int(row.winner == row.home), X))
    stats["labelled_matches"] = len(matches)
    return ratings, matches


def _stack(matches: Sequence[MatchData], width: int) -> Tuple[np.ndarray, np.ndarray]:
    if not matches:
        return np.empty((0, width), dtype=np.float32), np.empty(0, dtype=int)
    X = np.vstack([m.X[:, :width] for m in matches])
    y = np.concatenate([np.full(len(m.X), m.y, dtype=int) for m in matches])
    return X, y


def walk_forward(
    matches: Sequence[MatchData],
    family: str,
    version: str,
    *,
    seed: int = 0,
    min_train_matches: int = 200,
    min_test_matches: int = 30,
    compare_v1: bool = True,
    on_fold: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Season-by-season walk-forward: fit on seasons < Y-1, calibrate on Y-1, test on Y."""
    width = V2_WIDTH if version == "v2" else V1_WIDTH
    folds: List[Dict[str, Any]] = []
    pooled_p: List[np.ndarray] = []
    pooled_y: List[np.ndarray] = []
    pooled_p1: List[np.ndarray] = []
    seasons = sorted({m.season for m in matches})
    for year in seasons:
        train = [m for m in matches if m.season < year - 1]
        calib = [m for m in matches if m.season == year - 1]
        test = [m for m in matches if m.season == year]
        if len(train) < min_train_matches or not calib or len(test) < min_test_matches:
            continue
        if {m.match_id for m in test} & {m.match_id for m in train + calib}:
            raise RuntimeError("test season leaked into training")
        X_tr, y_tr = _stack(train, width)
        X_ca, y_ca = _stack(calib, width)
        X_te, y_te = _stack(test, width)
        model = FairValueModel(family, seed=seed, version=version)
        model.fit_arrays(X_tr, y_tr, X_ca, y_ca)
        model.trained_through = max(m.date for m in train)
        p = model.predict_X(X_te)
        fold: Dict[str, Any] = {
            "season": year,
            "trained_through": model.trained_through,
            "calibration_season": year - 1,
            "n_train_matches": len(train),
            "n_calib_matches": len(calib),
            "n_test_matches": len(test),
            "n_test_rows": int(len(y_te)),
            "model": _metrics(p, y_te),
            "tier_mix_test": dict(Counter(m.tier for m in test)),
        }
        pooled_p.append(p)
        pooled_y.append(y_te)
        if version == "v2" and compare_v1:
            base = FairValueModel(family, seed=seed, version="v1")
            base.fit_arrays(X_tr[:, :V1_WIDTH], y_tr, X_ca[:, :V1_WIDTH], y_ca)
            p1 = base.predict_X(X_te[:, :V1_WIDTH])
            fold["baseline_v1_state_only"] = _metrics(p1, y_te)
            pooled_p1.append(p1)
        folds.append(fold)
        if on_fold is not None:
            on_fold(fold)
    result: Dict[str, Any] = {"folds": folds, "min_train_matches": min_train_matches,
                              "min_test_matches": min_test_matches}
    if pooled_p:
        y_all = np.concatenate(pooled_y)
        result["pooled_model"] = _metrics(np.concatenate(pooled_p), y_all)
        if pooled_p1:
            result["pooled_baseline_v1_state_only"] = _metrics(np.concatenate(pooled_p1), y_all)
    return result


def fit_final(matches: Sequence[MatchData], family: str, version: str, seed: int = 0) -> FairValueModel:
    """Model for deployment: fit on every season but the last, calibrate on the last season."""
    if not matches:
        raise ValueError("no labelled matches")
    width = V2_WIDTH if version == "v2" else V1_WIDTH
    last = max(m.season for m in matches)
    train = [m for m in matches if m.season < last]
    calib = [m for m in matches if m.season == last]
    if not train or not calib:
        raise ValueError("need at least two seasons for the final fit")
    X_tr, y_tr = _stack(train, width)
    X_ca, y_ca = _stack(calib, width)
    model = FairValueModel(family, seed=seed, version=version).fit_arrays(X_tr, y_tr, X_ca, y_ca)
    model.trained_through = max(m.date for m in matches)
    return model


def run_walk_forward(args: argparse.Namespace) -> Dict[str, Any]:
    family = args.family
    stats: Counter = Counter()
    ratings, matches = build_dataset(
        Path(args.data_root), family, key=args.key, timing=args.timing, stride=args.stride, stats=stats
    )
    result = walk_forward(
        matches, family, args.version, seed=args.seed, min_train_matches=args.min_train,
        min_test_matches=args.min_test, compare_v1=not args.no_baseline,
    )
    report: Dict[str, Any] = {
        "family": family,
        "version": args.version,
        "key": args.key or "all",
        "gender_filter": gender_for_key(args.key),
        "timing": args.timing,
        "stride": args.stride,
        "data_root": str(args.data_root),
        "counts": dict(stats),
        "ratings_rejected": dict(ratings.rejected),
        "caveats": CAVEATS,
        **result,
    }
    if args.version == "v2":
        model = fit_final(matches, family, args.version, seed=args.seed)
        out = model_path(family, args.key, "v2", root=Path(args.out_dir))
        model.save(out)
        report["final_model"] = str(out)
        report["final_model_trained_through"] = model.trained_through
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{family}_{args.version}_{args.key or 'all'}_walkforward.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def _summary_lines(report: Dict[str, Any]) -> List[str]:
    lines = [f"{report['family']} {report['version']} key={report['key']} timing={report['timing']}"]
    for f in report["folds"]:
        m = f["model"]
        line = (f"  season {f['season']}: n_test={f['n_test_matches']} matches / {f['n_test_rows']} rows  "
                f"brier={m['brier']:.4f} logloss={m['log_loss']:.4f} ece={m['ece']:.4f}")
        if "baseline_v1_state_only" in f:
            b = f["baseline_v1_state_only"]
            line += f"  | v1 brier={b['brier']:.4f}"
        lines.append(line)
    if "pooled_model" in report:
        m = report["pooled_model"]
        lines.append(f"  pooled: brier={m['brier']:.4f} logloss={m['log_loss']:.4f} ece={m['ece']:.4f} n={m['n']}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None, help="v1: specialist key (required). v2: optional label and gender filter")
    ap.add_argument("--family", choices=FAMILIES, default=None, help="walk-forward on Cricsheet for this family")
    ap.add_argument("--version", choices=VERSIONS, default="v1")
    ap.add_argument("--timing", choices=("season", "match"), default="season")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--min-train", type=int, default=200)
    ap.add_argument("--min-test", type=int, default=30)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-baseline", action="store_true", help="skip the v1 state-only baseline per fold")
    args = ap.parse_args()

    if args.family is not None:
        report = run_walk_forward(args)
        print("\n".join(_summary_lines(report)))
        print(f"report: {report['report_path']}")
        return
    if args.key is None or args.version != "v1":
        ap.error("without --family, pass --key and --version v1 (legacy per-specialist training)")
    print(json.dumps(train(args.key, Path(args.out_dir)), indent=2))


if __name__ == "__main__":
    main()
