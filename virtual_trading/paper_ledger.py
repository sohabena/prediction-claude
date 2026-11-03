"""Paper bet ledger (S71): virtual bets, fills, settlement and CLV in the ``paper_bets`` table.

Nothing here moves money. Every row is a virtual bet placed by a strategy variant.

Exchange conventions and the fill rule follow ``strategy/base.py``:
- BACK fills at the back price, LAY fills at the lay price (the takeable price of the side).
- A bet is decided at ``t`` on the book at ``t`` and fills at ``t + 60 s`` at the takeable price, only if that price
  is still within the limit: BACK needs price >= limit, LAY needs price <= limit. Otherwise it is ``unfilled``.
- Settlement: a win for the selection pays per ``bet_gross_pnl``; a tie, no result or abandonment voids the bet
  (P&L 0, status ``void``); an unresolved result leaves the bet ``filled`` until a result is known.
- Commission is not modelled, so P&L is gross.

The session is an async SQLAlchemy session (or any object with the same ``add_all``, ``flush`` and ``execute``
coroutines), injected so tests can use fakes. Times are timezone-aware datetimes; ``now`` defaults to UTC now and
is a parameter so the logic can be tested without a clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from backend.models.paper_bets import PaperBet
from shared.metrics import clv_prob
from strategy.base import OUTCOMES, bet_gross_pnl

FILL_DELAY = timedelta(seconds=60)
IN_PLAY_CLV_DELAY = timedelta(minutes=10)

# result_type values from match_results. "win" is a decided result. Any other value that is not listed in
# VOID_RESULT_TYPES (for example "unresolved") leaves the bets open.
WIN_RESULT_TYPES = frozenset({"win"})
VOID_RESULT_TYPES = frozenset({"tie", "no_result", "no result", "abandoned", "abandonment"})

BookAt = Callable[[str, datetime], Optional[Mapping[str, Any]]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def _pick(value: Any, selection: str) -> Any:
    """Per-selection context values are dicts keyed home/away; a scalar applies to both selections."""
    if isinstance(value, Mapping):
        return value.get(selection)
    return value


def takeable_price(book: Optional[Mapping[str, Any]], side: str, selection: str) -> Optional[float]:
    """The price a bet on ``side`` of ``selection`` takes: back_<sel> for BACK, lay_<sel> for LAY."""
    if not book or side not in ("BACK", "LAY") or selection not in ("home", "away"):
        return None
    value = book.get(f"{'back' if side == 'BACK' else 'lay'}_{selection}")
    if value is None:
        return None
    price = float(value)
    return price if price > 1.0 else None


def liability_for(side: str, price: Optional[float], stake: float) -> Optional[float]:
    """Worst-case loss of a bet at ``price``: the stake for BACK, stake * (price - 1) for LAY."""
    if side == "BACK":
        return stake
    if price is None:
        return None
    return stake * (price - 1.0)


def _within_limit(side: str, price: float, limit: Optional[float]) -> bool:
    if limit is None:
        return True
    return price >= limit if side == "BACK" else price <= limit


def resolve_outcome(result: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Map a match result to ``home``, ``away``, ``void`` or None (unresolved).

    ``result`` carries ``winner``, ``result_type``, ``team_home`` and ``team_away``.
    """
    if not result:
        return None
    result_type = _norm(result.get("result_type"))
    if result_type in VOID_RESULT_TYPES:
        return "void"
    if result_type not in WIN_RESULT_TYPES:
        return None
    winner = _norm(result.get("winner"))
    if not winner:
        return None
    if winner == _norm(result.get("team_home")):
        return "home"
    if winner == _norm(result.get("team_away")):
        return "away"
    return None


async def record_intents(
    session: Any,
    variant: str,
    match_id: str,
    intents: Iterable[Any],
    context: Mapping[str, Any],
) -> list[PaperBet]:
    """Add one ``pending`` row per order intent, with the decision context.

    ``context`` holds: ``decided_at`` (aware datetime), ``team_home``, ``team_away``, ``source``
    (prematch | inplay | market_only), ``in_play`` (bool), ``book`` (dict of back_/lay_ home/away),
    ``score_state`` (dict or None), and ``p_used``, ``edge`` (required) and ``p_model``, ``p_market`` (optional).
    Each of these may be a dict keyed home/away or a scalar that applies to both selections.
    """
    decided_at = context["decided_at"]
    if decided_at.tzinfo is None:
        raise ValueError("context decided_at must be timezone-aware")
    book = dict(context.get("book") or {})
    rows: list[PaperBet] = []
    for intent in intents:
        sel = intent.selection
        team = context["team_home"] if sel == "home" else context["team_away"]
        rows.append(
            PaperBet(
                bet_id=uuid.uuid4(),
                variant=variant,
                match_id=match_id,
                decided_at=decided_at,
                filled_at=None,
                status="pending",
                side=intent.side,
                selection=sel,
                team=team,
                price_limit=intent.price_limit,
                price_filled=None,
                stake=float(intent.stake),
                liability=liability_for(intent.side, intent.price_limit, float(intent.stake)),
                reason=intent.reason or "",
                p_model=_pick(context.get("p_model"), sel),
                p_market=_pick(context.get("p_market"), sel),
                p_used=float(_pick(context["p_used"], sel)),
                edge=float(_pick(context["edge"], sel)),
                source=context["source"],
                in_play=bool(context.get("in_play", False)),
                score_state=context.get("score_state"),
                book=book,
                pnl=None,
                settled_at=None,
                result=None,
                clv=None,
                closing_price=None,
            )
        )
    if rows:
        session.add_all(rows)
        await session.flush()
    return rows


