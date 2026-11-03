"""S63 evaluation harness: the referee for every probability source and policy.

A probability source is a set of rows ``{match_id, snapshot, p_home, p_sd?}`` where ``snapshot`` is a
``snapshot_id`` from the S58 dataset. The market itself is a source (:func:`market_predictions`), so a source equal to
the market must show exactly zero difference.

For each arena (pre-match and in-play) and each pre-registered segment the harness reports:

- log loss, Brier and ECE of the source against the de-vigged market at the same snapshot, with a paired bootstrap
  CI of the log-loss difference (resampled by match, seeded);
- a policy simulation: one bet at most per match, sized by ``strategy.staking`` on the book 60 s after the signal,
  filled at the takeable price, settled on ``home_won``, with CLV (``shared.metrics.clv_prob``) against the binding
  reference and simulated P&L, both with bootstrap CIs;
- the R1 and R2 rung status (``pass`` / ``fail`` / ``insufficient``) under the thresholds of the plan section 4.

Pure functions except :func:`load_dataset` and :func:`write_report`. No database, no network, no Docker.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from shared.metrics import clv_prob
from strategy.staking import select, stake
from training import segments as seg
from training.market import DEFAULT_DEVIG, market_prob_home

EVAL_VERSION = "1.0.0"
N_BOOT = 2000
BOOT_SEED = 20261103
BANKROLL = 1000.0
ECE_BINS = 10
PROB_EPS = 1e-9
R1_MIN_MATCHES = 200
R2_MIN_MATCHES = 150
ARENAS = ("prematch", "inplay")
BINDING_CLOSE = {"prematch": "last price before the off (T-0)", "inplay": "same side 10 min after (book_10m)"}


# --------------------------------------------------------------------------- dataset and sources
def load_dataset(dataset_dir: Path) -> dict[str, Any]:
    """Read ``manifest.json``, ``matches.jsonl`` and ``snapshots.jsonl`` from an S58 dataset directory."""
    directory = Path(dataset_dir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    matches = _read_jsonl(directory / "matches.jsonl")
    snapshots = _read_jsonl(directory / "snapshots.jsonl")
    return {"dir": str(directory), "manifest": manifest, "matches": matches, "snapshots": snapshots}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def arena_of(snapshot: Mapping[str, Any]) -> str:
    return "inplay" if snapshot.get("in_play") else "prematch"


def flat_book(book: Mapping[str, Any]) -> dict[str, Any]:
    """S58 nested snapshot book to the flat tick keys that ``training.market`` reads (``back_home`` and so on)."""
    return {
        "back_home": book["home"]["back"],
        "lay_home": book["home"]["lay"],
        "back_away": book["away"]["back"],
        "lay_away": book["away"]["lay"],
    }


def market_predictions(dataset: Mapping[str, Any], method: str = DEFAULT_DEVIG) -> list[dict[str, Any]]:
    """The market as a probability source: its de-vigged home probability at every snapshot with prices."""
    rows = []
    for snap in dataset["snapshots"]:
        p = market_prob_home(flat_book(snap["book"]), method)
        if p is None:
            continue
        rows.append({"match_id": snap["match_id"], "snapshot": snap["snapshot_id"], "p_home": p, "p_sd": 0.0})
    return rows


def validate_predictions(
    predictions: Sequence[Mapping[str, Any]],
    snapshots_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject malformed rows, unknown snapshots, mismatched matches and duplicate (snapshot) keys."""
    seen: set[str] = set()
    for i, row in enumerate(predictions):
        for key in ("match_id", "snapshot", "p_home"):
            if row.get(key) is None:
                raise ValueError(f"prediction row {i} is missing {key!r}")
        snapshot_id = str(row["snapshot"])
        if snapshot_id not in snapshots_by_id:
            raise ValueError(f"prediction row {i}: unknown snapshot {snapshot_id!r}")
        if str(snapshots_by_id[snapshot_id]["match_id"]) != str(row["match_id"]):
            raise ValueError(f"prediction row {i}: snapshot {snapshot_id!r} belongs to another match")
        p = float(row["p_home"])
        if not (math.isfinite(p) and 0.0 <= p <= 1.0):
            raise ValueError(f"prediction row {i}: p_home must be in [0, 1], got {row['p_home']!r}")
        p_sd = row.get("p_sd")
        if p_sd is not None and not (math.isfinite(float(p_sd)) and float(p_sd) >= 0.0):
            raise ValueError(f"prediction row {i}: p_sd must be finite and >= 0, got {p_sd!r}")
        if snapshot_id in seen:
            raise ValueError(f"prediction row {i}: duplicate snapshot {snapshot_id!r}")
        seen.add(snapshot_id)


