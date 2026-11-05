"""S107 part B: the tail-bet decision rule (pure: no I/O, no clock, no database).

The "1.01 lottery ticket" of ``docs/strategy/live-betting-expert-persona.md``: in play, in the second innings, back a
side priced at long odds only when its price is clearly longer than the chase model's chance for that side. Small,
fixed stakes, at most a few per match, never a lay.

``snapshot`` reads one fresh live state (the latest ``match_context`` row, the first-innings target and the batters at
the crease) into the chase model's inputs. ``decide`` returns at most one order per call from the takeable back prices
and the model's probability of the home side. The caller (``virtual_trading.paper_trader``) supplies the variant's
ledger facts (tickets on the match, tickets per side, time of the last tail decision, the variant's position) and
runs the S75 new-risk guards on the order; this module does not repeat them.

Rule, in order (the first refusal is the reason):
1. in play (before the off there is no tail bet);
2. fewer than ``TAIL_MAX_TICKETS`` tail tickets on the match (filled or pending);
3. at least ``TAIL_SPACING_S`` since the last tail decision on the match;
4. for each side: back price at least ``TAIL_MIN_PRICE`` (10.0), price times the side's probability at least
   ``TAIL_VALUE_RATIO`` (1.2, so the price is 1.2 times the fair odds), and fewer than ``TAIL_MAX_PER_SIDE`` tail
   tickets already on that side. The side with the higher ratio is taken;
5. the worst case of the variant's book on the match, after a ``TAIL_STAKE`` (2,000) ticket, stays within the match cap.

The order's price limit is the lowest price at which the ratio is still ``(1 + TAIL_VALUE_RATIO) / 2`` (half the margin)
and never below ``TAIL_MIN_PRICE``: the fill rule (a price 60 s later, as for every ticket) then needs the back price to
stay at or above the limit. A tail bet is ``reason = "tail"``; the guards treat it as new risk.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from strategy.base import OrderIntent, bet_gross_pnl
from strategy.model_edge import AWAY, HOME, Book

VARIANT = "tail"
REASON = "tail"
TAIL_MIN_PRICE = 10.0
TAIL_VALUE_RATIO = 1.2
TAIL_HALF_RATIO = (1.0 + TAIL_VALUE_RATIO) / 2.0
TAIL_STAKE = 2_000.0
TAIL_MAX_TICKETS = 3
TAIL_MAX_PER_SIDE = 2  # no more than two tail tickets on one side of a match
TAIL_SPACING_S = 300.0
CHASE_INNINGS = 2
CHASE_FORMAT = "T20"  # the chase model is trained on 20-over T20 innings only (ODI and other formats: no tail bets)
CHASE_BALLS = 120
# Tail bets only in the last 10 overs of the chase (lead review, 2026-11-05): earlier, the state model knows the
# runs and wickets but not the teams, and the replay's first tail bets (120 and 114 balls left, Lumbini v Pokhara)
# backed the bowling side at 21.0 only because the market rated the chasing side stronger. Both lost.
TAIL_MAX_BALLS_LEFT = 60
BATTER_KEYS = ("name", "runs", "balls", "on_strike")
REFUSALS = (
    "not_in_play", "ticket_limit", "spacing", "price_below_min", "no_value", "side_limit", "match_cap",
)


@dataclass(frozen=True)
class ChaseSnapshot:
    """The chase state the model reads: the batting side, runs needed, balls and wickets left, and the two batters
    (``striker`` and ``non_striker``, each a dict with ``name``, ``runs`` and ``balls`` in this innings) when the live
    score names them, else None."""

    batting_side: str
    runs_needed: float
    balls_left: float
    wickets_left: float
    striker: Optional[Mapping[str, Any]]
    non_striker: Optional[Mapping[str, Any]]


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def players_of(value: Any) -> Optional[Mapping[str, Any]]:
    """The ``players`` block of a live row (S107 part A), as a dict. A JSON string is decoded; anything else is None."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, Mapping) else None