async def fill_pending(session: Any, book_at: BookAt, now: Optional[datetime] = None) -> dict[str, int]:
    """Fill or reject pending bets decided at least 60 s ago.

    The fill is tested against the book at ``decided_at + 60 s``. Returns counts of ``filled`` and ``unfilled``.
    """
    now = now or _utc_now()
    cutoff = now - FILL_DELAY
    stmt = select(PaperBet).where(PaperBet.status == "pending", PaperBet.decided_at <= cutoff)
    rows = (await session.execute(stmt)).scalars().all()
    counts = {"filled": 0, "unfilled": 0}
    for row in rows:
        fill_time = row.decided_at + FILL_DELAY
        price = takeable_price(book_at(row.match_id, fill_time), row.side, row.selection)
        if price is not None and _within_limit(row.side, price, row.price_limit):
            row.status = "filled"
            row.filled_at = fill_time
            row.price_filled = price
            row.liability = liability_for(row.side, price, row.stake)
            counts["filled"] += 1
        else:
            row.status = "unfilled"
            counts["unfilled"] += 1
    if rows:
        await session.flush()
    return counts


async def settle(
    session: Any, results: Mapping[str, Mapping[str, Any]], now: Optional[datetime] = None
) -> list[PaperBet]:
    """Settle filled bets of matches that have a resolved result.

    ``results`` maps match_id to a dict with ``winner``, ``result_type``, ``team_home`` and ``team_away``. A match
    without a resolved result stays filled. Returns the rows that changed.
    """
    if not results:
        return []
    now = now or _utc_now()
    stmt = select(PaperBet).where(PaperBet.status == "filled", PaperBet.match_id.in_(list(results)))
    rows = (await session.execute(stmt)).scalars().all()
    changed: list[PaperBet] = []
    for row in rows:
        outcome = resolve_outcome(results.get(row.match_id))
        if outcome is None:
            continue
        if outcome == "void":
            row.status = "void"
            row.result = "void"
            row.pnl = 0.0
        else:
            row.status = "settled"
            row.result = outcome
            row.pnl = bet_gross_pnl(row.side, row.selection, row.price_filled, row.stake, outcome)
        row.settled_at = now
        changed.append(row)
    if changed:
        await session.flush()
    return changed


def clv_for(row: Any, reference_price: Optional[float]) -> Optional[float]:
    """CLV of a filled bet against ``reference_price`` (same side), in probability points.

    Positive means the bet beat the reference. Uses ``shared.metrics.clv_prob``.
    """
    if row.price_filled is None or reference_price is None:
        return None
    return clv_prob(row.side, float(row.price_filled), float(reference_price))


def reference_price(row: Any, book_at: BookAt, off_at: Optional[datetime] = None) -> Optional[float]:
    """The reference price for CLV, on the bet's own side.

    In-play bets: the price 10 min after the fill. Pre-match bets: the last price at or before ``off_at``.
    """
    if row.in_play:
        if row.filled_at is None:
            return None
        when = row.filled_at + IN_PLAY_CLV_DELAY
    else:
        if off_at is None:
            return None
        when = off_at
    return takeable_price(book_at(row.match_id, when), row.side, row.selection)


async def record_clv(session: Any, row: Any, book_at: BookAt, off_at: Optional[datetime] = None) -> Optional[float]:
    """Set ``closing_price`` and ``clv`` on a filled bet from its reference price. Returns the CLV."""
    ref = reference_price(row, book_at, off_at)
    row.closing_price = ref
    row.clv = clv_for(row, ref)
    await session.flush()
    return row.clv


async def position(session: Any, variant: str, match_id: str) -> dict[str, float]:
    """Outcome P&Ls and worst-case loss of the filled bets of one variant on one match.

    Keys: ``home``, ``away``, ``draw`` (P&L if that outcome occurs; strategy convention, so a draw loses for both
    selections, the stricter case for the cap) and ``liability`` (the worst loss, >= 0).
    """
    stmt = select(PaperBet).where(
        PaperBet.status == "filled",
        PaperBet.variant == variant,
        PaperBet.match_id == match_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    pnl = {outcome: 0.0 for outcome in OUTCOMES}
    for row in rows:
        for outcome in OUTCOMES:
            pnl[outcome] += bet_gross_pnl(row.side, row.selection, row.price_filled, row.stake, outcome)
    worst = max(0.0, -min(pnl.values()))
    return {"home": pnl["home"], "away": pnl["away"], "draw": pnl["draw"], "liability": worst}
