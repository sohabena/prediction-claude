"""S72 paper trader: one decision cycle over the live matches, and the helpers the loop script runs.

A cycle (every 15 s from ``scripts/run_paper_trader.py``) does, in this order:

1. Candidates: real matches (SRL excluded unless ``include_simulated``) with a price in the last 5 min, and either in
   play or pre-match within 6 h of the start. Matches with a decided result are not candidates.
2. As-of books: the S58 merge rule (each price field from its latest row at or before t, within 15 min, listing and
   match-page sources together), read from ``v_match_odds``. A match with no fresh book gives no trade.
3. Ledger: fill pending bets decided at least 60 s ago (S71, book at decided + 60 s), each cut to the size the match
   page showed at the fill time (S87; no size known: at most 10,000, marked ``size_unknown``), record CLV where it is
   due, settle filled bets against ``match_results``.
4. Decide: ``ProbabilityService.estimate`` (S69) once per match, then the book guards (S75: band, crossed book), then
   ``model_edge.decide`` (S70) per variant with the position rebuilt from the ledger. A (variant, match) pair with a
   pending bet is skipped. In play without a fresh in-play score (S83), a model variant gets no model probability:
   it may reduce or green up, never enter, add or flip, and each such variant-match is counted as
   ``inplay_without_state``. Each intent then faces the new-risk guards (S75: staleness, spread, flip cooldown, rate).
5. Record each intent that passes as a pending row in ``paper_bets`` with its decision context (S71). Every refusal
   is counted by reason in the cycle report (``guard_refusals``).
6. Placebo controls (S85), after the model variants, for each candidate the reference variant placed an intent on this
   cycle: ``placebo_random`` places one intent with the reference's stake on a seeded-random side and selection,
   ``placebo_favourite`` backs the market favourite with that stake. Priced and capped by ``model_edge.placebo_ticket``
   and checked by the S75 new-risk guards. They use the market probability only, so the source is ``market_only``.
   S97: the ``mkt_dyn`` and ``mkt_dyn_e06`` variants take their probability from the market-dynamics model (source
   ``market_dynamics``): the band price now plus the predicted 10-minute change. In play only; pre-match, or with no
   model file, they open no new risk. Everything else about them is the same decision and guard path.

Nothing is kept in memory between cycles: positions, pending bets and the last trade time all come from the ledger,
so a restart resumes exactly where the table says. Writes go only to ``paper_bets``. No real money, no site access.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select, text

from backend.models.paper_bets import PaperBet
from strategy import model_edge, staking, trade_guards
from strategy.base import MatchedBet, OrderIntent
from strategy.model_edge import Book, Variant
from strategy.trade_guards import GuardConfig
from training.dataset import STALE_LIMIT_S, _book, _price_series, match_family  # _book/_price_series: the S58 rule
from virtual_trading import paper_ledger as ledger
from virtual_trading import paper_review

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "paper_trader.log"
MODEL_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
FAMILIES = ("t20", "odi")

MAX_MATCH_LOSS = 100_000.0
# Operator, 2026-11-03: 20% tolerance on the per-match budget. Decisions are sized to MAX_MATCH_LOSS; no fill may
# take a variant's worst outcome on a match beyond HARD_MATCH_LOSS (a fill can land above its decision price).
HARD_MATCH_LOSS = MAX_MATCH_LOSS * 1.2
FRESH_PRICE = timedelta(minutes=5)
PREMATCH_WINDOW = timedelta(hours=6)
STATE_FRESH = timedelta(minutes=5)
CONTEXT_LEAD = timedelta(hours=2)
PENDING_LOOKBACK = timedelta(minutes=20)  # covers the 15 min staleness limit plus the 60 s fill delay
CLV_DELAY = timedelta(minutes=10)
W_GRID = (0.3, 0.5, 0.7)
EDGE_GRID = (0.03, 0.06)
# S97: the market-dynamics variants and their minimum edge. Their probability is the model's, not the blend's (w = 1).
DYNAMICS_MIN_EDGE = {"mkt_dyn": 0.03, "mkt_dyn_e06": 0.06}
DYNAMICS_VARIANTS = frozenset(DYNAMICS_MIN_EDGE)


# ----------------------------------------------------------------------------- variants (one place)
def variant_name(w: float, min_edge: float) -> str:
    """``w05_e03``: shrink weight and minimum edge, both as integers."""
    return f"w{round(w * 10):02d}_e{round(min_edge * 100):02d}"


def build_variants(max_match_loss: float = MAX_MATCH_LOSS) -> tuple[Variant, ...]:
    """The grid of the plan (w x min_edge), the market-only control, the two S97 market-dynamics variants and the two S85
    placebos. Every variant has its own cap and ledger. The placebos are placed from the reference's decisions (see
    ``run_cycle``); they are never ranked."""
    grid = [
        Variant(variant_name(w, e), w=w, min_edge=e, max_match_loss=max_match_loss)
        for w in W_GRID
        for e in EDGE_GRID
    ]
    control = Variant("market_only", w=0.0, min_edge=0.03, max_match_loss=max_match_loss, market_only=True)
    dynamics = [
        Variant(name, w=1.0, min_edge=edge, max_match_loss=max_match_loss)  # S97: w=1, the model's probability alone
        for name, edge in DYNAMICS_MIN_EDGE.items()
    ]
    placebos = [
        Variant(paper_review.PLACEBO_RANDOM, w=0.0, min_edge=0.03, max_match_loss=max_match_loss, market_only=True,
                placebo="random"),
        Variant(paper_review.PLACEBO_FAVOURITE, w=0.0, min_edge=0.03, max_match_loss=max_match_loss, market_only=True,
                placebo="favourite"),
    ]
    return tuple(grid + [control] + dynamics + placebos)


def placebo_random_choice(match_id: str, decided_at: datetime, variant_name: str) -> tuple[str, str]:
    """The side and selection of one ``placebo_random`` decision (S85): two bits of a SHA-256 over the match, the decision
    time and the variant name. Reproducible: the same inputs give the same choice in every run and process (Python's
    ``hash`` is salted per process and is not used)."""
    stamp = decided_at.astimezone(timezone.utc).isoformat()
    digest = hashlib.sha256(f"{match_id}|{stamp}|{variant_name}".encode("utf-8")).digest()
    side = "BACK" if digest[0] & 1 == 0 else "LAY"
    selection = "home" if digest[0] & 2 == 0 else "away"
    return side, selection


# ----------------------------------------------------------------------------- candidates (pure)
@dataclass(frozen=True)
class Candidate:
    match_id: str
    team_home: str
    team_away: str
    competition: str
    match_start_time: datetime
    is_simulated: bool
    toss_winner: Optional[str]
    toss_decision: Optional[str]
    last_price_at: datetime
    in_play: bool


def select_candidates(
    registry: Iterable[Mapping[str, Any]],
    last_price_at: Mapping[str, datetime],
    now: datetime,
    include_simulated: bool = False,
) -> tuple[list[Candidate], dict[str, int]]:
    """Matches to trade this cycle, and the count of each reason a registry row was left out."""
    out: list[Candidate] = []
    skipped: Counter = Counter()
    for row in registry:
        mid = str(row["match_id"])
        last = last_price_at.get(mid)
        if last is None:
            continue  # no price in the window: not a candidate, not a skip
        if row.get("canonical_match_id") is not None:
            skipped["twin"] += 1  # S96: a second event id of a fixture; its canonical match is the candidate
            continue
        if now - last > FRESH_PRICE:
            skipped["stale_price"] += 1
            continue
        if row.get("settled"):
            skipped["settled"] += 1
            continue
        if row.get("is_simulated") and not include_simulated:
            skipped["simulated"] += 1
            continue
        if row.get("bookmaker_only") is True:  # S105: only the bookmaker's _BR market; its runners are never bettable
            skipped["bookmaker_only"] += 1
            continue
        # S86: the actual off when recorded, else the scheduled start
        start = row.get("actual_start_time") or row.get("match_start_time")
        if start is None:
            skipped["no_start"] += 1
            continue
        if start - now > PREMATCH_WINDOW:
            skipped["too_early"] += 1
            continue
        out.append(
            Candidate(
                match_id=mid,
                team_home=str(row.get("team_home") or ""),
                team_away=str(row.get("team_away") or ""),
                competition=str(row.get("competition") or ""),
                match_start_time=start,
                is_simulated=bool(row.get("is_simulated")),
                toss_winner=row.get("toss_winner"),
                toss_decision=row.get("toss_decision"),
                last_price_at=last,
                in_play=start <= now,
            )
        )
    return out, dict(skipped)


# ----------------------------------------------------------------------------- as-of books (S58 rule)
class PriceCache:
    """In-memory price series per match, built from ``v_match_odds`` rows. ``book_at`` is the S58 as-of rule."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["match_id"]), []).append(row)
        self._series = {mid: _price_series(rs) for mid, rs in grouped.items()}

    def book_at(self, match_id: str, t: datetime) -> Optional[dict[str, Optional[float]]]:
        """Flat book at ``t`` (back_home, lay_home, back_away, lay_away); None when no field is fresh."""
        found = self.book_and_ages_at(match_id, t)
        return None if found is None else found[0]

    def book_and_ages_at(
        self, match_id: str, t: datetime
    ) -> Optional[tuple[dict[str, Optional[float]], dict[str, Optional[float]]]]:
        """The flat book at ``t`` and the age in whole seconds of each field (None where the field is missing).

        Each field has its own as-of row (S58), so each field has its own age; the staleness guards (S75) read these.
        """
        series = self._series.get(str(match_id))
        if series is None:
            return None
        nested = _book(series, t)
        flat = {f"{kind}_{side}": nested[side][kind] for side in ("home", "away") for kind in ("back", "lay")}
        if not any(v is not None for v in flat.values()):
            return None
        ages = {
            f"{kind}_{side}": nested[side][f"stale_s_{kind}"] for side in ("home", "away") for kind in ("back", "lay")
        }
        return flat, ages


