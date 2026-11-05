"""S107 part B: the chase model, P(the chasing side wins) from the state of a Cricsheet T20 second innings (pure).

Dataset (``summarise_match`` / ``build_rows``): from ``info`` and ``innings`` of each Cricsheet T20 match (no DLS, no
super over, a decided result, 20 overs). One row per legal ball of the second innings, the state BEFORE that ball:
runs needed, balls left, wickets in hand, the two batters' runs and balls in this innings, and their career T20 record
as of the match's date. Career figures use only matches dated before the match (same-day matches are excluded), so no
result of the match or of a later one can reach a row. The label is 1 when the chasing side won.

Subsampling: every state of the last ``TAIL_BALLS`` (60) legal balls, where the tail states live, and one state per
over (legal ball index divisible by 6) before that. The choice depends only on ``balls_left``, a feature, so the
conditional probability the model learns is not biased by it.

Models: ``core`` (runs needed, balls left, wickets in hand, required rate) and ``players`` (core plus the two batters'
career strike rate, average and balls and their runs and balls in this innings). Both are scikit-learn histogram
gradient boosting classifiers with monotone constraints (more runs needed, lower; more balls or wickets, higher). The
``empirical`` table is the base-rate lookup of the same state (runs bucket, balls bucket, wickets), with back-off, used
as the benchmark. Live, ``ChaseModel.estimate`` uses ``players`` only when both batters' names resolve to career
records; otherwise ``core``.

Names: a live name ("Lokesh Bahadur Bam") resolves to a Cricsheet name ("LB Bam") by the exact name first, then by the
first initial and the last word (``name_key``), only when that key is unique among all career names.

No I/O here except ``ChaseModel.load`` and ``load_current`` (reads ``models/chase/current.json``); no database.
"""

from __future__ import annotations

import json
import math
import re
from array import array
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from shared.elo import family_for_match_type

VERSION = "s107-chase-1"
OVERS = 20
BALLS = 120
WICKETS = 10
TAIL_BALLS = 60  # every state in the last 60 legal balls is a training row
OVER_EVERY = 6  # earlier states: one per over
RETIRED_KINDS = frozenset({"retired hurt", "retired not out", "retired out"})  # not counted as a dismissal
CORE = "core"
PLAYERS = "players"
CORE_FEATURES = ("runs_needed", "balls_left", "wickets_left", "req_rate")
PLAYER_FEATURES = CORE_FEATURES + (
    "bat_sr_s", "bat_avg_s", "bat_balls_s", "inn_runs_s", "inn_balls_s",
    "bat_sr_n", "bat_avg_n", "bat_balls_n", "inn_runs_n", "inn_balls_n",
)
MONOTONE = {"runs_needed": -1, "balls_left": 1, "wickets_left": 1, "req_rate": -1}
RUN_EDGES = np.array([1, 2, 3, 4, 6, 8, 10, 12, 15, 18, 21, 25, 30, 35, 40, 50, 60, 75, 90, 110, 140])
BALL_EDGES = np.array([3, 6, 9, 12, 18, 24, 30, 36, 42, 48, 54, 60, 72, 84, 96, 108])
EMPIRICAL_MIN_N = 30
TAIL_BIN_EDGES = (0.0, 0.02, 0.05, 0.10, 0.20, 0.50, 0.80, 0.90, 0.95, 0.98, 1.0 + 1e-9)
SEED = 20261105
_NAN = float("nan")
_LEGAL_KEYS = ("wides", "noballs")
CURRENT_PATH = Path(__file__).resolve().parent.parent / "models" / "chase" / "current.json"


# ----------------------------------------------------------------------------- names
def name_key(name: Any) -> str:
    """``first initial + last word`` upper-cased ("LB Bam" and "Lokesh Bahadur Bam" both give "L BAM"), or "" when the
    name has one word or no letters."""
    tokens = [t for t in re.split(r"\s+", str(name or "").strip()) if t]
    if len(tokens) < 2:
        return ""
    first = re.sub(r"[^A-Za-z]", "", tokens[0])[:1].upper()
    last = re.sub(r"[^A-Za-z]", "", tokens[-1]).upper()
    return f"{first} {last}" if first and last else ""