# --------------------------------------------------------------------------- metrics
def log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row binary log loss, with probabilities clipped away from 0 and 1."""
    q = np.clip(np.asarray(p, dtype=float), PROB_EPS, 1.0 - PROB_EPS)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))


def brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2


def calibration(p: np.ndarray, y: np.ndarray, n_bins: int = ECE_BINS) -> dict[str, Any]:
    """Expected calibration error and equal-width reliability bins."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    n = p.size
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    bins = []
    ece = 0.0
    for i in range(n_bins):
        mask = idx == i
        count = int(mask.sum())
        if count == 0:
            bins.append({"lo": float(edges[i]), "hi": float(edges[i + 1]), "n": 0,
                         "mean_p": None, "observed": None})
            continue
        mean_p = float(p[mask].mean())
        observed = float(y[mask].mean())
        ece += count / n * abs(mean_p - observed)
        bins.append({"lo": float(edges[i]), "hi": float(edges[i + 1]), "n": count,
                     "mean_p": mean_p, "observed": observed})
    return {"ece": float(ece) if n else None, "bins": bins}


def _bootstrap(values: np.ndarray, group: np.ndarray, n_groups: int, mode: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Paired bootstrap resampling whole groups (matches) with replacement; 95% percentile interval.

    ``mode="mean"`` returns the ratio of sums to counts (mean per row); ``mode="sum"`` returns the total.
    Groups with no rows still take part in the resampling, so the interval reflects every held-out match.
    """
    if n_groups == 0:
        return None, None, None
    sums = np.bincount(group, weights=values, minlength=n_groups) if values.size else np.zeros(n_groups)
    counts = np.bincount(group, minlength=n_groups).astype(float) if values.size else np.zeros(n_groups)
    total_sum, total_count = float(sums.sum()), float(counts.sum())
    if mode == "sum":
        point = total_sum
    else:
        point = total_sum / total_count if total_count > 0 else None
    rng = np.random.default_rng(BOOT_SEED)
    draws = rng.integers(0, n_groups, size=(N_BOOT, n_groups))
    s = sums[draws].sum(axis=1)
    if mode == "sum":
        stats = s
    else:
        c = counts[draws].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            stats = np.where(c > 0, s / np.where(c > 0, c, 1.0), np.nan)
    if np.all(np.isnan(stats)):
        return point, None, None
    lo, hi = np.nanpercentile(stats, [2.5, 97.5])
    return point, float(lo), float(hi)


# --------------------------------------------------------------------------- evaluation rows
def build_eval_rows(dataset: Mapping[str, Any], predictions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Join source predictions with the market at the same snapshot and the match outcome.

    A row is kept only when the match is settled (``home_won`` is 0 or 1) and the market has a usable home
    probability at the snapshot. Rows keep a reference to their snapshot for the policy simulation.
    """
    matches = {str(m["match_id"]): m for m in dataset["matches"]}
    snapshots = {s["snapshot_id"]: s for s in dataset["snapshots"]}
    validate_predictions(predictions, snapshots)
    rows = []
    for pred in predictions:
        snap = snapshots[str(pred["snapshot"])]
        match = matches.get(str(snap["match_id"]))
        if match is None or match.get("home_won") not in (0, 1):
            continue
        p_mkt = market_prob_home(flat_book(snap["book"]), DEFAULT_DEVIG)
        if p_mkt is None:
            continue
        row = {
            "match_id": str(snap["match_id"]),
            "snapshot_id": snap["snapshot_id"],
            "arena": arena_of(snap),
            "t": snap["t"],
            "p_home": float(pred["p_home"]),
            "p_sd": float(pred.get("p_sd") or 0.0),
            "p_market": float(p_mkt),
            "home_won": int(match["home_won"]),
            "family": match.get("family"),
            "competition": match.get("competition"),
            "is_simulated": bool(match.get("is_simulated")),
            "match_start_time": match.get("match_start_time"),
            "snapshot": snap,
        }
        row["segments"] = seg.segments_for(row)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- policy simulation
def _fill_book(book_60s: Mapping[str, Any]) -> dict[str, Any]:
    """The book in the key convention of ``strategy.staking.select`` with takeable prices.

    A BACK fills at the runner's back price and a LAY at its lay price (``market.takeable``), so the keys map
    straight across.
    """
    return {
        "back_home": book_60s["home"]["back"],
        "lay_home": book_60s["home"]["lay"],
        "back_away": book_60s["away"]["back"],
        "lay_away": book_60s["away"]["lay"],
    }


def _fill_price(side: str, book: Mapping[str, Any], selection: str) -> Optional[float]:
    """Takeable decimal price for ``side`` on ``selection`` in a snapshot book, or None."""
    leg = book.get(selection)
    if not leg:
        return None
    price = leg["back"] if side == "BACK" else leg["lay"]
    if price is None or float(price) <= 1.0:
        return None
    return float(price)


def _settle(side: str, selection: str, price: float, stake_amount: float, home_won: int) -> float:
    selection_won = (selection == "home") == (home_won == 1)
    if side == "BACK":
        return stake_amount * (price - 1.0) if selection_won else -stake_amount
    return -stake_amount * (price - 1.0) if selection_won else stake_amount


def simulate_policy(rows: Sequence[Mapping[str, Any]], snapshots_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One bet at most per match and arena, at the first snapshot where ``strategy.staking`` says bet.

    Probabilities come from the source at the decision snapshot; prices from the book 60 s later. Stakes are sized
    on a fixed bankroll with the default caps, and per-day exposure accumulates in match-start order.
    """
    bets: list[dict[str, Any]] = []
    day_risk: dict[str, float] = defaultdict(float)
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[(row["arena"], row["match_id"])].append(row)
    ordered = sorted(
        by_key.items(),
        key=lambda item: (item[0][0], str(item[1][0]["match_start_time"] or ""), item[0][1]),
    )
    for (arena, match_id), group in ordered:
        for row in sorted(group, key=lambda r: r["t"]):
            selected = select(
                row["p_home"], row["p_sd"], _fill_book(row["snapshot"]["book_60s"]),
                segment=seg.POLICY_SEGMENT, min_edge_by_segment=seg.policy_min_edges(),
            )
            if selected is None:
                continue
            p_sel = row["p_home"] if selected.selection == "home" else 1.0 - row["p_home"]
            day = str(row["match_start_time"] or "")[:10] or "unknown"
            amount = stake(p_sel, row["p_sd"], selected.price, selected.side, BANKROLL,
                           open_day_exposure=day_risk[day])
            if amount <= 0.0:
                continue
            risk = amount if selected.side == "BACK" else amount * (selected.price - 1.0)
            day_risk[day] += risk
            if arena == "prematch":
                close_snap = snapshots_by_id.get(f"{match_id}:T-0")  # last book before the off
                close_book = close_snap["book"] if close_snap else None
            else:
                close_book = row["snapshot"]["book_10m"]  # same side ten minutes after the signal
            close_price = _fill_price(selected.side, close_book, selected.selection) if close_book else None
            clv = clv_prob(selected.side, selected.price, close_price) if close_price is not None else None
            bets.append({
                "match_id": match_id,
                "snapshot_id": row["snapshot_id"],
                "arena": arena,
                "segments": row["segments"],
                "side": selected.side,
                "selection": selected.selection,
                "price": selected.price,
                "p_selection": p_sel,
                "stake": amount,
                "risk": risk,
                "pnl": _settle(selected.side, selected.selection, selected.price, amount, row["home_won"]),
                "clv": clv,
            })
            break  # one bet per match and arena
    return bets


# --------------------------------------------------------------------------- summaries
def _ci(point: Optional[float], lo: Optional[float], hi: Optional[float]) -> dict[str, Optional[float]]:
    return {"point": point, "lo": lo, "hi": hi}


def _r1(matches: int, diff: dict[str, Optional[float]]) -> dict[str, Any]:
    needed = max(0, R1_MIN_MATCHES - matches)
    if matches < R1_MIN_MATCHES:
        status = "insufficient"
    elif diff["lo"] is None:
        status = "fail"
    elif diff["hi"] < 0.0:
        status = "pass"
    else:
        status = "fail"
    return {"status": status, "threshold_matches": R1_MIN_MATCHES, "matches_needed": needed,
            "logloss_diff": diff, "rule": "source log loss < market, paired 95% CI entirely below 0"}


def _r2(matches: int, bets: int, clv: dict[str, Optional[float]], pnl: dict[str, Optional[float]],
        clv_n: int) -> dict[str, Any]:
    needed = max(0, R2_MIN_MATCHES - matches)
    if matches < R2_MIN_MATCHES:
        status = "insufficient"
    elif bets == 0 or clv_n == 0:
        status = "fail" if bets == 0 else "insufficient"
    elif clv["lo"] is not None and pnl["lo"] is not None and clv["lo"] > 0.0 and pnl["lo"] > 0.0:
        status = "pass"
    else:
        status = "fail"
    return {"status": status, "threshold_matches": R2_MIN_MATCHES, "matches_needed": needed,
            "bets": bets, "bets_with_clv": clv_n, "clv_per_bet": clv,
            "pnl_total": pnl, "rule": "mean CLV per bet and total simulated P&L both have 95% CI lower bound > 0"}


def _segment_result(arena: str, name: str, rows: list[dict[str, Any]], bets: list[dict[str, Any]]) -> dict[str, Any]:
    match_ids = sorted({r["match_id"] for r in rows})
    index = {m: i for i, m in enumerate(match_ids)}
    n_matches = len(match_ids)
    y = np.array([r["home_won"] for r in rows], dtype=float)
    p_src = np.array([r["p_home"] for r in rows], dtype=float)
    p_mkt = np.array([r["p_market"] for r in rows], dtype=float)
    row_group = np.array([index[r["match_id"]] for r in rows], dtype=int)

    diff = log_loss(p_src, y) - log_loss(p_mkt, y)
    diff_point, diff_lo, diff_hi = _bootstrap(diff, row_group, n_matches, "mean")
    ll_src = float(log_loss(p_src, y).mean()) if rows else None
    ll_mkt = float(log_loss(p_mkt, y).mean()) if rows else None

    bet_index = np.array([index[b["match_id"]] for b in bets], dtype=int)
    clv_vals = [(i, b["clv"]) for i, b in zip(bet_index, bets) if b["clv"] is not None]
    clv_arr = np.array([v for _, v in clv_vals], dtype=float)
    clv_grp = np.array([i for i, _ in clv_vals], dtype=int)
    clv_point, clv_lo, clv_hi = _bootstrap(clv_arr, clv_grp, n_matches, "mean")
    pnl_arr = np.array([b["pnl"] for b in bets], dtype=float)
    pnl_point, pnl_lo, pnl_hi = _bootstrap(pnl_arr, bet_index, n_matches, "sum")

    result = {
        "arena": arena,
        "segment": name,
        "matches": n_matches,
        "rows": len(rows),
        "source": {"logloss": ll_src, "brier": float(brier(p_src, y).mean()) if rows else None,
                   "calibration": calibration(p_src, y) if rows else None},
        "market": {"logloss": ll_mkt, "brier": float(brier(p_mkt, y).mean()) if rows else None,
                   "calibration": calibration(p_mkt, y) if rows else None},
        "r1": _r1(n_matches, _ci(diff_point, diff_lo, diff_hi)),
        "r2": _r2(n_matches, len(bets), _ci(clv_point, clv_lo, clv_hi), _ci(pnl_point, pnl_lo, pnl_hi),
                  len(clv_vals)),
    }
    return result


def evaluate(dataset: Mapping[str, Any], predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Run the harness for one probability source over both arenas and every pre-registered segment."""
    rows = build_eval_rows(dataset, predictions)
    snapshots_by_id = {s["snapshot_id"]: s for s in dataset["snapshots"]}
    results: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for arena in ARENAS:
        arena_rows = [r for r in rows if r["arena"] == arena]
        bets = simulate_policy(arena_rows, snapshots_by_id)
        coverage[arena] = {
            "rows": len(arena_rows),
            "matches": len({r["match_id"] for r in arena_rows}),
            "bets": len(bets),
        }
        for name in seg.SEGMENT_NAMES:
            seg_rows = [r for r in arena_rows if name in r["segments"]]
            seg_bets = [b for b in bets if name in b["segments"]]
            results.append(_segment_result(arena, name, seg_rows, seg_bets))
    return {
        "eval_version": EVAL_VERSION,
        "segments_version": seg.SEGMENTS_VERSION,
        "settings": {
            "n_boot": N_BOOT,
            "seed": BOOT_SEED,
            "bankroll": BANKROLL,
            "devig": DEFAULT_DEVIG,
            "latency_s": 60,
            "policy_min_edge": seg.POLICY_MIN_EDGE,
            "policy_segment": seg.POLICY_SEGMENT,
            "r1_min_matches": R1_MIN_MATCHES,
            "r2_min_matches": R2_MIN_MATCHES,
            "binding_close": BINDING_CLOSE,
            "commission": "none modelled; the takeable price already carries the spread",
        },
        "coverage": coverage,
        "results": results,
    }


def summarise_gates(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts of R1/R2 statuses, and the segments that pass each rung."""
    r1, r2 = Counter(), Counter()
    r1_pass, r2_pass = [], []
    for res in results:
        r1[res["r1"]["status"]] += 1
        r2[res["r2"]["status"]] += 1
        if res["r1"]["status"] == "pass":
            r1_pass.append(f"{res['arena']}/{res['segment']}")
        if res["r2"]["status"] == "pass":
            r2_pass.append(f"{res['arena']}/{res['segment']}")
    return {"r1_counts": dict(r1), "r2_counts": dict(r2), "r1_pass": r1_pass, "r2_pass": r2_pass,
            "signals_allowed": bool(r1_pass and r2_pass)}


def build_report(
    dataset: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    *,
    source_name: str,
    source_kind: str,
    predictions_file: Optional[str],
    predictions_rows: int,
    generated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assemble the gate report written to ``reports/gates/``."""
    manifest = dataset["manifest"]
    settled = sum(1 for m in dataset["matches"] if m.get("home_won") in (0, 1))
    generated = (generated_at or datetime.now(timezone.utc)).isoformat()
    return {
        "report": "s63_gate_report",
        "generated_at_utc": generated,
        "source": {"name": source_name, "kind": source_kind, "predictions_file": predictions_file,
                   "predictions_rows": predictions_rows},
        "dataset": {
            "dir": dataset["dir"],
            "dataset": manifest.get("dataset"),
            "generated_at_utc": manifest.get("generated_at_utc"),
            "git_sha": manifest.get("git_sha"),
            "matches": len(dataset["matches"]),
            "settled_matches": settled,
            "snapshots": len(dataset["snapshots"]),
        },
        **{k: evaluation[k] for k in ("eval_version", "segments_version", "settings", "coverage")},
        "gates": [
            {k: res[k] for k in ("arena", "segment", "matches", "rows", "r1", "r2")}
            | {"source_logloss": res["source"]["logloss"], "market_logloss": res["market"]["logloss"],
               "source_ece": (res["source"]["calibration"] or {}).get("ece"),
               "market_ece": (res["market"]["calibration"] or {}).get("ece")}
            for res in evaluation["results"]
        ],
        "results": evaluation["results"],
        "summary": summarise_gates(evaluation["results"]),
    }


# --------------------------------------------------------------------------- rendering
def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_ci(block: Mapping[str, Any], digits: int = 4) -> str:
    if block.get("point") is None:
        return "n/a"
    lo, hi = block.get("lo"), block.get("hi")
    if lo is None:
        return f"{_fmt(block['point'], digits)} (CI n/a)"
    return f"{_fmt(block['point'], digits)} [{_fmt(lo, digits)}, {_fmt(hi, digits)}]"


def _gate_text(block: Mapping[str, Any]) -> str:
    if block["status"] == "insufficient":
        return f"insufficient (need {block['matches_needed']} more matches)"
    return block["status"]


def render_markdown(report: Mapping[str, Any]) -> str:
    """Human-readable gate report."""
    src, ds = report["source"], report["dataset"]
    lines = [
        f"# S63 gate report: {src['name']} ({report['generated_at_utc'][:10]})",
        "",
        f"- Source: `{src['name']}` ({src['kind']}), de-vig `{report['settings']['devig']}`, "
        f"predictions rows: {src['predictions_rows']}",
        f"- Dataset: `{ds['dir']}` (sha `{ds['git_sha']}`), matches {ds['matches']}, settled {ds['settled_matches']}",
        f"- Segments version `{report['segments_version']}`, eval version `{report['eval_version']}`, "
        f"bootstrap {report['settings']['n_boot']} resamples by match, seed {report['settings']['seed']}",
        f"- Thresholds: R1 >= {report['settings']['r1_min_matches']} matches, "
        f"R2 >= {report['settings']['r2_min_matches']} matches (plan section 4)",
        "",
    ]
    summary = report["summary"]
    lines += [
        f"**Signals allowed: {'yes' if summary['signals_allowed'] else 'no'}.** "
        f"R1 pass: {', '.join(summary['r1_pass']) or 'none'}. "
        f"R2 pass: {', '.join(summary['r2_pass']) or 'none'}.",
        "",
    ]
    for arena in ARENAS:
        cov = report["coverage"][arena]
        lines += [
            f"## {arena}",
            "",
            f"Rows {cov['rows']}, matches {cov['matches']}, bets {cov['bets']}. "
            f"Binding close: {BINDING_CLOSE[arena]}.",
            "",
            "| segment | matches | rows | source-market log loss diff [95% CI] | R1 | bets | CLV per bet [95% CI] "
            "| simulated P&L [95% CI] | R2 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for gate in [g for g in report["results"] if g["arena"] == arena]:
            lines.append(
                f"| {gate['segment']} | {gate['matches']} | {gate['rows']} | {_fmt_ci(gate['r1']['logloss_diff'])} "
                f"| {_gate_text(gate['r1'])} | {gate['r2']['bets']} | {_fmt_ci(gate['r2']['clv_per_bet'])} "
                f"| {_fmt_ci(gate['r2']['pnl_total'], 2)} | {_gate_text(gate['r2'])} |"
            )
        lines.append("")
    lines += [
        "Rules: R1 passes when the source log loss is below the market's in a segment with at least "
        f"{R1_MIN_MATCHES} matches and the paired 95% CI lies entirely below zero. R2 passes when mean CLV per bet "
        f"and total simulated P&L both have a 95% CI lower bound above zero, with at least {R2_MIN_MATCHES} matches. "
        "Until R1 passes, the blend returns the market and no signals are emitted.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: Mapping[str, Any], out_dir: Path, stem: str, *, overwrite: bool = False) -> tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.md``. Refuses to replace a report from a different source unless asked."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    if json_path.exists() and not overwrite:
        previous = json.loads(json_path.read_text(encoding="utf-8"))
        if previous.get("source", {}).get("name") != report["source"]["name"]:
            raise FileExistsError(
                f"{json_path} holds the gate report for source {previous.get('source', {}).get('name')!r}; "
                "pass overwrite=True to replace it"
            )
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


__all__ = [
    "EVAL_VERSION",
    "load_dataset",
    "market_predictions",
    "validate_predictions",
    "build_eval_rows",
    "simulate_policy",
    "evaluate",
    "build_report",
    "render_markdown",
    "write_report",
    "log_loss",
    "brier",
    "calibration",
]