# ----------------------------------------------------------------------------- sizes shown (S87)
SIZE_STALE = timedelta(seconds=STALE_LIMIT_S)  # the same as-of limit as the prices (S58)
# Match-page runner rows of the match odds market, with the runner's side resolved from the registry names (the same
# normalisation as v_match_odds). Constants and bind parameters only. ``:until`` bounds the read (replay: the end).
SIZE_ROWS_SQL = r"""
    WITH registry AS (
        SELECT match_id,
               lower(btrim(regexp_replace(team_home, '\s+', ' ', 'g'))) AS home_key,
               lower(btrim(regexp_replace(team_away, '\s+', ' ', 'g'))) AS away_key
        FROM match_training_status
        WHERE match_id = ANY(:ids)
    ),
    runner AS (
        SELECT m.match_id, m.time, m.market_name, m.back, m.back_size, m.lay, m.lay_size,
               lower(btrim(regexp_replace(m.selection, '\s+', ' ', 'g'))) AS sel_key,
               r.home_key, r.away_key
        FROM market_ticks m
        JOIN registry r ON r.match_id = m.match_id
        WHERE m.match_id = ANY(:ids)
          AND m.market_type = 'match_odds' AND m.market_name = 'match_odds' AND m.source = 'lotusbook'
          AND m.time >= :since AND m.time <= :until
    )
    SELECT match_id, time, market_name,
           CASE WHEN sel_key = home_key THEN 'home' ELSE 'away' END AS selection,
           back, back_size, lay, lay_size
    FROM runner
    WHERE sel_key IN (home_key, away_key)
    ORDER BY match_id, time
"""

