"""S66 SRL feasibility: is a simulated (SRL) market beatable before any SRL model is built?

Reads an S58 training dataset (``matches.jsonl``, ``snapshots.jsonl``, ``manifest.json``) and answers four questions
for simulated matches only:

1. Closing (T-0) calibration: de-vigged market probability against the result, with Wilson 95% intervals and the
   fitted favourite-longshot line (``training.market.fit_fav_longshot``).
2. Margin: the booksum above one at the closing price (the overround), as a distribution.
3. Movement: how far the de-vigged home probability moves between T-60m and T-0.
4. In-play source gap: how often the in-play price comes from the listing or the match page, and how far the
   probability jumps when the source changes between consecutive in-play snapshots.

Verdict rule (written into the report, applied here):

* ``feasible`` only if there are at least 300 settled SRL matches with a closing price, AND the 95% confidence
  interval of the absolute calibration bias lies entirely above the median closing margin.
* Otherwise ``not feasible yet``, with the number of further settled SRL matches still needed. This is the larger of
  the shortfall to 300 and, when the point estimate of the bias exceeds the margin, the sample size at which the
  bias confidence interval would clear the margin (a point-estimate figure, so optimistic).

Usage::

    python scripts/srl_feasibility.py                    # latest dataset under data/training/
    python scripts/srl_feasibility.py --run-dir DIR      # a specific dataset
    python scripts/srl_feasibility.py --no-report        # print only, write no report file

The report is written to ``reports/research/srl_<YYYYMMDD>.md``. Reads files only: no database, no network.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training import market as mk  # noqa: E402

TRAINING_DIR = ROOT / "data" / "training"
REPORT_DIR = ROOT / "reports" / "research"
MIN_SETTLED_SRL = 300
N_BINS = 5
Z95 = mk._WILSON_Z  # two-sided 95%


# --------------------------------------------------------------------------- loading
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def latest_run_dir(training_dir: Path = TRAINING_DIR) -> Path:
    """Newest dataset directory (by name, which is a timestamp) that holds a snapshots file."""
    runs = sorted(p for p in training_dir.glob("*") if (p / "snapshots.jsonl").is_file())
    if not runs:
        raise FileNotFoundError(f"no S58 dataset found under {training_dir}")
    return runs[-1]


def load_run(run_dir: Path) -> dict[str, Any]:
    matches = _read_jsonl(run_dir / "matches.jsonl")
    snapshots = _read_jsonl(run_dir / "snapshots.jsonl")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    return {"matches": matches, "snapshots": snapshots, "manifest": manifest, "run_dir": str(run_dir)}


# --------------------------------------------------------------------------- market maths
def flat_book(book: dict[str, Any]) -> dict[str, Any]:
    """Map the nested S58 book (``book[side][back|lay]``) onto the flat keys ``training.market`` expects."""
    home = book.get("home") or {}
    away = book.get("away") or {}
    return {
        "back_home": home.get("back"),
        "lay_home": home.get("lay"),
        "back_away": away.get("back"),
        "lay_away": away.get("lay"),
    }


def closing_market_prob(book: dict[str, Any]) -> Optional[float]:
    """De-vigged home probability (module default method) or None when a runner has no usable price."""
    return mk.market_prob_home(flat_book(book))


def raw_booksum(book: dict[str, Any]) -> Optional[float]:
    """Sum of the raw mid implied probabilities of both runners, or None when a runner is unpriced."""
    flat = flat_book(book)
    ph = mk.implied(flat["back_home"], flat["lay_home"])
    pa = mk.implied(flat["back_away"], flat["lay_away"])
    if ph is None or pa is None or ph <= 0.0 or pa <= 0.0:
        return None
    return mk.overround(ph, pa) + 1.0


def _quantiles(values: list[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "p90": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "min": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(arr.max()),
    }


# --------------------------------------------------------------------------- analysis
def _index_prematch(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """match_id -> label -> snapshot, for the pre-match labels only."""
    index: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for snap in snapshots:
        if not snap.get("in_play"):
            index[snap["match_id"]][snap["label"]] = snap
    return index


def closing_rows(matches: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Settled simulated matches with a usable T-0 market probability, plus the accounting around them."""
    sim = {m["match_id"]: m for m in matches if m.get("is_simulated")}
    pre = _index_prematch(snapshots)
    settled = [mid for mid, m in sim.items() if m.get("home_won") in (0, 1)]
    rows: list[dict[str, Any]] = []
    missing_closing = 0
    for mid in sorted(settled):
        snap = pre.get(mid, {}).get("T-0")
        p = closing_market_prob(snap["book"]) if snap else None
        raw = raw_booksum(snap["book"]) if snap else None
        if p is None or raw is None:
            missing_closing += 1
            continue
        rows.append({
            "match_id": mid,
            "p_market": float(p),
            "outcome": int(sim[mid]["home_won"]),
            "margin": float(raw - 1.0),
        })
    return {
        "simulated_matches": len(sim),
        "settled_simulated": len(settled),
        "settled_without_closing_price": missing_closing,
        "rows": rows,
    }


def calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall closing calibration with a Wilson interval on the win rate and on the bias (observed - mean p)."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "note": "no settled simulated matches with a closing price"}
    p = np.asarray([r["p_market"] for r in rows], dtype=float)
    y = np.asarray([r["outcome"] for r in rows], dtype=int)
    successes = int(y.sum())
    obs = successes / n
    mean_p = float(p.mean())
    lo, hi = mk.wilson_interval(successes, n)
    bias = obs - mean_p
    bias_lo, bias_hi = lo - mean_p, hi - mean_p
    # Smallest absolute bias consistent with the interval: zero if the interval spans zero.
    if bias_lo <= 0.0 <= bias_hi:
        abs_bias_ci_low = 0.0
    else:
        abs_bias_ci_low = min(abs(bias_lo), abs(bias_hi))
    out: dict[str, Any] = {
        "n": n,
        "successes": successes,
        "observed_rate": obs,
        "observed_ci": [lo, hi],
        "mean_p_market": mean_p,
        "bias": bias,
        "bias_ci": [bias_lo, bias_hi],
        "abs_bias_ci_low": abs_bias_ci_low,
        "fav_longshot": None,
        "fav_longshot_note": None,
    }
    try:
        fit = mk.fit_fav_longshot(p, y, n_bins=N_BINS)
        out["fav_longshot"] = {
            "a": fit.a, "b": fit.b, "se_a": fit.se_a, "se_b": fit.se_b, "n": fit.n,
            "bins": [
                {"lo": b.lo, "hi": b.hi, "n": b.n, "mean_p_market": b.mean_p_market,
                 "observed_rate": b.observed_rate, "ci_low": b.ci_low, "ci_high": b.ci_high}
                for b in fit.bins
            ],
        }
    except (ValueError, RuntimeError) as exc:
        out["fav_longshot_note"] = f"not fitted: {exc}"
    return out


