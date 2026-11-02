"""Compare the v2 fair-value model with the Betfair market on the real-price corpus (S38, rung R1).

Usage:
    python scripts/eval_fair_value_vs_market.py --model models/fair_value/t20_v2_all.joblib \
        --real-dir data/real/aligned --data-root data

For every real episode (one Betfair market) the model needs the Cricsheet match it belongs to. That link is the
``cricsheet_match_id`` field written by the S36 alignment; episodes without it are counted and skipped. Pre-match
features are as of the match date (information the day before). A model is only evaluated on markets that start
after its ``trained_through`` date.

Segments: family x phase (pre-match, powerplay, middle, death) x competition tier (0 international, 1 club,
2 other, plus "all"). Per segment: model and market Brier, log loss and ECE, and the paired difference in Brier
(model minus market) with a 95 % bootstrap CI that resamples whole markets (episodes), because ticks inside one
market are correlated. Market probability is the de-vigged back/lay mid when both sides exist, else the de-vigged
last-traded prices. A tick with no market price is excluded from both sides.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_real_price_corpus import load_real_corpus  # noqa: E402
from scripts.train_fair_value import FAMILY_DIRS, ece, iter_cricsheet, log_loss, brier  # noqa: E402
from strategy.fair_value import FairValueModel  # noqa: E402
from strategy.ratings import CricketRatings  # noqa: E402

PHASES = ("pre", "powerplay", "middle", "death")
TIERS = ("0", "1", "2", "all")
PHASE_OVERS = {"t20": (6.0, 15.0), "odi": (10.0, 40.0)}
STATE_KEYS = ("score", "wickets", "overs", "innings", "balls_remaining", "run_rate", "req_run_rate")
PM_SIDE_PAIRS = (("pm_elo_home", "pm_elo_away"), ("pm_xi_home", "pm_xi_away"))
CAVEATS = [
    "Only markets linked to a Cricsheet match (cricsheet_match_id, S36) are evaluated.",
    "Pre-match features are as of the match date; the model must have been trained on data before each market.",
    "Market price: de-vigged back/lay mid when both sides exist, else de-vigged last-traded prices; ticks with no "
    "market price are excluded from both sides.",
    "Cricsheet 'home' is an arbitrary label; the ratings are re-oriented to the real market's home side by team name.",
]


def _implied(back: Any, lay: Any) -> Optional[float]:
    b = float(back or 0.0)
    l = float(lay or 0.0)
    if b > 1.0 and l > 1.0:
        return 1.0 / ((b + l) / 2.0)
    return None


def market_home_prob(tick: Dict[str, Any]) -> Optional[float]:
    """De-vigged probability that the market's home side wins, or None when no price is available."""
    h = _implied(tick.get("back_home"), tick.get("lay_home"))
    a = _implied(tick.get("back_away"), tick.get("lay_away"))
    if h is None or a is None:
        ltp_h = float(tick.get("ltp_home") or 0.0)
        ltp_a = float(tick.get("ltp_away") or 0.0)
        h = 1.0 / ltp_h if ltp_h > 1.0 else None
        a = 1.0 / ltp_a if ltp_a > 1.0 else None
    if h is None or a is None or h + a <= 0:
        return None
    return h / (h + a)


def phase_of(tick: Dict[str, Any], family: str) -> Optional[str]:
    if not tick.get("in_play"):
        return "pre"
    if family not in PHASE_OVERS:
        return None
    overs = float(tick.get("overs") or 0.0)
    pp_end, middle_end = PHASE_OVERS[family]
    if overs < pp_end:
        return "powerplay"
    if overs < middle_end:
        return "middle"
    return "death"


def orient_prematch(pm: Dict[str, float], row_home_is_real_home: bool) -> Dict[str, float]:
    """Re-orient Cricsheet-oriented pre-match fields to the real market's home side."""
    if row_home_is_real_home:
        return dict(pm)
    out = dict(pm)
    for home_key, away_key in PM_SIDE_PAIRS:
        out[home_key], out[away_key] = pm[away_key], pm[home_key]
    out["pm_toss_home"] = -pm["pm_toss_home"]
    return out


