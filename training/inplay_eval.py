"""S89 in-play model test: does the in-play model beat the LotusBook in-play price on our own recordings?

Pure functions and the gate file reader and writer. No database access here: ``scripts/evaluate_inplay.py`` reads the
database read-only, then calls :func:`evaluate_matches` and :func:`summarise`.

Population: real (not simulated) matches with ``score_pipeline`` = ``livescore-v2`` in ``match_training_status`` and a
decided result (``match_results`` with result_type ``win`` whose winner is one of the two teams).

Every recorded score state is a ``match_context`` row with ``is_live`` true. At each state time t:

* the in-play model's P(home) is ``ProbabilityService.estimate(match, book_t, history_t, as_of=t)``. ``book_t`` is the
  S58 as-of book at t (the latest price per field at or before t, within the 15 min staleness limit) and
  ``history_t`` holds only the context rows with time <= t. Only ``source == "inplay"`` counts: a pre-match fallback is
  not an in-play opinion, so that state is skipped and the reason is counted;
* the market's P(home) is the band de-vig of ``book_t`` (``training.market.TRADING_DEVIG``, as the trader uses it);
* the soft target is the market's P(home) at t + 10 min (``book_t+10m``). It is read as a label only, never an input.

Scoring. Log loss ``-(q ln p + (1 - q) ln(1 - p))`` and Brier ``(p - q)^2``, with ``q`` = 1 or 0 for the final result
(hard target) and ``q`` = the market 10 min later (soft target). Model and market are scored on the same states and the
difference is model minus market (negative means the model is better).

Clustering. Each state carries weight 1 / (number of states of its match), so every match counts once. The 95% CI is
the match-clustered seeded percentile bootstrap of :func:`shared.metrics.cluster_bootstrap` (2,000 draws).

Gate rule, per format, on the final-result log loss difference ``d`` (model minus market) with CI ``[lo, hi]``:

    inplay_model_allowed = matches >= 15  and  d <= 0  and  lo < 0

i.e. at least 15 matches, a point estimate that favours the model, and a CI whose lower bound reaches below zero (the
data do not rule out a model advantage). This is looser than the R1 rung of ``docs/architecture/training-strategy.md``,
which needs the whole CI below zero (hi < 0). With fewer than 15 matches the flag is False. A missing or unreadable
gate file means every format is False (:func:`load_inplay_gate`). ``hi`` is written to the report and not used by the
rule.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from shared.logging import setup_logging
from shared.metrics import CLUSTER_SEED, MIN_CLUSTERS_FOR_CI, cluster_bootstrap
from strategy.fair_value import family_of
from training.dataset import _book, _price_series, _to_dt, match_record
from training.evaluate import PROB_EPS, flat_book
from training.market import TRADING_DEVIG, market_prob_home

logger = setup_logging("inplay_eval")

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = "livescore-v2"
SOFT_HORIZON = timedelta(minutes=10)
STALE_PAD = timedelta(minutes=15)  # the as-of staleness limit (S58), used to bound the price window read
MIN_MATCHES = 15
FORMATS = ("t20", "odi", "multiday")  # ``family_of`` of a match format; "multiday" has no in-play model
N_BOOT = 2000
INPLAY_GATE_PATH = ROOT / "models" / "registry" / "inplay_gate.json"

RULE_TEXT = (
    "inplay_model_allowed = matches >= 15 and logloss_diff <= 0 and ci_lower < 0 "
    "(logloss_diff = model minus market, final-result log loss, match-clustered 95% bootstrap CI)"
)


# --------------------------------------------------------------------------- scoring
def soft_log_loss(p: Any, q: Any) -> np.ndarray:
    """Binary log loss against a soft target ``q`` in [0, 1]; with ``q`` in {0, 1} it is the usual log loss."""
    pp = np.clip(np.asarray(p, dtype=float), PROB_EPS, 1.0 - PROB_EPS)
    qq = np.clip(np.asarray(q, dtype=float), 0.0, 1.0)
    return -(qq * np.log(pp) + (1.0 - qq) * np.log(1.0 - pp))


def soft_brier(p: Any, q: Any) -> np.ndarray:
    """Squared error against a soft target ``q``; with ``q`` in {0, 1} it is the usual Brier score."""
    return (np.asarray(p, dtype=float) - np.asarray(q, dtype=float)) ** 2


def _num(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# --------------------------------------------------------------------------- as-of helpers
def history_until(rows: Iterable[Mapping[str, Any]], t: datetime) -> list[dict[str, Any]]:
    """Context rows at or before ``t``, in time order. Rows after ``t`` never reach the model."""
    kept = []
    for row in rows:
        ts = _to_dt(row.get("time"))
        if ts is not None and ts <= t:
            kept.append(dict(row))
    return sorted(kept, key=lambda r: _to_dt(r["time"]))


def market_home(book: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Band de-vigged P(home) of a nested S58 book, as the trader reads it; None when the book is unusable."""
    if not book:
        return None
    try:
        p = market_prob_home(flat_book(book), TRADING_DEVIG)
    except (KeyError, TypeError, ValueError):
        return None
    return _num(p)


