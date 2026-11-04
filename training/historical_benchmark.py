"""S103: R1 benchmark. Our pre-match model's probability against the de-vigged historical closing price.

Pure functions: no database, no network. Inputs are the out-of-sample walk-forward predictions written by
``scripts/train_prematch_model.py --export-predictions`` (one JSON line per test match) and the S98 odds records
(:mod:`training.historical_odds`), linked to Cricsheet matches with :class:`training.dataset.CricsheetIndex`.

Verdict rule (Golden Rulebook section 7): ``beats market`` only when the 95% match-clustered CI of
(model log loss - market log loss) lies entirely below 0 AND the segment has at least 200 matches. Under 200 matches
the verdict is ``too few matches``; otherwise ``does not beat market``. The ROI table is secondary: it prices bets at
the closing price, which a person cannot take, so it is for direction only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from shared.metrics import cluster_bootstrap
from training import historical_odds as ho
from training.dataset import CricsheetIndex, team_key

MIN_MATCHES = 200
ROI_THRESHOLDS = (0.03, 0.06)
ROI_NOTE = "closing price, not takeable by a person; for direction only"
CLOSE_KINDS = ("close", "average")
PREDICTION_KEYS = ("match_id", "season", "home", "away", "label", "p_v3", "p_elo")
_EPS = 1e-6
SEGMENT_KEYS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "family_gender": lambda row: f"{row['family']} {row['gender']}",
    "competition": lambda row: str(row.get("competition") or "unknown"),
}
CAVEATS = [
    "A closing price is the hardest benchmark: it holds all pre-match information. Beating Elo says nothing about "
    "beating the market.",
    "Only out-of-sample predictions count: each test match is predicted by a walk-forward fold whose training never "
    "saw its season.",
    "Market probability: power de-vig of the closing prices (research default). Each match is oriented to the model's "
    "home side by team name.",
    "Closing price: bookmaker priority pinnacle, then average/consensus records, then any other bookmaker. Within a "
    "bookmaker a close (or average) price is used first, else the last snapshot before the start.",
    "Clusters are matches. 95% intervals are match-clustered bootstrap percentiles with the shared fixed seed.",
    "Season-wise blend: leave-one-season-out logistic regression on the two logits. Its training includes later "
    "seasons, so it is a cross-validation, not a live forecast.",
]


# --------------------------------------------------------------------------- closing prices
def bookmaker_rank(record: ho.OddsRecord) -> int:
    """0 for Pinnacle, 1 for average or consensus prices, 2 for any other bookmaker."""
    name = str(record.bookmaker or "").casefold()
    if "pinnacle" in name:
        return 0
    if record.kind == "average" or "average" in name or "consensus" in name:
        return 1
    return 2


def _usable(record: ho.OddsRecord) -> bool:
    """Records with a bad price, time, team or bookmaker cannot give a closing price (S102 reports them)."""
    return not (set(ho.validate(record)) - {"unknown_winner"})


def _closing_entry(record: ho.OddsRecord, detail: Mapping[str, Any], rank: int, used: str) -> dict[str, Any]:
    """One closing price in Cricsheet order: ``home_team`` is the side Cricsheet lists first."""
    reversed_order = bool(detail.get("reversed"))
    p_first = ho.p_home_fair(record)
    if reversed_order:
        home_team, away_team = record.away, record.home
        p_home, price_home, price_away = 1.0 - p_first, record.price_away, record.price_home
    else:
        home_team, away_team = record.home, record.away
        p_home, price_home, price_away = p_first, record.price_home, record.price_away
    return {
        "cricsheet_match_id": str(detail["cricsheet_match_id"]),
        "p_home": float(p_home),
        "home_team": ho.normalise_team(home_team),
        "away_team": ho.normalise_team(away_team),
        "price_home": float(price_home),
        "price_away": float(price_away),
        "bookmaker": record.bookmaker,
        "bookmaker_rank": rank,
        "kind": used,
        "competition": record.competition,
        "format": record.format,
        "gender": record.gender,
        "source": record.source,
        "source_event_id": record.source_event_id,
        "start_time_utc": record.start_time_utc.isoformat(),
        "reversed": reversed_order,
        "day_delta": detail.get("day_delta"),
    }


def _pick_closing(items: Sequence[tuple[ho.OddsRecord, Mapping[str, Any]]]) -> Optional[dict[str, Any]]:
    """The closing price of one match: the best bookmaker that has a close or a pre-start snapshot."""
    groups: dict[str, list[tuple[ho.OddsRecord, Mapping[str, Any]]]] = {}
    for record, detail in items:
        groups.setdefault(record.bookmaker, []).append((record, detail))
    ranks = {book: min(bookmaker_rank(r) for r, _ in group) for book, group in groups.items()}
    for book in sorted(groups, key=lambda b: (ranks[b], b)):
        group = groups[book]
        closes = sorted(
            (item for item in group if item[0].kind in CLOSE_KINDS),
            key=lambda item: (item[0].kind != "close", item[0].source, item[0].source_event_id),
        )
        if closes:
            record, detail = closes[0]
            return _closing_entry(record, detail, ranks[book], record.kind)
        snapshots = [
            item for item in group
            if item[0].kind == "snapshot"
            and item[0].snapshot_time_utc is not None
            and item[0].snapshot_time_utc < item[0].start_time_utc
        ]
        if snapshots:
            record, detail = max(snapshots, key=lambda item: item[0].snapshot_time_utc)
            return _closing_entry(record, detail, ranks[book], "snapshot")
    return None


def closing_by_match(records: Iterable[ho.OddsRecord], index: CricsheetIndex) -> dict[str, dict[str, Any]]:
    """One closing fair P(home) per linked Cricsheet match, keyed by Cricsheet match id.

    ``home`` is the side Cricsheet lists first (``home_team``); ``p_home`` is its de-vigged probability and
    ``price_home`` / ``price_away`` the decimal prices of the same sides. The chosen bookmaker and price kind are
    recorded. Records that do not link to exactly one Cricsheet match, or fail the S98 checks, are skipped.
    """
    groups: dict[str, list[tuple[ho.OddsRecord, Mapping[str, Any]]]] = {}
    for record in records:
        if not _usable(record):
            continue
        detail = ho.link_detail(record, index)
        if detail is None:
            continue
        groups.setdefault(str(detail["cricsheet_match_id"]), []).append((record, detail))
    out: dict[str, dict[str, Any]] = {}
    for match_id in sorted(groups):
        entry = _pick_closing(groups[match_id])
        if entry is not None:
            out[match_id] = entry
    return out


# --------------------------------------------------------------------------- predictions
def load_predictions(path: Any) -> list[dict[str, Any]]:
    """Read the JSON lines written by ``train_prematch_model.py --export-predictions``."""
    rows: list[dict[str, Any]] = []
    with open(Path(path), "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [key for key in PREDICTION_KEYS if key not in row]
            if missing:
                raise ValueError(f"{path}: line {number} misses {missing}")
            rows.append(row)
    return rows


def _orient(pred: Mapping[str, Any], close: Mapping[str, Any], alias: Optional[Mapping[str, str]]) -> Optional[tuple]:
    """Market P(model home) and the decimal prices in the model's home order, or None when the teams do not match."""
    pred_home = team_key(pred.get("home"), alias)
    if pred_home == team_key(close["home_team"], alias):
        return float(close["p_home"]), float(close["price_home"]), float(close["price_away"])
    if pred_home == team_key(close["away_team"], alias):
        return 1.0 - float(close["p_home"]), float(close["price_away"]), float(close["price_home"])
    return None


