"""S58 training dataset: pure builders for match facts, Cricsheet linking and as-of price snapshots.

Nothing here touches the database. Every function takes plain row dicts, as read by
``scripts/build_training_dataset.py`` from ``match_training_status``, ``v_match_odds`` and ``match_results``.

Snapshots hold price state only. Labels (the book 60 s and 10 min after the snapshot) are stored in their own keys,
so a model reading ``book`` cannot see the future by accident.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from scraper.match_filter import is_simulated_name
from shared.naming import canonical_team_name

STALE_LIMIT_S = 15 * 60
LATENCY_S = 60
LONG_LATENCY_S = 10 * 60
INPLAY_EVERY_S = 120
PRICE_FIELDS = ("back_home", "lay_home", "back_away", "lay_away")
# (label, seconds relative to match_start_time). 0 means "last price before the start".
DEFAULT_SNAPSHOT_OFFSETS: tuple[tuple[str, int], ...] = (
    ("T-6h", -6 * 3600),
    ("T-60m", -3600),
    ("T-10m", -600),
    ("T-0", 0),
)
# Listing rows win ties against match-page rows at the same timestamp (the view prefers listing too).
_SOURCE_RANK = {"listing": 1, "match_page": 0}

# First-class domestic cricket is a test family (S67): Plunket Shield, Sheffield Shield, Ranji Trophy, County
# Championship.
_TEST_RE = re.compile(
    r"\btests?\b|\bplunket\b|\bsheffield shield\b|\branji trophy\b|\bcounty championship\b", re.IGNORECASE
)
# List A / one-day competitions (S67): Ford Trophy, Marsh Cup, ICC CWC League Two, and any "One-Day" name.
_ODI_RE = re.compile(
    r"\bodis?\b|one[- ]day|\bodm\b|\blist a\b|50[- ]over|\bford trophy\b|\bmarsh cup\b|\bleague two\b", re.IGNORECASE
)
_T20_RE = re.compile(
    r"t20|twenty ?20|\bipl\b|indian premier league|big bash|\bbbl\b|\bwbbl\b|\bpsl\b|pakistan super league"
    r"|caribbean premier league|\bcpl\b|bangladesh premier league|\bbpl\b|vitality blast|\bthe hundred\b"
    r"|\bsa20\b|nepal premier league|\bgt20\b|\bmlc\b|\bilt20\b|syed mushtaq ali|premier league srl"
    r"|\bd20\b|\bblast\b|\bsmash\b",
    re.IGNORECASE,
)
# Cricsheet ``info.match_type`` to family, used only when the competition and name give no token (S67).
_FAMILY_BY_CRICSHEET_TYPE: dict[str, str] = {
    "T20": "t20", "IT20": "t20", "ODI": "odi", "ODM": "odi", "TEST": "test", "MDM": "test",
}


# --------------------------------------------------------------------------- helpers
def _to_dt(value: Any) -> Optional[datetime]:
    """Parse a datetime or ISO string; naive values are taken as UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_date(value: Any) -> Optional[date]:
    """Parse a date, a datetime or a 'YYYY-MM-DD' prefix (Cricsheet ``info.dates`` entries)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def team_key(name: Any, team_alias: Optional[Mapping[str, str]] = None) -> str:
    """Comparison key for a team name: SRL-normalised, optionally aliased, case-folded."""
    text = canonical_team_name(name)
    if team_alias:
        text = team_alias.get(text, text)
    return text.casefold()


def match_family(competition: Any, name: Any = None, match_type: Any = None) -> str:
    """Return ``t20``, ``odi``, ``test`` or ``unknown`` from the competition and match name.

    Conflicting tokens (for example a Test and a T20 in one label) return ``unknown``. When the label has no
    family token at all, a Cricsheet ``match_type`` (from the linked match) decides, if it is a known type.
    """
    text = " ".join(str(part) for part in (competition, name) if part)
    found = {
        family
        for family, pattern in (("test", _TEST_RE), ("odi", _ODI_RE), ("t20", _T20_RE))
        if pattern.search(text)
    }
    if len(found) == 1:
        return found.pop()
    if found:
        return "unknown"
    return _FAMILY_BY_CRICSHEET_TYPE.get(str(match_type or "").strip().upper(), "unknown")


def group_by_match(rows: Iterable[Mapping[str, Any]], key: str = "match_id") -> dict[str, list[dict[str, Any]]]:
    """Group rows by ``match_id`` (as a string), keeping their order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(dict(row))
    return grouped


# --------------------------------------------------------------------------- Cricsheet linking
@dataclass(frozen=True)
class _CricsheetEntry:
    match_id: str
    first: str
    second: str
    day: date


