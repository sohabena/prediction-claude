"""S24: chronological Cricket Elo ratings with strict as-of queries.

Ratings are keyed by ``(family, gender, team)``. Families are ``t20``, ``odi`` and
``multiday`` (see :func:`family_for_match_type`). Matches must be fed in date order
through :meth:`CricketEloTracker.update`. Every query takes an ``as_of`` date and only
uses matches whose start date is strictly before ``as_of``, so a match never sees its
own result and never sees a later match.

Public API (stable; consumed by ``strategy/ratings.py`` in S38)::

    tracker = CricketEloTracker(k_by_family={"t20": 32, "odi": 24, "multiday": 16}, base=1500.0)
    tracker.update(match_info)                       # match_info: match_id, dates, teams, match_type, gender, outcome
    tracker.rating(team, family, gender, as_of)      # float
    tracker.expected(team_a, team_b, family, gender, as_of)  # P(team_a wins)
    tracker.history()                                # list of {match_id, date, team, family, gender, pre, post, ...}
    tracker.save(path); CricketEloTracker.load(path)

Callers that pass team names through a normalizer (for example the Cricsheet canonical
names) supply it once via ``normalize_team``; the tracker applies it to every team name
it reads or writes.
"""

from __future__ import annotations

import bisect
import gzip
import json
import math
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

BASE_RATING: float = 1500.0
DEFAULT_K_BY_FAMILY: dict[str, float] = {"t20": 32.0, "odi": 24.0, "multiday": 16.0}
MOV_MIN: float = 0.8
MOV_MAX: float = 2.2
TIE_MOV: float = 1.0
FORMAT_VERSION: int = 1
GENDERS: tuple[str, ...] = ("male", "female")

_FAMILY_BY_MATCH_TYPE: dict[str, str] = {
    "T20": "t20",
    "IT20": "t20",
    "ODI": "odi",
    "ODM": "odi",
    "TEST": "multiday",
    "MDM": "multiday",
}


def family_for_match_type(match_type: Any) -> Optional[str]:
    """Map a Cricsheet ``info.match_type`` to an Elo family, or None if unsupported.

    ``ODM`` maps to ``odi`` and ``MDM`` to ``multiday`` on purpose. ``strategy.fair_value.family_of``
    maps ``ODM`` to ``multiday`` and must not be used for Elo.
    """
    return _FAMILY_BY_MATCH_TYPE.get(str(match_type or "").strip().upper())


def margin_multiplier(outcome: Mapping[str, Any]) -> float:
    """Margin-of-victory multiplier in ``[MOV_MIN, MOV_MAX]`` for a decided result.

    Runs: ``ln(1 + runs / 25)``. Wickets: ``ln(1 + wickets / 2)``. Both are clamped.
    An innings victory is the dominant case and returns ``MOV_MAX``. A decided result
    with no usable margin (for example "Awarded") returns 1.0.
    """
    by = outcome.get("by") or {}
    if "innings" in by:
        return MOV_MAX
    runs = by.get("runs")
    wickets = by.get("wickets")
    if runs is not None and float(runs) > 0:
        raw = math.log(1.0 + float(runs) / 25.0)
    elif wickets is not None and float(wickets) > 0:
        raw = math.log(1.0 + float(wickets) / 2.0)
    else:
        return 1.0
    return min(MOV_MAX, max(MOV_MIN, raw))


def _strip(name: str) -> str:
    return str(name).strip()