# S105: the first time of each match's bookmaker (_BR) and exchange match-odds ticks at or before ``:until``. One read
# serves the replay cursor: the match is bookmaker_only as of t when first_bookmaker <= t and first_exchange is not
# <= t. The same rule as the live registry SQL (DbFeed.registry). Constants and bind parameters only.
MATCH_ODDS_FIRST_SQL = r"""
    SELECT match_id,
           min(time) FILTER (WHERE RIGHT(market_id, 3) = '_BR') AS first_bookmaker,
           min(time) FILTER (WHERE COALESCE(RIGHT(market_id, 3), '') <> '_BR') AS first_exchange
    FROM market_ticks
    WHERE match_id = ANY(:ids) AND market_type = 'match_odds' AND source = 'lotusbook' AND time <= :until
    GROUP BY match_id
"""


class SizeCache:
    """The size the match page showed for each side and selection, as of a time (S87). Built from ``SIZE_ROWS_SQL``
    rows (or the replay's copy of them).

    As-of rule: per market of the match (``market_name``), the latest row at or before t, no older than
    ``SIZE_STALE``. Among the markets that give a price for the side, the size of the highest back price (BACK) or
    the highest lay price (LAY) is returned: the choice v_match_odds makes for the book, so the size belongs to the
    price the book shows. None when no market gives a price, or the chosen row carries no size (DOM rows do not).
    """

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            key = (str(row["match_id"]), str(row["selection"]), str(row.get("market_name") or ""))
            grouped.setdefault(key, []).append(row)
        self._series: dict[tuple[str, str, str], tuple[list[datetime], list[Mapping[str, Any]]]] = {}
        self._markets: dict[tuple[str, str], list[str]] = {}
        for key, rs in grouped.items():
            ordered = sorted(rs, key=lambda r: r["time"])
            self._series[key] = ([r["time"] for r in ordered], ordered)
            self._markets.setdefault((key[0], key[1]), []).append(key[2])
        for names in self._markets.values():
            names.sort()

    def size_at(self, match_id: str, side: str, selection: str, t: datetime) -> Optional[float]:
        """The size shown for ``side`` (BACK or LAY) on ``selection`` as of ``t``, or None when unknown."""
        kind = "back" if side == "BACK" else "lay"
        mid = str(match_id)
        best: Optional[tuple[float, Optional[float]]] = None
        for name in self._markets.get((mid, selection), ()):
            times, rows = self._series[(mid, selection, name)]
            i = bisect.bisect_right(times, t) - 1
            if i < 0 or t - times[i] > SIZE_STALE:
                continue
            row = rows[i]
            price = row.get(kind)
            if price is None or float(price) <= 1.0:
                continue
            size = row.get(f"{kind}_size")
            if best is None or float(price) > best[0]:
                best = (float(price), None if size is None else float(size))
        return None if best is None else best[1]