class CricsheetIndex:
    """Cricsheet matches indexed by unordered team pair, built from ``info`` (teams, dates)."""

    def __init__(self, entries: Iterable[Mapping[str, Any]], *, team_alias: Optional[Mapping[str, str]] = None) -> None:
        self._alias = dict(team_alias) if team_alias else None
        self._by_pair: dict[frozenset, list[_CricsheetEntry]] = {}
        self._match_types: dict[str, str] = {}
        self.entries = 0
        for entry in entries:
            teams = list(entry.get("teams") or [])
            if len(teams) != 2:
                continue
            first, second = team_key(teams[0], self._alias), team_key(teams[1], self._alias)
            if not first or first == second:
                continue
            days = [d for d in (_to_date(x) for x in (entry.get("dates") or [])) if d is not None]
            if not days:
                continue
            self.entries += 1
            self._match_types[str(entry["match_id"])] = str(entry.get("match_type") or "")
            for day in days:
                self._by_pair.setdefault(frozenset((first, second)), []).append(
                    _CricsheetEntry(str(entry["match_id"]), first, second, day)
                )

    def link_detail(self, match_row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Return ``{cricsheet_match_id, reversed, day_delta}`` or None.

        Both canonical team names must match (either order; ``reversed`` is True when Cricsheet lists the
        LotusBook away side first) and the Cricsheet date must be within one day of ``match_start_time``.
        Two different Cricsheet matches at the same distance make the link ambiguous, so it returns None.
        """
        home = team_key(match_row.get("team_home"), self._alias)
        away = team_key(match_row.get("team_away"), self._alias)
        start = _to_dt(match_row.get("match_start_time"))
        if not home or not away or home == away or start is None:
            return None
        target = start.date()
        candidates = []
        for entry in self._by_pair.get(frozenset((home, away)), []):
            delta = abs((entry.day - target).days)
            if delta <= 1:
                candidates.append((delta, entry))
        if not candidates:
            return None
        best = min(delta for delta, _ in candidates)
        best_entries = [entry for delta, entry in candidates if delta == best]
        if len({entry.match_id for entry in best_entries}) != 1:
            return None
        chosen = best_entries[0]
        return {"cricsheet_match_id": chosen.match_id, "reversed": chosen.first != home, "day_delta": best}

    def link_cricsheet(self, match_row: Mapping[str, Any]) -> Optional[str]:
        """Return the Cricsheet match id for a registry row, or None."""
        detail = self.link_detail(match_row)
        return detail["cricsheet_match_id"] if detail else None

    def match_type_of(self, cricsheet_match_id: Any) -> str:
        """Cricsheet ``info.match_type`` of an indexed match (``""`` when unknown)."""
        return self._match_types.get(str(cricsheet_match_id or ""), "")


def link_cricsheet(match_row: Mapping[str, Any], cricsheet_index: CricsheetIndex) -> Optional[str]:
    """Return a Cricsheet match id or None (see :meth:`CricsheetIndex.link_detail`)."""
    return cricsheet_index.link_cricsheet(match_row)


# --------------------------------------------------------------------------- match facts
def match_record(
    match_row: Mapping[str, Any],
    result_row: Optional[Mapping[str, Any]] = None,
    cricsheet_link: Any = None,
) -> dict[str, Any]:
    """Match facts and outcome label for one recorded match.

    ``home_won`` is 1 or 0 only for a ``win`` whose winner is one of the two registry teams. Ties, unresolved
    results, missing results and unknown winners give None.
    ``cricsheet_link`` may be a Cricsheet id, a :meth:`CricsheetIndex.link_detail` dict, or None.
    """
    home = str(match_row.get("team_home") or "")
    away = str(match_row.get("team_away") or "")
    competition = str(match_row.get("competition") or "")
    name = match_row.get("name")
    start, start_basis = effective_start(match_row)
    result = dict(result_row) if result_row else {}
    result_type = result.get("result_type") or None
    winner = result.get("winner") or None

    home_won: Optional[int] = None
    winner_matched = False
    if winner and str(result_type or "").lower() == "win":
        winner_key = team_key(winner)
        if winner_key == team_key(home):
            home_won, winner_matched = 1, True
        elif winner_key == team_key(away):
            home_won, winner_matched = 0, True

    match_type = None
    if isinstance(cricsheet_link, Mapping):
        cricsheet_id = cricsheet_link.get("cricsheet_match_id")
        reversed_order = cricsheet_link.get("reversed")
        day_delta = cricsheet_link.get("day_delta")
        match_type = cricsheet_link.get("match_type")
    else:
        cricsheet_id = cricsheet_link
        reversed_order = None
        day_delta = None

    return {
        "match_id": str(match_row["match_id"]),
        "team_home": home,
        "team_away": away,
        "competition": competition,
        "match_start_time": _iso(match_row.get("match_start_time")),
        "actual_start_time": _iso(match_row.get("actual_start_time")),
        "start_basis": start_basis if start else None,
        "family": match_family(competition, name, match_type),
        "is_simulated": bool(match_row.get("is_simulated"))
        or is_simulated_name(home, away, competition),
        "training_status": match_row.get("training_status"),
        "result_type": result_type,
        "winner": winner,
        "winner_matched": winner_matched,
        "home_won": home_won,
        "result_source": result.get("source") if result else None,
        "result_completed_at": _iso(result.get("completed_at")),
        "cricsheet_match_id": cricsheet_id,
        "cricsheet_reversed": reversed_order,
        "cricsheet_day_delta": day_delta,
    }


def _iso(value: Any) -> Optional[str]:
    dt = _to_dt(value)
    return dt.isoformat() if dt else None


def effective_start(match_row: Mapping[str, Any]) -> tuple[Optional[datetime], str]:
    """The start every snapshot is measured from: ``actual_start_time`` (S86) when known, else ``match_start_time``.

    Returns ``(start, basis)`` with basis ``"actual_start_time"`` or ``"match_start_time"``; ``(None, ...)`` when
    neither is set. A registry row without the S86 column (old database) simply has no ``actual_start_time`` key
    and falls back to the scheduled start.
    """
    actual = _to_dt(match_row.get("actual_start_time"))
    if actual is not None:
        return actual, "actual_start_time"
    return _to_dt(match_row.get("match_start_time")), "match_start_time"


# --------------------------------------------------------------------------- as-of books
def _price_series(price_rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[list, list]]:
    """Per price field, the sorted (time, rank, value, source) points where that field is present."""
    series: dict[str, tuple[list, list]] = {}
    for field in PRICE_FIELDS:
        points = []
        for row in price_rows:
            value = row.get(field)
            ts = _to_dt(row.get("time"))
            if value is None or ts is None:
                continue
            source = row.get("source")
            points.append((ts, _SOURCE_RANK.get(source, 0), float(value), source))
        points.sort(key=lambda p: (p[0], p[1]))
        series[field] = (points, [p[0] for p in points])
    return series


def _asof(series: tuple[list, list], t: datetime) -> tuple[Optional[float], Optional[int], Optional[str]]:
    """Most recent value at or before ``t``, or (None, None, None) if it is older than the stale limit."""
    points, times = series
    index = bisect.bisect_right(times, t)
    if index == 0:
        return None, None, None
    ts, _, value, source = points[index - 1]
    age = (t - ts).total_seconds()
    if age > STALE_LIMIT_S:
        return None, None, None
    return value, int(age), source


def _book(series: dict[str, tuple[list, list]], t: datetime) -> dict[str, Any]:
    """As-of book at ``t``: per runner, back (takeable to back) and lay (takeable to lay) with staleness."""
    book: dict[str, Any] = {}
    for side in ("home", "away"):
        leg: dict[str, Any] = {}
        for kind in ("back", "lay"):
            value, stale, source = _asof(series[f"{kind}_{side}"], t)
            leg[kind] = value
            leg[f"stale_s_{kind}"] = stale
            leg[f"source_{kind}"] = source
        book[side] = leg
    return book


def _has_prices(book: Mapping[str, Any]) -> bool:
    return any(book[side][kind] is not None for side in ("home", "away") for kind in ("back", "lay"))


def _snapshot(
    match_id: str,
    label: str,
    t: datetime,
    start: datetime,
    series: dict[str, tuple[list, list]],
) -> dict[str, Any]:
    book = _book(series, t)
    return {
        "snapshot_id": f"{match_id}:{label}",
        "match_id": match_id,
        "label": label,
        "t": t.isoformat(),
        "time_to_start_s": int(round((start - t).total_seconds())),
        "in_play": t >= start,
        "has_prices": _has_prices(book),
        "book": book,
        # Labels for latency-aware evaluation only: a person acts 60 s after the signal, so the price then
        # (and 10 min later) is what a bet could have taken. Never a model input.
        "book_60s": _book(series, t + timedelta(seconds=LATENCY_S)),
        "book_10m": _book(series, t + timedelta(seconds=LONG_LATENCY_S)),
    }


def build_snapshots(
    price_rows: Sequence[Mapping[str, Any]],
    match_row: Mapping[str, Any],
    *,
    snapshot_offsets: Sequence[tuple[str, int]] = DEFAULT_SNAPSHOT_OFFSETS,
    inplay_every_s: int = INPLAY_EVERY_S,
) -> list[dict[str, Any]]:
    """Price snapshots for one match: pre-match offsets, then every ``inplay_every_s`` from the start.

    The start is ``actual_start_time`` when known, else ``match_start_time`` (see :func:`effective_start`); the
    pre-match / in-play split (``in_play``) is taken from that start.
    In-play snapshots run from that start up to the last recorded price. A pre-match offset of 0 is
    taken as the last instant before the start. Each snapshot keeps its as-of book; a field older than
    ``STALE_LIMIT_S`` is None. No row later than the snapshot time feeds ``book``.
    """
    start, _basis = effective_start(match_row)
    match_id = str(match_row["match_id"])
    if start is None or not price_rows:
        return []
    times = [t for t in (_to_dt(row.get("time")) for row in price_rows) if t is not None]
    if not times:
        return []
    last = max(times)
    series = _price_series(price_rows)

    snapshots = []
    for label, offset_s in snapshot_offsets:
        if offset_s == 0:
            t = start - timedelta(microseconds=1)
        else:
            t = start + timedelta(seconds=offset_s)
        snapshots.append(_snapshot(match_id, label, t, start, series))

    if inplay_every_s > 0:
        step = 0
        while True:
            t = start + timedelta(seconds=step * inplay_every_s)
            if t > last:
                break
            snapshots.append(_snapshot(match_id, f"IP+{step * inplay_every_s // 60}m", t, start, series))
            step += 1
    return snapshots


# --------------------------------------------------------------------------- dataset
def build_dataset(
    match_rows: Sequence[Mapping[str, Any]],
    price_rows_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
    result_rows_by_match: Mapping[str, Mapping[str, Any]],
    cricsheet_index: Optional[CricsheetIndex] = None,
    *,
    snapshot_offsets: Sequence[tuple[str, int]] = DEFAULT_SNAPSHOT_OFFSETS,
    inplay_every_s: int = INPLAY_EVERY_S,
) -> dict[str, Any]:
    """Build match and snapshot rows for every registry match that has at least one recorded price.

    Returns ``{"matches": [...], "snapshots": [...], "stats": {...}}``.
    """
    matches: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    stats: Counter = Counter()
    registry_ids: set[str] = set()
    for row in match_rows:
        match_id = str(row["match_id"])
        registry_ids.add(match_id)
        prices = list(price_rows_by_match.get(match_id) or [])
        if not prices:
            stats["matches_without_prices"] += 1
            continue
        if effective_start(row)[0] is None:
            stats["matches_without_start"] += 1
        link = cricsheet_index.link_detail(row) if cricsheet_index else None
        if link is not None and cricsheet_index is not None:
            link = {**link, "match_type": cricsheet_index.match_type_of(link["cricsheet_match_id"])}
        matches.append(match_record(row, result_rows_by_match.get(match_id), link))
        snapshots.extend(
            build_snapshots(prices, row, snapshot_offsets=snapshot_offsets, inplay_every_s=inplay_every_s)
        )
    stats["price_matches_without_registry"] = len(set(price_rows_by_match) - registry_ids)
    stats["registry_rows"] = len(registry_ids)
    if cricsheet_index is not None:
        stats["cricsheet_entries_indexed"] = cricsheet_index.entries
    return {"matches": matches, "snapshots": snapshots, "stats": dict(stats)}


def summarise(dataset: Mapping[str, Any], *, price_rows_total: Optional[int] = None) -> dict[str, Any]:
    """Counts for the manifest and the dry run."""
    matches = dataset["matches"]
    snapshots = dataset["snapshots"]
    with_result = [m for m in matches if m["home_won"] is not None]
    linked = [m for m in matches if m["cricsheet_match_id"]]
    starts = sorted(m["match_start_time"] for m in matches if m["match_start_time"])
    price_times = sorted(s["t"] for s in snapshots)
    summary: dict[str, Any] = {
        "matches": len(matches),
        "matches_with_result": len(with_result),
        "matches_without_result": len(matches) - len(with_result),
        "matches_with_result_row": sum(1 for m in matches if m["result_source"]),
        "matches_with_cricsheet_link": len(linked),
        "matches_without_cricsheet_link": len(matches) - len(linked),
        "simulated_matches": sum(1 for m in matches if m["is_simulated"]),
        "families": dict(Counter(m["family"] for m in matches)),
        "snapshots": len(snapshots),
        "snapshots_with_prices": sum(1 for s in snapshots if s["has_prices"]),
        "snapshots_in_play": sum(1 for s in snapshots if s["in_play"]),
        "snapshots_by_label": _label_counts(snapshots),
        "match_start_first": starts[0] if starts else None,
        "match_start_last": starts[-1] if starts else None,
        "snapshot_time_first": price_times[0] if price_times else None,
        "snapshot_time_last": price_times[-1] if price_times else None,
    }
    if price_rows_total is not None:
        summary["price_rows"] = price_rows_total
    summary.update(dataset.get("stats", {}))
    return summary


def _label_counts(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Snapshot counts per pre-match label; all in-play snapshots are summed under ``IP+*``."""
    counts: Counter = Counter()
    for snap in snapshots:
        label = snap["label"]
        counts["IP+*" if snap["in_play"] else label] += 1
    return dict(counts)
