"""S72 paper trader: one decision cycle over the live matches, and the helpers the loop script runs.

A cycle (every 15 s from ``scripts/run_paper_trader.py``) does, in this order:

1. Candidates: real matches (SRL excluded unless ``include_simulated``) with a price in the last 5 min, and either in
   play or pre-match within 6 h of the start. Matches with a decided result are not candidates.
2. As-of books: the S58 merge rule (each price field from its latest row at or before t, within 15 min, listing and
   match-page sources together), read from ``v_match_odds``. A match with no fresh book gives no trade.
3. Ledger: fill pending bets decided at least 60 s ago (S71, book at decided + 60 s), record CLV where it is due,
   settle filled bets against ``match_results``.
4. Decide: ``ProbabilityService.estimate`` (S69) once per match, then ``model_edge.decide`` (S70) per variant with the
   position rebuilt from the ledger. A (variant, match) pair with a pending bet is skipped.
5. Record each intent as a pending row in ``paper_bets`` with its decision context (S71).

Nothing is kept in memory between cycles: positions, pending bets and the last trade time all come from the ledger,
so a restart resumes exactly where the table says. Writes go only to ``paper_bets``. No real money, no site access.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select, text

from backend.models.paper_bets import PaperBet
from strategy import model_edge, staking
from strategy.base import MatchedBet
from strategy.model_edge import Book, Variant
from training.dataset import _book, _price_series, match_family  # _book/_price_series: the S58 rule itself
from virtual_trading import paper_ledger as ledger
from virtual_trading import paper_review

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "paper_trader.log"
MODEL_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
FAMILIES = ("t20", "odi")

MAX_MATCH_LOSS = 100_000.0
FRESH_PRICE = timedelta(minutes=5)
PREMATCH_WINDOW = timedelta(hours=6)
STATE_FRESH = timedelta(minutes=5)
CONTEXT_LEAD = timedelta(hours=2)
PENDING_LOOKBACK = timedelta(minutes=20)  # covers the 15 min staleness limit plus the 60 s fill delay
CLV_DELAY = timedelta(minutes=10)
W_GRID = (0.3, 0.5, 0.7)
EDGE_GRID = (0.03, 0.06)


# ----------------------------------------------------------------------------- variants (one place)
def variant_name(w: float, min_edge: float) -> str:
    """``w05_e03``: shrink weight and minimum edge, both as integers."""
    return f"w{round(w * 10):02d}_e{round(min_edge * 100):02d}"


def build_variants(max_match_loss: float = MAX_MATCH_LOSS) -> tuple[Variant, ...]:
    """The grid of the plan (w x min_edge) plus the market-only control. Every variant has its own cap and ledger."""
    grid = [
        Variant(variant_name(w, e), w=w, min_edge=e, max_match_loss=max_match_loss)
        for w in W_GRID
        for e in EDGE_GRID
    ]
    control = Variant("market_only", w=0.0, min_edge=0.03, max_match_loss=max_match_loss, market_only=True)
    return tuple(grid + [control])


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
        if now - last > FRESH_PRICE:
            skipped["stale_price"] += 1
            continue
        if row.get("settled"):
            skipped["settled"] += 1
            continue
        if row.get("is_simulated") and not include_simulated:
            skipped["simulated"] += 1
            continue
        start = row.get("match_start_time")
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
        series = self._series.get(str(match_id))
        if series is None:
            return None
        nested = _book(series, t)
        flat = {f"{kind}_{side}": nested[side][kind] for side in ("home", "away") for kind in ("back", "lay")}
        return flat if any(v is not None for v in flat.values()) else None


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
            "SELECT r.match_id, r.team_home, r.team_away, r.competition, r.match_start_time, r.is_simulated, "
            "r.toss_winner, r.toss_decision, "
            "EXISTS (SELECT 1 FROM match_results m WHERE m.match_id = r.match_id AND m.result_type = 'win') AS settled "
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
) -> dict[str, Any]:
    """One decision cycle. Returns the report (JSON-serialisable) that the loop writes to the log.

    ``suspended`` names variants from the last S73 review: they keep reducing and green-up but open no new risk
    (``max_trades_per_match = 0``). ``primary`` is a label for the log only.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    suspended_set = frozenset(suspended)

    # 1. Candidates and the ledger's open bets (their matches need prices too, for fills and CLV).
    last_prices = await feed.last_prices(now)
    open_rows = await _open_rows(session)
    open_ids = {r.match_id for r in open_rows}
    registry = await feed.registry(sorted(set(last_prices) | open_ids))
    starts = {str(r["match_id"]): r.get("match_start_time") for r in registry}
    candidates, skipped = select_candidates(registry, last_prices, now, include_simulated)

    # 2. As-of prices from the earliest point any open bet or candidate needs.
    since = now - PENDING_LOOKBACK
    for row in open_rows:
        since = min(since, row.decided_at - PENDING_LOOKBACK)
    price_ids = sorted({c.match_id for c in candidates} | open_ids)
    cache = PriceCache(await feed.price_rows(price_ids, since) if price_ids else [])

    # 3. Fills, CLV and settlement.
    fills = await ledger.fill_pending(session, cache.book_at, now)
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

    matches_report: list[dict[str, Any]] = []
    intents_report: list[dict[str, Any]] = []
    by_variant: Counter = Counter()
    skip_match: Counter = Counter()
    pending_skips = 0
    for cand in candidates:
        book = cache.book_at(cand.match_id, now)
        if book is None:
            skip_match["no_book"] += 1
            continue
        rows = context.get(cand.match_id, [])
        state = _state_rows(rows, now)
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
        matches_report.append(
            {
                "match_id": cand.match_id,
                "in_play": cand.in_play,
                "source": est.source,
                "p_model": est.p_model,
                "p_market": est.p_market,
            }
        )
        blend_inputs = (est.p_model, est.p_market)
        for variant in variants:
            key = (variant.name, cand.match_id)
            if key in pending:
                pending_skips += 1
                continue
            position = books.get(key, Book())
            last = last_trade.get(key)
            decide_as = replace(variant, max_trades_per_match=0) if variant.name in suspended_set else variant
            intents = model_edge.decide(
                decide_as,
                blend_inputs[0],
                blend_inputs[1],
                book,
                position,
                now=now.timestamp(),
                last_trade_at=last.timestamp() if last is not None else None,
            )
            if not intents:
                continue
            p_blend = model_edge.blend_probability(variant, est.p_model, est.p_market)
            context_for_rows = {
                "decided_at": now,
                "team_home": cand.team_home,
                "team_away": cand.team_away,
                "source": est.source,
                "in_play": cand.in_play,
                "book": book,
                "score_state": _jsonable(rows[-1]) if state is not None else None,
                "p_model": est.p_model,
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
                    }
                )

    return {
        "cycle_at": now.isoformat(),
        "candidates": len(candidates),
        "considered": len(matches_report),
        "skipped_registry": skipped,
        "skipped_matches": dict(skip_match),
        "skipped_pending": pending_skips,
        "matches": matches_report,
        "intents": len(intents_report),
        "intents_by_variant": dict(by_variant),
        "intent_detail": intents_report,
        "fills": fills,
        "clv_recorded": clv_recorded,
        "settled": dict(settled_counts),
        "primary": primary,
        "suspended": sorted(suspended_set),
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
        )
    append_log(report, log_path)
    return report


__all__ = [
    "Candidate",
    "DbFeed",
    "PriceCache",
    "build_variants",
    "ledger_state",
    "load_probability_service",
    "run_cycle",
    "run_once",
    "select_candidates",
    "variant_name",
]
