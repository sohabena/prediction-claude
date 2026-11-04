"""S74 replay feed: the read side of ``paper_trader.run_cycle`` over recorded history, as of a cursor time.

``ReplayFeed.load`` runs the read-only SELECTs once, for the whole recorded window (everything at or before ``end``).
After that the feed answers every method as of its cursor (``seek(now)``): a row recorded after the cursor is never
returned, so a decision at ``t`` cannot see the future. Methods mirror ``paper_trader.DbFeed`` with these changes:

- ``last_prices``, ``price_rows`` and ``context_rows`` are capped at the cursor. In the live feed "now" is the present,
  so no cap is needed there; in replay the cap is the look-ahead guard.
- ``results``: a result is available once its ``completed_at`` is at or before the cursor. A result with no
  ``completed_at`` is available once the match has been silent for ``SETTLE_DELAY`` after its last tick at or before
  the cursor (the tick is the latest one the cursor has seen, so this uses nothing recorded after ``now``).
- ``registry``: ``settled`` is true only once a decided result is available. The toss fields are exposed only from
  ``match_start_time - TOSS_LEAD``: the table holds the toss without a time stamp, so this is an approximation of when
  a live cycle would have seen it (documented as a parity note).

- ``size_rows`` (S87): the match-page sizes (``SIZE_ROWS_SQL``) loaded once up to ``end`` and answered as of the cursor,
  like the prices. No size row after the cursor is ever returned, so a fill at ``t`` cannot see a later size.
- ``registry`` (S105): ``bookmaker_only`` as of the cursor. Loaded once as the first bookmaker and first exchange tick
  times per match (``MATCH_ODDS_FIRST_SQL``); the flag is true from the first ``_BR`` tick until the first exchange tick.

Fills, CLV and settlement are not computed here. ``run_cycle`` computes them from these reads at later cycles, which
is how the 60 s fill delay and the 10 min in-play CLV are reproduced: by stepping the clock, not by peeking.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import text

from virtual_trading import paper_ledger as ledger
from virtual_trading.paper_trader import FRESH_PRICE, MATCH_ODDS_FIRST_SQL, SIZE_ROWS_SQL

SETTLE_DELAY = timedelta(minutes=10)  # results without completed_at: silence after the last tick
TOSS_LEAD = timedelta(minutes=30)  # toss fields visible from this long before the start
CONTEXT_FIELDS = (
    "time", "is_live", "score", "wickets", "overs", "innings", "balls_remaining", "batting_team", "bowling_team",
    "run_rate", "req_run_rate", "match_format", "status",
)
PRICE_FIELDS = ("time", "back_home", "lay_home", "back_away", "lay_away", "source")
SIZE_FIELDS = ("time", "selection", "market_name", "back", "back_size", "lay", "lay_size")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _pick_result(rows: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The DbFeed rule for one match: the latest row (completed_at DESC NULLS LAST), a decided row beats it."""
    ordered = sorted(
        rows,
        key=lambda r: (r.get("completed_at") is None, -(r["completed_at"].timestamp()) if r.get("completed_at") else 0.0),
    )
    chosen: Optional[dict[str, Any]] = None
    for row in ordered:
        if chosen is None or row["result_type"] == "win":
            chosen = dict(row)
    return chosen