# ----------------------------------------------------------------------------- Cricsheet match facts (pure)
@dataclass
class ChaseFacts:
    label: int  # 1 when the chasing side won
    target: int
    # per row: (legal ball index, runs needed, balls left, wickets left, striker, non-striker, striker runs and balls
    # in this innings, non-striker runs and balls in this innings)
    states: list[tuple]


@dataclass
class MatchFacts:
    match_id: str
    date: str
    season: int
    female: bool
    bat_totals: dict[str, list[int]]  # career contribution of the regular innings: name -> [runs, balls, outs]
    chase: Optional[ChaseFacts]
    excluded: str = ""  # why there is no chase ("" when there is one)


def _extras(delivery: Mapping[str, Any]) -> Mapping[str, Any]:
    return delivery.get("extras") or {}


def _is_legal(delivery: Mapping[str, Any]) -> bool:
    extras = _extras(delivery)
    return not any(k in extras for k in _LEGAL_KEYS)


def _deliveries(innings: Mapping[str, Any]):
    for over in innings.get("overs") or []:
        for delivery in over.get("deliveries") or []:
            yield delivery


def _innings_total(innings: Mapping[str, Any]) -> int:
    return sum(int((d.get("runs") or {}).get("total", 0) or 0) for d in _deliveries(innings))


def _accumulate_career(innings: Mapping[str, Any], totals: dict[str, list[int]]) -> None:
    """Batter runs, balls faced (wides excluded) and dismissals of one innings, into ``totals``."""
    for d in _deliveries(innings):
        batter = d.get("batter")
        if batter:
            rec = totals.setdefault(str(batter), [0, 0, 0])
            rec[0] += int((d.get("runs") or {}).get("batter", 0) or 0)
            if "wides" not in _extras(d):
                rec[1] += 1
        for wicket in d.get("wickets") or []:
            out = wicket.get("player_out")
            if out and wicket.get("kind") not in RETIRED_KINDS:
                totals.setdefault(str(out), [0, 0, 0])[2] += 1


def _chase_states(innings: Mapping[str, Any], target: int) -> list[tuple]:
    """States before each legal ball of the chase, subsampled as the module docstring says."""
    runs = 0
    wickets = 0
    legal = 0
    inn_runs: dict[str, int] = defaultdict(int)
    inn_balls: dict[str, int] = defaultdict(int)
    states: list[tuple] = []
    for d in _deliveries(innings):
        if runs >= target or wickets >= WICKETS or legal >= BALLS:
            break
        striker = str(d.get("batter") or "")
        non = str(d.get("non_striker") or "")
        if _is_legal(d):
            balls_left = BALLS - legal
            if balls_left <= TAIL_BALLS or legal % OVER_EVERY == 0:
                states.append((
                    legal, target - runs, balls_left, WICKETS - wickets, striker, non,
                    inn_runs[striker], inn_balls[striker], inn_runs[non], inn_balls[non],
                ))
            legal += 1
        if striker:
            inn_runs[striker] += int((d.get("runs") or {}).get("batter", 0) or 0)
            if "wides" not in _extras(d):
                inn_balls[striker] += 1
        runs += int((d.get("runs") or {}).get("total", 0) or 0)
        for wicket in d.get("wickets") or []:
            if wicket.get("kind") not in RETIRED_KINDS:
                wickets += 1
    return states


def _chase_exclusion(info: Mapping[str, Any], all_innings: Sequence[Mapping[str, Any]],
                     regular: Sequence[Mapping[str, Any]]) -> str:
    """Why a match gives no chase rows ("" when it does)."""
    if info.get("overs") not in (None, OVERS) or info.get("balls_per_over") not in (None, 6):
        return "not_20_overs"
    if len(all_innings) != len(regular):
        return "super_over"
    if len(regular) != 2:
        return "not_two_innings"
    outcome = info.get("outcome") or {}
    if outcome.get("method"):
        return "method"  # D/L and other rain rules
    winner = outcome.get("winner")
    if not winner:
        return "no_result"
    if winner not in (regular[0].get("team"), regular[1].get("team")):
        return "winner_not_a_side"
    return ""


