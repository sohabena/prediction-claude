"""S97 train the market-dynamics model: leave-one-match-out evaluation, the final model, activation and the report.

Reads the dataset written by ``scripts/build_market_dynamics_dataset.py`` (no database access). Writes::

    models/market_dynamics/<YYYYMMDD>.joblib     the model (estimator, feature list, training matches)
    models/market_dynamics/current.json          the active model, when activated
    reports/market_dynamics/<YYYYMMDD>/summary.md and summary.json

Usage::

    python scripts/train_market_dynamics.py                          # evaluate, train, activate when there is none
    python scripts/train_market_dynamics.py --activate               # force the new model active
    python scripts/train_market_dynamics.py --activate-if-not-worse  # weekly rule: replace the active model only when
                                                                     # its simulated CLV on the matches it did not
                                                                     # train on is not worse than the candidate's

Evaluation is leave-one-match-out (each match is predicted by a model fitted on the others). It is not a walk-forward
test: a model may be fitted on matches later than the one it predicts. The simulated CLV is a model of a bet, not a
fill: it uses band mids and the half-spread at the fill (see ``training/market_dynamics.py``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import sklearn  # noqa: E402

from shared.logging import setup_logging  # noqa: E402
from shared.metrics import MIN_CLUSTERS_FOR_CI  # noqa: E402
from training import market_dynamics as md  # noqa: E402
from virtual_trading.paper_review import headline  # noqa: E402

logger = setup_logging("train_market_dynamics")

DATA_DIR = ROOT / "data" / "market_dynamics"
MODEL_DIR = ROOT / "models" / "market_dynamics"
REPORT_DIR = ROOT / "reports" / "market_dynamics"
REQUIRED = ("match_id", "t", "y10", "y60", "p60", "p600", "fill_to_ref", "half_spread_fill", "half_spread_now",
            *md.FEATURES)


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Dataset rows as dicts, sorted by match and time. Parquet or CSV, by extension."""
    import pandas as pd

    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise SystemExit(f"{path}: missing columns {missing}")
    frame["match_id"] = frame["match_id"].astype(str)
    frame = frame.sort_values(["match_id", "t"], kind="stable").reset_index(drop=True)
    return frame.to_dict("records")


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _model_summary(name: str, s: dict[str, Any]) -> str:
    """One line of the headline table: trades, CLV and the excess over placebo, with the sample size."""
    clv, excess = s["clv"], s["excess_over_placebo"]
    return (
        f"| {name} | {s['trades']} | {s['matches_with_trades']} | "
        f"{headline(clv['estimate'], clv['ci_lower'], clv['ci_upper'], matches=clv['matches'], bets=s['trades'], digits=4)} | "
        f"{headline(s['placebo_clv']['estimate'], s['placebo_clv']['ci_lower'], s['placebo_clv']['ci_upper'], matches=s['placebo_clv']['matches'], bets=s['trades'], digits=4)} | "
        f"{headline(excess['estimate'], excess['ci_lower'], excess['ci_upper'], matches=excess['matches'], bets=s['trades'], digits=4)} |"
    )