def join_matches(
    predictions: Sequence[Mapping[str, Any]],
    closing: Mapping[str, Mapping[str, Any]],
    *,
    team_alias: Optional[Mapping[str, str]] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Predictions with their closing price, oriented to the model's home side. Returns (rows, counts)."""
    counts = {
        "predictions": len(predictions), "duplicate_predictions": 0, "without_closing": 0,
        "orientation_mismatch": 0, "joined": 0,
    }
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for pred in predictions:
        match_id = str(pred["match_id"])
        if match_id in seen:
            counts["duplicate_predictions"] += 1
            continue
        seen.add(match_id)
        close = closing.get(match_id)
        if close is None:
            counts["without_closing"] += 1
            continue
        oriented = _orient(pred, close, team_alias)
        if oriented is None:
            counts["orientation_mismatch"] += 1
            continue
        p_market, price_home, price_away = oriented
        season = pred.get("season")
        rows.append({
            "match_id": match_id,
            "date": str(pred.get("date") or ""),
            "season": int(season) if season is not None else int(str(pred.get("date", "0000"))[:4]),
            "family": str(pred.get("family") or "unknown"),
            "gender": str(pred.get("gender") or "unknown"),
            "competition": str(close.get("competition") or "unknown"),
            "label": int(pred["label"]),
            "p_model": float(pred["p_v3"]),
            "p_elo": float(pred["p_elo"]),
            "p_market": p_market,
            "price_home": price_home,
            "price_away": price_away,
            "bookmaker": close.get("bookmaker"),
            "kind": close.get("kind"),
        })
    counts["joined"] = len(rows)
    return rows, counts


# --------------------------------------------------------------------------- statistics
def verdict(n_matches: int, ci_upper: Optional[float]) -> str:
    """The verdict rule of section 7: never a verdict under 200 matches."""
    if n_matches < MIN_MATCHES:
        return "too few matches"
    if ci_upper is not None and math.isfinite(ci_upper) and ci_upper < 0.0:
        return "beats market"
    return "does not beat market"


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _match_log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    q = np.clip(p, _EPS, 1.0 - _EPS)
    return -(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))