def paired_bootstrap_ci(
    d_sq: np.ndarray, episode_ids: np.ndarray, n_boot: int, rng: np.random.Generator, alpha: float = 0.05
) -> Tuple[Optional[float], Optional[float]]:
    """Percentile CI of the mean per-tick Brier difference, resampling whole episodes."""
    if len(d_sq) == 0:
        return None, None
    _, inv = np.unique(episode_ids, return_inverse=True)
    sums = np.bincount(inv, weights=d_sq)
    counts = np.bincount(inv).astype(float)
    n_ep = len(sums)
    if n_ep < 2:
        return None, None
    idx = rng.integers(0, n_ep, size=(n_boot, n_ep))
    stats = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(stats, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lo), float(hi)


def _metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    return {"brier": brier(p, y), "log_loss": log_loss(p, y), "ece": ece(p, y)}


def _segment(records: Sequence[Tuple[str, str, int, float, float, int]], family: str, phase: str, tier: str,
             n_boot: int, rng: np.random.Generator, min_episodes: int) -> Dict[str, Any]:
    """Metrics for one segment. ``records`` are (phase, tier, episode, p_model, p_market, y) tuples."""
    picked = [r for r in records if (phase == "all" or r[0] == phase) and (tier == "all" or r[1] == tier)]
    out: Dict[str, Any] = {"family": family, "phase": phase, "tier": tier,
                           "n_ticks": len(picked), "n_episodes": len({r[2] for r in picked})}
    if not picked:
        out.update({"model": None, "market": None, "brier_diff_model_minus_market": None,
                    "brier_diff_ci95": [None, None], "status": "no_data"})
        return out
    p_model = np.asarray([r[3] for r in picked])
    p_mkt = np.asarray([r[4] for r in picked])
    y = np.asarray([r[5] for r in picked], dtype=float)
    ep_ids = np.asarray([r[2] for r in picked])
    d_sq = (p_model - y) ** 2 - (p_mkt - y) ** 2
    lo, hi = paired_bootstrap_ci(d_sq, ep_ids, n_boot, rng)
    out.update({
        "model": _metrics(p_model, y),
        "market": _metrics(p_mkt, y),
        "brier_diff_model_minus_market": float(np.mean(d_sq)),
        "brier_diff_ci95": [lo, hi],
        "status": "ok" if out["n_episodes"] >= min_episodes else "insufficient_episodes",
    })
    return out


