"""Market probability module (S59): one definition of "the market's probability" and of the margin we pay.

Default de-vig method is ``power``: it solves ``p_i ** k`` summing to 1 for k. Unlike the multiplicative method,
which takes the same proportion off every runner, the power method takes proportionally more off longshots, which
matches the favourite-longshot bias seen in exchange prices. Shin is kept as the insider-trading alternative: it
also corrects for longshots, via a closed form, but its root find is slower and gains little on two-runner books.
Multiplicative is the simplest baseline and is exact on a fair book.

Pure functions, numpy and scipy only. No I/O, no database, no network. Prices are decimal odds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Union

import numpy as np
from scipy.optimize import brentq

from shared.metrics import side_of

DEVIG_METHODS = ("band", "multiplicative", "power", "shin")
# Research and evaluation baseline (dataset, blend, referee): power de-vig of each runner's mid.
DEFAULT_DEVIG = "power"
# Trading: "band" = midpoint of the probability range that BOTH runners' back and lay prices allow (a two-runner
# exchange book links them: backing away at o is laying home at o/(o-1)). It cannot sit outside the book, so a bet at
# a price the book itself offers never shows an edge from the book alone. With wide spreads the per-runner mids can sum
# below 1 and power de-vig then lands outside the book (seen live 2026-11-03: it priced Janakpur at 25.5% when the book
# allowed 23.1-24.4%, and the market-only control traded on it). Falls back to "power" when a price is missing.
TRADING_DEVIG = "band"

_WILSON_Z = 1.959963984540054  # two-sided 95%
_LOGIT_EPS = 1e-6

ArrayLike = Union[float, Sequence[float], np.ndarray]


def _valid_price(price: Any) -> Optional[float]:
    """Return the price as a float when it is a usable decimal price (> 1, finite), else None."""
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 1.0:
        return None
    return value


def implied(back: Any, lay: Any) -> Optional[float]:
    """Mid implied probability for one runner from its best back and lay prices.

    The mid is the mean of ``1/back`` and ``1/lay``. A missing side uses the other side alone. Both missing gives None.
    """
    b = _valid_price(back)
    l = _valid_price(lay)
    if b is None and l is None:
        return None
    if b is None:
        return 1.0 / l
    if l is None:
        return 1.0 / b
    return 0.5 * (1.0 / b + 1.0 / l)


def takeable(side: Any, back: Any, lay: Any) -> Optional[float]:
    """Implied probability of the price a bet on ``side`` would actually fill at.

    LotusBook shows, per runner, the price a backer gets in the Back column and the price a layer gets in the Lay
    column (back is the lower of the two, e.g. 1.84 / 2.2), so a BACK bet fills at ``back`` and a LAY bet at ``lay``.
    Unlike :func:`implied`, there is no fallback: if the price that would be taken is missing, the result is None.
    """
    price = _valid_price(back) if side_of(side) == "BACK" else _valid_price(lay)
    if price is None:
        return None
    return 1.0 / price


def overround(p_home_raw: float, p_away_raw: float) -> float:
    """Booksum minus one for a two-runner book. Positive means the book carries a margin."""
    return float(p_home_raw) + float(p_away_raw) - 1.0


def _check_raw(p_home_raw: float, p_away_raw: float) -> tuple[float, float]:
    ph, pa = float(p_home_raw), float(p_away_raw)
    if not (math.isfinite(ph) and math.isfinite(pa)) or ph <= 0.0 or pa <= 0.0:
        raise ValueError(f"raw implied probabilities must be finite and positive, got {p_home_raw}, {p_away_raw}")
    return ph, pa


def devig_multiplicative(p_home_raw: float, p_away_raw: float) -> tuple[float, float]:
    """Proportional de-vig: divide each raw probability by the booksum."""
    ph, pa = _check_raw(p_home_raw, p_away_raw)
    total = ph + pa
    return ph / total, pa / total


def devig_power(p_home_raw: float, p_away_raw: float) -> tuple[float, float]:
    """Power de-vig: find k with ``p_home**k + p_away**k == 1`` and return the powered probabilities.

    With a booksum above one, k > 1 and the favourite keeps more of its probability than under the multiplicative
    method. Solved by Brent's method on k. Returns a pair summing to 1 within 1e-9.
    """
    ph, pa = _check_raw(p_home_raw, p_away_raw)
    total = ph + pa
    if abs(total - 1.0) < 1e-12:
        return ph, pa

    def f(k: float) -> float:
        return ph ** k + pa ** k - 1.0

    if total > 1.0:
        lo, hi = 1.0, 2.0
        while f(hi) > 0.0:
            hi *= 2.0
            if hi > 1e6:
                raise ValueError("power de-vig: could not bracket k")
    else:
        lo, hi = 0.0, 1.0
    k = brentq(f, lo, hi, xtol=1e-14, rtol=1e-14, maxiter=500)
    qh, qa = ph ** k, pa ** k
    s = qh + qa
    return qh / s, qa / s


def devig_shin(p_home_raw: float, p_away_raw: float) -> tuple[float, float]:
    """Shin de-vig for two runners, after Shin (1993) and Jullien and Salanie (1994).

    With ``S`` the booksum and ``q_i = pi_i**2 / S``, the fair probability is
    ``p_i(z) = (sqrt(z**2 + 4 (1 - z) q_i) - z) / (2 (1 - z))``, where z is the insider proportion. It is found by
    Brent's method so that the probabilities sum to 1. The algebraically equivalent form
    ``2 q_i / (sqrt(z**2 + 4 (1 - z) q_i) + z)`` is used so that z near 1 stays numerically stable.
    """
    ph, pa = _check_raw(p_home_raw, p_away_raw)
    total = ph + pa
    if abs(total - 1.0) < 1e-12:
        return ph, pa
    if total < 1.0:
        # Shin is defined for books with a margin. Below one the proportional fair values are used.
        return devig_multiplicative(ph, pa)
    qh, qa = ph * ph / total, pa * pa / total

    def probs(z: float) -> tuple[float, float]:
        ph_z = 2.0 * qh / (math.sqrt(z * z + 4.0 * (1.0 - z) * qh) + z)
        pa_z = 2.0 * qa / (math.sqrt(z * z + 4.0 * (1.0 - z) * qa) + z)
        return ph_z, pa_z

    def f(z: float) -> float:
        a, b = probs(z)
        return a + b - 1.0

    z = brentq(f, 0.0, 1.0, xtol=1e-15, rtol=1e-15, maxiter=500)
    a, b = probs(z)
    s = a + b
    return a / s, b / s


_DEVIG = {
    "multiplicative": devig_multiplicative,
    "power": devig_power,
    "shin": devig_shin,
}


def _band_bounds(book: Mapping[str, Any]) -> Optional[tuple[float, float]]:
    """(low, high) of P(home) allowed by the book, or None when a bound is missing on either side.

    Lower bounds: 1/lay_home (laying home is offered at that price) and 1 - 1/back_away (backing away = laying home).
    Upper bounds: 1/back_home and 1 - 1/lay_away.
    """
    bh, lh = _valid_price(book.get("back_home")), _valid_price(book.get("lay_home"))
    ba, la = _valid_price(book.get("back_away")), _valid_price(book.get("lay_away"))
    lows = [x for x in ((1.0 / lh) if lh else None, (1.0 - 1.0 / ba) if ba else None) if x is not None]
    highs = [x for x in ((1.0 / bh) if bh else None, (1.0 - 1.0 / la) if la else None) if x is not None]
    if not lows or not highs:
        return None
    return max(lows), min(highs)


def _band_crossed(book: Mapping[str, Any]) -> bool:
    bounds = _band_bounds(book)
    return bounds is not None and bounds[0] > bounds[1] + 1e-12


def band_bounds(book: Mapping[str, Any]) -> Optional[tuple[float, float]]:
    """Public: (low, high) of P(home) the two runners' prices allow, or None when a bound is missing (S75 guards)."""
    return _band_bounds(book)