def summarise_match(raw: Mapping[str, Any]) -> Optional[MatchFacts]:
    """The facts of one Cricsheet match (``raw["match_id"]`` set by the reader), or None when it is not a T20 match
    with a date. A match that gives no chase still gives its career totals (``chase`` is None, ``excluded`` says why)."""
    info = raw.get("info") or {}
    if family_for_match_type(info.get("match_type")) != "t20":
        return None
    dates = info.get("dates") or []
    if not dates:
        return None
    date = str(dates[0])[:10]
    all_innings = raw.get("innings") or []
    regular = [inn for inn in all_innings if not inn.get("super_over")]
    totals: dict[str, list[int]] = {}
    for inn in regular:
        _accumulate_career(inn, totals)
    facts = MatchFacts(str(raw.get("match_id") or ""), date, int(date[:4]),
                       str(info.get("gender") or "").lower() == "female", totals, None)
    reason = _chase_exclusion(info, all_innings, regular)
    if reason:
        facts.excluded = reason
        return facts
    first, second = regular
    target = _innings_total(first) + 1
    winner = (info.get("outcome") or {}).get("winner")
    states = _chase_states(second, target)
    if not states:
        facts.excluded = "no_chase_states"
        return facts
    facts.chase = ChaseFacts(int(winner == second.get("team")), target, states)
    return facts


# ----------------------------------------------------------------------------- rows (pure)
COLUMNS = ("match", "season", "y", "legal", "runs_needed", "balls_left", "wickets_left",
           "s_inn_runs", "s_inn_balls", "n_inn_runs", "n_inn_balls",
           "s_runs", "s_balls", "s_outs", "n_runs", "n_balls", "n_outs")


class RowTable:
    """Training rows as parallel float arrays (compact enough for about a million rows)."""

    def __init__(self) -> None:
        self.cols: dict[str, array] = {c: array("d") for c in COLUMNS}

    def __len__(self) -> int:
        return len(self.cols["y"])

    def add(self, **values: float) -> None:
        for name in COLUMNS:
            self.cols[name].append(float(values[name]))

    def arrays(self) -> dict[str, np.ndarray]:
        return {c: np.frombuffer(a, dtype=np.float64).copy() for c, a in self.cols.items()}


def build_rows(facts: Sequence[MatchFacts]) -> tuple[RowTable, dict[str, list[int]], list[str]]:
    """Rows for every chase, with career figures as of each match's date.

    Matches are processed by date. The career record used by a match is the one before its date (every match of the
    same date is added only after the whole date is read). Returns the rows, the career record after the last date
    (for the live model) and the match ids in row order (``match`` is the index into that list).
    """
    ordered = sorted(facts, key=lambda f: (f.date, f.match_id))
    career: dict[str, list[int]] = {}
    table = RowTable()
    match_ids: list[str] = []
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j].date == ordered[i].date:
            j += 1
        group = ordered[i:j]
        for f in group:
            if f.chase is None:
                continue
            match_index = len(match_ids)
            match_ids.append(f.match_id)
            for st in f.chase.states:
                s_c = career.get(st[4])
                n_c = career.get(st[5])
                table.add(
                    match=match_index, season=f.season, y=f.chase.label, legal=st[0],
                    runs_needed=st[1], balls_left=st[2], wickets_left=st[3],
                    s_inn_runs=st[6], s_inn_balls=st[7], n_inn_runs=st[8], n_inn_balls=st[9],
                    s_runs=_NAN if s_c is None else s_c[0], s_balls=_NAN if s_c is None else s_c[1],
                    s_outs=_NAN if s_c is None else s_c[2],
                    n_runs=_NAN if n_c is None else n_c[0], n_balls=_NAN if n_c is None else n_c[1],
                    n_outs=_NAN if n_c is None else n_c[2],
                )
        for f in group:
            for name, rec in f.bat_totals.items():
                acc = career.setdefault(name, [0, 0, 0])
                acc[0] += rec[0]
                acc[1] += rec[1]
                acc[2] += rec[2]
        i = j
    return table, career, match_ids