def _manifest_links(real_dir: Path) -> Dict[str, str]:
    """market_id -> cricsheet_match_id from the corpus manifest (written by the S36 alignment)."""
    manifest = json.loads((real_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        str(e["market_id"]): str(e["cricsheet_match_id"])
        for e in manifest.get("matched", []) + manifest.get("accepted", [])  # S36 writes "matched"
        if e.get("cricsheet_match_id")
    }


def evaluate(
    model_file: Path,
    real_dir: Path,
    data_root: Path,
    *,
    n_boot: int = 1000,
    seed: int = 0,
    min_episodes: int = 20,
) -> Dict[str, Any]:
    model = FairValueModel.load(model_file)
    if model.version != "v2":
        raise ValueError(f"{model_file} is a {model.version} model; the market comparison needs v2")
    if not model.trained_through:
        raise ValueError(f"{model_file} has no trained_through date; refusing to evaluate")
    family = model.family
    stats: Counter = Counter()
    dirs = [data_root / d for d in FAMILY_DIRS[family]]
    ratings = CricketRatings(lambda: iter_cricsheet(dirs, family, stats))
    episodes = load_real_corpus(out=real_dir)
    links = _manifest_links(real_dir)

    records: List[Tuple[str, str, int, float, float, int]] = []  # (phase, tier, episode, p_model, p_mkt, y)
    for ep_idx, ep in enumerate(episodes):
        if ep.get("family") != family:
            stats["other_family"] += 1
            continue
        cid = links.get(str(ep.get("market_id")))
        if not cid:
            stats["unlinked_no_cricsheet_match_id"] += 1
            continue
        row = ratings.rows.get(str(cid))
        if row is None:
            stats["cricsheet_match_not_found"] += 1
            continue
        start = str(ep.get("start") or "")[:10]
        if start <= model.trained_through:
            stats["before_model_trained_through"] += 1
            continue
        ticks = ep.get("ticks") or []
        if not ticks:
            stats["no_ticks"] += 1
            continue
        meta = ticks[0].get("_meta") or {}
        real_home = str(ep.get("team_home") or "")
        if real_home == row.home:
            home_is_real = True
        elif real_home == row.away:
            home_is_real = False
        else:
            stats["team_name_mismatch"] += 1
            continue
        if meta.get("winner_is_home") is not None:
            y = int(bool(meta["winner_is_home"]))
        elif meta.get("winner"):
            y = int(meta["winner"] == real_home)
        else:
            stats["no_result"] += 1
            continue
        pm = orient_prematch(ratings.pre_match(row.match_id, start), home_is_real)
        feature_ticks: List[Dict[str, Any]] = []
        kept: List[Tuple[Dict[str, Any], str, float]] = []
        for tick in ticks:
            phase = phase_of(tick, family)
            if phase is None:
                stats["tick_phase_unknown"] += 1
                continue
            if tick.get("in_play") and any(tick.get(k) is None for k in STATE_KEYS):
                stats["tick_missing_state"] += 1
                continue
            p_mkt = market_home_prob(tick)
            if p_mkt is None:
                stats["tick_no_market_price"] += 1
                continue
            feature_ticks.append({**tick, **pm, "team_home": real_home, "team_away": str(ep.get("team_away") or "")})
            kept.append((tick, phase, p_mkt))
        if not kept:
            stats["episode_no_usable_ticks"] += 1
            continue
        p_model = model.predict_batch(feature_ticks)
        tier = str(int(row.tier))
        for (tick, phase, p_mkt), pmod in zip(kept, p_model):
            records.append((phase, tier, ep_idx, float(pmod), float(p_mkt), y))
        stats["episodes_used"] += 1

    rng = np.random.default_rng(seed)
    segments: List[Dict[str, Any]] = []
    for phase in PHASES + ("all",):
        for tier in TIERS:
            seg = _segment(records, family, phase, tier, n_boot, rng, min_episodes)
            if seg["n_ticks"] or phase != "all":
                segments.append(seg)
    return {
        "model": str(model_file),
        "family": family,
        "model_version": model.version,
        "model_trained_through": model.trained_through,
        "real_dir": str(real_dir),
        "phase_rule": {"pre": "in_play false", "overs_thresholds": PHASE_OVERS[family] if family in PHASE_OVERS else None},
        "bootstrap": {"n": n_boot, "seed": seed, "unit": "market (episode)", "ci": "95% percentile"},
        "min_episodes": min_episodes,
        "counts": dict(stats),
        "segments": segments,
        "caveats": CAVEATS,
        "rung": "R1",
    }


def _fmt(x: Optional[float], digits: int = 4) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def to_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# Fair value v2 vs market ({report['family']}, rung R1)",
        "",
        f"Model trained through {report['model_trained_through']}. Negative brier diff means the model is closer to "
        "the outcome than the market.",
        "",
        "| segment | tier | episodes | ticks | model Brier | market Brier | diff (model-market) | 95% CI | model LL | market LL | model ECE | market ECE | status |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["segments"]:
        m, k = s.get("model") or {}, s.get("market") or {}
        ci = s.get("brier_diff_ci95") or [None, None]
        ci_txt = "-" if ci[0] is None else f"[{ci[0]:.4f}, {ci[1]:.4f}]"
        lines.append(
            f"| {s['phase']} | {s['tier']} | {s['n_episodes']} | {s['n_ticks']} | {_fmt(m.get('brier'))} | "
            f"{_fmt(k.get('brier'))} | {_fmt(s.get('brier_diff_model_minus_market'))} | {ci_txt} | "
            f"{_fmt(m.get('log_loss'))} | {_fmt(k.get('log_loss'))} | {_fmt(m.get('ece'))} | {_fmt(k.get('ece'))} | "
            f"{s['status']} |"
        )
    lines += ["", "Caveats:"] + [f"- {c}" for c in report["caveats"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--real-dir", default="data/real/aligned")
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--out-json", default="docs/reports/fair_value_vs_market.json")
    ap.add_argument("--out-md", default="docs/reports/fair_value_vs_market.md")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-episodes", type=int, default=20)
    args = ap.parse_args()
    report = evaluate(Path(args.model), Path(args.real_dir), Path(args.data_root), n_boot=args.bootstrap,
                      seed=args.seed, min_episodes=args.min_episodes)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = to_markdown(report)
    Path(args.out_md).write_text(md, encoding="utf-8")
    print(md)
    print(f"json: {out_json}")


if __name__ == "__main__":
    main()
