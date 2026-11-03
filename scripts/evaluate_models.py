"""Run the S63 evaluation harness on an S58 training dataset and write the gate report.

Reads files only (no database, no network). Without ``--predictions`` the market itself is the probability source,
which must show zero difference against itself.

Usage::

    python scripts/evaluate_models.py                                  # market as the source, latest dataset
    python scripts/evaluate_models.py --predictions preds.jsonl --source-name prematch_v3
    python scripts/evaluate_models.py --dataset data/training/20261103-0522 --date 20261103

``--predictions`` is JSON lines with ``match_id``, ``snapshot`` (a snapshot_id from snapshots.jsonl), ``p_home``
and optionally ``p_sd``. Output: ``reports/gates/<YYYYMMDD>.json`` and ``.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.evaluate import build_report, evaluate, load_dataset, market_predictions, write_report  # noqa: E402

DATA_ROOT = ROOT / "data" / "training"
GATES_DIR = ROOT / "reports" / "gates"


def latest_dataset(root: Path = DATA_ROOT) -> Path:
    candidates = sorted(p for p in root.glob("*") if (p / "manifest.json").is_file())
    if not candidates:
        raise SystemExit(f"no S58 dataset with a manifest.json under {root}")
    return candidates[-1]


def _read_predictions(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _print_summary(report: dict) -> None:
    print(f"source: {report['source']['name']} ({report['source']['kind']})  "
          f"segments {report['segments_version']}  eval {report['eval_version']}")
    print(f"dataset: {report['dataset']['dir']}  matches {report['dataset']['matches']}  "
          f"settled {report['dataset']['settled_matches']}  snapshots {report['dataset']['snapshots']}")
    for arena in ("prematch", "inplay"):
        cov = report["coverage"][arena]
        print(f"[{arena}] rows {cov['rows']} matches {cov['matches']} bets {cov['bets']}")
        for gate in (g for g in report["results"] if g["arena"] == arena):
            r1, r2 = gate["r1"], gate["r2"]
            diff = r1["logloss_diff"]
            print(f"  {gate['segment']:<16} m={gate['matches']:<4} dLL={_f(diff['point'])} "
                  f"[{_f(diff['lo'])}, {_f(diff['hi'])}]  R1={r1['status']:<12} "
                  f"bets={r2['bets']:<3} R2={r2['status']}")
    print(f"signals allowed: {report['summary']['signals_allowed']}")


def _f(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:+.4f}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=None, help="S58 dataset directory (default: latest)")
    parser.add_argument("--predictions", type=Path, default=None, help="JSON lines of source predictions")
    parser.add_argument("--source-name", default=None, help="name recorded in the report (default: market)")
    parser.add_argument("--date", default=None, help="report date YYYYMMDD (default: today, UTC)")
    parser.add_argument("--out-dir", type=Path, default=GATES_DIR)
    parser.add_argument("--overwrite", action="store_true", help="replace a report written for another source")
    args = parser.parse_args(argv)

    dataset_dir = args.dataset or latest_dataset()
    dataset = load_dataset(dataset_dir)
    now = datetime.now(timezone.utc)
    if args.predictions is None:
        predictions = market_predictions(dataset)
        source_name = args.source_name or "market"
        kind, pred_file = "market", None
    else:
        predictions = _read_predictions(args.predictions)
        if not args.source_name:
            raise SystemExit("--source-name is required with --predictions")
        source_name, kind, pred_file = args.source_name, "predictions", str(args.predictions)

    evaluation = evaluate(dataset, predictions)
    report = build_report(
        dataset, evaluation, source_name=source_name, source_kind=kind, predictions_file=pred_file,
        predictions_rows=len(predictions), generated_at=now,
    )
    stem = args.date or now.strftime("%Y%m%d")
    json_path, md_path = write_report(report, args.out_dir, stem, overwrite=args.overwrite)
    _print_summary(report)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