def band_prob_home(book: Mapping[str, Any]) -> Optional[float]:
    """Midpoint of the P(home) range the two runners' prices jointly allow; None when a bound is missing or the
    book contradicts itself (the range is empty, e.g. a stale or mixed book)."""
    bounds = _band_bounds(book)
    if bounds is None:
        return None
    low, high = bounds
    if low > high + 1e-12:
        return None
    return float(min(1.0, max(0.0, (low + high) / 2.0)))


def market_prob_home(book: Mapping[str, Any], method: str = DEFAULT_DEVIG) -> Optional[float]:
    """De-vigged home win probability from a snapshot book.

    ``book`` holds ``back_home``, ``lay_home``, ``back_away`` and ``lay_away``. Each runner's mid implied probability
    uses whichever of its prices are present. Returns None when either runner has no usable price, or when the
    implied probabilities are not positive.
    """
    if method not in _DEVIG and method != "band":
        raise ValueError(f"unknown de-vig method {method!r}; expected one of {DEVIG_METHODS}")
    if method == "band":
        banded = band_prob_home(book)
        if banded is not None or _band_crossed(book):
            return banded
        method = "power"  # a price is missing: no band to read
    ph = implied(book.get("back_home"), book.get("lay_home"))
    pa = implied(book.get("back_away"), book.get("lay_away"))
    if ph is None or pa is None or ph <= 0.0 or pa <= 0.0:
        return None
    fair_home, _ = _DEVIG[method](ph, pa)
    return float(fair_home)


def margin_paid(side: Any, price: float, p_fair: float) -> float:
    """Expected loss per unit staked when betting ``side`` at ``price`` with true probability ``p_fair``.

    A unit back stake wins ``price - 1`` with probability ``p_fair`` and loses 1 otherwise, so its expected loss is
    ``1 - p_fair * price``. A unit lay stake is the reverse, with expected loss ``p_fair * price - 1``. Both are zero
    at a fair price and positive when the price is worse than fair.
    """
    p = float(p_fair)
    d = float(price)
    if not (0.0 < p < 1.0) or d <= 1.0:
        raise ValueError(f"need 0 < p_fair < 1 and price > 1, got p_fair={p_fair}, price={price}")
    if side_of(side) == "LAY":
        return p * d - 1.0
    return 1.0 - p * d