def _ci(result: Mapping[str, Any]) -> dict[str, Optional[float]]:
    return {
        "estimate": _num(result["estimate"]),
        "ci_lower": _num(result["ci_lower"]),
        "ci_upper": _num(result["ci_upper"]),
    }


def _empty_block() -> dict[str, Any]:
    empty_ci = {"estimate": None, "ci_lower": None, "ci_upper": None}
    return {
        "n_matches": 0, "model_log_loss": None, "market_log_loss": None, "log_loss_diff": dict(empty_ci),
        "model_brier": None, "market_brier": None, "brier_diff": dict(empty_ci),
        "verdict": verdict(0, None), "min_matches": MIN_MATCHES,
    }


def _compare(rows: Sequence[Mapping[str, Any]], model_key: str) -> dict[str, Any]:
    """Model (``model_key``) against the market on the same matches, with match-clustered intervals."""
    if not rows:
        return _empty_block()
    y = np.array([float(r["label"]) for r in rows])
    p_model = np.array([float(r[model_key]) for r in rows])
    p_market = np.array([float(r["p_market"]) for r in rows])
    ids = [str(r["match_id"]) for r in rows]
    ll_model, ll_market = _match_log_loss(p_model, y), _match_log_loss(p_market, y)
    br_model, br_market = (p_model - y) ** 2, (p_market - y) ** 2
    ll_diff = cluster_bootstrap(ll_model - ll_market, ids)
    br_diff = cluster_bootstrap(br_model - br_market, ids)
    n = len(rows)
    return {
        "n_matches": n,
        "model_log_loss": float(ll_model.mean()),
        "market_log_loss": float(ll_market.mean()),
        "log_loss_diff": _ci(ll_diff),
        "model_brier": float(br_model.mean()),
        "market_brier": float(br_market.mean()),
        "brier_diff": _ci(br_diff),
        "verdict": verdict(n, ll_diff["ci_upper"]),
        "min_matches": MIN_MATCHES,
    }


