"""Train and walk-forward the session-runs model (S48) on Cricsheet.

    python scripts/train_session_model.py --family t20 --out-dir <dir> [--timing season] [--stride 3]

Walk-forward rule, as in S38: for each season Y, fit on seasons < Y-1, calibrate the dispersion on Y-1, test on Y.
``--timing season`` (default) builds the venue par and XI strength as of 1 January of the match's season, so no
result from the predicted season can reach the features. ``--timing match`` uses the information dated before each
match.

Reports per season and pooled: discrete CRPS, pinball loss at 10/50/90 %, the "current run rate x balls left"
baseline at 50 %, and reliability of ``p_at_least`` at three lines per window (the training 25/50/75 percentiles
of the remaining runs). No hyper-parameter is tuned on the holdout seasons.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.train_fair_value import (  # noqa: E402
    FAMILY_DIRS,
    iter_cricsheet,
    season_cutoff,
    season_of,
)
from shared.logging import setup_logging  # noqa: E402
from shared.venue_priors import VenuePriors  # noqa: E402
from strategy.ratings import CricketRatings  # noqa: E402
from strategy.session_model import (  # noqa: E402
    FEATURE_NAMES,
    MAX_OVERS,
    WINDOW_OVERS,
    SessionRunsModel,
    build_rows,
    concat_rows,
    crps_discrete,
    design_rows,
    pinball,
    trace_innings,
)

logger = setup_logging("train_session_model")

DEFAULT_OUT = Path("models/session_model")
DEFAULT_DATA_ROOT = Path("data")
TAUS = (0.1, 0.5, 0.9)
CAVEATS = [
    "Session lines are not priced against any market here; conventions are confirmed by S47 before use.",
    "Venue par and XI strength are as of the cutoff (season start by default); no match sees its own result.",
    "Rain-shortened innings are used only for windows the innings actually reached.",
]


def build_dataset(
    data_root: Path, family: str, *, timing: str = "season", stride: int = 3, stats: Optional[Counter] = None
) -> Dict[str, Any]:
    """Stacked training rows for one family, plus per-season match counts. Streams Cricsheet twice."""
    if family not in WINDOW_OVERS:
        raise ValueError(f"unknown family {family!r}")
    if timing not in ("season", "match"):
        raise ValueError(f"unknown timing {timing!r}")
    dirs = [data_root / d for d in FAMILY_DIRS[family]]
    stats = stats if stats is not None else Counter()
    ratings = CricketRatings(lambda: iter_cricsheet(dirs, family, stats))
    priors = VenuePriors.build(iter_cricsheet(dirs, family, stats))
    parts: List[Dict[str, np.ndarray]] = []
    matches_per_season: Counter = Counter()
    for raw in iter_cricsheet(dirs, family, stats):
        row = ratings.rows.get(raw["match_id"])
        if row is None:
            stats["no_row"] += 1
            continue
        season = season_of(row.date)
        cutoff = row.date if timing == "match" else season_cutoff(season)
        pv = priors.get(row.venue, family, row.gender, cutoff, city=row.city or None)
        par = pv["par_run_rate"]
        if par is None:
            stats["no_par"] += 1
            continue
        innings = [i for i in (raw.get("innings") or []) if not i.get("super_over")][:2]
        used = False
        for inn in innings:
            bat = str(inn.get("team") or "").strip()
            if bat not in row.teams:
                stats["unknown_batting_team"] += 1
                continue
            bowl = row.teams[1] if bat == row.teams[0] else row.teams[0]
            xi_bat = ratings.xi_strength(family, row.gender, list(row.players.get(bat, ())), cutoff)
            xi_bowl = ratings.xi_strength(family, row.gender, list(row.players.get(bowl, ())), cutoff)
            block = build_rows(
                trace_innings(inn),
                family=family,
                match_overs=row.overs or MAX_OVERS[family],
                par_rr=float(par),
                xi_diff=float(xi_bat - xi_bowl),
                female=row.gender == "female",
                stride=stride,
                season=season,
            )
            if len(block["target"]):
                parts.append(block)
                used = True
        if used:
            matches_per_season[season] += 1
    stats["matches_used"] = sum(matches_per_season.values())
    rows = concat_rows(parts)
    return {"rows": rows, "matches_per_season": dict(matches_per_season), "ratings_rejected": dict(ratings.rejected)}


def _baseline_point(rows: Dict[str, np.ndarray], mask: np.ndarray, family: str) -> np.ndarray:
    """Current run rate times balls left (par run rate before the first ball)."""
    bd = rows["balls_done"][mask].astype(float)
    runs = rows["runs"][mask]
    par = rows["par_rr"][mask]
    wb = rows["window_balls"][mask].astype(float)
    crr = np.where(bd > 0, runs / np.maximum(bd / 6.0, 1e-9), par)
    return crr * (wb - bd) / 6.0


def _reliability(p: np.ndarray, obs: np.ndarray, bins: int = 10) -> Dict[str, Any]:
    if len(p) == 0:
        return {"ece": None, "deciles": []}
    idx = np.minimum((p * bins).astype(int), bins - 1)
    deciles: List[Dict[str, float]] = []
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        gap = float(abs(p[m].mean() - obs[m].mean()))
        ece += float(m.mean()) * gap
        deciles.append({"bin": b, "n": int(m.sum()), "mean_pred": float(p[m].mean()),
                        "obs_rate": float(obs[m].mean())})
    return {"ece": float(ece), "deciles": deciles}


def _line_thresholds(rows: Dict[str, np.ndarray], train_mask: np.ndarray, windows: Sequence[int]) -> Dict[int, List[int]]:
    """Integer thresholds per window at the training 25/50/75 percentiles of remaining runs (YES if runs >= T)."""
    out: Dict[int, List[int]] = {}
    for k in windows:
        m = train_mask & (rows["window_overs"] == k)
        tgt = rows["target"][m]
        if len(tgt) == 0:
            continue
        out[k] = [int(np.floor(np.percentile(tgt, q))) + 1 for q in (25, 50, 75)]
    return out


def walk_forward(
    rows: Dict[str, np.ndarray],
    family: str,
    matches_per_season: Dict[int, int],
    *,
    min_train_matches: int = 200,
    min_test_matches: int = 30,
) -> Dict[str, Any]:
    seasons = sorted(matches_per_season)
    windows = WINDOW_OVERS[family]
    X_all, off_all = design_rows(rows, family)
    y_all = rows["target"]
    s_all = rows["season"]
    folds: List[Dict[str, Any]] = []
    pooled = {"crps": [], "n": [], "pb": {t: [] for t in TAUS}, "base50": [], "p_pred": [], "obs": []}
    for year in seasons:
        n_train_m = sum(c for s, c in matches_per_season.items() if s < year - 1)
        n_test_m = matches_per_season.get(year, 0)
        n_calib_m = matches_per_season.get(year - 1, 0)
        if n_train_m < min_train_matches or n_calib_m == 0 or n_test_m < min_test_matches:
            continue
        tr = s_all < year - 1
        ca = s_all == year - 1
        te = s_all == year
        model = SessionRunsModel(family).fit(X_all[tr], off_all[tr], y_all[tr])
        model.trained_through = str(year - 2)
        model.calibrate(X_all[ca], off_all[ca], y_all[ca], season=str(year - 1))
        model.feature_names = list(FEATURE_NAMES)
        n_p, p_p = model.nb_params(X_all[te], off_all[te])
        y_te = y_all[te]
        crps = crps_discrete(n_p, p_p, y_te)
        pb = {}
        for tau in TAUS:
            q = stats.nbinom.ppf(tau, n_p, p_p)
            pb[tau] = pinball(y_te, q, tau)
            pooled["pb"][tau].append((pb[tau], len(y_te)))
        base = _baseline_point(rows, te, family)
        base50 = pinball(y_te, base, 0.5)
        thr = _line_thresholds(rows, tr, windows)
        p_lines: List[np.ndarray] = []
        o_lines: List[np.ndarray] = []
        for k, ts in thr.items():
            mk = rows["window_overs"][te] == k
            for t in ts:
                p_lines.append(stats.nbinom.sf(t - 1, n_p[mk], p_p[mk]))
                o_lines.append((y_te[mk] >= t).astype(float))
        p_arr = np.concatenate(p_lines) if p_lines else np.empty(0)
        o_arr = np.concatenate(o_lines) if o_lines else np.empty(0)
        pooled["crps"].append((crps, len(y_te)))
        pooled["base50"].append((base50, len(y_te)))
        pooled["p_pred"].append(p_arr)
        pooled["obs"].append(o_arr)
        folds.append({
            "season": year,
            "trained_through": model.trained_through,
            "calibration_season": year - 1,
            "n_train_matches": n_train_m,
            "n_calib_matches": n_calib_m,
            "n_test_matches": n_test_m,
            "n_test_rows": int(len(y_te)),
            "dispersion_r": float(np.exp(model.theta)),
            "crps": crps,
            "pinball": {f"q{int(t * 100)}": pb[t] for t in TAUS},
            "baseline_pinball_q50": base50,
            "model_beats_baseline_q50": bool(pb[0.5] < base50),
            "p_at_least_reliability": _reliability(p_arr, o_arr),
        })

    def wmean(items: List[tuple]) -> Optional[float]:
        if not items:
            return None
        vals = np.asarray([v for v, _ in items])
        w = np.asarray([n for _, n in items], dtype=float)
        return float(np.sum(vals * w) / np.sum(w))

    result: Dict[str, Any] = {"folds": folds, "min_train_matches": min_train_matches,
                              "min_test_matches": min_test_matches}
    if folds:
        p_all = np.concatenate(pooled["p_pred"]) if pooled["p_pred"] else np.empty(0)
        o_all = np.concatenate(pooled["obs"]) if pooled["obs"] else np.empty(0)
        result["pooled"] = {
            "crps": wmean(pooled["crps"]),
            "pinball": {f"q{int(t * 100)}": wmean(pooled["pb"][t]) for t in TAUS},
            "baseline_pinball_q50": wmean(pooled["base50"]),
            "seasons_model_beats_baseline_q50": sum(1 for f in folds if f["model_beats_baseline_q50"]),
            "seasons": len(folds),
            "p_at_least_reliability": _reliability(p_all, o_all),
        }
    return result


def fit_final(rows: Dict[str, np.ndarray], family: str) -> SessionRunsModel:
    """Deployment model: fit on every season but the last, calibrate on the last season."""
    X, off = design_rows(rows, family)
    seasons = rows["season"]
    last = int(seasons.max())
    tr = seasons < last
    if not tr.any():
        raise ValueError("need at least two seasons for the final fit")
    model = SessionRunsModel(family).fit(X[tr], off[tr], rows["target"][tr])
    model.trained_through = str(last - 1)
    model.calibrate(X[seasons == last], off[seasons == last], rows["target"][seasons == last], season=str(last))
    model.feature_names = list(FEATURE_NAMES)
    return model


def run(args: argparse.Namespace) -> Dict[str, Any]:
    stats: Counter = Counter()
    data = build_dataset(Path(args.data_root), args.family, timing=args.timing, stride=args.stride, stats=stats)
    logger.info("session_rows_built", family=args.family, rows=int(len(data["rows"]["target"])),
                matches=int(stats.get("matches_used", 0)))
    rows = data["rows"]
    result = walk_forward(rows, args.family, data["matches_per_season"],
                          min_train_matches=args.min_train, min_test_matches=args.min_test)
    model = fit_final(rows, args.family)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"session_{args.family}.json"
    model_path.write_text(json.dumps({
        "family": args.family,
        "feature_names": model.feature_names,
        "beta": model.beta.tolist(),
        "theta": model.theta,
        "trained_through": model.trained_through,
        "calibrated_on": model.calibrated_on,
        "window_overs": list(WINDOW_OVERS[args.family]),
        "yes_rule": "runs >= ceil(line)",
        "price_scale": 100.0,
    }, indent=2), encoding="utf-8")
    report: Dict[str, Any] = {
        "family": args.family,
        "timing": args.timing,
        "stride": args.stride,
        "data_root": str(args.data_root),
        "counts": dict(stats),
        "matches_per_season": data["matches_per_season"],
        "ratings_rejected": data["ratings_rejected"],
        "rows_total": int(len(rows["target"])),
        "caveats": CAVEATS,
        "final_model": str(model_path),
        "final_dispersion_r": float(np.exp(model.theta)),
        **result,
    }
    report_path = out_dir / f"{args.family}_session_walkforward.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def _summary_lines(report: Dict[str, Any]) -> List[str]:
    lines = [f"{report['family']} session model timing={report['timing']} rows={report['rows_total']}"]
    for f in report.get("folds", []):
        pb = f["pinball"]
        lines.append(
            f"  season {f['season']}: test_matches={f['n_test_matches']} rows={f['n_test_rows']} "
            f"crps={f['crps']:.4f} pb10={pb['q10']:.4f} pb50={pb['q50']:.4f} pb90={pb['q90']:.4f} "
            f"baseline_pb50={f['baseline_pinball_q50']:.4f} beats={f['model_beats_baseline_q50']}"
        )
    if "pooled" in report:
        p = report["pooled"]
        lines.append(
            f"  pooled: crps={p['crps']:.4f} pb50={p['pinball']['q50']:.4f} "
            f"baseline_pb50={p['baseline_pinball_q50']:.4f} "
            f"seasons_beating_baseline={p['seasons_model_beats_baseline_q50']}/{p['seasons']} "
            f"p_reliability_ece={p['p_at_least_reliability']['ece']}"
        )
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", choices=sorted(WINDOW_OVERS), required=True)
    ap.add_argument("--timing", choices=("season", "match"), default="season")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--min-train", type=int, default=200)
    ap.add_argument("--min-test", type=int, default=30)
    ap.add_argument("--stride", type=int, default=3)
    args = ap.parse_args()
    report = run(args)
    print("\n".join(_summary_lines(report)))
    print(f"report: {report['report_path']}")


if __name__ == "__main__":
    main()