def render_markdown(report: dict[str, Any]) -> str:
    d = report["dataset"]
    ev = report["evaluation"]
    act = report["activation"]
    lines = [
        f"# S97 market-dynamics model, {report['date']}",
        "",
        f"Model `{report['model_file']}` (version {report['version']}), scikit-learn {report['sklearn']}. Target: the "
        "band market probability 10 minutes after a snapshot minus the probability at the snapshot. Features: market "
        "(band price, changes over 1 to 10 minutes, volatility, spreads, distance from the reference price, time since "
        "the last change) and the scoreboard where a live score exists, with a no-scoreboard flag.",
        "",
        "## Sample, stated honestly",
        "",
        f"- Dataset `{d['file']}`: {d['matches']} real matches, {d['rows']} snapshots in play every 30 s "
        f"({d['rows_with_scoreboard']} with a live scoreboard from {d['matches_with_score_rows']} matches, "
        f"{d['rows_without_scoreboard']} without).",
        f"- Matches with at least one labelled snapshot: {d['matches']}. Below {MIN_CLUSTERS_FOR_CI} matches every "
        "interval below is labelled 'too few matches' and is not evidence.",
        f"- Snapshots dropped, by reason: {d['dropped']}.",
        "",
        "## Leave-one-match-out accuracy",
        "",
        f"- Mean absolute error of the 10-minute change: model {ev['mae_model']:.4f} against the zero baseline "
        f"{ev['mae_zero']:.4f} (ratio {ev['mae_ratio']:.3f}; below 1 means the model beats no change).",
        f"- Direction accuracy on moves larger than the half-spread: {ev['direction_accuracy_over_spread']:.3f} over "
        f"{ev['direction_rows_over_spread']} snapshots.",
        "",
        "## Simulated CLV after the 60 s delay and the spread (match-clustered)",
        "",
        "A simulated trade is taken when |predicted change| exceeds the threshold, at most every 2 minutes and 30 per "
        "match. The CLV is the realised move in the bet's direction from the fill (60 s) to 10 minutes after the "
        "decision, minus half the home spread at the fill. The placebo takes a seeded random side at the same times.",
        "",
        "| threshold | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV "
        "[95% CI] | excess over placebo [95% CI] |",
        "|---|---:|---:|---|---|---|",
    ]
    for theta, s in ev["by_theta"].items():
        lines.append(_model_summary(f"|change| > {theta}", s))
    lines += [
        "",
        "Split by scoreboard at the primary threshold (0.02):",
        "",
        "| segment | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV "
        "[95% CI] | excess over placebo [95% CI] |",
        "|---|---:|---:|---|---|---|",
    ]
    for label, seg in ev["by_scoreboard"].items():
        lines.append(_model_summary(f"{label} ({seg['rows']} snapshots, {seg['matches']} matches)", seg["trades"]))
    lines += [
        "",
        "## Activation",
        "",
        f"- Decision: {'activated' if act['activated'] else 'not activated'}. {act['reason']}.",
    ]
    if act.get("active_before"):
        lines.append(f"- Active model before this run: `{act['active_before']}`.")
    if act.get("compared_matches") is not None:
        lines.append(f"- Held-out comparison on {act['compared_matches']} matches the active model did not train on: "
                     f"candidate {act['candidate_clv']}, active {act['current_clv']} (simulated CLV, primary threshold).")
    lines += [
        "",
        "## Reading",
        "",
        "- Positive excess over placebo on 10 or more matches, with the interval above zero, is the only evidence this "
        "model adds to the market. Anything less is not a result.",
        "- The model is not walk-forward, uses only in-play snapshots, and its CLV is simulated from recorded prices, "
        "not from fills. The paper replay (`scripts/replay_paper.py`) is the test of the trading rules.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate the S97 market-dynamics model (no database).")
    parser.add_argument("--dataset", type=Path, default=None,
                        help="dataset file (default data/market_dynamics/dataset.parquet, or .csv)")
    parser.add_argument("--date", default=None, help="YYYYMMDD of the model and report (default: today, UTC)")
    parser.add_argument("--models-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--reports-dir", type=Path, default=REPORT_DIR)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--activate", action="store_true", help="make the new model the active one")
    group.add_argument("--activate-if-not-worse", action="store_true",
                       help="weekly rule: replace the active model only when the candidate is not worse on the "
                            "matches the active model did not train on")
    parser.add_argument("--no-activate", action="store_true", help="never change current.json")
    args = parser.parse_args(argv)

    dataset = args.dataset
    if dataset is None:
        dataset = DATA_DIR / "dataset.parquet"
        if not dataset.exists():
            dataset = DATA_DIR / "dataset.csv"
    if not dataset.exists():
        raise SystemExit(f"no dataset at {dataset}: run scripts/build_market_dynamics_dataset.py first")
    rows = load_rows(dataset)
    if not rows:
        raise SystemExit(f"{dataset} has no rows")
    manifest_path = dataset.parent / "manifest.json"
    dropped: Any = "no dataset manifest next to the file"
    if manifest_path.exists():
        stats = json.loads(manifest_path.read_text(encoding="utf-8")).get("counts", {}).get("stats", {})
        dropped = {k: v for k, v in stats.items() if k.startswith("snapshots_") and k != "snapshots_rows"}
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    matches = sorted({r["match_id"] for r in rows})
    has_score = np.asarray([float(r["no_scoreboard"]) < 0.5 for r in rows])
    logger.info("dataset_loaded", rows=len(rows), matches=len(matches), path=str(dataset))

    pred = md.leave_one_match_out(rows)
    evaluation = md.evaluate(rows, pred)
    estimator = md.fit(rows)

    args.models_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.models_dir / f"{stamp}.joblib"
    payload = {
        "estimator": estimator,
        "features": list(md.FEATURES),
        "version": md.VERSION,
        "date": stamp,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_match_ids": matches,
        "n_rows": len(rows),
        "dataset": str(dataset.relative_to(ROOT)) if dataset.is_relative_to(ROOT) else str(dataset),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
    }
    joblib.dump(payload, model_path)
    model_rel = model_path.relative_to(ROOT).as_posix() if model_path.is_relative_to(ROOT) else str(model_path)
    current_path = args.models_dir / "current.json"

    # Activation: the weekly rule compares on the matches the active model did not train on.
    current_exists = current_path.exists()
    active_before = None
    current_clv = None
    candidate_clv = None
    compared = 0
    if current_exists:
        try:
            pointer = json.loads(current_path.read_text(encoding="utf-8"))
            active_before = pointer.get("model")
            active = joblib.load(ROOT / active_before)
            trained = set(map(str, active.get("train_match_ids", [])))
            held = [i for i, r in enumerate(rows) if r["match_id"] not in trained]
            held_matches = {rows[i]["match_id"] for i in held}
            compared = len(held_matches)
            if held:
                subset = [rows[i] for i in held]
                cand_pred = [float(pred[i]) for i in held]
                cur_pred = md.predict_rows(active["estimator"], subset)
                candidate_clv = md.clv_of_model(subset, cand_pred)["clv"]["estimate"]
                current_clv = md.clv_of_model(subset, list(cur_pred))["clv"]["estimate"]
        except Exception as exc:  # an unreadable active model: the candidate replaces it only by the rule below
            logger.error("active_model_unreadable", error=f"{type(exc).__name__}: {exc}")
            current_exists = False
            compared = 0
    if args.no_activate:
        activated, reason = False, "--no-activate: current.json not changed"
    elif args.activate:
        activated, reason = True, "forced with --activate"
    elif args.activate_if_not_worse:
        activated, reason = md.decide_activation(candidate_clv, current_clv, current_exists=current_exists,
                                                 compared_matches=compared)
    elif not current_exists:
        activated, reason = True, "no active model: the first model is activated"
    else:
        activated, reason = False, "not activated: an active model exists (use --activate or --activate-if-not-worse)"
    if activated:
        tmp = current_path.with_suffix(".json.tmp")
        pointer = {
            "model": model_rel,
            "version": md.VERSION,
            "date": stamp,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "train_matches": len(matches),
            "lomo_simulated_clv_primary": evaluation["by_theta"][f"{md.THETA:.2f}"]["clv"]["estimate"],
        }
        tmp.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
        os.replace(tmp, current_path)

    report = {
        "date": stamp,
        "version": md.VERSION,
        "model_file": model_rel,
        "sklearn": sklearn.__version__,
        "dataset": {
            "file": dataset.name,
            "rows": len(rows),
            "matches": len(matches),
            "matches_with_score_rows": int(len({r["match_id"] for r, s in zip(rows, has_score) if s})),
            "rows_with_scoreboard": int(has_score.sum()),
            "rows_without_scoreboard": int((~has_score).sum()),
            "dropped": dropped,
        },
        "evaluation": evaluation,
        "activation": {
            "activated": bool(activated),
            "reason": reason,
            "active_before": active_before,
            "compared_matches": compared if current_exists else None,
            "candidate_clv": candidate_clv,
            "current_clv": current_clv,
        },
    }
    out_dir = args.reports_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    primary = evaluation["by_theta"][f"{md.THETA:.2f}"]
    print(json.dumps({
        "model": model_rel, "activated": bool(activated), "reason": reason, "rows": len(rows), "matches": len(matches),
        "mae_model": evaluation["mae_model"], "mae_zero": evaluation["mae_zero"],
        "trades_primary": primary["trades"], "matches_with_trades": primary["matches_with_trades"],
        "clv_primary": primary["clv"], "excess_primary": primary["excess_over_placebo"],
        "report": str(out_dir),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
