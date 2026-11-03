"""Paper bet ledger (S71): virtual bets, fills, settlement and CLV in the ``paper_bets`` table.

Nothing here moves money. Every row is a virtual bet placed by a strategy variant.

Exchange conventions and the fill rule follow ``strategy/base.py``:
- BACK fills at the back price, LAY fills at the lay price (the takeable price of the side).
- A bet is decided at ``t`` on the book at ``t`` and fills at ``t + 60 s`` (``FILL_DELAY``; 30, 60 or 120 s in the
  replay sensitivity runs) at the takeable price, only if that price is still within the limit: BACK needs
  price >= limit, LAY needs price <= limit. Otherwise it is ``unfilled``.
- Size (S87): when the caller gives a ``size_at`` callback, a fill is cut to the size shown for that side and price at
  the fill time. The asked stake is kept in ``stake_asked``; ``stake`` becomes the filled stake and ``liability`` is
  recomputed from it. The remainder lapses. With no size known the fill is at most ``UNKNOWN_SIZE_CAP`` and
  ``fill_note`` says ``size_unknown``. Units of the size: see ``LAY_SIZE_UNIT`` and ``max_stake``.
- Settlement: a win for the selection pays per ``bet_gross_pnl``; a tie, a draw, no result or abandonment voids the bet
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
# S87: stake ceiling for a fill when no size is known for the side and price (listing-only book, or no match-page row).
UNKNOWN_SIZE_CAP = 10_000.0
# S87 reading of the size on a LAY. The fixtures (tests/fixtures/lotusbook) show only ``size 100`` / ``size 0`` on the
# match page and ``"size"`` next to ``"price"`` in sma-event JSON, with no unit, so this is a convention, not a
# measurement. The JSON follows the exchange API shape, where the size on both sides is the stake that can be matched
# at that price (a LAY of stake S carries liability S * (price - 1)). "stake" uses the size as the LAY stake; "liability"
# would take it as the liability (LAY stake = size / (price - 1)). Change it only with evidence.
LAY_SIZE_UNIT = "stake"

# result_type values from match_results. "win" is a decided result. Any other value that is not listed in
# VOID_RESULT_TYPES (for example "unresolved") leaves the bets open.
WIN_RESULT_TYPES = frozenset({"win"})
VOID_RESULT_TYPES = frozenset({"tie", "draw", "no_result", "no result", "abandoned", "abandonment"})
# S75: result types that end a match for the registry (no more decisions). The detector writes "draw" for a drawn
# match, which is void for betting. Any other type (or none) leaves the match open.
SETTLED_RESULT_TYPES = frozenset({"win", "tie", "no_result", "draw", "abandoned"})

BookAt = Callable[[str, datetime], Optional[Mapping[str, Any]]]
# size_at(match_id, side, selection, t) -> the size shown for that side and selection as of t, or None when unknown.
SizeAt = Callable[[str, str, str, datetime], Optional[float]]


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


def max_stake(side: str, price: float, size: Optional[float]) -> Optional[float]:
    """Largest stake that the shown ``size`` takes at ``price`` (S87). None when the size is unknown.

    BACK: the size is the stake. LAY: the size is the stake when ``LAY_SIZE_UNIT`` is "stake", or the liability when it
    is "liability" (stake = size / (price - 1)). See the comment on ``LAY_SIZE_UNIT``.
    """
    if size is None:
        return None
    if side == "BACK" or LAY_SIZE_UNIT == "stake":
        return float(size)
    return float(size) / (price - 1.0)


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
                stake_asked=float(intent.stake),
                fill_note=None,
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


async def fill_pending(
    session: Any,
    book_at: BookAt,
    now: Optional[datetime] = None,
    *,
    delay: timedelta = FILL_DELAY,
    size_at: Optional[SizeAt] = None,
    unknown_size_cap: float = UNKNOWN_SIZE_CAP,
    hard_loss_limit: Optional[float] = None,
) -> dict[str, int]:
    """Fill or reject pending bets decided at least ``delay`` ago.

    ``hard_loss_limit`` (operator, 2026-11-03: the 100,000 per-match budget with 20% tolerance, so 120,000): a fill
    that would take its variant's worst outcome on the match below ``-hard_loss_limit`` is refused (``unfilled``,
    ``fill_note`` ``over_loss_limit``). Fills that do not worsen the worst outcome always pass.

    The fill is tested against the book at ``decided_at + delay``. Returns counts of ``filled`` and ``unfilled``.

    With ``size_at`` (S87) a fill is also cut to the size shown at the fill time: ``stake_asked`` keeps the asked stake,
    ``stake`` becomes the filled stake, ``liability`` follows it and ``fill_note`` is ``size_capped`` (known size bound),
    ``size_unknown`` (no size: at most ``unknown_size_cap``) or None. A known size of zero is an unfilled bet
    (``size_zero``). With ``size_at`` None the behaviour is the pre-S87 one, plus ``stake_asked`` set to the stake.
    The counts gain ``size_capped``, ``size_unknown`` and ``size_zero`` only when ``size_at`` is given.
    """
    now = now or _utc_now()
    cutoff = now - delay
    stmt = select(PaperBet).where(PaperBet.status == "pending", PaperBet.decided_at <= cutoff)
    rows = (await session.execute(stmt)).scalars().all()
    counts = {"filled": 0, "unfilled": 0}
    if size_at is not None:
        counts.update({"size_capped": 0, "size_unknown": 0, "size_zero": 0})
    positions: dict[tuple[str, str], list[float]] = {}
    if hard_loss_limit is not None and rows:
        held = (await session.execute(select(PaperBet).where(PaperBet.status == "filled"))).scalars().all()
        for bet in held:
            _add_outcomes(positions, bet.variant, bet.match_id, bet.side, bet.selection, bet.price_filled, bet.stake)
    for row in sorted(rows, key=lambda r: r.decided_at):
        if row.stake_asked is None:  # rows written before S87
            row.stake_asked = row.stake
        fill_time = row.decided_at + delay
        price = takeable_price(book_at(row.match_id, fill_time), row.side, row.selection)
        if price is None or not _within_limit(row.side, price, row.price_limit):
            row.status = "unfilled"
            counts["unfilled"] += 1
            continue
        stake = float(row.stake_asked)
        note: Optional[str] = None
        if size_at is not None:
            size = size_at(row.match_id, row.side, row.selection, fill_time)
            if size is None:
                # no size known: the cap applies and the fill is marked, whether or not the cap binds
                cap, note = float(unknown_size_cap), "size_unknown"
                counts["size_unknown"] += 1
            else:
                cap = max_stake(row.side, price, size)
                if cap is None or cap <= 0.0:
                    row.status = "unfilled"
                    row.fill_note = "size_zero"
                    counts["unfilled"] += 1
                    counts["size_zero"] += 1
                    continue
                if cap < stake:
                    note = "size_capped"
                    counts["size_capped"] += 1
            stake = min(stake, cap)
        if hard_loss_limit is not None:
            key = (row.variant, row.match_id)
            before = positions.get(key, [0.0, 0.0])
            trial = {key: list(before)}
            _add_outcomes(trial, row.variant, row.match_id, row.side, row.selection, price, stake)
            if min(trial[key]) < -hard_loss_limit and min(trial[key]) < min(before):
                row.status = "unfilled"
                row.fill_note = "over_loss_limit"
                counts["unfilled"] += 1
                counts["over_loss_limit"] = counts.get("over_loss_limit", 0) + 1  # present only when one is refused
                continue
            positions[key] = trial[key]
        row.status = "filled"
        row.filled_at = fill_time
        row.price_filled = price
        row.stake = stake
        row.fill_note = note
        row.liability = liability_for(row.side, price, stake)
        counts["filled"] += 1
    if rows:
        await session.flush()
    return counts


def _add_outcomes(positions: dict[tuple[str, str], list[float]], variant: str, match_id: str, side: str,
                  selection: str, price: Optional[float], stake: Optional[float]) -> None:
    """Add one matched bet to ``positions[(variant, match_id)] = [P&L if home wins, P&L if away wins]``."""
    if price is None or stake is None:
        return
    win = stake * (price - 1.0) if side == "BACK" else -stake * (price - 1.0)
    lose = -stake if side == "BACK" else stake
    pos = positions.setdefault((variant, match_id), [0.0, 0.0])
    if selection == "home":
        pos[0] += win
        pos[1] += lose
    else:
        pos[0] += lose
        pos[1] += win


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


async def recent_decisions(session: Any, since: datetime, variants: Iterable[str]) -> list[Any]:
    """Every paper order of ``variants`` decided at or after ``since``, whatever its status (S75 guard history: the
    rate and flip rules). Filtered by variant too, so the (variant, decided_at) index serves the read."""
    names = sorted(set(variants))
    if not names:
        return []
    stmt = select(PaperBet).where(PaperBet.variant.in_(names), PaperBet.decided_at >= since)
    return list((await session.execute(stmt)).scalars().all())


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