def _bat_features(inn_runs: float, inn_balls: float, career: Optional[tuple[float, float, float]]) -> list[float]:
    """strike rate, average, career balls, in-innings runs and balls of one batter (NaN where unknown)."""
    if career is None:
        return [_NAN, _NAN, _NAN, float(inn_runs), float(inn_balls)]
    runs, balls, outs = career
    sr = 100.0 * runs / balls if balls > 0 else _NAN
    avg = runs / outs if outs > 0 else _NAN
    return [sr, avg, float(balls), float(inn_runs), float(inn_balls)]


def feature_vector(
    kind: str,
    *,
    runs_needed: float,
    balls_left: float,
    wickets_left: float,
    striker: tuple[float, float],
    non_striker: tuple[float, float],
    striker_career: Optional[tuple[float, float, float]] = None,
    non_striker_career: Optional[tuple[float, float, float]] = None,
) -> list[float]:
    """The model inputs of one state, in ``CORE_FEATURES`` or ``PLAYER_FEATURES`` order. Used by training and live."""
    rate = 6.0 * runs_needed / balls_left if balls_left > 0 else 0.0
    x = [float(runs_needed), float(balls_left), float(wickets_left), rate]
    if kind == CORE:
        return x
    x += _bat_features(striker[0], striker[1], striker_career)
    x += _bat_features(non_striker[0], non_striker[1], non_striker_career)
    return x


def _row_career(arrays: dict[str, np.ndarray], prefix: str, i: int) -> Optional[tuple[float, float, float]]:
    balls = arrays[f"{prefix}_balls"][i]
    if math.isnan(balls):
        return None
    return (float(arrays[f"{prefix}_runs"][i]), float(balls), float(arrays[f"{prefix}_outs"][i]))


def design_matrix(arrays: dict[str, np.ndarray], kind: str) -> np.ndarray:
    """Feature matrix of every row of a table (``arrays`` from ``RowTable.arrays``)."""
    n = len(arrays["y"])
    width = len(CORE_FEATURES) if kind == CORE else len(PLAYER_FEATURES)
    out = np.empty((n, width), dtype=np.float64)
    if kind == CORE:
        rn, bl, wl = arrays["runs_needed"], arrays["balls_left"], arrays["wickets_left"]
        out[:, 0] = rn
        out[:, 1] = bl
        out[:, 2] = wl
        out[:, 3] = np.where(bl > 0, 6.0 * rn / np.maximum(bl, 1.0), 0.0)
        return out
    for i in range(n):
        out[i] = feature_vector(
            kind, runs_needed=arrays["runs_needed"][i], balls_left=arrays["balls_left"][i],
            wickets_left=arrays["wickets_left"][i],
            striker=(arrays["s_inn_runs"][i], arrays["s_inn_balls"][i]),
            non_striker=(arrays["n_inn_runs"][i], arrays["n_inn_balls"][i]),
            striker_career=_row_career(arrays, "s", i), non_striker_career=_row_career(arrays, "n", i),
        )
    return out


def matched_rows(arrays: dict[str, np.ndarray]) -> np.ndarray:
    """Rows where both batters have a career record (the live ``players`` model's population)."""
    sb, nb = arrays["s_balls"], arrays["n_balls"]
    return (~np.isnan(sb)) & (sb > 0) & (~np.isnan(nb)) & (nb > 0)


# ----------------------------------------------------------------------------- empirical base rates
def _cells(keys: np.ndarray, y: np.ndarray) -> dict[int, tuple[float, int]]:
    uniq, inv = np.unique(keys, return_inverse=True)
    counts = np.bincount(inv)
    sums = np.bincount(inv, weights=y)
    return {int(k): (float(s / c), int(c)) for k, s, c in zip(uniq, sums, counts)}


