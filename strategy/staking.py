"""S62 bet selection and uncertainty-aware staking for manual bets.

Pure functions, no I/O. Signals are information for a person who places the bet by hand; nothing here places a bet.

Conventions
- ``p`` is the probability that the selection wins (``p_home`` for the home selection, ``1 - p_home`` for away).
- ``p_sd`` is the posterior standard deviation of ``p`` (from S61). ``p_mean`` is its posterior mean.
- ``price`` is decimal odds (> 1.0). For a BACK it is the back price; for a LAY it is the lay price.
- ``edge`` is the expected profit per unit of stake: per unit of the backer's stake for a BACK bet, and per unit of
  the backer's stake for a LAY bet (the layer risks ``price - 1`` per unit, see below).
- Commission and half spread are not part of ``edge``: the caller passes the takeable price (the book 60 s after the
  signal, see S58/S59), which already carries the spread. Commission belongs to the caller's policy.

Lay maths (worked example, per unit of backer's stake)
    Lay the selection at ``price`` = 2.20 when the model says ``p`` = 0.40 (the selection wins 40%).
    - If the selection loses (prob 0.60) the layer keeps the backer's stake: +1.00.
    - If the selection wins (prob 0.40) the layer pays out (price - 1) = 1.20: -1.20.
    edge = 0.60 * 1.00 - 0.40 * 1.20 = 0.12 per unit of backer's stake.
    Liability per unit = price - 1 = 1.20. Kelly on bankroll as liability = edge = 0.12
    (equivalently 1 - p * price = 1 - 0.88).
    Break-even: the lay has an edge when ``p < 1 / price`` (here 1 / 2.20 = 0.4545).

Back maths: ``edge = p * price - 1``; Kelly on bankroll as stake = ``edge / (price - 1)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

BACK = "BACK"
LAY = "LAY"
_SIDES = (BACK, LAY)
# Book keys, same convention as the tick dicts used by strategy.value_policy.
BOOK_KEYS = (
    ("home", "back_home", BACK), ("home", "lay_home", LAY),
    ("away", "back_away", BACK), ("away", "lay_away", LAY),
)


def _side(side: str) -> str:
    s = str(side).upper()
    if s not in _SIDES:
        raise ValueError(f"side must be BACK or LAY, got {side!r}")
    return s


def _check_price(price: float) -> float:
    price = float(price)
    if not math.isfinite(price) or price <= 1.0:
        raise ValueError(f"price must be finite and > 1.0, got {price!r}")
    return price


def _check_prob(p: float) -> float:
    p = float(p)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability must be in [0, 1], got {p!r}")
    return p


def _check_sd(p_sd: float) -> float:
    p_sd = float(p_sd)
    if not math.isfinite(p_sd) or p_sd < 0.0:
        raise ValueError(f"p_sd must be finite and >= 0, got {p_sd!r}")
    return p_sd


def edge(side: str, p: float, price: float) -> float:
    """Expected profit per unit of backer's stake at win probability ``p``.

    BACK: ``p * price - 1``. LAY: ``(1 - p) - p * (price - 1)`` (see the module docstring for the worked example).
    """
    side = _side(side)
    p = _check_prob(p)
    price = _check_price(price)
    if side == BACK:
        return p * price - 1.0
    return (1.0 - p) - p * (price - 1.0)


def edge_lower(side: str, p_mean: float, p_sd: float, price: float, z: float = 1.0) -> float:
    """Edge evaluated at the pessimistic probability: ``p_mean - z * p_sd`` for a BACK, ``p_mean + z * p_sd`` for a LAY.

    Edge is linear in ``p``, so this is the lower bound of the edge at a ``z``-sigma credible level.
    """
    side = _side(side)
    p_sd = _check_sd(p_sd)
    z = float(z)
    if z < 0.0:
        raise ValueError(f"z must be >= 0, got {z!r}")
    p_mean = _check_prob(p_mean)
    shift = -z * p_sd if side == BACK else z * p_sd
    p_pess = min(1.0, max(0.0, p_mean + shift))
    return edge(side, p_pess, price)


def edge_prob(side: str, p_mean: float, price: float) -> float:
    """Distance from break-even in probability points: ``p - 1/price`` for a BACK, ``1/price - p`` for a LAY."""
    side = _side(side)
    price = _check_price(price)
    p_mean = _check_prob(p_mean)
    return p_mean - 1.0 / price if side == BACK else 1.0 / price - p_mean


def shrink_factor(p_mean: float, p_sd: float, price: float, side: str) -> float:
    """Uncertainty shrink ``1 / (1 + (p_sd / edge_prob)^2)`` in [0, 1]; 0 when there is no edge on the mean."""
    p_sd = _check_sd(p_sd)
    ep = edge_prob(side, p_mean, price)
    if ep <= 0.0:
        return 0.0
    if p_sd == 0.0:
        return 1.0
    return 1.0 / (1.0 + (p_sd / ep) ** 2)


def kelly_of_bankroll(side: str, p: float, price: float) -> float:
    """Full Kelly fraction of bankroll for the mean edge, 0 when there is no edge.

    BACK: fraction of bankroll staked = ``edge / (price - 1)``.
    LAY: fraction of bankroll risked as liability = ``edge`` (the layer's stake is ``liability / (price - 1)``).
    """
    e = edge(side, p, price)
    if e <= 0.0:
        return 0.0
    return e / (_check_price(price) - 1.0) if _side(side) == BACK else e


@dataclass(frozen=True)
class Selection:
    """A bet worth considering: side, selection ("home"/"away"), takeable price and its edges."""

    side: str
    selection: str
    price: float
    edge: float        # at the posterior mean
    edge_lower: float  # at the pessimistic probability (the value compared with the segment minimum)


def _valid_price(value: object) -> Optional[float]:
    if value is None:
        return None
    price = float(value)
    return price if math.isfinite(price) and price > 1.0 else None


def select(
    p_mean: float,
    p_sd: float,
    book_after_latency: Mapping[str, object],
    *,
    segment: str,
    min_edge_by_segment: Mapping[str, float],
    z: float = 1.0,
) -> Optional[Selection]:
    """Best BACK or LAY on the book the caller passes (the book 60 s after the signal), or None.

    A candidate needs ``edge_lower > min_edge_by_segment[segment]`` at its takeable price. Among candidates the one with
    the highest ``edge_lower`` wins; ties keep the order home back, home lay, away back, away lay.
    ``book_after_latency`` uses the tick keys ``back_home``, ``lay_home``, ``back_away``, ``lay_away``; missing or
    invalid prices are skipped.
    """
    if segment not in min_edge_by_segment:
        raise ValueError(f"unknown segment {segment!r}; pre-registered segments are {sorted(min_edge_by_segment)}")
    minimum = float(min_edge_by_segment[segment])
    p_home = _check_prob(p_mean)
    _check_sd(p_sd)
    best: Optional[Selection] = None
    for selection, key, side in BOOK_KEYS:
        price = _valid_price(book_after_latency.get(key))
        if price is None:
            continue
        p = p_home if selection == "home" else 1.0 - p_home
        low = edge_lower(side, p, p_sd, price, z=z)  # sd of 1 - p equals sd of p
        if low <= minimum:
            continue
        if best is None or low > best.edge_lower:
            best = Selection(side, selection, price, edge(side, p, price), low)
    return best


def stake(
    p_mean: float,
    p_sd: float,
    price: float,
    side: str,
    bankroll: float,
    *,
    kelly_fraction: float = 0.25,
    max_match_frac: float = 0.02,
    max_day_frac: float = 0.06,
    open_match_exposure: float = 0.0,
    open_day_exposure: float = 0.0,
) -> float:
    """Human-rounded stake (backer's stake) for one bet, or 0.0.

    Risk is the money at risk: the stake for a BACK, the liability ``stake * (price - 1)`` for a LAY. Risk is
    ``kelly_fraction * kelly * shrink * bankroll``, then capped so that this match's total risk stays within
    ``max_match_frac * bankroll`` and the day's within ``max_day_frac * bankroll`` (``open_*_exposure`` is risk already
    taken, in the same units). The stake is rounded down by ``round_stake`` and is 0 below the minimum stake.
    """
    side = _side(side)
    p_sd = _check_sd(p_sd)
    price = _check_price(price)
    p_mean = _check_prob(p_mean)
    bankroll = float(bankroll)
    if bankroll <= 0.0 or kelly_fraction <= 0.0:
        return 0.0
    kelly = kelly_of_bankroll(side, p_mean, price)
    if kelly <= 0.0:
        return 0.0
    shrink = shrink_factor(p_mean, p_sd, price, side)
    risk = kelly_fraction * kelly * shrink * bankroll
    room = min(
        max_match_frac * bankroll - float(open_match_exposure),
        max_day_frac * bankroll - float(open_day_exposure),
    )
    risk = min(risk, room)
    if risk <= 0.0:
        return 0.0
    raw = risk if side == BACK else risk / (price - 1.0)
    from strategy.value_policy import round_stake  # local import: value_policy imports this module

    return round_stake(raw)
