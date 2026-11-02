"""
Conservative alignment of Cricsheet ball-by-ball data to real price ticks (S36).

Cricsheet has every delivery but no timestamps. Betfair files have timestamps and prices
but no score. This module estimates when each delivery was bowled and releases its match
state (score, wickets, overs, innings, target) only ``safety_lag`` seconds after the
estimate, so that a backtest never sees a state before the market could have.

Estimation, per market:
  * anchor innings 1 at the first in-play tick;
  * spread the deliveries with a pace model (seconds per delivery, per wicket, per over change);
  * detect the innings break as the longest flat stretch of the price series near the predicted
    end of innings 1, fit one pace factor to the observed innings-1 duration, and anchor
    innings 2 at the first price move after the break.

Errors are asymmetric. A state shown early is a fake edge; a state shown late only makes the
result pessimistic. The release lag is therefore the 95th percentile of the early error
(at least 60 s) measured on recorded matches, and falls back to 300 s when there are fewer than
five validation matches, or when more than 1 % of states would still be released early. A market
with only one anchor (no innings break found) is withheld: no lag bounds its pace error.

Ambiguous matches (two candidate markets for one Cricsheet file, or two candidate files for one
market) are rejected, not guessed.

Known gaps: the pace constants are defaults, not fitted (no LotusBook-recorded matches with
Cricsheet files were available); wicket-ball snapping to price jumps is not implemented.

Usage:
    python scripts/align_ball_by_ball.py --real data/real/betfair --cricsheet data/raw --out data/real/aligned
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import gzip
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.build_real_price_corpus import load_real_corpus, market_relpath  # noqa: E402
from scripts.cricsheet_harmonizer import canonicalize_team  # noqa: E402
from shared.logging import setup_logging  # noqa: E402
from strategy.fair_value import family_of  # noqa: E402

logger = setup_logging("align_ball_by_ball")

SCHEMA_VERSION = 1
SAFETY_QUANTILE = 0.95
LAG_FLOOR_S = 60.0
FALLBACK_LAG_S = 300.0  # synthetic p100 early error was 171 s; keep a wide margin until pace is fitted
MIN_VALIDATION_MATCHES = 5
MAX_EARLY_FRACTION = 0.01
MIN_ANCHORS_FOR_CALIBRATED_LAG = 2
PACE_FACTOR_BOUNDS = (0.7, 1.5)
FLAT_LOGIT_EPS = 0.01
WINDOW_FLOOR_S = 1800.0
WINDOW_FRACTION = 0.35
PACE_CONSTANTS_SOURCE = "default_unfitted"
SUPPORTED_FAMILIES = ("t20", "odi")
CRICKET_FORMATS = {"T20": "T20", "IT20": "T20", "ODI": "ODI"}
NON_WICKET_KINDS = ("retired hurt", "retired not out")
DEFAULT_REAL = _REPO_ROOT / "data" / "real" / "betfair"
DEFAULT_CRICSHEET = _REPO_ROOT / "data" / "raw"
DEFAULT_OUT = _REPO_ROOT / "data" / "real" / "aligned"
STATE_KEYS = ("score", "wickets", "overs", "innings", "target", "balls_remaining", "run_rate", "req_run_rate")


class AlignmentError(ValueError):
    """The market cannot be aligned to its Cricsheet match (missing anchor or innings)."""


class UnsupportedFormat(ValueError):
    """The family has no pace model (multi-day or unknown formats)."""


class SingleAnchorError(AlignmentError):
    """No innings break was found, so the innings-2 anchor is a guess and the market is withheld."""


@dataclass(frozen=True)
class Pace:
    """Format-typical pace. Defaults are unfitted; see PACE_CONSTANTS_SOURCE."""

    seconds_per_delivery: float
    seconds_per_wicket: float
    seconds_per_over: float
    innings_break_s: float
    min_break_s: float
    max_balls: int


PACE: dict[str, Pace] = {
    "t20": Pace(
        seconds_per_delivery=30.0, seconds_per_wicket=60.0, seconds_per_over=20.0,
        innings_break_s=20 * 60, min_break_s=8 * 60, max_balls=120,
    ),
    "odi": Pace(
        seconds_per_delivery=32.0, seconds_per_wicket=60.0, seconds_per_over=20.0,
        innings_break_s=40 * 60, min_break_s=15 * 60, max_balls=300,
    ),
}


@dataclass(frozen=True)
class SafetyLag:
    seconds: float
    source: str


DEFAULT_SAFETY_LAG = SafetyLag(FALLBACK_LAG_S, "fallback_unvalidated")


@dataclass(frozen=True)
class Delivery:
    """One Cricsheet delivery with the match state as it stands after it."""

    innings: int
    legal: bool
    wicket: bool
    ends_over: bool
    score: int
    wickets: int
    legal_balls: int
    target: Optional[int]


@dataclass(frozen=True)
class CricketMatch:
    """A Cricsheet match. ``deliveries`` is empty in the lightweight index."""

    name: str
    rel: str
    file: Path
    family: str
    match_format: str
    match_date: date
    teams: frozenset[str]
    deliveries: tuple[Delivery, ...] = ()


@dataclass(frozen=True)
class Alignment:
    times: tuple[datetime, ...]
    anchors: int
    pace_factor: float
    pace_residual: Optional[float]
    break_start: Optional[datetime]
    break_end: Optional[datetime]


@dataclass(frozen=True)
class RecordedMatch:
    """LotusBook-recorded ground truth: price ticks plus the observed time of each state."""

    match_id: str
    family: str
    ticks: tuple[dict[str, Any], ...]
    deliveries: tuple[Delivery, ...]
    truth: tuple[tuple[int, datetime], ...]


@dataclass(frozen=True)
class ValidationReport:
    matches: int
    states: int
    median_error_s: Optional[float]
    p95_error_s: Optional[float]
    p95_early_s: Optional[float]
    candidate_lag_s: Optional[float]
    early_fraction_at_lag: Optional[float]
    safety_lag: SafetyLag

    def as_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "states": self.states,
            "median_error_s": _round(self.median_error_s),
            "p95_error_s": _round(self.p95_error_s),
            "p95_early_s": _round(self.p95_early_s),
            "candidate_lag_s": _round(self.candidate_lag_s),
            "early_fraction_at_lag": _round(self.early_fraction_at_lag, 4),
            "safety_lag_s": self.safety_lag.seconds,
            "lag_source": self.safety_lag.source,
        }


@dataclass(frozen=True)
class MatchResult:
    matched: tuple[tuple[dict[str, Any], CricketMatch], ...]
    unmatched: tuple[dict[str, str], ...]
    rejected: tuple[dict[str, str], ...]


# ================================================================
# Cricsheet parsing and index
# ================================================================


def _canonical(name: str) -> str:
    return canonicalize_team(name) or name.strip()


def parse_cricket_deliveries(data: dict[str, Any]) -> tuple[Delivery, ...]:
    """Walk the first two non-super-over innings and record the state after every delivery.

    Wides and no-balls do not count as legal balls; wickets with kinds that are not dismissals
    (retired hurt) are not counted. The arithmetic follows ``prepare_specialist_datasets.py``.
    """
    innings_list = [inn for inn in data.get("innings", []) if not inn.get("super_over")][:2]
    deliveries: list[Delivery] = []
    first_total: Optional[int] = None
    for idx, inn in enumerate(innings_list, start=1):
        target: Optional[int] = None
        if idx == 2:
            explicit = (inn.get("target") or {}).get("runs")
            if explicit is not None:
                target = int(explicit)
            elif first_total is not None:
                target = first_total + 1
        score = wickets = legal_balls = 0
        for over in inn.get("overs", []):
            for delivery in over.get("deliveries", []):
                extras = delivery.get("extras") or {}
                legal = not ("wides" in extras or "noballs" in extras)
                score += int((delivery.get("runs") or {}).get("total", 0) or 0)
                dismissals = [
                    w for w in delivery.get("wickets", [])
                    if str(w.get("kind", "")).lower() not in NON_WICKET_KINDS
                ]
                wickets += len(dismissals)
                if legal:
                    legal_balls += 1
                deliveries.append(Delivery(
                    innings=idx,
                    legal=legal,
                    wicket=bool(dismissals),
                    ends_over=legal and legal_balls % 6 == 0,
                    score=score,
                    wickets=wickets,
                    legal_balls=legal_balls,
                    target=target,
                ))
        if idx == 1:
            first_total = score
    return tuple(deliveries)


def _entry_from_info(info: dict[str, Any], path: Path, root: Path) -> Optional[CricketMatch]:
    fmt = CRICKET_FORMATS.get(str(info.get("match_type", "")).strip().upper())
    if fmt is None:
        return None
    dates = info.get("dates") or []
    teams = info.get("teams") or []
    if not dates or len(teams) != 2:
        return None
    names = frozenset(_canonical(str(t)) for t in teams)
    if len(names) != 2:
        return None
    return CricketMatch(
        name=path.stem,
        rel=path.relative_to(root).as_posix(),
        file=path,
        family=family_of(fmt),
        match_format=fmt,
        match_date=date.fromisoformat(str(dates[0])[:10]),
        teams=names,
    )


def index_cricsheet(root: Path) -> list[CricketMatch]:
    """Index every limited-overs Cricsheet JSON under ``root`` (metadata only, no deliveries)."""
    entries: list[CricketMatch] = []
    for path in sorted(root.rglob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        entry = _entry_from_info(data.get("info") or {}, path, root)
        if entry is not None:
            entries.append(entry)
    return entries


def load_deliveries(entry: CricketMatch) -> CricketMatch:
    with open(entry.file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return replace(entry, deliveries=parse_cricket_deliveries(data))


# ================================================================
# Price series helpers
# ================================================================


def _as_utc(value: Any) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _price(tick: dict[str, Any]) -> Optional[float]:
    for key in ("ltp_home", "back_home"):
        value = tick.get(key)
        if value is not None and float(value) > 1.0:
            return float(value)
    return None


def _logit_of_price(price: float) -> float:
    p = min(1.0 - 1e-4, max(1e-4, 1.0 / price))
    return math.log(p / (1.0 - p))


@dataclass(frozen=True)
class _Quiet:
    start: datetime
    end: datetime


def _quiet_runs(priced: Sequence[tuple[datetime, float]], min_len_s: float) -> list[_Quiet]:
    """Maximal runs of consecutive price pairs whose logit barely moves, at least ``min_len_s`` long."""
    runs: list[_Quiet] = []
    start: Optional[datetime] = None
    end: Optional[datetime] = None

    def close() -> None:
        if start is not None and end is not None and (end - start).total_seconds() >= min_len_s:
            runs.append(_Quiet(start, end))

    for (ta, la), (tb, lb) in zip(priced, priced[1:]):
        if abs(lb - la) < FLAT_LOGIT_EPS:
            if start is None:
                start = ta
            end = tb
        else:
            close()
            start = None
    close()
    return runs


def _detect_break(
    priced: Sequence[tuple[datetime, float]], t0: datetime, predicted_end_s: float, pace: Pace
) -> Optional[_Quiet]:
    """Quiet run whose start is nearest the predicted end of innings 1, inside a window."""
    predicted = t0 + timedelta(seconds=predicted_end_s)
    window = max(WINDOW_FLOOR_S, WINDOW_FRACTION * predicted_end_s)
    best: Optional[_Quiet] = None
    best_dist = math.inf
    for run in _quiet_runs(priced, pace.min_break_s):
        dist = abs((run.start - predicted).total_seconds())
        if dist <= window and dist < best_dist:
            best, best_dist = run, dist
    return best


def _offsets(deliveries: Sequence[Delivery], pace: Pace) -> tuple[list[float], float]:
    """Seconds from the first ball to each delivery, and the innings end offset."""
    offsets: list[float] = []
    t = 0.0
    for d in deliveries:
        offsets.append(t)
        t += pace.seconds_per_delivery
        if d.wicket:
            t += pace.seconds_per_wicket
        if d.ends_over:
            t += pace.seconds_per_over
    return offsets, t


# ================================================================
# Ball-time estimation and state release
# ================================================================


def align_deliveries(
    deliveries: Sequence[Delivery], price_ticks: Sequence[dict[str, Any]], family: str
) -> Alignment:
    """Estimate the bowled time of every delivery from the price series (see module docstring)."""
    if family not in PACE:
        raise UnsupportedFormat(f"family {family!r} has no pace model")
    pace = PACE[family]
    in_play = sorted(
        ((_as_utc(t["time"]), t) for t in price_ticks if t.get("in_play")), key=lambda item: item[0]
    )
    if not in_play:
        raise AlignmentError("no in-play tick to anchor innings 1")
    first_idx = [i for i, d in enumerate(deliveries) if d.innings == 1]
    second_idx = [i for i, d in enumerate(deliveries) if d.innings == 2]
    if not first_idx or not second_idx:
        raise AlignmentError("both innings are required")

    t0 = in_play[0][0]
    off1, end1 = _offsets([deliveries[i] for i in first_idx], pace)
    off2, _ = _offsets([deliveries[i] for i in second_idx], pace)

    priced: list[tuple[datetime, float]] = []
    for dt, tick in in_play:
        price = _price(tick)
        if price is not None:
            priced.append((dt, _logit_of_price(price)))

    brk = _detect_break(priced, t0, end1, pace) if end1 > 0 else None
    ratio = (brk.start - t0).total_seconds() / end1 if brk is not None else 0.0
    if brk is not None and PACE_FACTOR_BOUNDS[0] <= ratio <= PACE_FACTOR_BOUNDS[1]:
        factor = ratio
        residual: Optional[float] = math.log(ratio)
        anchor2 = next((dt for dt, _ in in_play if dt > brk.end), brk.end)
        anchors = 2
        break_start, break_end = brk.start, brk.end
    else:
        factor = 1.0
        residual = None
        anchor2 = t0 + timedelta(seconds=end1 + pace.innings_break_s)
        anchors = 1
        break_start = break_end = None

    times: list[Optional[datetime]] = [None] * len(deliveries)
    for i, off in zip(first_idx, off1):
        times[i] = t0 + timedelta(seconds=factor * off)
    for i, off in zip(second_idx, off2):
        times[i] = anchor2 + timedelta(seconds=factor * off)
    return Alignment(
        times=tuple(t for t in times if t is not None),
        anchors=anchors,
        pace_factor=factor,
        pace_residual=residual,
        break_start=break_start,
        break_end=break_end,
    )


def estimate_ball_times(
    deliveries: Sequence[Delivery], price_ticks: Sequence[dict[str, Any]], family: str
) -> list[datetime]:
    """Estimated bowled time of each delivery, in input order."""
    return list(align_deliveries(deliveries, price_ticks, family).times)


def _overs_float(legal_balls: int) -> float:
    return legal_balls // 6 + (legal_balls % 6) / 10.0


def _state_fields(d: Delivery, pace: Pace) -> dict[str, Any]:
    balls_remaining = max(0, pace.max_balls - d.legal_balls)
    run_rate = round(d.score / (d.legal_balls / 6.0), 2) if d.legal_balls else 0.0
    req_run_rate: Optional[float] = None
    if d.innings == 2 and d.target is not None and balls_remaining > 0:
        req_run_rate = round(max(0, d.target - d.score) / (balls_remaining / 6.0), 2)
    return {
        "score": d.score,
        "wickets": d.wickets,
        "overs": _overs_float(d.legal_balls),
        "innings": d.innings,
        "target": d.target,
        "balls_remaining": balls_remaining,
        "run_rate": run_rate,
        "req_run_rate": req_run_rate,
    }


def _empty_state() -> dict[str, Any]:
    return {key: None for key in STATE_KEYS}


def enrich(
    real_episode: dict[str, Any],
    cricket: CricketMatch,
    *,
    safety_lag: SafetyLag = DEFAULT_SAFETY_LAG,
) -> dict[str, Any]:
    """Return a copy of the episode whose ticks carry the state released as of each tick time.

    A state is released at ``estimated_time + lag``; release times are non-decreasing because
    estimates are. Ticks before the first release carry ``None`` state. The alignment quality is
    recorded in ``ticks[0]["_meta"]["alignment_quality"]``.
    """
    ticks = real_episode["ticks"]
    if not ticks:
        raise ValueError("episode has no ticks")
    family = real_episode["family"]
    pace = PACE[family]
    alignment = align_deliveries(cricket.deliveries, ticks, family)
    if alignment.anchors < MIN_ANCHORS_FOR_CALIBRATED_LAG:
        # Without a detected innings break the pace error is unbounded (measured up to ~21 min early
        # on synthetic fixtures with default pace), so no lag is safe: withhold the market.
        raise SingleAnchorError("no innings break detected; states withheld")
    lag = safety_lag
    release = [t + timedelta(seconds=lag.seconds) for t in alignment.times]
    states = [_state_fields(d, pace) for d in cricket.deliveries]

    enriched: list[dict[str, Any]] = []
    for tick in ticks:
        k = bisect.bisect_right(release, _as_utc(tick["time"])) - 1
        out = dict(tick)
        out.update(states[k] if k >= 0 else _empty_state())
        enriched.append(out)

    quality = {
        "anchors": alignment.anchors,
        "innings_break_detected": True,
        "pace_factor": round(alignment.pace_factor, 4),
        "pace_residual": _round(alignment.pace_residual, 4),
        "pace_constants": PACE_CONSTANTS_SOURCE,
        "safety_lag_s": lag.seconds,
        "lag_source": lag.source,
        "cricsheet_file": cricket.rel,
        "cricsheet_match_id": Path(cricket.rel).stem,  # S38 evaluator join key (file stem)
    }
    first_meta = dict(ticks[0].get("_meta") or {})
    first_meta["alignment_quality"] = quality
    enriched[0]["_meta"] = first_meta
    return {**real_episode, "ticks": enriched}


# ================================================================
# Market to Cricsheet matching
# ================================================================


def match_markets(episodes: Sequence[dict[str, Any]], entries: Sequence[CricketMatch]) -> MatchResult:
    """Match each real episode to one Cricsheet match, rejecting ambiguous pairs.

    A candidate has the same family, a match date within one day of the market start, and the same
    canonical team set. Two candidate files for one market, or one file claimed by two markets,
    are both rejected.
    """
    by_family: dict[str, list[CricketMatch]] = defaultdict(list)
    for entry in entries:
        by_family[entry.family].append(entry)

    candidates: list[list[CricketMatch]] = []
    for ep in episodes:
        if ep["family"] not in SUPPORTED_FAMILIES:
            candidates.append([])
            continue
        ep_date = _as_utc(ep["start"]).date()
        ep_teams = frozenset({_canonical(ep["team_home"]), _canonical(ep["team_away"])})
        candidates.append([
            e for e in by_family.get(ep["family"], [])
            if abs((e.match_date - ep_date).days) <= 1 and e.teams == ep_teams
        ])
    claims = Counter(c.rel for cands in candidates for c in cands)

    matched: list[tuple[dict[str, Any], CricketMatch]] = []
    unmatched: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for ep, cands in zip(episodes, candidates):
        market = str(ep["market_id"])
        if ep["family"] not in SUPPORTED_FAMILIES:
            rejected.append({"market_id": market, "reason": "unsupported_format", "detail": str(ep["family"])})
        elif not cands:
            unmatched.append({"market_id": market, "reason": "no_cricsheet_match"})
        elif len(cands) > 1:
            rejected.append({"market_id": market, "reason": "ambiguous_cricsheet",
                             "detail": ", ".join(c.rel for c in cands)})
        elif claims[cands[0].rel] > 1:
            rejected.append({"market_id": market, "reason": "ambiguous_market", "detail": cands[0].rel})
        else:
            matched.append((ep, cands[0]))
    return MatchResult(tuple(matched), tuple(unmatched), tuple(rejected))


# ================================================================
# Validation against recorded matches
# ================================================================


def _legal_from_overs(overs: Any) -> int:
    tenths = round(float(overs) * 10)
    return (tenths // 10) * 6 + tenths % 10


def truth_from_observations(
    deliveries: Sequence[Delivery], observations: Sequence[tuple[datetime, int, Any, int, int]]
) -> tuple[tuple[int, datetime], ...]:
    """Map recorded context rows ``(time, innings, overs, score, wickets)`` to delivery indices.

    The earliest time at which a state was recorded is kept per delivery. That time is an upper
    bound on the true bowled time, so the measured error is pessimistic (conservative).
    """
    index: dict[tuple[int, int, int, int], int] = {}
    for i, d in enumerate(deliveries):
        index.setdefault((d.innings, d.legal_balls, d.score, d.wickets), i)
    earliest: dict[int, datetime] = {}
    for time_value, innings, overs, score, wickets in observations:
        if innings is None or overs is None or score is None or wickets is None:
            continue
        key = (int(innings), _legal_from_overs(overs), int(score), int(wickets))
        i = index.get(key)
        if i is None:
            continue
        t = _as_utc(time_value)
        if i not in earliest or t < earliest[i]:
            earliest[i] = t
    return tuple(sorted(earliest.items()))


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(len(ordered), rank) - 1]


def select_safety_lag(errors_s: Sequence[float], n_matches: int) -> SafetyLag:
    """Pick the release lag from signed errors ``true - estimated`` (positive = estimate ran early)."""
    if n_matches < MIN_VALIDATION_MATCHES or not errors_s:
        return SafetyLag(FALLBACK_LAG_S, f"fallback_too_few_matches({n_matches}<{MIN_VALIDATION_MATCHES})")
    early = [max(0.0, e) for e in errors_s]
    candidate = max(LAG_FLOOR_S, _quantile(early, SAFETY_QUANTILE))
    early_fraction = sum(1 for e in errors_s if e > candidate) / len(errors_s)
    if early_fraction <= MAX_EARLY_FRACTION:
        return SafetyLag(candidate, "p95_validated")
    return SafetyLag(FALLBACK_LAG_S, f"fallback_early_fraction({early_fraction:.3f}>{MAX_EARLY_FRACTION})")


def validate_alignment(matches: Sequence[RecordedMatch]) -> ValidationReport:
    """Replace recorded state times with estimates and measure how early the states would release."""
    errors: list[float] = []
    used = 0
    for m in matches:
        if not m.truth:
            continue
        try:
            alignment = align_deliveries(m.deliveries, m.ticks, m.family)
        except (AlignmentError, UnsupportedFormat):
            continue
        if alignment.anchors < MIN_ANCHORS_FOR_CALIBRATED_LAG:
            continue
        used += 1
        errors.extend((true - alignment.times[i]).total_seconds() for i, true in m.truth)

    lag = select_safety_lag(errors, used)
    if not errors:
        return ValidationReport(used, 0, None, None, None, None, None, lag)
    early = [max(0.0, e) for e in errors]
    p95_early = _quantile(early, SAFETY_QUANTILE)
    candidate = max(LAG_FLOOR_S, p95_early)
    return ValidationReport(
        matches=used,
        states=len(errors),
        median_error_s=statistics.median(errors),
        p95_error_s=_quantile(errors, SAFETY_QUANTILE),
        p95_early_s=p95_early,
        candidate_lag_s=candidate,
        early_fraction_at_lag=sum(1 for e in errors if e > lag.seconds) / len(errors),
        safety_lag=lag,
    )


def _recorded_ticks(odds_rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    ticks = []
    for row in odds_rows:
        ticks.append({
            "time": _as_utc(row["time"]).isoformat(),
            "in_play": bool(row.get("is_live")),
            "ltp_home": None,
            "back_home": row.get("back_home"),
        })
    return tuple(ticks)


def _recorded_episode(rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Episode-shaped dict used only for matching (market_id, family, teams, start)."""
    odds = rec.get("odds") or []
    context = rec.get("context") or []
    result = rec.get("result") or {}
    if not odds or not context or not context[0].get("match_format"):
        return None
    return {
        "market_id": rec["match_id"],
        "family": family_of(str(context[0]["match_format"])),
        "team_home": str(result.get("team_home", "")),
        "team_away": str(result.get("team_away", "")),
        "start": _as_utc(odds[0]["time"]).isoformat(),
    }


