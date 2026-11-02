"""Train and report the calibrated fair-value model for a specialist's format family (S14).

Usage: python scripts/train_fair_value.py --key t20i_men [--out-dir models/fair_value]

Note: on synthetic corpora the market price is itself a hand-built model, so "edge"
versus the market measures model disagreement, not real alpha. Validate on recorded
odds (S16) before trusting any edge.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.fair_value import FairValueModel, _label, family_of  # noqa: E402

DEFAULT_OUT = Path("models/fair_value")


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
    """De-vigged mid probability that home wins."""
    def mid(b: Any, l: Any) -> float:
        b, l = float(b or 0), float(l or 0)
        if b > 1 and l > 1:
            return 1.0 / ((b + l) / 2.0)
        return 1.0 / b if b > 1 else 0.5

    h = mid(tick.get("back_home"), tick.get("lay_home"))
    a = mid(tick.get("back_away"), tick.get("lay_away"))
    return h / (h + a) if h + a > 0 else 0.5


def _metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    return {"brier": brier(p, y), "log_loss": log_loss(p, y), "ece": ece(p, y)}


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
    """Fit on the train split, report on the holdout, save model + JSON report."""
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    print(json.dumps(train(args.key, Path(args.out_dir)), indent=2))


if __name__ == "__main__":
    main()