def batters_of(players: Optional[Mapping[str, Any]]) -> Optional[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """(striker, non-striker) from the ``batsmen`` list, when there are exactly two batters and exactly one is on strike."""
    if players is None:
        return None
    batsmen = players.get("batsmen")
    if not isinstance(batsmen, list) or len(batsmen) != 2:
        return None
    if not all(isinstance(b, Mapping) for b in batsmen):
        return None
    strikers = [b for b in batsmen if b.get("on_strike") is True]
    if len(strikers) != 1:
        return None
    striker = strikers[0]
    non = batsmen[1] if batsmen[0] is striker else batsmen[0]
    if not str(striker.get("name") or "").strip() or not str(non.get("name") or "").strip():
        return None
    return striker, non


def chase_format_ok(latest: Mapping[str, Any]) -> bool:
    """True for a 20-over T20 row (the chase model's domain)."""
    return str(latest.get("match_format") or "").strip().upper() == CHASE_FORMAT


def snapshot(
    latest: Mapping[str, Any],
    *,
    target: Optional[int],
    batting_side: Optional[str],
    players: Any = None,
) -> Optional[ChaseSnapshot]:
    """The chase state of the latest live row, or None when it is not a usable second-innings state.

    ``target`` is the first-innings total + 1 (``strategy.probability.derive_target``): without it no state is used,
    because the runs needed are not known. ``batting_side`` is "home" or "away" (the batting team's side).
    """
    if batting_side not in (HOME, AWAY) or target is None:
        return None
    if not chase_format_ok(latest):
        return None
    if int(_num(latest.get("innings")) or 0) != CHASE_INNINGS:
        return None
    score = _num(latest.get("score"))
    wickets = _num(latest.get("wickets"))
    balls_left = _num(latest.get("balls_remaining"))
    if score is None or wickets is None or balls_left is None:
        return None
    runs_needed = float(target) - score
    wickets_left = 10.0 - wickets
    if runs_needed < 1.0 or balls_left < 1.0 or wickets_left < 1.0 or balls_left > TAIL_MAX_BALLS_LEFT:
        return None
    batters = batters_of(players_of(players))
    striker, non = batters if batters is not None else (None, None)
    return ChaseSnapshot(batting_side, runs_needed, balls_left, wickets_left, striker, non)


def home_probability(p_chase: float, batting_side: str) -> float:
    """P(home wins) from P(the chasing side wins)."""
    if batting_side not in (HOME, AWAY):
        raise ValueError(f"batting_side must be home or away, got {batting_side!r}")
    p = float(p_chase)
    return p if batting_side == HOME else 1.0 - p


def _back_price(prices: Mapping[str, Any], selection: str) -> Optional[float]:
    x = _num(prices.get(f"back_{selection}"))
    return x if x is not None and x > 1.0 else None


def _worst_after(position: Book, selection: str, price: float, stake: float) -> float:
    home = position.home_pnl + bet_gross_pnl("BACK", selection, price, stake, HOME)
    away = position.away_pnl + bet_gross_pnl("BACK", selection, price, stake, AWAY)
    return max(0.0, -min(home, away))


def decide(
    prices: Mapping[str, Any],
    p_home: float,
    *,
    in_play: bool,
    now: float,
    tickets: int,
    per_side: Mapping[str, int],
    last_tail_at: Optional[float],
    position: Book,
    cap: float,
    stake: float = TAIL_STAKE,
) -> tuple[Optional[OrderIntent], str]:
    """At most one tail back. Returns (order or None, the refusal reason or "bet")."""
    if not in_play:
        return None, "not_in_play"
    if tickets >= TAIL_MAX_TICKETS:
        return None, "ticket_limit"
    if last_tail_at is not None and now - last_tail_at < TAIL_SPACING_S:
        return None, "spacing"
    if not (0.0 <= p_home <= 1.0):
        raise ValueError(f"p_home must be a probability, got {p_home!r}")
    long_sides = [s for s in (HOME, AWAY) if (_back_price(prices, s) or 0.0) >= TAIL_MIN_PRICE]
    if not long_sides:
        return None, "price_below_min"
    options: list[tuple[float, str, float, float]] = []
    blocked_by_side = False
    for selection in long_sides:
        price = _back_price(prices, selection)
        p_sel = p_home if selection == HOME else 1.0 - p_home
        if price is None or p_sel <= 0.0 or price * p_sel < TAIL_VALUE_RATIO:
            continue
        if per_side.get(selection, 0) >= TAIL_MAX_PER_SIDE:
            blocked_by_side = True
            continue
        options.append((price * p_sel, selection, price, p_sel))
    if not options:
        return None, ("side_limit" if blocked_by_side else "no_value")
    _ratio, selection, price, p_sel = max(options, key=lambda o: o[0])
    if _worst_after(position, selection, price, stake) > cap:
        return None, "match_cap"
    limit = max(TAIL_MIN_PRICE, min(price, TAIL_HALF_RATIO / p_sel))
    return OrderIntent("BACK", selection, limit, stake, REASON), "bet"


__all__ = [
    "ChaseSnapshot",
    "REFUSALS",
    "TAIL_MAX_PER_SIDE",
    "TAIL_MAX_TICKETS",
    "TAIL_MIN_PRICE",
    "TAIL_SPACING_S",
    "TAIL_STAKE",
    "TAIL_VALUE_RATIO",
    "VARIANT",
    "batters_of",
    "chase_format_ok",
    "decide",
    "home_probability",
    "players_of",
    "snapshot",
]
