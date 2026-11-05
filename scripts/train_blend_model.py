"""S61: fit the market-blend model on an S58 dataset, walk forward by match date, and write its predictions.

    python scripts/train_blend_model.py [--dataset DIR] [--snapshot T-10m] [--include-simulated]

For every recorded match the S60 pre-match probability is built from the Cricsheet ratings (``load_ratings``) and the
loaded ``models/prematch/<family>_v3.joblib`` (t20 and odi only; a team unknown to the ratings gives no model). Each
settled match is one row at the chosen snapshot. The walk-forward refits before every match date on earlier dates
only. Outputs (never bets):

* ``models/blend/predictions.jsonl``: out-of-sample ``{match_id, snapshot, p_home, p_sd, ...}`` rows, the contract of
  ``scripts/evaluate_models.py --predictions``;
* ``models/blend/latest.json``: the posterior fitted on every usable row, with the priors and the row counts.

Score the predictions with::

    python scripts/evaluate_models.py --predictions models/blend/predictions.jsonl --source-name blend_v1 --date 20261103-blend
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cricsheet_harmonizer import canonicalize_team  # noqa: E402
from scripts.evaluate_models import latest_dataset  # noqa: E402
from strategy.live_context import DEFAULT_CACHE, load_ratings  # noqa: E402
from strategy.prematch_model import PrematchModel  # noqa: E402
from training import segments as seg  # noqa: E402
from training.blend import (  # noqa: E402
    BLEND_SEGMENTS,
    BLEND_VERSION,
    DEFAULT_PRIOR_SD,
    DEFAULT_SNAPSHOT,
    BlendModel,
    build_blend_rows,
    prematch_probabilities,
    walk_forward,
)
from training.evaluate import load_dataset  # noqa: E402

DEFAULT_OUT = ROOT / "models" / "blend"
DEFAULT_PREMATCH = ROOT / "models" / "prematch"
DEFAULT_DATA_ROOT = ROOT / "data"
FAMILIES = ("t20", "odi")


def _load_prematch_models(directory: Path) -> Dict[str, PrematchModel]:
    models: Dict[str, PrematchModel] = {}
    for family in FAMILIES:
        path = directory / f"{family}_v3.joblib"
        if path.exists():
            models[family] = PrematchModel.load(path)
    return models


def _write_jsonl(path: Path, records: list[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=False) + "\n")


def _fmt(block: Dict[str, float]) -> str:
    return f"{block['mean']:+.4f} (sd {block['sd']:.4f}, 95% [{block['lo95']:+.4f}, {block['hi95']:+.4f}])"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=None, help="S58 dataset directory (default: latest)")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="snapshot label to model (default T-10m)")
    parser.add_argument("--include-simulated", action="store_true", help="include SRL (simulated) matches")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--prematch-dir", type=Path, default=DEFAULT_PREMATCH)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--ratings-cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)

    dataset_dir = args.dataset or latest_dataset()
    dataset = load_dataset(dataset_dir)
    ratings = load_ratings(args.data_root, cache=args.ratings_cache)
    models = _load_prematch_models(args.prematch_dir)

    p_model = prematch_probabilities(dataset["matches"], ratings=ratings, models=models,
                                     normalize_team=canonicalize_team)
    rows, reasons = build_blend_rows(dataset, p_model, snapshot_label=args.snapshot,
                                     include_simulated=args.include_simulated)
    rated_matches = sum(1 for v in p_model.values() if v is not None)
    rated_rows = sum(1 for r in rows if r.has_model)

    print(f"dataset: {dataset_dir}  matches {len(dataset['matches'])}  snapshot {args.snapshot}")
    print(f"p_model: {rated_matches} of {len(dataset['matches'])} recorded matches rated "
          f"(models: {sorted(models) or 'none'})")
    print(f"usable rows: {len(rows)} (settled, priced at {args.snapshot}); with p_model: {rated_rows}")
    print(f"excluded: {dict(reasons)}")

    predictions = walk_forward(rows)
    p_model_by_row = {(r.match_id, r.snapshot_id): r.p_model for r in rows}
    records = [{
        "match_id": rec["match_id"],
        "snapshot": rec["snapshot_id"],
        "p_home": rec["p_home"],
        "p_sd": rec["p_sd"],
        "date": rec["date"],
        "p_market": rec["p_market"],
        "p_model": p_model_by_row.get((rec["match_id"], rec["snapshot_id"])),
        "has_model": rec["has_model"],
        "n_train": rec["n_train"],
        "train_max_date": rec["train_max_date"],
    } for rec in predictions]
    pred_path = args.out_dir / "predictions.jsonl"
    _write_jsonl(pred_path, records)

    final = BlendModel().fit(rows)
    post = final.posterior()
    print(f"posterior on {final.n_train} rows: fitted={final.fitted}")
    print(f"  a: {_fmt(post['a'])}")
    print(f"  b: {_fmt(post['b'])}")
    print(f"  c: {_fmt(post['c'])}")
    for name in BLEND_SEGMENTS:
        print(f"  {name}: {_fmt(post['segments'][name])}")
    print(f"walk-forward: {len(records)} predictions over {len({r['date'] for r in records})} match dates")

    latest = {
        "version": BLEND_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_dir),
        "snapshot": args.snapshot,
        "include_simulated": bool(args.include_simulated),
        "devig": "power",
        "segments_version": seg.SEGMENTS_VERSION,
        "segments": list(BLEND_SEGMENTS),
        "prior_sd": DEFAULT_PRIOR_SD,
        "prior_mean": {"a": 0.0, "b": 1.0, "c": 0.0, "d": 0.0},
        "usable_rows": len(rows),
        "rows_with_model": rated_rows,
        "matches_rated": rated_matches,
        "excluded": dict(reasons),
        "outcomes": dict(Counter(r.home_won for r in rows)),
        "fitted": final.fitted,
        "posterior": post,
        "walk_forward_predictions": len(records),
        "predictions_file": str(pred_path),
        "prematch_models": {k: str(args.prematch_dir / f"{k}_v3.joblib") for k in models},
    }
    latest_path = args.out_dir / "latest.json"
    latest_path.write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {pred_path}")
    print(f"wrote {latest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
