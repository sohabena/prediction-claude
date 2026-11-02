"""S25: in-house venue par and toss priors from Cricsheet, strictly as-of.

Public API (stable; consumed by S38 ``strategy/ratings.py``):

- ``family_for_match_type(match_type) -> str | None``: Cricsheet ``match_type`` to ``t20`` / ``odi`` / ``multiday``.
  ``ODM`` maps to ``odi`` and ``MDM`` to ``multiday``; unknown types return ``None`` and are skipped.
- ``canonical_venue(name, city=None) -> str``: normalised venue key (lowercase, punctuation and the trailing
  ``, <city>`` removed, alias table applied). ``city`` is accepted for uniform call sites; the city tiebreak for
  ambiguous names happens inside :class:`VenuePriors`, as of the query date.
- ``VenuePriors.build(matches)``: ``matches`` are raw Cricsheet match dicts (``{"info": ..., "innings": [...]}``).
- ``VenuePriors.get(venue, family, gender, as_of, city=None) -> dict``: the prior for a venue, computed only from
  matches whose first date is strictly before ``as_of``. Falls back to the (family, gender) average, also as of
  ``as_of``, when the venue has fewer than ``MIN_SAMPLE`` (5) counted first innings; ``fallback=True`` then.
- ``VenuePriors.snapshot() -> dict``: full-period convenience snapshot for the JSON artifact. Backtests must use
  ``get(..., as_of)``, never the snapshot.

Metrics: ``par_first_innings`` is the mean first-innings total of counted innings; ``par_run_rate`` is runs per
over over the same innings; ``chase_win_pct`` and ``toss_winner_win_pct`` are percentages (0-100).
A first innings is counted when it is complete (full quota of overs, or all out) in limited-overs matches, and
whenever it has at least one legal ball in multi-day matches. Super-over innings are ignored.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

MIN_SAMPLE = 5
DEFAULT_MAX_OVERS: Dict[str, int] = {"t20": 20, "odi": 50}

_FAMILY_BY_MATCH_TYPE: Dict[str, str] = {
    "T20": "t20",
    "IT20": "t20",
    "ODI": "odi",
    "ODM": "odi",
    "TEST": "multiday",
    "MDM": "multiday",
}

# Canonical name -> variants that name the same ground but differ beyond punctuation and the trailing city.
# Only the top grounds in the raw Cricsheet set whose names genuinely differ are listed; everything else is
# handled by the normalisation rules.
_VENUE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "M Chinnaswamy Stadium": ("Chinnaswamy Stadium", "M Chinnaswamy Stadium, Bangalore"),
    "MA Chidambaram Stadium": ("M A Chidambaram Stadium", "Chepauk"),
    "Arun Jaitley Stadium": ("Feroz Shah Kotla", "Feroz Shah Kotla Ground"),
    "Narendra Modi Stadium": ("Sardar Patel Stadium, Motera", "Motera Stadium"),
    "New Wanderers Stadium": ("Wanderers Stadium", "Wanderers Cricket Ground"),
    "Kennington Oval": ("The Oval",),
    "Lord's": ("Lords", "Lord's Cricket Ground"),
    "Brisbane Cricket Ground": ("The Gabba", "Gabba"),
    "Punjab Cricket Association Stadium": (
        "Punjab Cricket Association IS Bindra Stadium",
        "PCA Stadium",
    ),
    "R Premadasa Stadium": ("R. Premadasa Stadium", "Premadasa Stadium"),
}

_CITY_SPLIT = re.compile(r",|(?<=[A-Za-z]{3})\.\s")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _normalise(text: str) -> str:
    """Lowercase, replace punctuation with spaces and collapse whitespace."""
    return " ".join(_NON_ALNUM.sub(" ", text.lower()).split())


def _strip_city(name: str) -> str:
    """Drop everything after the first comma (the trailing ', <city>') and normalise."""
    head = _CITY_SPLIT.split(name.strip(), maxsplit=1)[0]
    return _normalise(head)


def _build_alias_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for canonical, variants in _VENUE_ALIASES.items():
        target = _strip_city(canonical)
        for name in (canonical, *variants):
            index[_strip_city(name)] = target
    return index


_ALIAS_INDEX: Dict[str, str] = _build_alias_index()


def canonical_venue(name: Optional[str], city: Optional[str] = None) -> str:
    """Return the normalised venue key, or ``""`` when the name is blank."""
    del city  # accepted for uniform call sites; see module docstring
    base = _strip_city(name or "")
    if not base:
        return ""
    return _ALIAS_INDEX.get(base, base)


def family_for_match_type(match_type: Optional[str]) -> Optional[str]:
    """Map a Cricsheet ``match_type`` to ``t20`` / ``odi`` / ``multiday``; ``None`` if unsupported."""
    return _FAMILY_BY_MATCH_TYPE.get(str(match_type or "").strip().upper())


def _as_iso(value: Any) -> str:
    """Return ``YYYY-MM-DD`` for a date, datetime or ISO-like string; raise ``ValueError`` otherwise."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        raise ValueError(f"not an ISO date: {value!r}")
    return text[:10]