def _logit(p: Iterable[float]) -> np.ndarray:
    q = np.clip(np.asarray(list(p), dtype=float), _EPS, 1.0 - _EPS)
    return np.log(q / (1.0 - q))


def blend_check(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Leave-one-season-out logistic blend of (logit market, logit model), compared with the market alone.

    A season is predicted only when the other seasons hold at least two seasons and both outcomes.
    """
    predicted: list[dict[str, Any]] = []
    used: list[int] = []
    skipped: list[int] = []
    for season in sorted({r["season"] for r in rows}):
        test = [r for r in rows if r["season"] == season]
        train = [r for r in rows if r["season"] != season]
        y_train = np.array([r["label"] for r in train])
        if len({r["season"] for r in train}) < 2 or len(set(y_train.tolist())) < 2:
            skipped.append(season)
            continue
        x_train = np.column_stack([_logit(r["p_market"] for r in train), _logit(r["p_model"] for r in train)])
        x_test = np.column_stack([_logit(r["p_market"] for r in test), _logit(r["p_model"] for r in test)])
        model = LogisticRegression(C=1.0, max_iter=1000).fit(x_train, y_train)
        probs = model.predict_proba(x_test)[:, 1]
        predicted.extend({**r, "p_blend": float(p)} for r, p in zip(test, probs))
        used.append(season)
    block = _compare(predicted, "p_blend")
    block.update({
        "method": "leave-one-season-out logistic regression on (logit market, logit model)",
        "seasons_predicted": used,
        "seasons_skipped": skipped,
    })
    return block


def closing_roi(rows: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, Any]:
    """Flat one-unit back at the closing price when the model's probability gap exceeds ``threshold``.

    The gap is in probability points (0.03 = 3 points), not edge (Golden Rulebook section 7, rule 7).
    """
    values: list[float] = []
    clusters: list[str] = []
    sides = {"home": 0, "away": 0}
    for row in rows:
        gap = float(row["p_model"]) - float(row["p_market"])
        if gap > threshold:
            side, won, price = "home", int(row["label"]) == 1, float(row["price_home"])
        elif -gap > threshold:
            side, won, price = "away", int(row["label"]) == 0, float(row["price_away"])
        else:
            continue
        values.append(price - 1.0 if won else -1.0)
        clusters.append(str(row["match_id"]))
        sides[side] += 1
    result = cluster_bootstrap(values, clusters)
    return {
        "threshold_points": round(threshold * 100.0, 1),
        "n_bets": len(values),
        "n_matches": len(set(clusters)),
        "back_home": sides["home"],
        "back_away": sides["away"],
        "roi_per_unit": _num(result["estimate"]),
        "ci_lower": _num(result["ci_lower"]),
        "ci_upper": _num(result["ci_upper"]),
        "note": ROI_NOTE,
    }


def benchmark(
    predictions: Sequence[Mapping[str, Any]],
    closing: Mapping[str, Mapping[str, Any]],
    *,
    segments: Sequence[str] = ("family_gender", "competition"),
    team_alias: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """The R1 report: overall, per segment, Elo reference, blend and the secondary ROI table."""
    unknown = [name for name in segments if name not in SEGMENT_KEYS]
    if unknown:
        raise ValueError(f"unknown segments {unknown}; choose from {sorted(SEGMENT_KEYS)}")
    rows, counts = join_matches(predictions, closing, team_alias=team_alias)
    seg_out: dict[str, dict[str, Any]] = {}
    for name in segments:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(SEGMENT_KEYS[name](row), []).append(row)
        seg_out[name] = {key: _compare(groups[key], "p_model") for key in sorted(groups)}
    return {
        "min_matches": MIN_MATCHES,
        "matches": counts,
        "overall": _compare(rows, "p_model"),
        "elo_reference": _compare(rows, "p_elo"),
        "segments": seg_out,
        "blend": blend_check(rows),
        "roi": [closing_roi(rows, threshold) for threshold in ROI_THRESHOLDS],
        "caveats": list(CAVEATS),
    }


# --------------------------------------------------------------------------- report text
def _fmt(value: Any, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.4f}" if signed else f"{value:.4f}"


def _diff_cell(diff: Mapping[str, Any]) -> str:
    if diff.get("estimate") is None:
        return "-"
    return f"{_fmt(diff['estimate'], True)} ({_fmt(diff['ci_lower'], True)} to {_fmt(diff['ci_upper'], True)})"


def _table(title: str, items: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
    lines = [
        f"### {title}", "",
        "| Segment | Matches | Model log loss | Market log loss | Model - market log loss (95% CI) | "
        "Model Brier | Market Brier | Verdict |",
        "|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for name, block in items:
        lines.append(
            f"| {name} | {block['n_matches']} | {_fmt(block['model_log_loss'])} | "
            f"{_fmt(block['market_log_loss'])} | {_diff_cell(block['log_loss_diff'])} | "
            f"{_fmt(block['model_brier'])} | {_fmt(block['market_brier'])} | {block['verdict']} |"
        )
    return lines + [""]


def render_markdown(report: Mapping[str, Any]) -> str:
    """The human-readable R1 report (one table per segment; sample sizes always shown)."""
    m = report["matches"]
    lines = [
        "# R1 benchmark: pre-match model against the closing price (S103)", "",
        f"Generated {report.get('generated_utc', '-')} from `{report.get('predictions_file', '-')}`.", "",
        "**Verdict rule.** `beats market` only when the 95% match-clustered interval of (model log loss - market "
        f"log loss) lies below 0 and there are at least {MIN_MATCHES} matches. Under {MIN_MATCHES} matches the "
        "verdict is `too few matches`. A negative difference favours the model.", "",
        "## Matches", "",
        f"- predictions read: {m['predictions']} (duplicates {m['duplicate_predictions']}, no closing price "
        f"{m['without_closing']}, team orientation mismatch {m['orientation_mismatch']})",
        f"- matches joined with a closing price: {m['joined']}",
        f"- closing prices built from the stores: {report.get('closing_matches', '-')}",
        f"- verification: {report.get('verification', 'not checked')}", "",
    ]
    lines += _table("Overall", [("all matches", report["overall"])])
    for name, groups in report["segments"].items():
        lines += _table(f"By {name}", list(groups.items()))
    lines += _table("Reference: Elo against the market (not a verdict on the model)",
                    [("all matches", report["elo_reference"])])
    blend = report["blend"]
    lines += _table("Blend: leave-one-season-out logistic on the two logits", [("blend", blend)])
    lines += [
        f"Blend seasons predicted: {blend['seasons_predicted'] or 'none'}; skipped: {blend['seasons_skipped'] or 'none'}.",
        "",
        "### Secondary: flat-stake ROI at the closing price", "",
        f"Closing price, not takeable by a person; for direction only. Bets when the model's probability exceeds the market's by more than "
        "the threshold, one unit each.", "",
        "| Gap threshold (points) | Bets | Matches | Back home | Back away | ROI per unit (95% CI) |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for roi in report["roi"]:
        cell = "-" if roi["roi_per_unit"] is None else (
            f"{_fmt(roi['roi_per_unit'], True)} ({_fmt(roi['ci_lower'], True)} to {_fmt(roi['ci_upper'], True)})"
        )
        lines.append(
            f"| {roi['threshold_points']} | {roi['n_bets']} | {roi['n_matches']} | {roi['back_home']} | "
            f"{roi['back_away']} | {cell} |"
        )
    lines += ["", "## Caveats", ""]
    lines += [f"- {text}" for text in report.get("caveats", CAVEATS)]
    return "\n".join(lines) + "\n"