# ----------------------------------------------------------------------------- ledger state (from paper_bets)
def ledger_state(rows: Sequence[Any]) -> tuple[dict[tuple[str, str], Book], set[tuple[str, str]], dict[tuple[str, str], datetime]]:
    """Positions of filled bets, keys with a pending bet, and the last trade time, per (variant, match)."""
    books: dict[tuple[str, str], Book] = {}
    pending: set[tuple[str, str]] = set()
    last: dict[tuple[str, str], datetime] = {}
    for row in rows:
        key = (row.variant, row.match_id)
        if row.status == "pending":
            pending.add(key)
        elif row.status == "filled":
            books.setdefault(key, Book()).apply(
                MatchedBet(
                    side=row.side,
                    selection=row.selection,
                    price=float(row.price_filled),
                    stake=float(row.stake),
                    placed_at=row.decided_at.timestamp(),
                    matched_at=row.filled_at.timestamp(),
                    fill_index=0,
                    reason=row.reason or "",
                )
            )
        if row.status in ("pending", "filled"):
            if key not in last or row.decided_at > last[key]:
                last[key] = row.decided_at
    return books, pending, last


async def _open_rows(session: Any) -> list[Any]:
    stmt = select(PaperBet).where(PaperBet.status.in_(["pending", "filled"]))
    return list((await session.execute(stmt)).scalars().all())


# ----------------------------------------------------------------------------- database feed
_SETTLED_SQL = ", ".join(f"'{t}'" for t in sorted(ledger.SETTLED_RESULT_TYPES))  # constants only, never user input