@dataclass(frozen=True)
class _InningsSummary:
    team: str
    runs: int
    legal_balls: int
    wickets: int


def _summarise_innings(innings: Mapping[str, Any]) -> _InningsSummary:
    runs = 0
    legal = 0
    wickets = 0
    for over in innings.get("overs") or []:
        for delivery in over.get("deliveries") or []:
            runs += int((delivery.get("runs") or {}).get("total") or 0)
            extras = delivery.get("extras") or {}
            if "wides" not in extras and "noballs" not in extras:
                legal += 1
            wickets += len(delivery.get("wickets") or [])
    return _InningsSummary(str(innings.get("team") or "").strip(), runs, legal, wickets)


@dataclass(frozen=True)
class _MatchRecord:
    date: str
    family: str
    gender: str
    venue: str
    city: str
    first_runs: Optional[int]
    first_overs: float
    chase_result: Optional[bool]
    toss_result: Optional[bool]


def _max_overs(info: Mapping[str, Any], family: str) -> Optional[int]:
    if family == "multiday":
        return None
    try:
        return int(info.get("overs"))
    except (TypeError, ValueError):
        return DEFAULT_MAX_OVERS[family]


def _extract(match: Mapping[str, Any]) -> Optional[_MatchRecord]:
    info = match.get("info") or {}
    family = family_for_match_type(info.get("match_type"))
    dates = info.get("dates") or []
    if family is None or not dates:
        return None
    try:
        match_date = _as_iso(dates[0])
    except ValueError:
        return None

    gender = _normalise(str(info.get("gender") or "")) or "unknown"
    raw_innings = match.get("innings") or []
    had_super_over = any(bool(i.get("super_over")) for i in raw_innings)
    summaries = [_summarise_innings(i) for i in raw_innings if not i.get("super_over")]

    first_runs: Optional[int] = None
    first_overs = 0.0
    if summaries and summaries[0].legal_balls > 0:
        first = summaries[0]
        cap = _max_overs(info, family)
        complete = cap is None or first.legal_balls >= cap * 6 or first.wickets >= 10
        if complete:
            first_runs = first.runs
            first_overs = first.legal_balls / 6.0

    outcome = info.get("outcome") or {}
    winner = str(outcome.get("winner") or "").strip()
    result = str(outcome.get("result") or "").strip().lower()
    decided_in_play = bool(winner) and not had_super_over and result not in {"tie", "draw", "no result"}

    chase_result: Optional[bool] = None
    if decided_in_play and len(summaries) >= 2 and summaries[1].team:
        chase_result = summaries[1].team == winner

    toss = info.get("toss") or {}
    toss_winner = str(toss.get("winner") or "").strip()
    toss_result: Optional[bool] = None
    if decided_in_play and toss_winner:
        toss_result = toss_winner == winner

    city_raw = str(info.get("city") or "")
    return _MatchRecord(
        date=match_date,
        family=family,
        gender=gender,
        venue=canonical_venue(str(info.get("venue") or "")),
        city=_normalise(city_raw),
        first_runs=first_runs,
        first_overs=first_overs,
        chase_result=chase_result,
        toss_result=toss_result,
    )