class ReplayFeed:
    """Recorded rows for one replay window, read once, answered as of ``cursor``. Implements the run_cycle feed API."""

    def __init__(
        self,
        *,
        prices: Iterable[Mapping[str, Any]],
        registry: Iterable[Mapping[str, Any]],
        context: Iterable[Mapping[str, Any]],
        results: Iterable[Mapping[str, Any]],
        end: datetime,
        sizes: Iterable[Mapping[str, Any]] = (),
        match_odds_times: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        if end.tzinfo is None:
            raise ValueError("end must be timezone-aware")
        self.end = end
        self.cursor: Optional[datetime] = None
        # S105: rows of MATCH_ODDS_FIRST_SQL (match_id, first_bookmaker, first_exchange); no row: never bookmaker_only
        self._match_odds_first = {str(r["match_id"]): dict(r) for r in match_odds_times}
        self._prices: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in prices:
            if row["time"] <= end:
                self._prices[str(row["match_id"])].append(dict(row))
        self._price_times: dict[str, list[datetime]] = {}
        for mid, rows in self._prices.items():
            rows.sort(key=lambda r: r["time"])
            self._price_times[mid] = [r["time"] for r in rows]
        self._registry = {str(r["match_id"]): dict(r) for r in registry}
        self._context: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in context:
            if row["time"] <= end:
                self._context[str(row["match_id"])].append(dict(row))
        self._context_times: dict[str, list[datetime]] = {}
        for mid, rows in self._context.items():
            rows.sort(key=lambda r: r["time"])
            self._context_times[mid] = [r["time"] for r in rows]
        self._results: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in results:
            self._results[str(row["match_id"])].append(dict(row))
        self._sizes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sizes:
            if row["time"] <= end:
                self._sizes[str(row["match_id"])].append(dict(row))
        self._size_times: dict[str, list[datetime]] = {}
        for mid, rows in self._sizes.items():
            rows.sort(key=lambda r: r["time"])
            self._size_times[mid] = [r["time"] for r in rows]

    # ------------------------------------------------------------------ loading (the only database access)
    @classmethod
    async def load(cls, session: Any, *, end: datetime) -> "ReplayFeed":
        """Read the recorded rows at or before ``end`` with SELECTs only. ``session`` should be read-only."""
        prices = (await session.execute(text(
            "SELECT match_id, time, back_home, lay_home, back_away, lay_away, source FROM v_match_odds "
            "WHERE time <= :end ORDER BY match_id, time"
        ), {"end": end})).mappings().all()
        ids = sorted({str(r["match_id"]) for r in prices})
        # S86: actual_start_time only when the column exists (migration applied); an old database gets NULL.
        has_actual = bool((await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'match_training_status' AND column_name = 'actual_start_time')"
        ))).scalar())
        actual_expr = "actual_start_time" if has_actual else "NULL::timestamptz AS actual_start_time"
        # S96: canonical_match_id only when the column exists (migration applied); an old database gets NULL.
        has_canonical = bool((await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'match_training_status' AND column_name = 'canonical_match_id')"
        ))).scalar())
        canonical_expr = "canonical_match_id" if has_canonical else "NULL::text AS canonical_match_id"
        # S105: the first bookmaker and exchange match-odds ticks, one read up to the end (the cursor applies in registry)
        match_odds_times = (await session.execute(text(MATCH_ODDS_FIRST_SQL), {"ids": ids, "until": end})).mappings().all() \
            if ids else []
        registry = (await session.execute(text(
            "SELECT match_id, team_home, team_away, competition, match_start_time, is_simulated, toss_winner, "
            f"toss_decision, {actual_expr}, {canonical_expr} FROM match_training_status WHERE match_id = ANY(:ids)"
        ), {"ids": ids})).mappings().all() if ids else []
        context = (await session.execute(text(
            "SELECT match_id, time, is_live, score, wickets, overs, innings, balls_remaining, batting_team, "
            "bowling_team, run_rate, req_run_rate, match_format, status FROM match_context "
            "WHERE time <= :end ORDER BY match_id, time"
        ), {"end": end})).mappings().all()
        results = (await session.execute(text(
            "SELECT match_id, winner, loser, result_type, team_home, team_away, completed_at FROM match_results"
        ))).mappings().all()
        # S87: the match-page sizes up to the end (the cursor filter is applied in size_rows)
        sizes = (await session.execute(text(SIZE_ROWS_SQL), {"ids": ids, "since": _EPOCH, "until": end})).mappings().all() \
            if ids else []
        return cls(
            prices=[dict(r) for r in prices],
            registry=[dict(r) for r in registry],
            context=[dict(r) for r in context],
            results=[dict(r) for r in results],
            end=end,
            sizes=[dict(r) for r in sizes],
            match_odds_times=[dict(r) for r in match_odds_times],
        )

    # ------------------------------------------------------------------ cursor
    def seek(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("cursor must be timezone-aware")
        self.cursor = now

    def _now(self, now: Optional[datetime] = None) -> datetime:
        if self.cursor is None:
            raise RuntimeError("seek(now) before reading the replay feed")
        if now is not None and now != self.cursor:
            raise ValueError(f"run_cycle asked for {now.isoformat()} but the cursor is {self.cursor.isoformat()}")
        return self.cursor

    def match_info(self, match_id: str) -> dict[str, Any]:
        """Names of a match from the registry (for the report only; not a decision input)."""
        row = self._registry.get(str(match_id), {})
        return {k: row.get(k) for k in ("team_home", "team_away", "competition", "match_start_time", "is_simulated")}

    def last_price_time(self) -> Optional[datetime]:
        """The latest recorded tick at or before ``end``, over all matches."""
        times = [t[-1] for t in self._price_times.values() if t]
        return max(times) if times else None

    def first_price_time(self) -> Optional[datetime]:
        times = [t[0] for t in self._price_times.values() if t]
        return min(times) if times else None

    def next_price_time(self, after: datetime) -> Optional[datetime]:
        """The earliest recorded price tick strictly after ``after`` (for the driver's skip-ahead), or None."""
        best: Optional[datetime] = None
        for times in self._price_times.values():
            i = bisect.bisect_right(times, after)
            if i < len(times) and (best is None or times[i] < best):
                best = times[i]
        return best

    # ------------------------------------------------------------------ feed API (as DbFeed)
    async def last_prices(self, now: datetime) -> dict[str, datetime]:
        t = self._now(now)
        out: dict[str, datetime] = {}
        for mid, times in self._price_times.items():
            i = bisect.bisect_right(times, t) - 1
            if i >= 0 and times[i] >= t - FRESH_PRICE:
                out[mid] = times[i]
        return out

    async def registry(self, match_ids: Sequence[str]) -> list[dict[str, Any]]:
        t = self._now()
        out = []
        for mid in match_ids:
            row = self._registry.get(str(mid))
            if row is None:
                continue
            start = row.get("match_start_time")
            settled = any(
                r["result_type"] in ledger.SETTLED_RESULT_TYPES and self._result_available(str(mid), r, t)
                for r in self._results.get(str(mid), [])
            )
            toss_known = start is not None and t >= start - TOSS_LEAD
            # S105: the live rule as of the cursor: a bookmaker tick at or before t and no exchange tick at or before t
            first = self._match_odds_first.get(str(mid), {})
            first_br, first_ex = first.get("first_bookmaker"), first.get("first_exchange")
            bookmaker_only = first_br is not None and first_br <= t and not (first_ex is not None and first_ex <= t)
            # S86: the actual start is visible only once it was observed (its time is at or before the cursor), so a
            # replay decision never uses an off that a live cycle could not yet have known. None before that.
            actual = row.get("actual_start_time")
            actual_known = actual if (actual is not None and actual <= t) else None
            out.append({
                "match_id": str(mid),
                "team_home": row.get("team_home"),
                "team_away": row.get("team_away"),
                "competition": row.get("competition"),
                "match_start_time": start,
                "actual_start_time": actual_known,
                "is_simulated": row.get("is_simulated"),
                "canonical_match_id": row.get("canonical_match_id"),  # S96: select_candidates skips a twin
                "toss_winner": row.get("toss_winner") if toss_known else None,
                "toss_decision": row.get("toss_decision") if toss_known else None,
                "settled": settled,
                "bookmaker_only": bookmaker_only,  # S105
            })
        return out

    async def price_rows(self, match_ids: Sequence[str], since: datetime) -> list[dict[str, Any]]:
        t = self._now()
        out: list[dict[str, Any]] = []
        for mid in sorted({str(m) for m in match_ids}):
            times = self._price_times.get(mid)
            if not times:
                continue
            lo = bisect.bisect_left(times, since)
            hi = bisect.bisect_right(times, t)
            for row in self._prices[mid][lo:hi]:
                out.append({"match_id": mid, **{k: row[k] for k in PRICE_FIELDS}})
        return out

    async def size_rows(self, match_ids: Sequence[str], since: datetime, now: datetime) -> list[dict[str, Any]]:
        """The match-page size rows between ``since`` and the cursor (S87). Nothing after the cursor is returned."""
        t = self._now(now)
        out: list[dict[str, Any]] = []
        for mid in sorted({str(m) for m in match_ids}):
            times = self._size_times.get(mid)
            if not times:
                continue
            lo = bisect.bisect_left(times, since)
            hi = bisect.bisect_right(times, t)
            for row in self._sizes[mid][lo:hi]:
                out.append({"match_id": mid, **{k: row.get(k) for k in SIZE_FIELDS}})
        return out

    async def context_rows(self, match_ids: Sequence[str], since: datetime, now: datetime) -> list[dict[str, Any]]:
        t = self._now(now)
        out: list[dict[str, Any]] = []
        for mid in sorted({str(m) for m in match_ids}):
            times = self._context_times.get(mid)
            if not times:
                continue
            lo = bisect.bisect_left(times, since)
            hi = bisect.bisect_right(times, t)
            for row in self._context[mid][lo:hi]:
                out.append({"match_id": mid, **{k: row.get(k) for k in CONTEXT_FIELDS}})
        return out

    async def results(self, match_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        t = self._now()
        out: dict[str, dict[str, Any]] = {}
        for mid in sorted({str(m) for m in match_ids}):
            available = [r for r in self._results.get(mid, []) if self._result_available(mid, r, t)]
            picked = _pick_result(available)
            if picked is not None:
                out[mid] = {k: picked.get(k) for k in ("match_id", "winner", "loser", "result_type", "team_home",
                                                       "team_away")}
        return out

    def _result_available(self, mid: str, row: Mapping[str, Any], t: datetime) -> bool:
        if row.get("completed_at") is not None:
            return row["completed_at"] <= t
        times = self._price_times.get(mid)
        if not times:
            return False
        i = bisect.bisect_right(times, t) - 1
        return i >= 0 and times[i] + SETTLE_DELAY <= t