class DbFeed:
    """Read-only SELECTs for one cycle. Every write goes through ``virtual_trading.paper_ledger``."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def last_prices(self, now: datetime) -> dict[str, datetime]:
        stmt = text(
            "SELECT match_id, max(time) AS last_at FROM v_match_odds WHERE time >= :cutoff GROUP BY match_id"
        )
        rows = (await self.session.execute(stmt, {"cutoff": now - FRESH_PRICE})).fetchall()
        return {str(r.match_id): r.last_at for r in rows}

    async def registry(self, match_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not match_ids:
            return []
        stmt = text(
            "SELECT r.match_id, r.team_home, r.team_away, r.competition, r.match_start_time, r.actual_start_time, r.is_simulated, "
            # S96: read through to_jsonb, so the trader runs on a database without the migration (NULL there)
            "r.toss_winner, r.toss_decision, (to_jsonb(r) ->> 'canonical_match_id') AS canonical_match_id, "
            f"EXISTS (SELECT 1 FROM match_results m WHERE m.match_id = r.match_id AND m.result_type IN ({_SETTLED_SQL})) "
            "AS settled, "
            # S105: bookmaker_only = the match has a bookmaker (_BR) match-odds tick and no other match-odds tick. The replay
            # feed applies the same rule as of its cursor (MATCH_ODDS_FIRST_SQL). Scalar subqueries with LIMIT 1: each is an
            # index probe on match_id (EXISTS here made the planner scan the whole hypertable every cycle).
            "(COALESCE((SELECT true FROM market_ticks b WHERE b.match_id = r.match_id AND b.market_type = 'match_odds' "
            "AND b.source = 'lotusbook' AND RIGHT(b.market_id, 3) = '_BR' LIMIT 1), false) "
            "AND NOT COALESCE((SELECT true FROM market_ticks x WHERE x.match_id = r.match_id AND x.market_type = 'match_odds' "
            "AND x.source = 'lotusbook' AND COALESCE(RIGHT(x.market_id, 3), '') <> '_BR' LIMIT 1), false)) AS bookmaker_only "
            "FROM match_training_status r WHERE r.match_id = ANY(:ids)"
        )
        rows = (await self.session.execute(stmt, {"ids": list(match_ids)})).mappings().all()
        return [dict(r) for r in rows]

    async def price_rows(self, match_ids: Sequence[str], since: datetime) -> list[dict[str, Any]]:
        stmt = text(
            "SELECT match_id, time, back_home, lay_home, back_away, lay_away, source FROM v_match_odds "
            "WHERE match_id = ANY(:ids) AND time >= :since ORDER BY match_id, time"
        )
        rows = (await self.session.execute(stmt, {"ids": list(match_ids), "since": since})).mappings().all()
        return [dict(r) for r in rows]

    async def context_rows(self, match_ids: Sequence[str], since: datetime, now: datetime) -> list[dict[str, Any]]:
        if not match_ids:
            return []
        stmt = text(
            "SELECT match_id, time, is_live, score, wickets, overs, innings, balls_remaining, batting_team, "
            "bowling_team, run_rate, req_run_rate, match_format, status FROM match_context "
            "WHERE match_id = ANY(:ids) AND time >= :since AND time <= :now ORDER BY match_id, time"
        )
        rows = (await self.session.execute(stmt, {"ids": list(match_ids), "since": since, "now": now})).mappings()
        return [dict(r) for r in rows.all()]

    async def size_rows(self, match_ids: Sequence[str], since: datetime, now: datetime) -> list[dict[str, Any]]:
        """Read-only (S87): the match-page rows that carry the sizes, between ``since`` and ``now``."""
        if not match_ids:
            return []
        rows = (await self.session.execute(
            text(SIZE_ROWS_SQL), {"ids": list(match_ids), "since": since, "until": now}
        )).mappings().all()
        return [dict(r) for r in rows]

    async def results(self, match_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not match_ids:
            return {}
        stmt = text(
            "SELECT match_id, winner, loser, result_type, team_home, team_away FROM match_results "
            "WHERE match_id = ANY(:ids) ORDER BY completed_at DESC NULLS LAST"
        )
        out: dict[str, dict[str, Any]] = {}
        for row in (await self.session.execute(stmt, {"ids": list(match_ids)})).mappings().all():
            mid = str(row["match_id"])
            # A decided result beats an undecided one for the same match; otherwise the latest row stands.
            if mid not in out or row["result_type"] == "win":
                out[mid] = dict(row)
        return out


# ----------------------------------------------------------------------------- one cycle
def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def _state_rows(rows: list[Mapping[str, Any]], now: datetime) -> Optional[list[Mapping[str, Any]]]:
    """Live score history for the in-play model, or None when the latest row is missing, stale or not live."""
    if not rows:
        return None
    latest = rows[-1]
    if latest.get("time") is None or latest["time"] < now - STATE_FRESH or latest.get("is_live") is False:
        return None
    return rows


async def run_cycle(
    session: Any,
    feed: Any,
    probability: Any,
    variants: Sequence[Variant],
    *,
    now: Optional[datetime] = None,
    include_simulated: bool = False,
    suspended: Iterable[str] = (),
    primary: Optional[str] = None,
    guards: GuardConfig = GuardConfig(),
    fill_delay: timedelta = ledger.FILL_DELAY,
    size_cap: bool = False,  # S87: off until the unit of the shown size is verified (decision 2026-11-03)
    reference: str = paper_review.DEFAULT_REFERENCE,  # S85: the variant whose decisions the placebos copy
    market_dynamics: Any = None,  # S97: a model_for(match_id) source (strategy.market_dynamics); None: no model
) -> dict[str, Any]:
    """One decision cycle. Returns the report (JSON-serialisable) that the loop writes to the log.

    ``suspended`` names variants from the last S73 review: they keep reducing and green-up but open no new risk
    (``max_trades_per_match = 0``). ``primary`` is a label for the log only. ``guards`` are the S75 settings.
    ``reference`` is the variant the placebo controls copy (S85); it must be in ``variants`` for them to trade.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    suspended_set = frozenset(suspended)
    guard_refusals: Counter = Counter()

    # 1. Candidates and the ledger's open bets (their matches need prices too, for fills and CLV).
    last_prices = await feed.last_prices(now)
    open_rows = await _open_rows(session)
    open_ids = {r.match_id for r in open_rows}
    registry = await feed.registry(sorted(set(last_prices) | open_ids))
    starts = {str(r["match_id"]): r.get("actual_start_time") or r.get("match_start_time") for r in registry}
    candidates, skipped = select_candidates(registry, last_prices, now, include_simulated)

    # 2. As-of prices from the earliest point any open bet or candidate needs.
    since = now - PENDING_LOOKBACK
    for row in open_rows:
        since = min(since, row.decided_at - PENDING_LOOKBACK)
    price_ids = sorted({c.match_id for c in candidates} | open_ids)
    raw_prices = await feed.price_rows(price_ids, since) if price_ids else []
    cache = PriceCache(raw_prices)
    prices_by_match: dict[str, list[dict[str, Any]]] = {}  # S97: the same rows, per match, for the dynamics features
    if market_dynamics is not None:
        for row in raw_prices:
            prices_by_match.setdefault(str(row["match_id"]), []).append(row)
    dynamics_counts: Counter = Counter()
    # S87: the sizes shown, read once here because the fill callback is synchronous. A feed without size_rows, or
    # size_cap False (replay --no-size-cap), gives no size: the fill is the pre-S87 one.
    sizes = None
    if size_cap and price_ids and hasattr(feed, "size_rows") and any(r.status == "pending" for r in open_rows):
        sizes = SizeCache(await feed.size_rows(price_ids, since, now))

    # 3. Fills, CLV and settlement.
    fills = await ledger.fill_pending(
        session, cache.book_at, now, delay=fill_delay, size_at=sizes.size_at if sizes is not None else None,
        hard_loss_limit=HARD_MATCH_LOSS,
    )
    open_rows = await _open_rows(session)
    clv_recorded = 0
    for row in open_rows:
        if row.status != "filled" or row.clv is not None or row.filled_at is None:
            continue
        if row.in_play:
            if row.filled_at + CLV_DELAY > now:
                continue
            off = None
        else:
            off = starts.get(row.match_id)
            if off is None or now < off:
                continue
        if await ledger.record_clv(session, row, cache.book_at, off_at=off) is not None:
            clv_recorded += 1
    filled_ids = sorted({r.match_id for r in open_rows if r.status == "filled"})
    results = await feed.results(filled_ids)
    settled_rows = await ledger.settle(session, results, now) if results else []
    settled_counts = Counter(r.status for r in settled_rows)
    open_rows = await _open_rows(session)
    books, pending, last_trade = ledger_state(open_rows)

    # 4. Decisions for each candidate, per variant.
    ctx_since = min([c.match_start_time for c in candidates], default=now) - CONTEXT_LEAD
    ctx_ids = [c.match_id for c in candidates]
    context: dict[str, list[dict[str, Any]]] = {}
    ctx_rows = await feed.context_rows(ctx_ids, ctx_since, now) if ctx_ids else []
    for row in ctx_rows:
        context.setdefault(str(row["match_id"]), []).append(row)

    # S75 history: every decision of the last window (any status), per (variant, match), for the rate and flip guards.
    history = await ledger.recent_decisions(
        session, now - timedelta(seconds=guards.history_seconds), [v.name for v in variants]
    )
    recent_by_key: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for row in history:
        recent_by_key.setdefault((row.variant, row.match_id), []).append((row.decided_at.timestamp(), row.reason or ""))

    matches_report: list[dict[str, Any]] = []
    intents_report: list[dict[str, Any]] = []
    by_variant: Counter = Counter()
    skip_match: Counter = Counter()
    pending_skips = 0
    for cand in candidates:
        found = cache.book_and_ages_at(cand.match_id, now)
        if found is None:
            skip_match["no_book"] += 1
            continue
        book, ages = found
        rows = context.get(cand.match_id, [])
        # The score feed sends a "live" 0/0 row before the start (2026-11-03, West Indies A v England Lions at 16:23
        # for a 17:00 start): before the off the pre-match model prices the match, not the in-play model.
        state = _state_rows(rows, now) if cand.in_play else None
        fmt = rows[-1].get("match_format") if rows else None
        match = {
            "match_id": cand.match_id,
            "team_home": cand.team_home,
            "team_away": cand.team_away,
            "competition": cand.competition,
            "family": match_family(cand.competition, None, fmt),
            "match_format": fmt,
            "toss_winner": cand.toss_winner,
            "toss_decision": cand.toss_decision,
        }
        est = probability.estimate(match, book, state, as_of=now)
        if est.p_market is None:
            skip_match["no_market"] += 1
            continue
        # S97: the market-dynamics probability, in play only (pre-match it stays None: the dynamics variants open no new
        # risk before the off). The features read the same price and score rows as the rest of the cycle.
        dyn_p: Optional[float] = None
        if market_dynamics is not None and cand.in_play:
            dyn_model = market_dynamics.model_for(cand.match_id)
            if dyn_model is not None:
                dyn_p = dyn_model.estimate(prices_by_match.get(cand.match_id, []), rows, now, off=cand.match_start_time)
            dynamics_counts["estimated" if dyn_p is not None else "no_estimate"] += 1
        matches_report.append(
            {
                "match_id": cand.match_id,
                "in_play": cand.in_play,
                "source": est.source,
                "p_model": est.p_model,
                "p_market": est.p_market,
                "p_dynamics": dyn_p,
            }
        )
        refusal = trade_guards.book_refusal(book, est.p_market)
        if refusal is not None:
            guard_refusals[refusal] += 1  # the book is not trusted: no variant trades this match this cycle
            continue
        # S83: in play, a model probability is used only from the in-play model with a fresh live score (source
        # ``inplay``). Otherwise the model variants are priced as market-only: model_edge gets no p_model, so only
        # reduce and green-up can trade. Pre-match decisions are unchanged.
        unpriced_in_play = cand.in_play and est.source != "inplay"
        reference_intents: list[OrderIntent] = []
        for variant in variants:
            if variant.placebo:
                continue  # S85: the placebo controls follow the model variants (step 6)
            key = (variant.name, cand.match_id)
            if key in pending:
                pending_skips += 1
                continue
            if variant.name in DYNAMICS_VARIANTS:
                # S97: the model's own probability (None pre-match or without a model: no new risk, as market_only)
                p_model_used = dyn_p
                source_used = "market_dynamics" if dyn_p is not None else "market_only"
            else:
                if unpriced_in_play and not variant.market_only:
                    guard_refusals["inplay_without_state"] += 1
                p_model_used = None if unpriced_in_play else est.p_model
                source_used = "market_only" if unpriced_in_play else est.source
            position = books.get(key, Book())
            last = last_trade.get(key)
            decide_as = replace(variant, max_trades_per_match=0) if variant.name in suspended_set else variant
            intents = model_edge.decide(
                decide_as,
                p_model_used,
                est.p_market,
                book,
                position,
                now=now.timestamp(),
                last_trade_at=last.timestamp() if last is not None else None,
                in_play=cand.in_play,
            )
            kept = []
            for intent in intents:
                refusal = trade_guards.intent_refusal(
                    intent,
                    book=book,
                    ages=ages,
                    in_play=cand.in_play,
                    recent=recent_by_key.get(key, ()),
                    now=now.timestamp(),
                    cfg=guards,
                )
                if refusal is None:
                    kept.append(intent)
                else:
                    guard_refusals[refusal] += 1
            intents = kept
            if variant.name == reference:
                reference_intents = list(intents)  # S85: what the placebo controls copy (kept, so placed)
            if not intents:
                continue
            p_blend = model_edge.blend_probability(variant, p_model_used, est.p_market)
            context_for_rows = {
                "decided_at": now,
                "team_home": cand.team_home,
                "team_away": cand.team_away,
                "source": source_used,
                "in_play": cand.in_play,
                "book": book,
                "score_state": _jsonable(rows[-1]) if state is not None else None,
                "p_model": p_model_used,
                "p_market": est.p_market,
            }
            for intent in intents:
                p_sel = p_blend if intent.selection == "home" else 1.0 - p_blend
                # Edge at the decision price (the limit is set where only half the minimum edge remains)
                decision_price = book.get(f"{'back' if intent.side == 'BACK' else 'lay'}_{intent.selection}")
                edge = staking.edge(intent.side, p_sel, decision_price or intent.price_limit)
                await ledger.record_intents(
                    session,
                    variant.name,
                    cand.match_id,
                    [intent],
                    {**context_for_rows, "p_used": p_sel, "edge": edge},
                )
                by_variant[variant.name] += 1
                intents_report.append(
                    {
                        "match_id": cand.match_id,
                        "variant": variant.name,
                        "side": intent.side,
                        "selection": intent.selection,
                        "price_limit": intent.price_limit,
                        "stake": round(intent.stake, 2),
                        "reason": intent.reason,
                        "source": source_used,
                        "in_play": cand.in_play,
                    }
                )

        # S85: the placebo controls. Each takes the reference's kept intent on this candidate (decide places at most one
        # per cycle) with its stake, at the same decision time, and is priced and guarded like any other ticket.
        for variant in variants:
            if not variant.placebo or not reference_intents:
                continue
            key = (variant.name, cand.match_id)
            if key in pending:
                pending_skips += 1
                continue
            position = books.get(key, Book())
            for ref_intent in reference_intents:
                if variant.placebo == "random":
                    side, selection = placebo_random_choice(cand.match_id, now, variant.name)
                else:
                    fav = model_edge.favourite(book)
                    if fav is None:
                        continue
                    side, selection = "BACK", fav
                intent = model_edge.placebo_ticket(
                    variant,
                    est.p_market,
                    book,
                    position,
                    side=side,
                    selection=selection,
                    stake=ref_intent.stake,
                    in_play=cand.in_play,
                    new_risk=variant.name not in suspended_set,
                )
                if intent is None:
                    continue
                refusal = trade_guards.intent_refusal(
                    intent,
                    book=book,
                    ages=ages,
                    in_play=cand.in_play,
                    recent=recent_by_key.get(key, ()),
                    now=now.timestamp(),
                    cfg=guards,
                )
                if refusal is not None:
                    guard_refusals[refusal] += 1
                    continue
                p_sel = est.p_market if intent.selection == "home" else 1.0 - est.p_market
                decision_price = book.get(f"{'back' if intent.side == 'BACK' else 'lay'}_{intent.selection}")
                edge = staking.edge(intent.side, p_sel, decision_price or intent.price_limit)
                await ledger.record_intents(
                    session,
                    variant.name,
                    cand.match_id,
                    [intent],
                    {
                        "decided_at": now,
                        "team_home": cand.team_home,
                        "team_away": cand.team_away,
                        "source": "market_only",  # no model probability: the market alone
                        "in_play": cand.in_play,
                        "book": book,
                        "score_state": _jsonable(rows[-1]) if state is not None else None,
                        "p_model": None,
                        "p_market": est.p_market,
                        "p_used": p_sel,
                        "edge": edge,
                    },
                )
                by_variant[variant.name] += 1
                intents_report.append(
                    {
                        "match_id": cand.match_id,
                        "variant": variant.name,
                        "side": intent.side,
                        "selection": intent.selection,
                        "price_limit": intent.price_limit,
                        "stake": round(intent.stake, 2),
                        "reason": intent.reason,
                        "source": "market_only",
                        "in_play": cand.in_play,
                        "placebo": variant.placebo,
                    }
                )

    return {
        "cycle_at": now.isoformat(),
        "reference": reference,
        "candidates": len(candidates),
        "considered": len(matches_report),
        "skipped_registry": skipped,
        "skipped_matches": dict(skip_match),
        "skipped_pending": pending_skips,
        "matches": matches_report,
        "intents": len(intents_report),
        "intents_by_variant": dict(by_variant),
        "intent_detail": intents_report,
        "guard_refusals": dict(sorted(guard_refusals.items())),
        "fills": fills,
        "clv_recorded": clv_recorded,
        "settled": dict(settled_counts),
        "primary": primary,
        "suspended": sorted(suspended_set),
        "market_dynamics": {"loaded": market_dynamics is not None, **dict(dynamics_counts)},  # S97
    }