def _cell_key(runs_needed: float, balls_left: float, wickets_left: float) -> tuple[int, int, int]:
    rb = int(np.searchsorted(RUN_EDGES, runs_needed, side="right"))
    bb = int(np.searchsorted(BALL_EDGES, balls_left, side="right"))
    return rb, bb, int(wickets_left)


def fit_empirical(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    """Base-rate table: (runs bucket, balls bucket, wickets) cells with at least ``EMPIRICAL_MIN_N`` rows; otherwise the
    (runs bucket, balls bucket) cell; otherwise the overall rate."""
    rb = np.searchsorted(RUN_EDGES, arrays["runs_needed"], side="right").astype(np.int64)
    bb = np.searchsorted(BALL_EDGES, arrays["balls_left"], side="right").astype(np.int64)
    wl = arrays["wickets_left"].astype(np.int64)
    y = arrays["y"]
    fine = _cells((rb * 100 + bb) * 100 + wl, y)
    coarse = _cells(rb * 100 + bb, y)
    return {
        "fine": {k: v for k, v in fine.items() if v[1] >= EMPIRICAL_MIN_N},
        "coarse": {k: v for k, v in coarse.items() if v[1] >= EMPIRICAL_MIN_N},
        "overall": float(np.mean(y)) if len(y) else 0.5,
        "min_n": EMPIRICAL_MIN_N,
    }


def empirical_probability(table: Mapping[str, Any], runs_needed: float, balls_left: float, wickets_left: float) -> float:
    rb, bb, wl = _cell_key(runs_needed, balls_left, wickets_left)
    hit = table["fine"].get((rb * 100 + bb) * 100 + wl)
    if hit is not None:
        return hit[0]
    hit = table["coarse"].get(rb * 100 + bb)
    return hit[0] if hit is not None else table["overall"]


def empirical_array(table: Mapping[str, Any], arrays: dict[str, np.ndarray]) -> np.ndarray:
    return np.asarray([
        empirical_probability(table, r, b, w)
        for r, b, w in zip(arrays["runs_needed"], arrays["balls_left"], arrays["wickets_left"])
    ], dtype=np.float64)


# ----------------------------------------------------------------------------- estimators
def make_estimator(kind: str, seed: int = SEED) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    features = CORE_FEATURES if kind == CORE else PLAYER_FEATURES
    cst = [MONOTONE.get(f, 0) for f in features]
    return HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0,
        monotonic_cst=cst, early_stopping=False, random_state=seed,
    )


