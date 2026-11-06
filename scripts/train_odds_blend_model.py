"""Train and evaluate the Market-Informed Blend Model on OddsPapi & aussportsbetting historical odds.

Splits data into:
- Train Split: matches strictly before 2026-07-01 (1,100 matches)
- Test Split: matches from 2026-07-01 to 2026-10-25 (543 matches, held-out out-of-sample)

Evaluates:
- Probability calibration (Log loss, Brier score, ECE) vs Market and Elo baselines
- Out-of-sample betting profitability under the S109 corridor (1.35 <= odds < 4.00)
- Saves model artifacts to:
    - models/blend/latest.json (S61 BlendModel contract)
    - models/blend/market_blend_v1.json and .joblib (Enhanced Value Blend model)
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import numpy as np

from training import segments as seg
from training.blend import (
    BLEND_SEGMENTS,
    BLEND_VERSION,
    DEFAULT_PRIOR_SD,
    BlendModel,
    BlendRow,
)


def logit(p: float, eps: float = 1e-6) -> float:
    p_clipped = max(eps, min(1.0 - eps, p))
    return float(np.log(p_clipped / (1.0 - p_clipped)))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-6) -> float:
    p_clipped = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p_clipped) + (1.0 - y) * np.log(1.0 - p_clipped)))


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece_score(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        in_bin = (p >= bin_boundaries[i]) & (p < bin_boundaries[i + 1])
        if in_bin.any():
            bin_acc = y[in_bin].mean()
            bin_conf = p[in_bin].mean()
            ece += np.abs(bin_acc - bin_conf) * in_bin.sum() / len(y)
    return float(ece)


FEATURE_COLS = [
    "delta_elo_mkt",       # (logit(p_elo) - logit(p_market))
    "xi_diff",             # player XI strength difference
    "tier",                # 0: international, 1: domestic league
    "is_women",            # 1: women, 0: men
    "is_t20",              # 1: t20, 0: odi
    "overround_excess",    # overround - 1.05
    "is_favourite",        # 1 if p_market >= 0.5
]


def extract_features(row: Dict[str, Any]) -> List[float]:
    delta_elo_mkt = float(row.get("delta_elo_mkt", 0.0))
    xi_diff = float(row.get("xi_diff", 0.0))
    tier = float(row.get("tier", 1))
    is_women = 1.0 if str(row.get("gender", "")).lower() == "female" else 0.0
    is_t20 = 1.0 if str(row.get("family", "")).lower() == "t20" else 0.0
    overround_excess = float(row.get("overround", 1.05)) - 1.05
    is_fav = 1.0 if float(row.get("p_market", 0.5)) >= 0.5 else 0.0

    return [
        delta_elo_mkt,
        xi_diff,
        tier,
        is_women,
        is_t20,
        overround_excess,
        is_fav,
    ]


class MarketBlendModel:
    """Bayesian regularized logistic shift on top of the de-vigged market logit.

    P(home) = sigmoid( logit(P_market) + beta_0 + sum_j beta_j * X_j )
    When all beta = 0, P(home) = P_market exactly.
    """

    def __init__(self, C: float = 1.0) -> None:
        self.C = C
        self.intercept_: float = 0.0
        self.coef_: np.ndarray = np.zeros(len(FEATURE_COLS))
        self.feature_names: List[str] = list(FEATURE_COLS)

    def fit(self, X: np.ndarray, y: np.ndarray, offsets: np.ndarray) -> "MarketBlendModel":
        from scipy.optimize import minimize

        n_samples, n_features = X.shape
        def objective(params: np.ndarray) -> Tuple[float, np.ndarray]:
            b0 = params[0]
            betas = params[1:]
            z = offsets + b0 + X @ betas
            p = sigmoid(z)
            p_clip = np.clip(p, 1e-9, 1.0 - 1e-9)
            nll = -np.sum(y * np.log(p_clip) + (1.0 - y) * np.log(1.0 - p_clip))
            reg = (0.5 / self.C) * (b0 ** 2 + np.sum(betas ** 2))
            loss = nll + reg

            err = p - y
            grad_b0 = np.sum(err) + (1.0 / self.C) * b0
            grad_betas = X.T @ err + (1.0 / self.C) * betas
            grad = np.concatenate([[grad_b0], grad_betas])
            return loss, grad

        init_params = np.zeros(n_features + 1)
        res = minimize(objective, init_params, jac=True, method="L-BFGS-B")
        self.intercept_ = float(res.x[0])
        self.coef_ = np.asarray(res.x[1:], dtype=float)
        return self

    def predict_proba(self, X: np.ndarray, offsets: np.ndarray) -> np.ndarray:
        z = offsets + self.intercept_ + X @ self.coef_
        return sigmoid(z)

    def predict_one(self, row: Dict[str, Any]) -> float:
        p_mkt = float(row["p_market"])
        off = logit(p_mkt)
        feats = np.asarray(extract_features(row), dtype=float)
        z = off + self.intercept_ + float(np.dot(feats, self.coef_))
        return float(1.0 / (1.0 + np.exp(-z)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": "MarketBlendModel",
            "version": "1.0.0",
            "regularization_C": self.C,
            "intercept": self.intercept_,
            "coefficients": {name: float(coef) for name, coef in zip(self.feature_names, self.coef_)},
            "feature_names": self.feature_names,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketBlendModel":
        model = cls(C=data.get("regularization_C", 1.0))
        model.intercept_ = float(data["intercept"])
        coeffs = data["coefficients"]
        model.coef_ = np.array([coeffs[name] for name in model.feature_names], dtype=float)
        return model


def make_native_blend_rows(data: List[Dict[str, Any]]) -> List[BlendRow]:
    rows: List[BlendRow] = []
    for r in data:
        segs: List[str] = []
        if r["family"] == "t20":
            segs.append("family_t20")
        elif r["family"] == "odi":
            segs.append("family_odi")
        elif r["family"] == "test":
            segs.append("family_test")
        if r["gender"] == "female":
            segs.append("gender_women")
        if r["tier"] == 1:
            segs.append("tier_2")
        if r["is_home_favourite"]:
            segs.append("market_favourite")
        if r["p_market"] < 0.25:
            segs.append("market_longshot")

        rows.append(
            BlendRow(
                match_id=r["match_id"],
                snapshot_id="close",
                date=r["date"],
                p_market=float(r["p_market"]),
                p_model=float(r["p_elo"]),
                home_won=int(r["home_won"]),
                segments=tuple(segs),
            )
        )
    return rows


def simulate_trading(
    rows: List[Dict[str, Any]],
    p_pred: np.ndarray,
    min_edge: float = 0.03,
    min_odds: float = 1.35,
    max_odds: float = 4.00,
    unit_stake: float = 1000.0,
) -> Dict[str, Any]:
    """Simulate value betting with the S109 corridor and positive EV requirement."""
    n_bets = 0
    wins = 0
    total_staked = 0.0
    total_pnl = 0.0
    clv_sum = 0.0

    trades = []
    for i, r in enumerate(rows):
        p_home_pred = float(p_pred[i])
        p_away_pred = 1.0 - p_home_pred

        price_home = float(r["price_home"])
        price_away = float(r["price_away"])
        p_home_mkt = float(r["p_market"])
        p_away_mkt = 1.0 - p_home_mkt

        home_won = int(r["home_won"])

        ev_home = (p_home_pred * price_home) - 1.0
        ev_away = (p_away_pred * price_away) - 1.0

        chosen_side = None
        chosen_price = 0.0
        chosen_p_pred = 0.0
        chosen_p_mkt = 0.0
        chosen_won = False

        if ev_home >= min_edge and min_odds <= price_home < max_odds:
            chosen_side = "home"
            chosen_price = price_home
            chosen_p_pred = p_home_pred
            chosen_p_mkt = p_home_mkt
            chosen_won = (home_won == 1)
        elif ev_away >= min_edge and min_odds <= price_away < max_odds:
            chosen_side = "away"
            chosen_price = price_away
            chosen_p_pred = p_away_pred
            chosen_p_mkt = p_away_mkt
            chosen_won = (home_won == 0)

        if chosen_side is not None:
            n_bets += 1
            stake = unit_stake
            total_staked += stake
            if chosen_won:
                wins += 1
                pnl = stake * (chosen_price - 1.0)
            else:
                pnl = -stake
            total_pnl += pnl

            fair_odds = 1.0 / chosen_p_mkt
            clv = (chosen_price / fair_odds) - 1.0
            clv_sum += clv

            trades.append({
                "match_id": r["match_id"],
                "date": r["date"],
                "side": chosen_side,
                "price": chosen_price,
                "p_pred": chosen_p_pred,
                "p_mkt": chosen_p_mkt,
                "won": chosen_won,
                "pnl": pnl,
            })

    win_rate = wins / n_bets if n_bets > 0 else 0.0
    roi = total_pnl / total_staked if total_staked > 0 else 0.0
    mean_clv = clv_sum / n_bets if n_bets > 0 else 0.0

    return {
        "n_bets": n_bets,
        "wins": wins,
        "win_rate": win_rate,
        "total_staked": total_staked,
        "total_pnl": total_pnl,
        "roi": roi,
        "mean_clv": mean_clv,
        "trades": trades,
    }


def main() -> int:
    dataset_path = ROOT / "data" / "historical_odds" / "training_dataset.jsonl"
    if not dataset_path.exists():
        print(f"Error: {dataset_path} does not exist. Run scripts/build_odds_training_dataset.py first.")
        return 1

    print(f"Reading dataset from {dataset_path}...")
    with dataset_path.open("r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]

    SPLIT_DATE = "2026-07-01"
    train_rows = [r for r in rows if r["date"] < SPLIT_DATE]
    test_rows = [r for r in rows if r["date"] >= SPLIT_DATE]

    print(f"Total Matches: {len(rows)}")
    print(f"Train Matches: {len(train_rows)} ({train_rows[0]['date']} to {train_rows[-1]['date']})")
    print(f"Test Matches:  {len(test_rows)} ({test_rows[0]['date']} to {test_rows[-1]['date']})")

    # Save split files
    train_path = ROOT / "data" / "historical_odds" / "train_split.jsonl"
    test_path = ROOT / "data" / "historical_odds" / "test_split.jsonl"
    with train_path.open("w", encoding="utf-8") as f:
        for r in train_rows:
            f.write(json.dumps(r) + "\n")
    with test_path.open("w", encoding="utf-8") as f:
        for r in test_rows:
            f.write(json.dumps(r) + "\n")

    y_train = np.array([r["home_won"] for r in train_rows], dtype=float)
    y_test = np.array([r["home_won"] for r in test_rows], dtype=float)

    p_mkt_train = np.array([r["p_market"] for r in train_rows], dtype=float)
    p_mkt_test = np.array([r["p_market"] for r in test_rows], dtype=float)

    p_elo_train = np.array([r["p_elo"] for r in train_rows], dtype=float)
    p_elo_test = np.array([r["p_elo"] for r in test_rows], dtype=float)

    off_train = np.array([logit(p) for p in p_mkt_train], dtype=float)
    off_test = np.array([logit(p) for p in p_mkt_test], dtype=float)

    X_train = np.array([extract_features(r) for r in train_rows], dtype=float)
    X_test = np.array([extract_features(r) for r in test_rows], dtype=float)

    # 1. Baseline Performance
    print("\n" + "=" * 60)
    print("PROBABILITY CALIBRATION BASELINES")
    print("=" * 60)
    print(f"TRAIN: Market Log-Loss: {log_loss(p_mkt_train, y_train):.4f} | Brier: {brier_score(p_mkt_train, y_train):.4f} | ECE: {ece_score(p_mkt_train, y_train):.4f}")
    print(f"TRAIN: Pure Elo Log-Loss:{log_loss(p_elo_train, y_train):.4f} | Brier: {brier_score(p_elo_train, y_train):.4f} | ECE: {ece_score(p_elo_train, y_train):.4f}")
    print(f"TEST:  Market Log-Loss: {log_loss(p_mkt_test, y_test):.4f} | Brier: {brier_score(p_mkt_test, y_test):.4f} | ECE: {ece_score(p_mkt_test, y_test):.4f}")
    print(f"TEST:  Pure Elo Log-Loss:{log_loss(p_elo_test, y_test):.4f} | Brier: {brier_score(p_elo_test, y_test):.4f} | ECE: {ece_score(p_elo_test, y_test):.4f}")

    # 2. Fit Native BlendModel
    print("\n" + "=" * 60)
    print("FITTING S61 BAYESIAN BLEND MODEL")
    print("=" * 60)
    native_train_rows = make_native_blend_rows(train_rows)
    native_test_rows = make_native_blend_rows(test_rows)
    native_model = BlendModel().fit(native_train_rows)
    native_post = native_model.posterior()
    p_native_test, p_native_sd = native_model.predict(native_test_rows)

    print(f"Native Blend fitted on {native_model.n_train} rows: fitted={native_model.fitted}")
    print(f"  Posterior a: {native_post['a']['mean']:+.4f} (sd {native_post['a']['sd']:.4f})")
    print(f"  Posterior b: {native_post['b']['mean']:+.4f} (sd {native_post['b']['sd']:.4f})")
    print(f"  Posterior c: {native_post['c']['mean']:+.4f} (sd {native_post['c']['sd']:.4f}, 95% [{native_post['c']['lo95']:+.4f}, {native_post['c']['hi95']:+.4f}])")

    # 3. Fit Enhanced Value Blend Model
    print("\n" + "=" * 60)
    print("FITTING ENHANCED VALUE BLEND MODEL")
    print("=" * 60)
    model = MarketBlendModel(C=5.0).fit(X_train, y_train, off_train)
    p_pred_train = model.predict_proba(X_train, off_train)
    p_pred_test = model.predict_proba(X_test, off_test)

    print(f"Enhanced Blend fitted. Intercept: {model.intercept_:+.4f}")
    for name, coef in zip(FEATURE_COLS, model.coef_):
        print(f"  {name:<18}: {coef:+.4f}")

    # 4. Out of Sample Comparison
    print("\n" + "=" * 60)
    print("OUT-OF-SAMPLE TEST SET METRICS (JULY - OCTOBER 2026)")
    print("=" * 60)
    test_loss_mkt = log_loss(p_mkt_test, y_test)
    test_loss_native = log_loss(p_native_test, y_test)
    test_loss_pred = log_loss(p_pred_test, y_test)

    test_brier_mkt = brier_score(p_mkt_test, y_test)
    test_brier_native = brier_score(p_native_test, y_test)
    test_brier_pred = brier_score(p_pred_test, y_test)

    test_ece_mkt = ece_score(p_mkt_test, y_test)
    test_ece_native = ece_score(p_native_test, y_test)
    test_ece_pred = ece_score(p_pred_test, y_test)

    print(f"Log-Loss: Market={test_loss_mkt:.4f} | S61 Native={test_loss_native:.4f} | Enhanced Blend={test_loss_pred:.4f}")
    print(f"Brier:    Market={test_brier_mkt:.4f} | S61 Native={test_brier_native:.4f} | Enhanced Blend={test_brier_pred:.4f}")
    print(f"ECE:      Market={test_ece_mkt:.4f} | S61 Native={test_ece_native:.4f} | Enhanced Blend={test_ece_pred:.4f}")

    # 5. Trading Backtest
    print("\n" + "=" * 60)
    print("SIMULATED VALUE TRADING PERFORMANCE ON TEST SET")
    print("=" * 60)
    print(f"{'Model':<15} | {'Min Edge':<8} | {'Bets':<5} | {'Wins':<5} | {'Win %':<6} | {'P&L ($)':<11} | {'ROI %':<8} | {'CLV %':<8}")
    print("-" * 75)

    for edge in [0.03, 0.05, 0.06]:
        # Enhanced Blend
        sim_enh = simulate_trading(test_rows, p_pred_test, min_edge=edge, min_odds=1.35, max_odds=4.00)
        print(f"{'Enhanced Blend':<15} | {edge*100:>4.1f}%   | {sim_enh['n_bets']:<5} | {sim_enh['wins']:<5} | {sim_enh['win_rate']*100:>5.1f}% | {sim_enh['total_pnl']:>+10.1f} | {sim_enh['roi']*100:>+5.2f}% | {sim_enh['mean_clv']*100:>+5.2f}%")

        # Native S61 Blend
        sim_nat = simulate_trading(test_rows, p_native_test, min_edge=edge, min_odds=1.35, max_odds=4.00)
        print(f"{'S61 Native':<15} | {edge*100:>4.1f}%   | {sim_nat['n_bets']:<5} | {sim_nat['wins']:<5} | {sim_nat['win_rate']*100:>5.1f}% | {sim_nat['total_pnl']:>+10.1f} | {sim_nat['roi']*100:>+5.2f}% | {sim_nat['mean_clv']*100:>+5.2f}%")

    # 6. Save Artifacts
    out_dir = ROOT / "models" / "blend"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save latest.json (S61 format)
    latest_path = out_dir / "latest.json"
    latest_dict = {
        "version": BLEND_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "data/historical_odds/training_dataset.jsonl",
        "snapshot": "close",
        "include_simulated": False,
        "devig": "power",
        "segments_version": seg.SEGMENTS_VERSION,
        "segments": list(BLEND_SEGMENTS),
        "prior_sd": DEFAULT_PRIOR_SD,
        "prior_mean": {"a": 0.0, "b": 1.0, "c": 0.0, "d": 0.0},
        "usable_rows": len(native_train_rows),
        "rows_with_model": len(native_train_rows),
        "matches_rated": len(native_train_rows),
        "excluded": {},
        "outcomes": dict(Counter(r.home_won for r in native_train_rows)),
        "fitted": native_model.fitted,
        "posterior": native_post,
        "test_metrics": {
            "n_test": len(native_test_rows),
            "log_loss_market": test_loss_mkt,
            "log_loss_blend": test_loss_native,
            "brier_market": test_brier_mkt,
            "brier_blend": test_brier_native,
            "ece_market": test_ece_mkt,
            "ece_blend": test_ece_native,
        },
    }
    with latest_path.open("w", encoding="utf-8") as f:
        json.dump(latest_dict, f, indent=2)

    # Save market_blend_v1.json and .joblib
    json_path = out_dir / "market_blend_v1.json"
    joblib_path = out_dir / "market_blend_v1.joblib"
    enh_dict = model.to_dict()
    enh_dict["generated_at"] = datetime.now(timezone.utc).isoformat()
    enh_dict["n_train"] = len(train_rows)
    enh_dict["n_test"] = len(test_rows)
    enh_dict["train_dates"] = [train_rows[0]["date"], train_rows[-1]["date"]]
    enh_dict["test_dates"] = [test_rows[0]["date"], test_rows[-1]["date"]]
    enh_dict["test_metrics"] = {
        "log_loss_market": test_loss_mkt,
        "log_loss_blend": test_loss_pred,
        "brier_market": test_brier_mkt,
        "brier_blend": test_brier_pred,
        "ece_market": test_ece_mkt,
        "ece_blend": test_ece_pred,
    }

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(enh_dict, f, indent=2)
    joblib.dump(model, joblib_path)

    print(f"\n[OK] Wrote S61 BlendModel artifact: {latest_path}")
    print(f"[OK] Wrote Enhanced MarketBlend artifact: {json_path}")
    print(f"[OK] Wrote Enhanced MarketBlend Joblib: {joblib_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
