"""S103: R1 benchmark. Our pre-match model against historical closing odds, per segment.

    python scripts/benchmark_historical_odds.py --predictions PATH [--root data/historical_odds]
        [--out reports/historical_odds] [--data-root data]

``PATH`` comes from ``scripts/train_prematch_model.py --family t20 --export-predictions PATH`` (out-of-sample test
predictions only). The source stores under ``--root`` (S98) are linked to Cricsheet under ``--data-root`` and the
closing prices are compared with the model. Writes ``<out>/r1-<UTC date>/report.json`` and ``report.md``.

No database, no network. Results from a store that fails the S102 verifier are not evidence (not checked here yet).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.train_fair_value import FAMILY_DIRS, iter_cricsheet  # noqa: E402
from training import historical_benchmark as hb  # noqa: E402
from training import historical_odds as ho  # noqa: E402
from training.dataset import CricsheetIndex  # noqa: E402

DEFAULT_OUT = Path("reports/historical_odds")
DEFAULT_DATA_ROOT = Path("data")
CRICSHEET_FAMILIES = ("t20", "odi")


def _team_alias() -> Dict[str, str]:
    """Franchise and national-team aliases shared with the Cricsheet harmonizer (same as build_training_dataset)."""
    from scripts.cricsheet_harmonizer import CANONICAL_TEAMS

    return dict(CANONICAL_TEAMS)


def _cricsheet_entries(data_root: Path) -> List[Dict[str, Any]]:
    """Teams, dates and type of every Cricsheet match under ``data_root``. ``match_id`` is the file stem, as the
    walk-forward rows use it."""
    entries: List[Dict[str, Any]] = []
    stats: Counter = Counter()
    for family in CRICSHEET_FAMILIES:
        dirs = [data_root / d for d in FAMILY_DIRS[family]]
        for raw in iter_cricsheet(dirs, family, stats):
            info = raw.get("info") or {}
            entries.append({
                "match_id": raw["match_id"],
                "teams": info.get("teams"),
                "dates": info.get("dates") or [],
                "match_type": info.get("match_type"),
            })
    return entries


def _store_summary(root: Path) -> tuple[List[ho.OddsRecord], Dict[str, Any]]:
    records: List[ho.OddsRecord] = []
    sources: Dict[str, Any] = {}
    for source in ho.SOURCES:
        rows, manifest = ho.read_store(source, root=root)
        records.extend(rows)
        sources[source] = {
            "records": len(rows),
            "provenance": manifest.get("provenance"),
            "created_at": manifest.get("created_at"),
            "problems": manifest.get("problems", {}),
        }
    return records, sources


def run(args: argparse.Namespace) -> Dict[str, Any]:
    predictions = hb.load_predictions(args.predictions)
    records, sources = _store_summary(Path(args.root))
    index = CricsheetIndex(_cricsheet_entries(Path(args.data_root)), team_alias=_team_alias())
    closing = hb.closing_by_match(records, index)
    now = datetime.now(timezone.utc)
    report = hb.benchmark(predictions, closing, team_alias=_team_alias())
    report.update({
        "generated_utc": now.isoformat(),
        "predictions_file": str(args.predictions),
        "records": len(records),
        "sources": sources,
        "closing_matches": len(closing),
        "verification": "not checked: the S102 verifier has not been run on these stores",
    })
    out_dir = Path(args.out) / f"r1-{now.date().isoformat()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report["report_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(hb.render_markdown(report), encoding="utf-8")
    return report


def _line(name: str, block: Dict[str, Any]) -> str:
    diff = block["log_loss_diff"]
    head = f"  {name:28s} n={block['n_matches']:<4d}"
    if diff["estimate"] is None:
        return f"{head} no estimate  {block['verdict']}"
    if diff["ci_lower"] is None:
        return f"{head} model-market logloss {diff['estimate']:+.4f} (no interval)  {block['verdict']}"
    return (f"{head} model-market logloss {diff['estimate']:+.4f} "
            f"(95% CI {diff['ci_lower']:+.4f} to {diff['ci_upper']:+.4f})  {block['verdict']}")


def summary_lines(report: Dict[str, Any]) -> List[str]:
    m = report["matches"]
    lines = [
        f"R1 benchmark: {m['joined']} matches joined ({m['predictions']} predictions, "
        f"{m['without_closing']} without a closing price, {m['orientation_mismatch']} orientation mismatches)",
        _line("all matches", report["overall"]),
    ]
    for name, groups in report["segments"].items():
        for key, block in groups.items():
            lines.append(_line(f"{name}: {key}", block))
    lines.append(_line("blend vs market", report["blend"]))
    for roi in report["roi"]:
        lines.append(f"  ROI at {roi['threshold_points']:g}-point gap: {roi['n_bets']} bets, "
                     f"ROI {roi['roi_per_unit'] if roi['roi_per_unit'] is not None else '-'} "
                     "(closing price, direction only)")
    return lines


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--predictions", required=True, help="JSON lines from train_prematch_model --export-predictions")
    ap.add_argument("--root", default=str(ho.DEFAULT_ROOT), help="historical odds store root (S98)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Cricsheet data root (for linking)")
    args = ap.parse_args(argv)
    report = run(args)
    print("\n".join(summary_lines(report)))
    print(f"report: {report['report_dir']}")


if __name__ == "__main__":
    main()