# Vector layout: n_first, sum_runs, sum_overs, n_chase, chase_wins, n_toss, toss_wins, n_matches
_ZERO: Tuple[float, ...] = (0.0,) * 8


def _vector(rec: _MatchRecord) -> Tuple[float, ...]:
    counted = rec.first_runs is not None
    return (
        1.0 if counted else 0.0,
        float(rec.first_runs or 0) if counted else 0.0,
        rec.first_overs if counted else 0.0,
        1.0 if rec.chase_result is not None else 0.0,
        1.0 if rec.chase_result else 0.0,
        1.0 if rec.toss_result is not None else 0.0,
        1.0 if rec.toss_result else 0.0,
        1.0,
    )


class _Series:
    """Date-sorted contributions with prefix sums, so a query at ``as_of`` is a single bisect."""

    __slots__ = ("dates", "cum")

    def __init__(self, entries: List[Tuple[str, Tuple[float, ...]]]) -> None:
        entries.sort(key=lambda e: e[0])
        self.dates: List[str] = [d for d, _ in entries]
        cum: List[Tuple[float, ...]] = [_ZERO]
        running = _ZERO
        for _, vec in entries:
            running = tuple(a + b for a, b in zip(running, vec))
            cum.append(running)
        self.cum = cum

    def at(self, cutoff: str) -> Tuple[float, ...]:
        """Totals over entries with date strictly before ``cutoff``."""
        return self.cum[bisect.bisect_left(self.dates, cutoff)]


def _prior_from(vals: Sequence[float]) -> Dict[str, Any]:
    n_first, sum_runs, sum_overs, n_chase, chase_wins, n_toss, toss_wins, n_matches = vals
    return {
        "par_first_innings": round(sum_runs / n_first, 4) if n_first else None,
        "par_run_rate": round(sum_runs / sum_overs, 4) if sum_overs else None,
        "chase_win_pct": round(100.0 * chase_wins / n_chase, 4) if n_chase else None,
        "toss_winner_win_pct": round(100.0 * toss_wins / n_toss, 4) if n_toss else None,
        "n": int(n_first),
        "n_chase": int(n_chase),
        "n_toss": int(n_toss),
        "n_matches": int(n_matches),
    }