def decided_result(result_rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """The decided (``win``) result row when one exists, else the latest row, else None."""
    if not result_rows:
        return None
    wins = [r for r in result_rows if str(r.get("result_type") or "").lower() == "win"]
    if wins:
        return wins[-1]
    return None


# --------------------------------------------------------------------------- per match
def evaluate_match(
    service: Any,
    match_row: Mapping[str, Any],
    result_row: Optional[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
    price_rows: Sequence[Mapping[str, Any]],
    *,
    horizon: timedelta = SOFT_HORIZON,
) -> tuple[list[dict[str, Any]], Counter]:
    """One record per recorded in-play state of one match, plus a count of the states that were skipped and why.

    ``service`` needs ``estimate(match, book, state, *, as_of)`` returning an object with ``p_model``, ``p_market``
    and ``source`` (``strategy.probability.ProbabilityService``, or a fake in tests).
    """
    skips: Counter = Counter()
    facts = match_record(match_row, result_row)
    home_won = facts["home_won"]
    if home_won is None:
        skips["no_decided_result"] += 1
        return [], skips

    ctx = sorted((dict(r) for r in context_rows if _to_dt(r.get("time")) is not None),
                 key=lambda r: _to_dt(r["time"]))
    fmt = next((r.get("match_format") for r in reversed(ctx) if r.get("match_format")), None)
    family = family_of(fmt)
    match = {
        "match_id": str(match_row["match_id"]),
        "team_home": facts["team_home"],
        "team_away": facts["team_away"],
        "competition": facts["competition"],
        "family": family,
        "match_format": fmt,
        "toss_winner": match_row.get("toss_winner"),
        "toss_decision": match_row.get("toss_decision"),
        "venue": None,
    }
    series = _price_series([dict(r) for r in price_rows])

    records: list[dict[str, Any]] = []
    for row in ctx:
        if not row.get("is_live") or row.get("score") is None:
            continue
        t = _to_dt(row["time"])
        book = _book(series, t)  # prices at or before t only (S58 as-of rule)
        history = history_until(ctx, t)  # score rows at or before t only
        est = service.estimate(match, book, history, as_of=t)
        p_market = _num(est.p_market)
        if p_market is None:
            skips["no_market"] += 1
            continue
        p_model = _num(est.p_model)
        if est.source != "inplay" or p_model is None:
            skips[f"not_inplay:{est.source}"] += 1
            continue
        # Label only: the market 10 min after t. Never passed to the model.
        q_later = market_home(_book(series, t + horizon))
        if q_later is None:
            skips["no_later_price"] += 1
        records.append({
            "match_id": match["match_id"],
            "family": family,
            "t": t.isoformat(),
            "innings": row.get("innings"),
            "p_model": p_model,
            "p_market": p_market,
            "q_later": q_later,
            "home_won": int(home_won),
        })
    return records, skips


def evaluate_matches(
    service: Any,
    registry_rows: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
    price_rows: Sequence[Mapping[str, Any]],
    *,
    horizon: timedelta = SOFT_HORIZON,
) -> dict[str, Any]:
    """Every real ``livescore-v2`` match with a decided result: its in-play state records and the coverage counts."""
    results: dict[str, list[Mapping[str, Any]]] = {}
    for r in result_rows:
        results.setdefault(str(r["match_id"]), []).append(r)
    contexts: dict[str, list[Mapping[str, Any]]] = {}
    for r in context_rows:
        contexts.setdefault(str(r["match_id"]), []).append(r)
    prices: dict[str, list[Mapping[str, Any]]] = {}
    for r in price_rows:
        prices.setdefault(str(r["match_id"]), []).append(r)

    counts: Counter = Counter()
    skips: Counter = Counter()
    records: list[dict[str, Any]] = []
    for reg in registry_rows:
        counts["registry"] += 1
        if str(reg.get("score_pipeline") or "") != PIPELINE:
            counts["not_livescore_v2"] += 1
            continue
        mid = str(reg["match_id"])
        facts = match_record(reg, decided_result(results.get(mid, [])))
        if facts["is_simulated"]:
            counts["simulated"] += 1
            continue
        if facts["home_won"] is None:
            counts["not_decided"] += 1
            continue
        counts["decided"] += 1
        recs, sk = evaluate_match(service, reg, decided_result(results.get(mid, [])), contexts.get(mid, []),
                                  prices.get(mid, []), horizon=horizon)
        skips.update(sk)
        if recs:
            counts["matches_evaluated"] += 1
            records.extend(recs)
    return {"records": records, "skips": dict(skips), "counts": dict(counts)}


# --------------------------------------------------------------------------- summary
def _block(values: np.ndarray, clusters: Sequence[str], weights: Sequence[float], n_boot: int, seed: int) -> dict:
    res = cluster_bootstrap(values, clusters, weights=weights, statistic="mean", n_boot=n_boot, seed=seed)
    return {
        "point": _num(res["estimate"]),
        "lo": _num(res["ci_lower"]),
        "hi": _num(res["ci_upper"]),
        "se": _num(res["se"]),
        "matches": int(res["n_clusters"]),
    }


def _target_block(rows: Sequence[Mapping[str, Any]], kind: str, n_boot: int, seed: int) -> dict[str, Any]:
    """Log loss and Brier of model, market and their difference for one target (``final`` or ``soft``)."""
    if not rows:
        empty = {"point": None, "lo": None, "hi": None, "se": None, "matches": 0}
        return {"matches": 0, "states": 0, "too_few_matches": True,
                "logloss": {"model": empty, "market": empty, "diff": empty},
                "brier": {"model": empty, "market": empty, "diff": empty}}
    y = np.asarray([r["home_won"] if kind == "final" else r["q_later"] for r in rows], dtype=float)
    p_m = np.asarray([r["p_model"] for r in rows], dtype=float)
    p_k = np.asarray([r["p_market"] for r in rows], dtype=float)
    clusters = [str(r["match_id"]) for r in rows]
    per_match = Counter(clusters)
    weights = [1.0 / per_match[c] for c in clusters]

    ll_m, ll_k = soft_log_loss(p_m, y), soft_log_loss(p_k, y)
    br_m, br_k = soft_brier(p_m, y), soft_brier(p_k, y)
    return {
        "matches": len(per_match),
        "states": len(rows),
        "too_few_matches": len(per_match) < MIN_CLUSTERS_FOR_CI,
        "logloss": {
            "model": _block(ll_m, clusters, weights, n_boot, seed),
            "market": _block(ll_k, clusters, weights, n_boot, seed),
            "diff": _block(ll_m - ll_k, clusters, weights, n_boot, seed),
        },
        "brier": {
            "model": _block(br_m, clusters, weights, n_boot, seed),
            "market": _block(br_k, clusters, weights, n_boot, seed),
            "diff": _block(br_m - br_k, clusters, weights, n_boot, seed),
        },
    }


def gate_allowed(matches: int, logloss_diff: Optional[float], ci_lower: Optional[float]) -> bool:
    """The rule in the module docstring: at least 15 matches, point estimate <= 0, CI lower bound < 0."""
    if matches < MIN_MATCHES or logloss_diff is None or ci_lower is None:
        return False
    return logloss_diff <= 0.0 and ci_lower < 0.0


def _gate_entry(final: Mapping[str, Any], evaluated_at: str) -> dict[str, Any]:
    diff = final["logloss"]["diff"]
    matches = int(final["matches"])
    return {
        "inplay_model_allowed": gate_allowed(matches, diff["point"], diff["lo"]),
        "matches": matches,
        "logloss_diff": diff["point"],
        "ci": [diff["lo"], diff["hi"]],
        "evaluated_at": evaluated_at,
    }


def summarise(
    records: Sequence[Mapping[str, Any]],
    *,
    n_boot: int = N_BOOT,
    seed: int = CLUSTER_SEED,
    evaluated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Per-format results on the final-result and soft targets, and the gate entry for each format."""
    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat()
    formats: dict[str, Any] = {}
    gate: dict[str, Any] = {}
    for fam in FORMATS:
        rows = [r for r in records if r["family"] == fam]
        final = _target_block(rows, "final", n_boot, seed)
        soft = _target_block([r for r in rows if r.get("q_later") is not None], "soft", n_boot, seed)
        formats[fam] = {"final": final, "soft": soft}
        gate[fam] = _gate_entry(final, evaluated_at)
    return {"formats": formats, "gate": gate, "rule": RULE_TEXT, "evaluated_at": evaluated_at,
            "n_boot": n_boot, "seed": seed}


# --------------------------------------------------------------------------- gate file
def closed_entry() -> dict[str, Any]:
    return {"inplay_model_allowed": False, "matches": 0, "logloss_diff": None, "ci": [None, None],
            "evaluated_at": None}


def closed_gate() -> dict[str, dict[str, Any]]:
    """Every format closed: the in-play model may not open new risk."""
    return {fam: closed_entry() for fam in FORMATS}


def json_safe(value: Any) -> Any:
    """NaN and infinity become None; mappings and sequences are converted recursively."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def write_gate(gate: Mapping[str, Any], path: Path = INPLAY_GATE_PATH) -> Path:
    """Write the gate file atomically (temporary file, then replace). NaN never reaches the JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(json_safe(dict(gate)), handle, indent=2, allow_nan=False)
    os.replace(tmp, path)
    return path


def load_inplay_gate(path: Path = INPLAY_GATE_PATH) -> dict[str, dict[str, Any]]:
    """The gate per format. A missing or unreadable file gives every format closed (``inplay_model_allowed`` False)."""
    gate = closed_gate()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return gate
    except (OSError, ValueError) as exc:
        logger.warning("inplay_gate_unreadable", path=str(path), error=type(exc).__name__)
        return gate
    if not isinstance(raw, Mapping):
        logger.warning("inplay_gate_malformed", path=str(path))
        return gate
    for fam, entry in raw.items():
        if isinstance(entry, Mapping):
            gate[str(fam)] = {**closed_entry(), **entry, "inplay_model_allowed": entry.get("inplay_model_allowed") is True}
    return gate


def inplay_model_allowed(gate: Mapping[str, Any], family: str) -> bool:
    """True only when the gate has this family with ``inplay_model_allowed`` exactly True."""
    entry = gate.get(family)
    return isinstance(entry, Mapping) and entry.get("inplay_model_allowed") is True


# --------------------------------------------------------------------------- report
def _f(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _ci_text(block: Mapping[str, Any], too_few: bool = False) -> str:
    if block.get("point") is None:
        return "n/a"
    text = f"{_f(block['point'])} [{_f(block['lo'])}, {_f(block['hi'])}]"
    return text + (" (too few matches)" if too_few else "")


def render_markdown(summary: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    lines = [
        "# S89 in-play model vs LotusBook in-play price",
        "",
        f"Generated {summary['evaluated_at']}. Real `livescore-v2` matches with a decided result. Model and market are "
        "scored on the same recorded in-play states; a state counts only when the in-play model (source `inplay`) "
        "priced it. Log loss and Brier: lower is better; the difference is model minus market (negative favours the "
        f"model). Match-clustered 95% CIs, {summary['n_boot']} seeded draws, each match weighted equally.",
        "",
        f"Gate rule: `{RULE_TEXT}`.",
        "",
        "## Coverage",
        "",
    ]
    for key, value in meta.get("counts", {}).items():
        lines.append(f"- {key}: {value}")
    if meta.get("skips"):
        lines.append("- skipped states: " + ", ".join(f"{k} {v}" for k, v in sorted(meta["skips"].items())))
    lines += ["", "## Per format (final result)", "",
              "| format | n matches / states | log loss model | log loss market | diff [95% CI] | Brier diff [95% CI] | gate |",
              "|---|---|---|---|---|---|---|"]
    for fam, block in summary["formats"].items():
        final = block["final"]
        gate = summary["gate"][fam]
        flag = "ALLOWED" if gate["inplay_model_allowed"] else "closed"
        if final["too_few_matches"] and final["matches"]:
            flag += " (too few matches)"
        lines.append(
            f"| {fam} | {final['matches']} / {final['states']} | {_f(final['logloss']['model']['point'])} "
            f"| {_f(final['logloss']['market']['point'])} | {_ci_text(final['logloss']['diff'], final['too_few_matches'])} "
            f"| {_ci_text(final['brier']['diff'], final['too_few_matches'])} | {flag} |"
        )
    lines += ["", "## Per format (soft target: market 10 min later)", "",
              "| format | n matches / states | log loss diff [95% CI] | Brier diff [95% CI] |",
              "|---|---|---|---|"]
    for fam, block in summary["formats"].items():
        soft = block["soft"]
        lines.append(f"| {fam} | {soft['matches']} / {soft['states']} | {_ci_text(soft['logloss']['diff'], soft['too_few_matches'])} "
                     f"| {_ci_text(soft['brier']['diff'], soft['too_few_matches'])} |")
    lines += ["", "## Gate file", "", "```json", json.dumps(json_safe(summary["gate"]), indent=2), "```", ""]
    return "\n".join(lines)


__all__ = [
    "FORMATS",
    "INPLAY_GATE_PATH",
    "MIN_MATCHES",
    "PIPELINE",
    "SOFT_HORIZON",
    "closed_gate",
    "decided_result",
    "evaluate_match",
    "evaluate_matches",
    "gate_allowed",
    "history_until",
    "inplay_model_allowed",
    "json_safe",
    "load_inplay_gate",
    "market_home",
    "render_markdown",
    "soft_brier",
    "soft_log_loss",
    "summarise",
    "write_gate",
]