def _to_iso_date(value: Any) -> str:
    """Normalise a date, datetime or ISO string to ``YYYY-MM-DD``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    return date.fromisoformat(text).isoformat()


class CricketEloTracker:
    """Sequential Elo tracker with zero-lookahead queries."""

    def __init__(
        self,
        k_by_family: Optional[Mapping[str, float]] = None,
        base: float = BASE_RATING,
        normalize_team: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.k_by_family: dict[str, float] = dict(k_by_family or DEFAULT_K_BY_FAMILY)
        self.base: float = float(base)
        self._normalize: Callable[[str], str] = normalize_team or _strip
        self._current: dict[tuple[str, str, str], float] = {}
        # Per key: sorted dates and the post-match rating after each match on that date.
        self._timeline_dates: dict[tuple[str, str, str], list[str]] = {}
        self._timeline_posts: dict[tuple[str, str, str], list[float]] = {}
        self._history: list[dict[str, Any]] = []
        self._match_ids: set[str] = set()
        self._last_date: Optional[str] = None

    # ------------------------------------------------------------------ helpers
    def _key(self, team: str, family: str, gender: str) -> tuple[str, str, str]:
        if family not in self.k_by_family:
            raise ValueError(f"unknown family {family!r}")
        return (family, str(gender).lower(), self._normalize(team))

    def _current_rating(self, key: tuple[str, str, str]) -> float:
        return self._current.get(key, self.base)

    # ------------------------------------------------------------------ queries
    def rating(self, team: str, family: str, gender: str, as_of: Any) -> float:
        """Rating of ``team`` using only matches with start date strictly before ``as_of``."""
        key = self._key(team, family, gender)
        dates = self._timeline_dates.get(key)
        if not dates:
            return self.base
        idx = bisect.bisect_left(dates, _to_iso_date(as_of))
        if idx == 0:
            return self.base
        return self._timeline_posts[key][idx - 1]

    def expected(self, team_a: str, team_b: str, family: str, gender: str, as_of: Any) -> float:
        """Probability that ``team_a`` beats ``team_b`` (as of ``as_of``)."""
        ra = self.rating(team_a, family, gender, as_of)
        rb = self.rating(team_b, family, gender, as_of)
        return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))

    def history(self) -> list[dict[str, Any]]:
        """One record per team per updated match, in update (date) order. Returns copies."""
        return [dict(r) for r in self._history]

    def current_ratings(self) -> list[dict[str, Any]]:
        """Latest rating per ``(family, gender, team)``, sorted by family, gender, then Elo descending."""
        rows = [
            {"team": team, "family": family, "gender": gender, "elo": elo}
            for (family, gender, team), elo in self._current.items()
        ]
        rows.sort(key=lambda r: (r["family"], r["gender"], -r["elo"], r["team"]))
        return rows

    # ------------------------------------------------------------------ updates
    def update(self, match_info: Mapping[str, Any]) -> bool:
        """Apply one match. Returns True if ratings changed, False for a no-result match.

        ``match_info`` uses Cricsheet ``info`` keys plus ``match_id``: ``dates``, ``teams``,
        ``match_type``, ``gender``, ``outcome``. Matches must arrive in non-decreasing date
        order. Invalid input raises ``ValueError`` without changing state.
        """
        match_id = str(match_info.get("match_id") or "").strip()
        if not match_id:
            raise ValueError("match_id is required")
        if match_id in self._match_ids:
            raise ValueError(f"duplicate match_id {match_id!r}")

        family = family_for_match_type(match_info.get("match_type"))
        if family is None or family not in self.k_by_family:
            raise ValueError(f"unsupported match_type {match_info.get('match_type')!r}")

        gender = str(match_info.get("gender") or "").strip().lower()
        if gender not in GENDERS:
            raise ValueError(f"unsupported gender {match_info.get('gender')!r}")

        dates = match_info.get("dates") or []
        if not dates:
            raise ValueError("dates is required")
        match_date = _to_iso_date(dates[0])

        teams = [self._normalize(t) for t in (match_info.get("teams") or [])]
        if len(teams) != 2 or teams[0] == teams[1] or not all(teams):
            raise ValueError(f"need two distinct teams, got {match_info.get('teams')!r}")
        team_a, team_b = teams

        outcome = match_info.get("outcome") or {}
        winner_raw = outcome.get("winner")
        if winner_raw:
            winner = self._normalize(winner_raw)
            if winner == team_a:
                score_a, mov = 1.0, margin_multiplier(outcome)
            elif winner == team_b:
                score_a, mov = 0.0, margin_multiplier(outcome)
            else:
                raise ValueError(f"winner {winner_raw!r} is not one of the teams")
            winner_team: Optional[str] = winner
        elif outcome.get("result") in ("tie", "draw"):
            score_a, mov = 0.5, TIE_MOV
            winner_team = None
        else:
            return False  # no result: nothing to learn, state untouched

        if self._last_date is not None and match_date < self._last_date:
            raise ValueError(
                f"matches must be fed in date order: {match_date} < {self._last_date}"
            )

        key_a = (family, gender, team_a)
        key_b = (family, gender, team_b)
        pre_a = self._current_rating(key_a)
        pre_b = self._current_rating(key_b)
        exp_a = 1.0 / (1.0 + 10.0 ** ((pre_b - pre_a) / 400.0))
        k = float(self.k_by_family[family])
        delta_a = k * mov * (score_a - exp_a)
        post_a = pre_a + delta_a
        post_b = pre_b - delta_a  # symmetric: the opponent's expectation is 1 - exp_a

        for key, pre, post, opponent, score in (
            (key_a, pre_a, post_a, team_b, score_a),
            (key_b, pre_b, post_b, team_a, 1.0 - score_a),
        ):
            team = key[2]
            self._current[key] = post
            self._timeline_dates.setdefault(key, []).append(match_date)
            self._timeline_posts.setdefault(key, []).append(post)
            self._history.append(
                {
                    "match_id": match_id,
                    "date": match_date,
                    "team": team,
                    "family": family,
                    "gender": gender,
                    "opponent": opponent,
                    "pre": pre,
                    "post": post,
                    "score": score,
                    "is_winner": None if winner_team is None else winner_team == team,
                }
            )

        self._match_ids.add(match_id)
        self._last_date = match_date
        return True

    def update_many(self, matches: Iterable[Mapping[str, Any]]) -> int:
        """Apply matches in the given order. Returns the number that changed ratings."""
        return sum(1 for m in matches if self.update(m))

    # ------------------------------------------------------------------ persistence
    def save(self, path: str | Path) -> None:
        """Write config and history as JSON. A ``.gz`` suffix gzips the file."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": FORMAT_VERSION,
            "base": self.base,
            "k_by_family": self.k_by_family,
            "records": self._history,
        }
        text = json.dumps(payload, separators=(",", ":"))
        if target.suffix == ".gz":
            with gzip.open(target, "wt", encoding="utf-8") as fh:
                fh.write(text)
        else:
            target.write_text(text, encoding="utf-8")

    @classmethod
    def load(
        cls, path: str | Path, normalize_team: Optional[Callable[[str], str]] = None
    ) -> "CricketEloTracker":
        """Rebuild a tracker from :meth:`save` output by replaying the history records."""
        source = Path(path)
        if source.suffix == ".gz":
            with gzip.open(source, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
        else:
            payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"unsupported Elo file version {payload.get('format_version')!r}")
        tracker = cls(k_by_family=payload["k_by_family"], base=payload["base"], normalize_team=normalize_team)
        for rec in payload["records"]:
            key = (rec["family"], rec["gender"], rec["team"])
            tracker._current[key] = float(rec["post"])
            tracker._timeline_dates.setdefault(key, []).append(rec["date"])
            tracker._timeline_posts.setdefault(key, []).append(float(rec["post"]))
            tracker._history.append(dict(rec))
            tracker._match_ids.add(rec["match_id"])
            tracker._last_date = rec["date"]
        return tracker