def move_t60_to_t0(matches: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Absolute and signed change in the de-vigged home probability between T-60m and T-0, simulated matches."""
    sim_ids = {m["match_id"] for m in matches if m.get("is_simulated")}
    pre = _index_prematch(snapshots)
    absolute: list[float] = []
    signed: list[float] = []
    for mid in sorted(sim_ids):
        a = pre.get(mid, {}).get("T-60m")
        b = pre.get(mid, {}).get("T-0")
        if not a or not b:
            continue
        pa, pb = closing_market_prob(a["book"]), closing_market_prob(b["book"])
        if pa is None or pb is None:
            continue
        signed.append(pb - pa)
        absolute.append(abs(pb - pa))
    return {
        "pairs": len(absolute),
        "abs_move": _quantiles(absolute),
        "mean_signed_move": float(np.mean(signed)) if signed else None,
    }


def inplay_source_gap(matches: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Source mix of in-play home prices and the probability jump when the source changes between snapshots.

    S58 keeps only the winning source per snapshot, so a same-time listing-vs-match-page gap cannot be read
    directly. A source switch between consecutive snapshots is used as the proxy.
    """
    sim_ids = {m["match_id"] for m in matches if m.get("is_simulated")}
    by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in snapshots:
        if snap.get("in_play") and snap["match_id"] in sim_ids:
            by_match[snap["match_id"]].append(snap)
    mix: Counter = Counter()
    switch_jumps: list[float] = []
    steady_moves: list[float] = []
    for snaps in by_match.values():
        snaps.sort(key=lambda s: s["t"])
        prev_p: Optional[float] = None
        prev_src: Optional[str] = None
        for snap in snaps:
            home = snap["book"].get("home") or {}
            src = home.get("source_back") or home.get("source_lay")
            mix[src or "none"] += 1
            p = closing_market_prob(snap["book"])
            if p is not None and prev_p is not None and src is not None and prev_src is not None:
                if src != prev_src:
                    switch_jumps.append(abs(p - prev_p))
                else:
                    steady_moves.append(abs(p - prev_p))
            if p is not None:
                prev_p, prev_src = p, src
    return {
        "in_play_snapshots": sum(len(s) for s in by_match.values()),
        "source_mix": dict(mix),
        "switch_jump": _quantiles(switch_jumps),
        "steady_move": _quantiles(steady_moves),
    }


def verdict(n_settled: int, bias: Optional[float], abs_bias_ci_low: Optional[float], mean_p: Optional[float],
            median_margin: Optional[float]) -> dict[str, Any]:
    """Apply the S66 verdict rule. Pure: all inputs are numbers from :func:`analyse`."""
    if n_settled < MIN_SETTLED_SRL or median_margin is None or abs_bias_ci_low is None:
        feasible = False
    else:
        feasible = abs_bias_ci_low > median_margin
    floor_short = max(0, MIN_SETTLED_SRL - n_settled)
    effect_need: Optional[int] = None
    if (bias is not None and median_margin is not None and mean_p is not None and 0.0 < mean_p < 1.0
            and abs(bias) > median_margin):
        n_total = (Z95 * math.sqrt(mean_p * (1.0 - mean_p)) / (abs(bias) - median_margin)) ** 2
        effect_need = max(0, math.ceil(n_total) - n_settled)
    if feasible:
        label, needed = "feasible", 0
    else:
        label = "not feasible yet"
        candidates = [floor_short] + ([effect_need] if effect_need is not None else [])
        needed = max(candidates) if max(candidates) > 0 else None
    return {
        "verdict": label,
        "settled_srl_matches": n_settled,
        "required_settled_srl_matches": MIN_SETTLED_SRL,
        "further_matches_needed": needed,
        "floor_shortfall": floor_short,
        "effect_based_need": effect_need,
        "median_margin": median_margin,
        "abs_bias_ci_low": abs_bias_ci_low,
    }


def analyse(matches: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    closing = closing_rows(matches, snapshots)
    rows = closing["rows"]
    cal = calibration(rows)
    margins = [r["margin"] for r in rows]
    median_margin = statistics.median(margins) if margins else None
    all_sim_margins: list[float] = []
    pre = _index_prematch(snapshots)
    sim_ids = {m["match_id"] for m in matches if m.get("is_simulated")}
    for mid in sim_ids:
        t0 = pre.get(mid, {}).get("T-0")
        if t0:
            raw = raw_booksum(t0["book"])
            if raw is not None:
                all_sim_margins.append(raw - 1.0)
    result = {
        "matches_total": len(matches),
        "simulated_matches": closing["simulated_matches"],
        "settled_simulated": closing["settled_simulated"],
        "settled_without_closing_price": closing["settled_without_closing_price"],
        "closing_sample": cal["n"],
        "calibration": cal,
        "margin_settled_closing": _quantiles(margins),
        "margin_all_simulated_closing": _quantiles(all_sim_margins),
        "move_t60_to_t0": move_t60_to_t0(matches, snapshots),
        "inplay_source_gap": inplay_source_gap(matches, snapshots),
    }
    result["verdict"] = verdict(
        n_settled=cal["n"],
        bias=cal.get("bias"),
        abs_bias_ci_low=cal.get("abs_bias_ci_low"),
        mean_p=cal.get("mean_p_market"),
        median_margin=median_margin,
    )
    return result


# --------------------------------------------------------------------------- report
def _pct(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{digits}f}%"


def _num(x: Optional[float], digits: int = 4) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _q_line(name: str, q: dict[str, Any]) -> str:
    if not q.get("n"):
        return f"- {name}: no observations"
    return (f"- {name} (n={q['n']}): min {_pct(q['min'], 2)}, p25 {_pct(q['p25'], 2)}, "
            f"median {_pct(q['median'], 2)}, p75 {_pct(q['p75'], 2)}, p90 {_pct(q['p90'], 2)}, "
            f"max {_pct(q['max'], 2)}")


def render_report(result: dict[str, Any], dataset: dict[str, Any], run_date: date) -> str:
    cal = result["calibration"]
    v = result["verdict"]
    man = dataset.get("manifest") or {}
    lines: list[str] = []
    add = lines.append
    add(f"# S66 SRL feasibility - {run_date.isoformat()}")
    add("")
    add("Separate track on simulated (SRL) matches. Question: is the SRL market beatable before any SRL model is "
        "built? Source: S58 training dataset, read from files only (no database, no Docker, no writes).")
    add("")
    add(f"- Dataset: `{dataset['run_dir']}`")
    if man:
        add(f"- Dataset generated: {man.get('generated_at_utc', 'n/a')} at git sha {man.get('git_sha', 'n/a')}")
    add(f"- Matches in dataset: {result['matches_total']} ({result['simulated_matches']} simulated)")
    add("")
    add("## Verdict")
    add("")
    add(f"**{v['verdict']}**")
    add("")
    add(f"- Settled simulated matches with a closing (T-0) price: {v['settled_srl_matches']} "
        f"(required: {v['required_settled_srl_matches']})")
    if v["further_matches_needed"] is None:
        add("- Further settled SRL matches needed: none estimated at the current effect size")
    else:
        add(f"- Further settled SRL matches needed (at least): {v['further_matches_needed']}")
    add(f"- Shortfall to the {MIN_SETTLED_SRL}-match floor: {v['floor_shortfall']}; "
        f"effect-based estimate: {v['effect_based_need'] if v['effect_based_need'] is not None else 'n/a'}")
    add(f"- Median closing margin: {_pct(v['median_margin'], 2)}; "
        f"smallest absolute bias consistent with the 95% CI: {_pct(v['abs_bias_ci_low'], 2)}")
    add("")
    add("### Verdict rule")
    add("")
    add("- `feasible` only if there are at least 300 settled SRL matches with a closing price AND the 95% confidence "
        "interval of the absolute calibration bias (observed win rate minus mean de-vigged closing probability, "
        "Wilson interval) lies entirely above the median closing margin.")
    add("- Otherwise `not feasible yet`, with the number of further settled SRL matches still needed: the larger of "
        "the shortfall to 300 and, when the point estimate of the bias exceeds the margin, the sample size at which "
        "the bias interval would clear the margin. That sample size is optimistic because it uses the point estimate.")
    add("- Margin is the booksum above one at the closing price (overround of the mid implied probabilities). "
        "De-vig method: power (module default).")
    add("")
    add("## Closing calibration (T-0, settled simulated matches)")
    add("")
    if cal["n"] == 0:
        add(f"No settled simulated match has a usable closing price ({cal.get('note', '')}).")
    else:
        add(f"- n = {cal['n']}, wins for home = {cal['successes']}, observed rate {_pct(cal['observed_rate'])} "
            f"(Wilson 95% CI {_pct(cal['observed_ci'][0])} to {_pct(cal['observed_ci'][1])})")
        add(f"- Mean de-vigged closing probability {_pct(cal['mean_p_market'])}")
        add(f"- Calibration bias (observed - mean p): {_pct(cal['bias'], 2)}, "
            f"95% CI {_pct(cal['bias_ci'][0], 2)} to {_pct(cal['bias_ci'][1], 2)}")
        fl = cal.get("fav_longshot")
        if fl:
            add(f"- Favourite-longshot line logit p_true = a + b * logit p_market: "
                f"a = {_num(fl['a'])} (se {_num(fl['se_a'])}), b = {_num(fl['b'])} (se {_num(fl['se_b'])}); "
                f"a = 0 and b = 1 means calibrated")
            add("")
            add("| bin | n | mean p_market | observed | Wilson 95% CI |")
            add("|---|---|---|---|---|")
            for b in fl["bins"]:
                add(f"| {b['lo']:.2f}-{b['hi']:.2f} | {b['n']} | {_pct(b['mean_p_market'])} | "
                    f"{_pct(b['observed_rate'])} | {_pct(b['ci_low'])} to {_pct(b['ci_high'])} |")
        else:
            add(f"- Favourite-longshot line {cal.get('fav_longshot_note', 'not fitted')}")
    add("")
    add("## Margin")
    add("")
    add(_q_line("Settled simulated closing margin", result["margin_settled_closing"]))
    add(_q_line("All simulated closing margins (no result needed)", result["margin_all_simulated_closing"]))
    add("")
    add("## Movement between T-60m and T-0")
    add("")
    mv = result["move_t60_to_t0"]
    add(f"- Matches with both prices: {mv['pairs']}")
    if mv["pairs"]:
        add(_q_line("Absolute change in de-vigged home probability", mv["abs_move"]))
        add(f"- Mean signed change (T-0 minus T-60m): {_pct(mv['mean_signed_move'], 2)}")
    add("")
    add("## In-play source gap (listing vs match page)")
    add("")
    ip = result["inplay_source_gap"]
    add(f"- In-play snapshots, simulated matches: {ip['in_play_snapshots']}")
    add(f"- Home price source mix: {ip['source_mix'] or 'none'}")
    add(_q_line("Probability jump at a source switch", ip["switch_jump"]))
    add(_q_line("Probability move with the same source", ip["steady_move"]))
    add("- The dataset keeps only the winning source per snapshot, so a same-time gap is not observable here. "
        "A switch between consecutive snapshots is the proxy.")
    add("")
    add("## Limitations")
    add("")
    add(f"- {result['settled_without_closing_price']} settled simulated match(es) have no usable closing price and "
        "are excluded from calibration.")
    add("- Settled simulated matches are few; calibration and margin are reported as observed, not as a model.")
    add("- No simulated-match model is proposed by this report. Any SRL model needs a verdict of `feasible` first.")
    add("")
    return "\n".join(lines)


def console_summary(result: dict[str, Any]) -> str:
    v = result["verdict"]
    cal = result["calibration"]
    needed = v["further_matches_needed"]
    lines = [
        f"SRL feasibility verdict: {v['verdict']}",
        f"  simulated matches: {result['simulated_matches']} (settled {result['settled_simulated']}, "
        f"with closing price {cal['n']})",
        f"  closing observed rate {_pct(cal.get('observed_rate'))} vs mean p {_pct(cal.get('mean_p_market'))}, "
        f"bias {_pct(cal.get('bias'), 2)}",
        f"  median closing margin {_pct(v['median_margin'], 2)}, abs-bias CI low {_pct(v['abs_bias_ci_low'], 2)}",
        f"  further settled SRL matches needed: {'none estimated' if needed is None else needed}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S66 SRL feasibility on an S58 dataset (read-only).")
    parser.add_argument("--run-dir", default=None, help="dataset directory (default: latest under data/training)")
    parser.add_argument("--date", default=None, help="report date YYYYMMDD (default: today)")
    parser.add_argument("--no-report", action="store_true", help="print only; write no report file")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else latest_run_dir()
    dataset = load_run(run_dir)
    run_date = date.fromisoformat(f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}") if args.date else date.today()

    result = analyse(dataset["matches"], dataset["snapshots"])
    report = render_report(result, dataset, run_date)
    print(report)
    print("")
    print(console_summary(result))
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"srl_{run_date.strftime('%Y%m%d')}.md"
        out.write_text(report + "\n", encoding="utf-8")
        print(f"\nreport written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