async def _fetch_recorded() -> list[dict[str, Any]]:
    """Read-only: LotusBook recordings (SELECTs only) via the S16 exporter's fetch helper."""
    from scripts.export_recorded_corpus import _fetch_matches

    return await _fetch_matches()


def _validate_against_db(entries: Sequence[CricketMatch]) -> ValidationReport:
    recorded = asyncio.run(_fetch_recorded())
    by_id = {rec["match_id"]: rec for rec in recorded}
    episodes = [ep for ep in (_recorded_episode(rec) for rec in recorded) if ep is not None]
    result = match_markets(episodes, entries)
    matches: list[RecordedMatch] = []
    for ep, entry in result.matched:
        rec = by_id[ep["market_id"]]
        cricket = load_deliveries(entry)
        observations = [
            (row["time"], row.get("innings"), row.get("overs"), row.get("score"), row.get("wickets"))
            for row in rec["context"]
        ]
        matches.append(RecordedMatch(
            match_id=ep["market_id"],
            family=ep["family"],
            ticks=_recorded_ticks(rec["odds"]),
            deliveries=cricket.deliveries,
            truth=truth_from_observations(cricket.deliveries, observations),
        ))
    logger.info("alignment_validation_matches", recorded=len(recorded), matched=len(matches))
    return validate_alignment(matches)


