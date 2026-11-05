"""S107 part B: train and evaluate the chase model (``training/chase_model.py``). Reads Cricsheet only, no database.

Reads every T20 match in ``data/raw/all`` (one JSON per match), builds the chase rows with career figures as of each
match's date, runs a season walk-forward (fit on seasons before Y, test on Y) for the ``core``, ``players`` and
``empirical`` benchmarks, then fits the final models on every row. Writes::

    models/chase/<YYYYMMDD>.joblib       the final models, the career record and the empirical table
    models/chase/current.json            the active model (unless --no-activate)
    reports/chase/<YYYYMMDD>/summary.md  walk-forward log loss, tail calibration, sample and exclusions
    reports/chase/<YYYYMMDD>/summary.json

Usage::

    python scripts/train_chase_model.py                 # full run (reads about 14,700 files; several minutes)
    python scripts/train_chase_model.py --limit 2000    # first 2,000 files by name (a quick check only)
    python scripts/train_chase_model.py --no-activate   # write the model but leave current.json alone

Subsampling: see the module docstring of ``training/chase_model.py`` (every state of the last 60 legal balls and one
state per over before that).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import sklearn  # noqa: E402

from shared.elo import family_for_match_type  # noqa: E402
from shared.metrics import cluster_bootstrap  # noqa: E402
from training import chase_model as cm  # noqa: E402

DATA_DIR = ROOT / "data" / "raw" / "all"
MODEL_DIR = ROOT / "models" / "chase"
REPORT_DIR = ROOT / "reports" / "chase"
MIN_TEST_MATCHES = 300  # a season is a walk-forward test year only with this many chases
MIN_TRAIN_MATCHES = 1000


def write_current(models_dir: Path, model_file: str, stamp: str) -> None:
    """Point ``current.json`` at the model (the trader loads the file it names)."""
    pointer = {"model": model_file, "version": cm.VERSION, "date": stamp}
    (models_dir / "current.json").write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")


def read_facts(data_dir: Path, limit: Optional[int] = None) -> tuple[list[cm.MatchFacts], Counter]:
    """Facts of every T20 match file, sorted by file name. Unreadable files are counted and skipped."""
    stats: Counter = Counter()
    facts: list[cm.MatchFacts] = []
    paths = sorted(data_dir.glob("*.json"))
    if limit:
        paths = paths[:limit]
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stats["unreadable_file"] += 1
            continue
        info = raw.get("info") or {}
        if family_for_match_type(info.get("match_type")) != "t20":
            stats["not_t20"] += 1
            continue
        raw["match_id"] = path.stem
        found = cm.summarise_match(raw)
        if found is None:
            stats["no_date"] += 1
            continue
        stats["t20_matches"] += 1
        if found.chase is None:
            stats[f"no_chase:{found.excluded}"] += 1
        else:
            stats["chases"] += 1
        facts.append(found)
    return facts, stats


def _ll_vec(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def walk_forward(arrays: dict[str, np.ndarray], log=print) -> dict[str, Any]:
    """Season walk-forward: for each test season Y, the models fitted on the rows of seasons before Y predict Y."""
    seasons = arrays["season"].astype(int)
    match = arrays["match"].astype(int)
    matched = cm.matched_rows(arrays)
    y_all = arrays["y"]
    out: dict[str, list[np.ndarray]] = {k: [] for k in ("season", "match", "y", "p_core", "p_players", "p_emp", "matched")}
    per_season = []
    for year in sorted(set(seasons.tolist())):
        train = seasons < year
        test = seasons == year
        n_test = int(len(np.unique(match[test])))
        n_train = int(len(np.unique(match[train])))
        if n_test < MIN_TEST_MATCHES or n_train < MIN_TRAIN_MATCHES:
            continue
        tr = {k: v[train] for k, v in arrays.items()}
        te = {k: v[test] for k, v in arrays.items()}
        models = cm.fit_models(tr)
        table = cm.fit_empirical(tr)
        p_core = cm.predict(models[cm.CORE], cm.design_matrix(te, cm.CORE))
        p_pl = cm.predict(models[cm.PLAYERS], cm.design_matrix(te, cm.PLAYERS))
        p_emp = cm.empirical_array(table, te)
        y = y_all[test]
        out["season"].append(seasons[test])
        out["match"].append(match[test])
        out["y"].append(y)
        out["p_core"].append(p_core)
        out["p_players"].append(p_pl)
        out["p_emp"].append(p_emp)
        out["matched"].append(matched[test])
        per_season.append({
            "season": year, "test_matches": n_test, "train_matches": n_train, "rows": int(test.sum()),
            "ll_core": cm.log_loss(p_core, y), "ll_players": cm.log_loss(p_pl, y), "ll_empirical": cm.log_loss(p_emp, y),
            "ll_players_matched": cm.log_loss(p_pl[matched[test]], y[matched[test]]),
            "ll_core_matched": cm.log_loss(p_core[matched[test]], y[matched[test]]),
        })
        log(f"season {year}: test chases {n_test}, train chases {n_train}, rows {int(test.sum())}")
    if not per_season:
        raise SystemExit("no walk-forward season: not enough chases")
    pooled = {k: np.concatenate(v) for k, v in out.items()}
    return {"pooled": pooled, "per_season": per_season}


def summarise_walk_forward(wf: dict[str, Any]) -> dict[str, Any]:
    pooled = wf["pooled"]
    y = pooled["y"]
    m = pooled["matched"].astype(bool)
    p_core, p_pl, p_emp = pooled["p_core"], pooled["p_players"], pooled["p_emp"]
    # Per-chase clustered comparison of the players and core models on the matched rows (match = cluster).
    d = _ll_vec(p_pl, y) - _ll_vec(p_core, y)
    per_match: dict[int, list[float]] = {}
    for mid, val, flag in zip(pooled["match"].astype(int), d, m):
        if flag:
            per_match.setdefault(int(mid), []).append(float(val))
    means = [float(np.mean(v)) for v in per_match.values()]
    diff_ci = cluster_bootstrap(means, list(per_match.keys()), statistic="mean", n_boot=2000) if means else {}
    tails = {
        "core": cm.calibration_table(p_core, y),
        "players_matched": cm.calibration_table(p_pl[m], y[m]),
        "empirical": cm.calibration_table(p_emp, y),
        "empirical_matched": cm.calibration_table(p_emp[m], y[m]),
    }
    low = lambda p: (p < 0.10)  # noqa: E731
    high = lambda p: (p > 0.90)  # noqa: E731
    tail_sets = {}
    for name, p, yy in (("core", p_core, y), ("players_matched", p_pl[m], y[m]), ("empirical", p_emp, y)):
        for label, sel in (("P under 10%", low(p)), ("P over 90%", high(p))):
            n = int(sel.sum())
            tail_sets[f"{name}|{label}"] = {
                "rows": n,
                "mean_predicted": float(np.mean(p[sel])) if n else math.nan,
                "observed": float(np.mean(yy[sel])) if n else math.nan,
            }
    return {
        "test_rows": int(len(y)),
        "test_chases": int(len(np.unique(pooled["match"]))),
        "matched_rows": int(m.sum()),
        "log_loss": {
            "core": cm.log_loss(p_core, y),
            "players_all_rows": cm.log_loss(p_pl, y),
            "players_matched": cm.log_loss(p_pl[m], y[m]),
            "core_matched": cm.log_loss(p_core[m], y[m]),
            "empirical": cm.log_loss(p_emp, y),
            "empirical_matched": cm.log_loss(p_emp[m], y[m]),
        },
        "players_minus_core_matched": diff_ci,
        "calibration": tails,
        "tail": tail_sets,
    }


def fit_final(arrays: dict[str, np.ndarray], career: dict[str, list[int]], stamp: str, n_matches: int,
              facts_stats: Counter, data_dir: Path) -> dict[str, Any]:
    models = cm.fit_models(arrays)
    table = cm.fit_empirical(arrays)
    keys: dict[str, str] = {}
    for name in career:
        k = cm.name_key(name)
        if not k:
            continue
        keys[k] = "" if (k in keys and keys[k] != name) else name
    payload = {
        cm.CORE: models[cm.CORE],
        cm.PLAYERS: models[cm.PLAYERS],
        "empirical": table,
        "career": {name: [int(v[0]), int(v[1]), int(v[2])] for name, v in career.items()},
        "career_keys": keys,
        "version": cm.VERSION,
        "date": stamp,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "core_features": list(cm.CORE_FEATURES),
        "players_features": list(cm.PLAYER_FEATURES),
        "n_matches": n_matches,
        "n_rows": int(len(arrays["y"])),
        "source": str(data_dir),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "subsampling": f"all states of the last {cm.TAIL_BALLS} legal balls; one state per over before that",
    }
    return payload


def render_markdown(report: dict[str, Any]) -> str:
    wf = report["walk_forward"]
    ll = wf["log_loss"]
    lines = [
        f"# S107 chase model, {report['date']}",
        "",
        f"Model `{report['model_file']}` (version {report['version']}), scikit-learn {report['sklearn']}.",
        "P(the chasing side wins) from one state before each legal ball of a 20-over T20 second innings. Core: runs "
        "needed, balls left, wickets in hand, required rate. Players: core plus both batters' career strike rate, "
        "average and balls (as of the match's date) and their runs and balls in this innings. Empirical: the base-rate "
        "table of the same state (runs bucket, balls bucket, wickets).",
        "",
        "## Sample",
        "",
        f"- Cricsheet T20 matches read: {report['stats'].get('t20_matches', 0)}; chases used: {report['stats'].get('chases', 0)}.",
        f"- Excluded, by reason: {report['exclusions']}.",
        f"- Training rows (final model): {report['n_rows']} from {report['n_matches']} chases. Subsampling: "
        f"{report['subsampling']}.",
        f"- Walk-forward test: {wf['test_chases']} chases, {wf['test_rows']} rows, {wf['matched_rows']} rows with both "
        "batters matched to a career record.",
        "",
        "## Walk-forward log loss (season Y predicted by a model fitted on seasons before Y)",
        "",
        "| model | rows | log loss |",
        "|---|---:|---:|",
        f"| core | all | {ll['core']:.4f} |",
        f"| players | matched | {ll['players_matched']:.4f} |",
        f"| core | matched (same rows as players) | {ll['core_matched']:.4f} |",
        f"| players | all rows (unmatched batters fall back to NaN) | {ll['players_all_rows']:.4f} |",
        f"| empirical base rate | all | {ll['empirical']:.4f} |",
        f"| empirical base rate | matched | {ll['empirical_matched']:.4f} |",
        "",
    ]
    diff = wf.get("players_minus_core_matched") or {}
    if diff:
        lines += [
            f"Players minus core on the matched rows, per match, clustered: {diff.get('estimate', math.nan):+.5f} "
            f"[{diff.get('ci_lower', math.nan):+.5f}, {diff.get('ci_upper', math.nan):+.5f}] over "
            f"{diff.get('n_clusters', 0)} chases. Negative means players is better.",
            "",
        ]
    lines += ["## Per season", "", "| season | test chases | train chases | core | players | empirical | players matched | core matched |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in wf["per_season"]:
        lines.append(
            f"| {row['season']} | {row['test_matches']} | {row['train_matches']} | {row['ll_core']:.4f} | "
            f"{row['ll_players']:.4f} | {row['ll_empirical']:.4f} | {row['ll_players_matched']:.4f} | "
            f"{row['ll_core_matched']:.4f} |"
        )
    lines += ["", "## Tail calibration (walk-forward, pooled)", "",
              "States the model puts under 10% or over 90%: rows, mean predicted, observed win rate.", "",
              "| model | P under 10%: rows | mean predicted | observed | P over 90%: rows | mean predicted | observed |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name in ("core", "players_matched", "empirical"):
        lo = wf["tail"][f"{name}|P under 10%"]
        hi = wf["tail"][f"{name}|P over 90%"]
        lines.append(
            f"| {name} | {lo['rows']} | {lo['mean_predicted']:.4f} | {lo['observed']:.4f} | {hi['rows']} | "
            f"{hi['mean_predicted']:.4f} | {hi['observed']:.4f} |"
        )
    lines += ["", "Reliability by bin (walk-forward, pooled):", ""]
    for name in ("core", "players_matched", "empirical"):
        lines += [f"{name}:", "", "| bin | rows | mean predicted | observed |", "|---|---:|---:|---:|"]
        for b in wf["calibration"][name]:
            if b["rows"]:
                lines.append(f"| {b['bin']} | {b['rows']} | {b['mean_predicted']:.4f} | {b['observed']:.4f} |")
        lines.append("")
    act = report["activation"]
    lines += ["## Activation", "", f"- {act}", "",
              "## Reading", "",
              "- A probability is only as good as the states it is judged on: the tail bets look at states under 10% and "
              "need the calibration above to hold. The walk-forward is season-clustered; no result of the test season "
              "enters its own features.",
              "- The players model is used live only when both batters resolve to a career record (name match).", ""]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate the S107 chase model (Cricsheet only).")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--date", default=None, help="YYYYMMDD for the model and report (default: today, UTC)")
    parser.add_argument("--models-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--reports-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="only the first N files by name (a quick check)")
    parser.add_argument("--no-activate", action="store_true", help="do not change current.json")
    parser.add_argument("--skip-walk-forward", action="store_true", help="fit the final model only")
    args = parser.parse_args(argv)

    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"reading {args.data_dir} ...", flush=True)
    facts, stats = read_facts(args.data_dir, args.limit)
    print(f"read: {dict(stats)}", flush=True)
    table, career, match_ids = cm.build_rows(facts)
    arrays = table.arrays()
    n_matches = len(match_ids)
    if n_matches == 0:
        raise SystemExit("no chase rows: check the data directory")
    print(f"rows {len(table)} from {n_matches} chases; career names {len(career)}", flush=True)

    exclusions = {k.split(":", 1)[1]: v for k, v in stats.items() if k.startswith("no_chase:")}
    wf_report: dict[str, Any] = {}
    if not args.skip_walk_forward:
        wf = walk_forward(arrays, log=lambda s: print(s, flush=True))
        wf_report = summarise_walk_forward(wf)
        wf_report["per_season"] = wf["per_season"]

    payload = fit_final(arrays, career, stamp, n_matches, stats, args.data_dir)
    args.models_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.models_dir / f"{stamp}.joblib"
    joblib.dump(payload, model_path)
    model_rel = model_path.relative_to(ROOT).as_posix() if model_path.is_relative_to(ROOT) else str(model_path)

    if args.no_activate:
        activation = "current.json not changed (--no-activate)"
    else:
        activation = f"activated `{model_rel}` in current.json"

    report = {
        "date": stamp,
        "version": cm.VERSION,
        "model_file": model_rel,
        "sklearn": sklearn.__version__,
        "stats": dict(stats),
        "exclusions": exclusions,
        "n_rows": int(len(table)),
        "n_matches": n_matches,
        "subsampling": payload["subsampling"],
        "walk_forward": wf_report,
        "activation": activation,
    }
    out_dir = args.reports_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    if wf_report:
        (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if not args.no_activate:
        write_current(args.models_dir, model_rel, stamp)
    print(f"model {model_rel}; {activation}", flush=True)
    if wf_report:
        ll = wf_report["log_loss"]
        print(f"walk-forward log loss: core {ll['core']:.4f}, players matched {ll['players_matched']:.4f}, "
              f"core matched {ll['core_matched']:.4f}, empirical {ll['empirical']:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