# ----------------------------------------------------------------------------- service wiring
def load_probability_service(root: Path = ROOT) -> Any:
    """Ratings and the family models, loaded once. Blocking: call it outside the event loop."""
    from scripts.cricsheet_harmonizer import canonicalize_team
    from strategy.fair_value import FairValueModel
    from strategy.live_context import load_ratings
    from strategy.prematch_model import PrematchModel
    from strategy.probability import ProbabilityService

    data = root / "data"
    ratings = load_ratings(data, cache=data / "ratings" / "cricket_ratings.joblib")
    prematch = {f: PrematchModel.load(root / "models" / "prematch" / f"{f}_v3.joblib") for f in FAMILIES}
    inplay = {f: FairValueModel.load(root / "models" / "fair_value" / f"{f}_v2_all.joblib") for f in FAMILIES}
    return ProbabilityService(ratings, prematch, inplay, normalize_team=canonicalize_team)


def append_log(record: Mapping[str, Any], path: Path = LOG_PATH) -> str:
    """Append one JSON line to the paper trader log and return it."""
    line = json.dumps(_jsonable(dict(record)), default=str, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return line


async def run_once(
    probability: Any,
    variants: Sequence[Variant],
    *,
    include_simulated: bool = False,
    log_path: Path = LOG_PATH,
) -> dict[str, Any]:
    """One cycle in one database transaction (committed on success, rolled back on error)."""
    from shared.db import get_session

    from strategy.market_dynamics import load_current  # S97: the active model, or None (dynamics variants: no risk)

    state = paper_review.load_state()
    async with get_session() as session:
        report = await run_cycle(
            session,
            DbFeed(session),
            probability,
            variants,
            include_simulated=include_simulated,
            suspended=paper_review.suspended_names(state),
            primary=state.get("primary"),
            reference=paper_review.reference_name(state),
            market_dynamics=load_current(),
        )
    append_log(report, log_path)
    return report


__all__ = [
    "Candidate",
    "DYNAMICS_VARIANTS",
    "DbFeed",
    "PriceCache",
    "build_variants",
    "ledger_state",
    "load_probability_service",
    "placebo_random_choice",
    "run_cycle",
    "run_once",
    "select_candidates",
    "variant_name",
]