# ================================================================
# Corpus writing and CLI
# ================================================================


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _encode_ticks(ticks: list[dict[str, Any]]) -> bytes:
    payload = json.dumps(ticks, sort_keys=True, separators=(",", ":"), default=str)
    return gzip.compress(payload.encode("utf-8"), compresslevel=9, mtime=0)


def align_corpus(
    episodes: Sequence[dict[str, Any]],
    entries: Sequence[CricketMatch],
    out: Path,
    *,
    safety_lag: SafetyLag,
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Align every matchable episode, write ``out/<family>/<market>.json.gz`` and ``out/manifest.json``."""
    result = match_markets(episodes, entries)
    out.mkdir(parents=True, exist_ok=True)
    matched_rows: list[dict[str, Any]] = []
    rejected = list(result.rejected)
    for ep, entry in result.matched:
        market = str(ep["market_id"])
        try:
            aligned = enrich(ep, load_deliveries(entry), safety_lag=safety_lag)
        except SingleAnchorError:
            rejected.append({"market_id": market, "reason": "no_innings_break", "detail": entry.rel})
            continue
        except (AlignmentError, UnsupportedFormat, OSError, ValueError) as exc:
            rejected.append({"market_id": market, "reason": "alignment_failed", "detail": type(exc).__name__})
            continue
        rel = market_relpath(ep["family"], market)
        payload = _encode_ticks(aligned["ticks"])
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(payload)
        quality = aligned["ticks"][0]["_meta"]["alignment_quality"]
        matched_rows.append({
            "market_id": market,
            "match_id": ep["match_id"],
            "family": ep["family"],
            "cricsheet_file": entry.rel,
            "cricsheet_match_id": Path(entry.rel).stem,
            "file": rel,
            "sha1": hashlib.sha1(payload).hexdigest(),
            "n_ticks": len(aligned["ticks"]),
            **quality,
        })

    rejected.sort(key=lambda r: (r["market_id"], r["reason"]))
    unmatched = sorted(result.unmatched, key=lambda r: r["market_id"])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "contract": "real tick + state as of released time (S36)",
        "pace_constants": PACE_CONSTANTS_SOURCE,
        "safety_lag": {"seconds": safety_lag.seconds, "source": safety_lag.source,
                       "floor_s": LAG_FLOOR_S, "fallback_s": FALLBACK_LAG_S},
        "validation": validation,
        "counts": {
            "matched": len(matched_rows),
            "unmatched": len(unmatched),
            "rejected": len(rejected),
            "rejected_by_reason": dict(sorted(Counter(r["reason"] for r in rejected).items())),
        },
        "matched": sorted(matched_rows, key=lambda r: (r["family"], r["market_id"])),
        "unmatched": unmatched,
        "rejected": rejected,
    }
    (out / "manifest.json").write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    logger.info("alignment_corpus_written", matched=len(matched_rows), unmatched=len(unmatched),
                rejected=len(rejected), lag_s=safety_lag.seconds, lag_source=safety_lag.source)
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Align Cricsheet ball-by-ball to real price ticks (S36).")
    parser.add_argument("--real", type=Path, default=DEFAULT_REAL, help="Real-price corpus (S35 output)")
    parser.add_argument("--cricsheet", type=Path, default=DEFAULT_CRICSHEET, help="Cricsheet JSON root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory (default data/real/aligned)")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Do not read LotusBook recordings; use the fallback lag")
    args = parser.parse_args(argv)

    episodes = load_real_corpus(out=args.real)
    entries = index_cricsheet(args.cricsheet)
    lag = DEFAULT_SAFETY_LAG
    validation: dict[str, Any] = {"skipped": "--skip-validation"}
    if not args.skip_validation:
        try:
            report = _validate_against_db(entries)
            lag = report.safety_lag
            validation = report.as_dict()
        except Exception as exc:  # noqa: BLE001 - DB is optional; the fallback lag is defined
            validation = {"skipped": f"db_unavailable:{type(exc).__name__}"}
            logger.warning("alignment_validation_skipped", error=type(exc).__name__)

    manifest = align_corpus(episodes, entries, args.out, safety_lag=lag, validation=validation)
    counts = manifest["counts"]
    print(f"markets in corpus: {len(episodes)}; cricsheet entries indexed: {len(entries)}")
    print(f"matched: {counts['matched']}  unmatched: {counts['unmatched']}  rejected: {counts['rejected']}")
    print(f"safety lag: {lag.seconds:.0f} s ({lag.source})")
    print(f"validation: {validation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