class VenuePriors:
    """In-memory venue priors with strict as-of queries. Build once, query many times."""

    def __init__(self) -> None:
        self._entries: Dict[str, List[Tuple[str, Tuple[float, ...]]]] = {}
        self._series: Dict[str, _Series] = {}
        # base venue -> {normalised city -> earliest match date at that city}
        self._city_first: Dict[str, Dict[str, str]] = {}
        # base venue -> sorted earliest dates, one per city; ambiguous as of d when 2+ are before d
        self._city_dates: Dict[str, List[str]] = {}
        self.matches_seen = 0
        self.matches_used = 0
        self.last_match_date = ""

    @classmethod
    def build(cls, matches: Iterable[Mapping[str, Any]]) -> "VenuePriors":
        priors = cls()
        for match in matches:
            priors.matches_seen += 1
            rec = _extract(match)
            if rec is None:
                continue
            priors.matches_used += 1
            if rec.date > priors.last_match_date:
                priors.last_match_date = rec.date
            vec = _vector(rec)
            keys = [f"f|{rec.family}|{rec.gender}"]
            if rec.venue:
                keys.append(f"v|{rec.venue}|{rec.family}|{rec.gender}")
                if rec.city:
                    keys.append(f"vc|{rec.venue}|{rec.city}|{rec.family}|{rec.gender}")
                    seen = priors._city_first.setdefault(rec.venue, {})
                    if rec.city not in seen or rec.date < seen[rec.city]:
                        seen[rec.city] = rec.date
            for key in keys:
                priors._entries.setdefault(key, []).append((rec.date, vec))
        priors._finalise()
        return priors

    def _finalise(self) -> None:
        for key, entries in self._entries.items():
            self._series[key] = _Series(entries)
        self._entries = {}
        for venue, cities in self._city_first.items():
            self._city_dates[venue] = sorted(cities.values())

    def _values(self, key: str, cutoff: str) -> Tuple[float, ...]:
        series = self._series.get(key)
        return series.at(cutoff) if series is not None else _ZERO

    def _is_ambiguous(self, venue: str, cutoff: str) -> bool:
        dates = self._city_dates.get(venue)
        return bool(dates) and bisect.bisect_left(dates, cutoff) >= 2

    def get(
        self,
        venue: Optional[str],
        family: str,
        gender: Optional[str],
        as_of: Any,
        city: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Venue prior using only matches dated strictly before ``as_of``.

        Returns ``par_first_innings``, ``par_run_rate``, ``chase_win_pct``, ``toss_winner_win_pct`` (None when
        the relevant sample is empty), ``n`` (counted first innings used), ``n_chase``, ``n_toss``,
        ``n_matches``, ``venue_n``, ``fallback``, ``group`` (the series used), ``venue``, ``family``, ``gender``
        and ``as_of``.
        """
        cutoff = _as_iso(as_of)
        gen = _normalise(str(gender or "")) or "unknown"
        fam_key = f"f|{family}|{gen}"
        fam_vals = self._values(fam_key, cutoff)

        base = canonical_venue(venue)
        venue_key: Optional[str] = None
        venue_vals: Tuple[float, ...] = _ZERO
        if base:
            city_norm = _normalise(str(city or ""))
            if city_norm and self._is_ambiguous(base, cutoff):
                venue_key = f"vc|{base}|{city_norm}|{family}|{gen}"
            else:
                venue_key = f"v|{base}|{family}|{gen}"
            venue_vals = self._values(venue_key, cutoff)

        venue_n = int(venue_vals[0])
        fallback = venue_n < MIN_SAMPLE
        vals = fam_vals if fallback else venue_vals
        out = _prior_from(vals)
        out.update(
            {
                "venue": base,
                "family": family,
                "gender": gen,
                "as_of": cutoff,
                "venue_n": venue_n,
                "fallback": fallback,
                "group": fam_key if fallback else venue_key,
            }
        )
        return out

    def snapshot(self) -> Dict[str, Any]:
        """Full-period convenience snapshot (no as-of cutoff). Not for backtests."""
        families: Dict[str, Any] = {}
        venues: Dict[str, Any] = {}
        for key in sorted(self._series):
            series = self._series[key]
            vals = series.cum[-1]
            parts = key.split("|")
            if parts[0] == "f":
                _, fam, gen = parts
                families[key] = {"family": fam, "gender": gen, **_prior_from(vals)}
                continue
            if vals[0] < 1:
                continue
            if parts[0] == "v":
                _, base, fam, gen = parts
                city = None
            else:
                _, base, city, fam, gen = parts
            entry = _prior_from(vals)
            venues[key] = {
                "venue": base,
                "city": city,
                "family": fam,
                "gender": gen,
                "par_score": entry["par_first_innings"],
                "par_rr": entry["par_run_rate"],
                "chase_win_pct": entry["chase_win_pct"],
                "toss_winner_win_pct": entry["toss_winner_win_pct"],
                "sample_size": entry["n"],
                "n_chase": entry["n_chase"],
                "n_toss": entry["n_toss"],
                "min_sample_met": entry["n"] >= MIN_SAMPLE,
            }
        return {
            "basis": "full_period_convenience_snapshot; backtests must use get(..., as_of)",
            "min_sample": MIN_SAMPLE,
            "matches_seen": self.matches_seen,
            "matches_used": self.matches_used,
            "last_match_date": self.last_match_date,
            "families": families,
            "venues": venues,
        }


# Alias kept for the name used in the story's task list.
VenuePriorsLookup = VenuePriors

__all__ = [
    "MIN_SAMPLE",
    "VenuePriors",
    "VenuePriorsLookup",
    "canonical_venue",
    "family_for_match_type",
]