def _logit(p: ArrayLike) -> np.ndarray:
    x = np.clip(np.asarray(p, dtype=float), _LOGIT_EPS, 1.0 - _LOGIT_EPS)
    return np.log(x / (1.0 - x))


def _sigmoid(x: ArrayLike) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def wilson_interval(successes: int, n: int, z: float = _WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Returns (nan, nan) when n is zero."""
    if n <= 0:
        return (float("nan"), float("nan"))
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class FavLongshotBin:
    """One probability bin of the calibration table."""

    lo: float
    hi: float
    n: int
    mean_p_market: float
    observed_rate: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True)
class FavLongshotFit:
    """Fitted favourite-longshot curve ``logit p_true = a + b * logit p_market``, with standard errors."""

    a: float
    b: float
    se_a: float
    se_b: float
    n: int
    bins: tuple[FavLongshotBin, ...] = field(default_factory=tuple)


def _fit_logistic_line(x: np.ndarray, y: np.ndarray, max_iter: int = 100) -> tuple[float, float, float, float]:
    """Maximum likelihood for ``y ~ Bernoulli(sigmoid(a + b x))`` by Newton iterations, with SEs from the Hessian."""
    design = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(max_iter):
        mu = _sigmoid(design @ beta)
        w = mu * (1.0 - mu)
        grad = design.T @ (y - mu)
        hess = design.T @ (design * w[:, None])
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError as exc:
            raise ValueError("favourite-longshot fit is singular: prices have no spread") from exc
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    else:
        raise RuntimeError("favourite-longshot fit did not converge")
    mu = _sigmoid(design @ beta)
    hess = design.T @ (design * (mu * (1.0 - mu))[:, None])
    cov = np.linalg.inv(hess)
    se = np.sqrt(np.diag(cov))
    return float(beta[0]), float(beta[1]), float(se[0]), float(se[1])


def fit_fav_longshot(p_market: ArrayLike, outcome: ArrayLike, n_bins: int = 10) -> FavLongshotFit:
    """Calibration of market probabilities against outcomes.

    Equal-width bins on [0, 1] report the observed win rate with a Wilson 95% interval. A logistic line in
    ``logit p_market`` is fitted by maximum likelihood, giving ``a``, ``b`` and their standard errors. ``a = 0`` and
    ``b = 1`` mean the market is calibrated; ``b > 1`` means favourites are underpriced relative to longshots.
    """
    p = np.asarray(p_market, dtype=float).ravel()
    y = np.asarray(outcome, dtype=float).ravel()
    if p.shape != y.shape:
        raise ValueError("p_market and outcome must have the same length")
    if p.size < 2:
        raise ValueError("need at least two observations to fit")
    if np.any(~np.isfinite(p)) or np.any((p <= 0.0) | (p >= 1.0)):
        raise ValueError("p_market values must lie strictly between 0 and 1")
    if not np.all((y == 0.0) | (y == 1.0)):
        raise ValueError("outcome must be 0 or 1")
    if n_bins < 1:
        raise ValueError("n_bins must be at least 1")

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    bins = []
    for i in range(n_bins):
        mask = idx == i
        n_i = int(mask.sum())
        if n_i == 0:
            bins.append(FavLongshotBin(float(edges[i]), float(edges[i + 1]), 0,
                                       float("nan"), float("nan"), float("nan"), float("nan")))
            continue
        successes = int(y[mask].sum())
        lo, hi = wilson_interval(successes, n_i)
        bins.append(FavLongshotBin(float(edges[i]), float(edges[i + 1]), n_i,
                                   float(p[mask].mean()), successes / n_i, lo, hi))

    if y.min() == y.max():
        raise ValueError("outcome has a single class; the calibration line cannot be fitted")
    a, b, se_a, se_b = _fit_logistic_line(_logit(p), y)
    return FavLongshotFit(a=a, b=b, se_a=se_a, se_b=se_b, n=int(p.size), bins=tuple(bins))


def apply_fav_longshot(p_market: ArrayLike, fit: FavLongshotFit) -> np.ndarray:
    """Recalibrated probability ``sigmoid(a + b * logit p_market)``. Accepts a scalar or an array."""
    return _sigmoid(fit.a + fit.b * _logit(p_market))


__all__ = [
    "DEVIG_METHODS",
    "DEFAULT_DEVIG",
    "TRADING_DEVIG",
    "band_bounds",
    "band_prob_home",
    "implied",
    "takeable",
    "overround",
    "devig_multiplicative",
    "devig_power",
    "devig_shin",
    "market_prob_home",
    "margin_paid",
    "wilson_interval",
    "FavLongshotBin",
    "FavLongshotFit",
    "fit_fav_longshot",
    "apply_fav_longshot",
]
