"""S64 research: do in-play LotusBook prices over- or under-react over the next 10 minutes?

Reads an S58 dataset (files only, no database, no network, no Docker) and writes
``reports/research/inplay_<YYYYMMDD>.md``. Produces no signals.

Usage::

    python scripts/research_inplay_dynamics.py
    python scripts/research_inplay_dynamics.py --dataset data/training/20261103-0522 --date 20261103 --print

Refuses to overwrite an existing report unless ``--overwrite`` is given.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training import inplay as ip  # noqa: E402
from training.evaluate import load_dataset  # noqa: E402
from training.segments import POLICY_MIN_EDGE  # noqa: E402

DEFAULT_DATASET = ROOT / "data" / "training" / "20261103-0522"


def _f(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _ci(block: dict[str, Any], digits: int = 4) -> str:
    if block.get("point") is None:
        return "n/a"
    return f"{block['point']:.{digits}f} [{_f(block['lo'], digits)}, {_f(block['hi'], digits)}]"


def _rate(block: dict[str, Any]) -> str:
    if block["n"] == 0:
        return "n/a (0 large moves)"
    return f"{block['hits']}/{block['n']} = {_f(block['rate'], 3)} (Wilson 95% [{_f(block['lo'], 3)}, {_f(block['hi'], 3)}])"


def run(dataset_dir: Path) -> dict[str, Any]:
    dataset = load_dataset(dataset_dir)
    rows = ip.build_rows(dataset)
    real_rows = [r for r in rows if not r["is_simulated"]]
    evaluated = ip.walk_forward(rows)
    evaluated_real = ip.walk_forward(real_rows)
    scores = ip.score_models(evaluated)
    scores_real = ip.score_models(evaluated_real) if evaluated_real else None
    in_play_snaps = [s for s in dataset["snapshots"] if s.get("in_play")]
    priced = [s for s in in_play_snaps if s.get("has_prices")]
    settled = {r["match_id"] for r in rows if r["home_won"] in (0, 1)}
    policy = ip.fade_policy_summary(dataset, rows)
    d6 = [abs(r["features"]["d6"]) for r in rows]
    return {
        "dataset": str(dataset_dir),
        "dataset_generated": dataset["manifest"].get("generated_at_utc"),
        "in_play_snapshots": len(in_play_snaps),
        "in_play_priced": len(priced),
        "matches_with_rows": len({r["match_id"] for r in rows}),
        "matches_real": len({r["match_id"] for r in real_rows}),
        "settled_matches": len(settled),
        "rows": len(rows),
        "rows_real": len(real_rows),
        "large_move_rows": sum(1 for r in rows if ip.large_move(r)),
        "abs_d6_median": float(sorted(d6)[len(d6) // 2]) if d6 else None,
        "label_sd": float(np.std([r["y"] for r in rows], ddof=1)) if len(rows) > 1 else None,
        "evaluable_rows": len(evaluated),
        "evaluable_matches": len({r["match_id"] for r in evaluated}),
        "scores": scores,
        "scores_real": scores_real,
        "detected": {name: ip.detected(scores, name) for name in ip.MODELS},
        "policy": policy,
    }


def render(result: dict[str, Any], date: str) -> str:
    s = result["scores"]
    lines = [
        f"# S64 in-play price dynamics, {date}",
        "",
        "Research track. Produces no signals. Question: after a large move in the de-vigged LotusBook home probability,",
        "does the price continue or revert over the next 10 minutes, measured at the price 60 s after the snapshot?",
        "",
        "## Data",
        "",
        f"- Dataset: `{result['dataset']}` (generated {result['dataset_generated']}).",
        f"- In-play snapshots: {result['in_play_snapshots']}, with prices: {result['in_play_priced']}.",
        f"- Matches with usable in-play rows: {result['matches_with_rows']} ({result['matches_real']} real, non-SRL).",
        f"- Matches with a settled result: {result['settled_matches']}.",
        f"- Rows (features and label complete): {result['rows']} ({result['rows_real']} real). "
        f"Large moves (|d6| >= {ip.LARGE_MOVE_P}): {result['large_move_rows']}.",
        f"- Median |d6| {_f(result['abs_d6_median'])}; sd of the label {_f(result['label_sd'])}.",
        "",
        "## Definitions (fixed before the run)",
        "",
        f"- Price: power de-vig of the snapshot's own `book`. Snapshots every {ip.STEP_S // 60} min in play.",
        f"- Features: {', '.join(ip.FEATURES)}. d2, d6, d10 are de-vigged probability moves over 2, 6 and 10 min; "
        "vol10 is the sd of the five 2-min moves; spread is the relative back/lay width; minutes is time since the "
        "first in-play snapshot.",
        f"- Label: p(book_10m) - p(book_60s) at the same snapshot. Both are S58 look-ahead books; neither is a feature.",
        f"- Walk-forward: matches in order of first in-play snapshot. Training rows must have their 10-min label closed "
        f"before the test match starts. A fold needs at least {ip.MIN_TRAIN_ROWS} training rows from "
        f"{ip.MIN_TRAIN_MATCHES} matches; other folds make no prediction.",
        f"- Models: random walk (no change), momentum and reversion (sign of d6 times the mean absolute label), ridge "
        f"(alpha {ip.RIDGE_ALPHA}), gradient boosting ({ip.GBM_PARAMS}).",
        "",
        "## Out-of-sample results (all matches)",
        "",
        f"Evaluable rows: {result['evaluable_rows']} across {result['evaluable_matches']} matches. "
        "R2 is out-of-sample against the mean of the test labels. MSE gain is the random walk's squared error minus "
        "the model's, averaged per row, with a match-clustered bootstrap CI; above zero means the model beats no change.",
        "",
        "| Model | OOS R2 | MSE gain vs random walk | Hit rate, large moves | Beats no-change with evidence |",
        "|---|---|---|---|---|",
    ]
    for name in ip.MODELS:
        block = s.get("models", {}).get(name)
        if block is None:
            lines.append(f"| {name} | n/a | n/a | n/a | n/a |")
            continue
        lines.append(f"| {name} | {_f(block['r2_oos'])} | {_ci(block['mse_gain_vs_rw'])} | "
                     f"{_rate(block['hit_large'])} | {'yes' if result['detected'][name] else 'no'} |")
    lines += [
        "",
        f"Fade hit rate on large moves (label moves against the 6-min move): {_rate(s.get('fade_hit_large', {'hits': 0, 'n': 0, 'rate': None, 'lo': None, 'hi': None}))}.",
        "",
        "## Real (non-SRL) matches only",
        "",
    ]
    real = result["scores_real"]
    if real and real.get("models"):
        lines.append(f"Evaluable rows: {real['rows']} across {real['matches']} matches.")
        lines.append("")
        lines.append("| Model | OOS R2 | MSE gain vs random walk |")
        lines.append("|---|---|---|")
        for name in ip.MODELS:
            block = real["models"].get(name)
            if block:
                lines.append(f"| {name} | {_f(block['r2_oos'])} | {_ci(block['mse_gain_vs_rw'])} |")
    else:
        lines.append("No real-match fold had enough training data to evaluate a model.")
    p = result["policy"]
    lines += [
        "",
        "## Policy simulation (S63 fill rule, 'fade large moves')",
        "",
        f"Rule: {p['rule']}; probability source = current price moved back by a fraction of the 6-min move. "
        f"Selection by `strategy.staking.select` with the pre-registered minimum edge {POLICY_MIN_EDGE}, one bet per "
        "match, filled at the takeable price 60 s after the snapshot, settled on the result, CLV against book_10m.",
        "",
        f"- Settled in-play rows: {p['rows']} across {p['settled_matches']} matches.",
        f"- Bets: {p['bets']} across {p['matches']} matches.",
        f"- Mean CLV per bet: {_f(p['clv_mean'])} (95% CI {_f(p['clv_ci'][0])} to {_f(p['clv_ci'][1])}, n={p['clv_n']}).",
        f"- Simulated P&L: {_f(p['pnl_total'], 2)} total, {_f(p['pnl_mean'], 2)} per bet "
        f"(95% CI {_f(p['pnl_ci'][0], 2)} to {_f(p['pnl_ci'][1], 2)}).",
        "",
        "## Reading",
        "",
    ]
    lines += _reading(result)
    return "\n".join(lines) + "\n"


def _reading(result: dict[str, Any]) -> list[str]:
    n_eval = result["evaluable_rows"]
    if n_eval == 0:
        return [
            f"There is no fold with enough training data ({ip.MIN_TRAIN_ROWS} rows from {ip.MIN_TRAIN_MATCHES} "
            "matches whose 10-minute labels closed before the test match). Nothing can be concluded.",
        ]
    any_detected = any(result["detected"].values())
    return [
        f"The data is too thin to conclude. {result['matches_with_rows']} matches and {result['large_move_rows']} large "
        f"moves give wide intervals, and only {result['evaluable_matches']} matches could be predicted out of sample.",
        "No model beats 'no change' with a CI above zero and a large-move hit rate CI above 0.5."
        if not any_detected else
        "At least one predictor passes the detection rule on this data. Treat it as noise until it holds on more "
        "matches and on the real-match subset.",
        "Under the S64 guardrail this produces no signals; it is research only. Re-run as the recorder grows.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports" / "research")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--print", action="store_true", help="also print the report to stdout")
    args = parser.parse_args(argv)

    result = run(args.dataset)
    text = render(result, args.date)
    out = args.out_dir / f"inplay_{args.date}.md"
    if out.exists() and not args.overwrite:
        print(f"refusing to overwrite {out}; pass --overwrite", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.print:
        print(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