def fit_models(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    """Core and players estimators on every row of the table."""
    y = arrays["y"].astype(int)
    return {kind: make_estimator(kind).fit(design_matrix(arrays, kind), y) for kind in (CORE, PLAYERS)}


def predict(estimator: Any, X: np.ndarray) -> np.ndarray:
    return np.asarray(estimator.predict_proba(X)[:, 1], dtype=np.float64)


# ----------------------------------------------------------------------------- metrics
def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    if len(y) == 0:
        return _NAN
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_table(p: np.ndarray, y: np.ndarray, edges: Sequence[float] = TAIL_BIN_EDGES) -> list[dict[str, Any]]:
    """Per probability bin: rows, mean predicted probability and observed win rate (NaN for an empty bin)."""
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        n = int(m.sum())
        out.append({
            "bin": f"[{lo:.2f}, {min(hi, 1.0):.2f})",
            "rows": n,
            "mean_predicted": float(np.mean(p[m])) if n else _NAN,
            "observed": float(np.mean(y[m])) if n else _NAN,
        })
    return out


# ----------------------------------------------------------------------------- live model
@dataclass(frozen=True)
class ChaseEstimate:
    p: float  # P(the chasing side wins)
    source: str  # "players" or "core"
    p_core: float
    p_players: Optional[float]
    p_empirical: float


class ChaseModel:
    """A fitted chase model with its career record and name index (the payload of ``scripts/train_chase_model.py``)."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if payload.get("version") != VERSION:
            raise ValueError(f"chase model version {payload.get('version')!r} is not {VERSION!r}")
        self.core = payload[CORE]
        self.players = payload[PLAYERS]
        self.empirical = payload["empirical"]
        self.career: dict[str, tuple[float, float, float]] = {k: tuple(v) for k, v in payload["career"].items()}
        self.keys: dict[str, str] = dict(payload.get("career_keys") or {})
        self.meta = {k: v for k, v in payload.items() if k not in (CORE, PLAYERS, "empirical", "career", "career_keys")}

    @classmethod
    def load(cls, path: Path) -> "ChaseModel":
        import joblib

        return cls(joblib.load(Path(path)))

    def career_for(self, name: Any) -> Optional[tuple[float, float, float]]:
        """The career record (runs, balls, outs) of a batter named as in Cricsheet or as in the live feed, or None."""
        text = str(name or "").strip()
        if not text:
            return None
        if text in self.career:
            return self.career[text]
        cricsheet = self.keys.get(name_key(text), "")
        return self.career.get(cricsheet) if cricsheet else None

    def estimate(
        self,
        *,
        runs_needed: float,
        balls_left: float,
        wickets_left: float,
        batters: Optional[tuple[Mapping[str, Any], Mapping[str, Any]]] = None,
    ) -> ChaseEstimate:
        """P(chasing side wins). ``batters`` is (striker, non-striker) with ``name``, ``runs`` and ``balls`` in this
        innings; the players model is used only when both names resolve to career records."""
        p_emp = empirical_probability(self.empirical, runs_needed, balls_left, wickets_left)
        core_x = np.asarray([feature_vector(CORE, runs_needed=runs_needed, balls_left=balls_left,
                                            wickets_left=wickets_left, striker=(0.0, 0.0), non_striker=(0.0, 0.0))])
        p_core = float(predict(self.core, core_x)[0])
        if batters is not None:
            s, n = batters
            sc, nc = self.career_for(s.get("name")), self.career_for(n.get("name"))
            if sc is not None and nc is not None and sc[1] > 0 and nc[1] > 0:  # matched: both have career balls
                x = feature_vector(
                    PLAYERS, runs_needed=runs_needed, balls_left=balls_left, wickets_left=wickets_left,
                    striker=(float(s.get("runs") or 0), float(s.get("balls") or 0)),
                    non_striker=(float(n.get("runs") or 0), float(n.get("balls") or 0)),
                    striker_career=sc, non_striker_career=nc,
                )
                p_pl = float(predict(self.players, np.asarray([x]))[0])
                return ChaseEstimate(p_pl, PLAYERS, p_core, p_pl, p_emp)
        return ChaseEstimate(p_core, CORE, p_core, None, p_emp)


_cache: dict[str, tuple[tuple[int, int], Optional[ChaseModel]]] = {}


def load_current(root: Optional[Path] = None) -> Optional[ChaseModel]:
    """The active model named by ``models/chase/current.json`` under ``root`` (default: the repository), or None when
    there is none or it cannot be read. Re-read only when a file changes (the trader calls this every cycle)."""
    base = Path(root) if root is not None else CURRENT_PATH.parents[2]
    path = base / "models" / "chase" / "current.json"
    if not path.exists():
        return None
    try:
        pointer = json.loads(path.read_text(encoding="utf-8"))
        model_path = base / pointer["model"]
        stamp = (path.stat().st_mtime_ns, model_path.stat().st_mtime_ns if model_path.exists() else -1)
    except Exception:
        return None
    key = str(path)
    if key in _cache and _cache[key][0] == stamp:
        return _cache[key][1]
    try:
        model: Optional[ChaseModel] = ChaseModel.load(model_path)
    except Exception:
        model = None
    _cache[key] = (stamp, model)
    return model


__all__ = [
    "BALLS", "CORE", "CORE_FEATURES", "ChaseEstimate", "ChaseFacts", "ChaseModel", "MatchFacts", "PLAYERS",
    "PLAYER_FEATURES", "RowTable", "VERSION", "build_rows", "calibration_table", "design_matrix",
    "empirical_array", "empirical_probability", "feature_vector", "fit_empirical", "fit_models", "load_current",
    "log_loss", "matched_rows", "name_key", "predict", "summarise_match",
]
