"""
PHOENIX synthetic exchange market (single home for the price generator).

Used by ``prepare_specialist_datasets.py`` and ``cricsheet_harmonizer.py``.

Design rule (S13): the synthetic market must carry NO free edge that a real market lacks.
Price = f(public match state) + z + x_t, in logit space, where

* ``z`` is an unobserved per-match latent strength offset (fixed for the match), so that
  ``f(state)`` alone is not the price and cannot simply be inverted by the agent;
* ``x_t`` is, by default, a **martingale** (random walk): given everything observable the
  expected next-tick change of ``x`` is zero, so deviations are not mean-reverting.

The old Ornstein-Uhlenbeck noise (``noise_mode="ou"``) is mean-reverting around a fair value
that is a function of observable state. That is a guaranteed edge with no live counterpart and
is kept ONLY for experiments / the audit's positive control. It is exploitable.
"""

from __future__ import annotations

import math
import random
from typing import Literal, Optional, Tuple

NoiseMode = Literal["martingale", "ou", "none"]

MARKET_MODEL_VERSION = "v2"
# Bumped whenever the synthetic price/volume generator changes. Recorded on every match.
CORPUS_FORMAT_VERSION = 2

LATENT_SIGMA = 0.3


def market_model_tag(noise_mode: str) -> str:
    """Tag written on every tick (and match) so corpora are distinguishable."""
    return f"{noise_mode}:{MARKET_MODEL_VERSION}"


class MarketNoise:
    """Outcome-independent noise in log-odds space plus a per-match latent offset.

    ``step()`` returns ``latent + x_t`` (the logit offset added to the state-based fair value).

    noise_mode:
      * ``martingale`` (default): ``x <- x + N(0, sigma)``. Expected next change is 0.
      * ``ou``: ``x <- (1 - theta) * x + N(0, sigma)``. Mean-reverting => EXPLOITABLE; experiments only.
      * ``none``: ``x`` stays 0 (only the latent offset remains).
    """

    def __init__(
        self,
        seed: int,
        sigma: float = 0.08,
        theta: float = 0.10,
        noise_mode: NoiseMode = "martingale",
        latent_sigma: float = LATENT_SIGMA,
    ) -> None:
        if noise_mode not in ("martingale", "ou", "none"):
            raise ValueError(f"unknown noise_mode: {noise_mode!r}")
        self._rng = random.Random(seed * 7919 + 13)
        self._sigma = sigma
        self._theta = theta
        self.noise_mode = noise_mode
        self._x = 0.0
        # Separate stream so the latent does not shift the noise path between modes.
        self.latent = random.Random(seed * 104729 + 7).gauss(0.0, latent_sigma) if latent_sigma > 0 else 0.0

    def step(self) -> float:
        if self.noise_mode == "martingale":
            self._x = self._x + self._rng.gauss(0.0, self._sigma)
        elif self.noise_mode == "ou":
            self._x = (1.0 - self._theta) * self._x + self._rng.gauss(0.0, self._sigma)
        return self.latent + self._x


class CricketWinProbModel:
    """Computes dynamic in-play win probabilities for cricket match states.

    The result is a pure function of the match state (plus an optional outcome-independent
    ``noise_logit`` offset from ``MarketNoise``). It never sees the match result.
    """

    @staticmethod
    def calculate_win_prob(
        fmt: str,
        innings: int,
        overs: float,
        score: int,
        wickets: int,
        target: Optional[int],
        batting_is_home: bool,
        is_women: bool = False,
        specialist_key: str = "",
        noise_logit: float = 0.0,
    ) -> float:
        fmt_upper = fmt.upper()
        is_t20 = "T20" in fmt_upper or "IPL" in fmt_upper
        is_odi = "ODI" in fmt_upper

        max_overs = 20.0 if is_t20 else (50.0 if is_odi else 100.0)
        max_balls = int(max_overs * 6)

        over_int = int(overs)
        balls_in_over = int(round((overs - over_int) * 10))
        balls_bowled = min(max_balls, over_int * 6 + balls_in_over)
        balls_remaining = max(0, max_balls - balls_bowled)
        wickets_left = max(0, 10 - wickets)

        # Calibrated par score run rates per format, league, and gender:
        if is_women:
            par_rr = 6.8 if is_t20 else (4.5 if is_odi else 2.8)
            wicket_decay = 1.35  # Wickets more punishing in women's matches
        else:
            if "IPL" in fmt_upper or specialist_key == "ipl":
                par_rr = 9.4  # ~188 in IPL
            elif is_t20:
                par_rr = 8.6  # ~172 in Men's T20
            elif is_odi:
                par_rr = 5.8  # ~290 in Men's ODI
            else:
                par_rr = 3.4  # ~340 in Men's Test
            wicket_decay = 1.05

        # Chase logic applies to the 2nd innings of limited-overs games and the 4th of a Test.
        is_chase = innings == 4 or (innings == 2 and max_overs < 100.0)

        if not is_chase:
            projected_score = score + (balls_remaining / 6.0) * par_rr * ((wickets_left / 10.0) ** (0.65 * wicket_decay))
            par_total = max_overs * par_rr
            diff = max(-500.0, min(500.0, projected_score - par_total))
            div = 15.0 if is_t20 else 30.0
            p_batting = 1.0 / (1.0 + math.exp(-diff / div))
            p_home = p_batting if batting_is_home else (1.0 - p_batting)
        else:
            effective_target = target or int(max_overs * par_rr)
            runs_needed = max(0, effective_target - score)

            if runs_needed <= 0:
                p_batting = 0.99
            elif balls_remaining <= 0 or wickets_left <= 0:
                p_batting = 0.01
            else:
                req_rr = (runs_needed / max(1, balls_remaining)) * 6.0
                rr_pressure = req_rr / max(par_rr, 1.0)
                wickets_factor = (wickets_left / 10.0) ** (1.1 * wicket_decay)

                z = max(-50.0, min(50.0, (2.2 * wickets_factor) - (1.4 * rr_pressure)))
                p_batting = 1.0 / (1.0 + math.exp(-z))

            p_home = p_batting if batting_is_home else (1.0 - p_batting)

        if noise_logit:
            p_c = max(1e-4, min(1.0 - 1e-4, p_home))
            p_home = 1.0 / (1.0 + math.exp(-(math.log(p_c / (1.0 - p_c)) + noise_logit)))

        return max(0.025, min(0.975, p_home))


def synthesize_exchange_ticks(
    p_home: float,
    seed: int,
) -> Tuple[float, float, float, float, float]:
    """Back/lay ladders from a home win probability: (bh, lh, ba, la, overround)."""
    rng = random.Random(seed)
    p_away = 1.0 - p_home

    # 1 to 3 ticks spread (0.8% to 1.8% overround)
    spread_prob = rng.uniform(0.008, 0.018)

    p_back_home = min(0.98, p_home + spread_prob / 2.0)
    p_lay_home = max(0.02, p_home - spread_prob / 2.0)

    back_home = round(max(1.02, min(50.0, 1.0 / p_back_home)), 2)
    lay_home = round(max(back_home + 0.02, min(52.0, 1.0 / p_lay_home)), 2)

    p_back_away = min(0.98, p_away + spread_prob / 2.0)
    p_lay_away = max(0.02, p_away - spread_prob / 2.0)

    back_away = round(max(1.02, min(50.0, 1.0 / p_back_away)), 2)
    lay_away = round(max(back_away + 0.02, min(52.0, 1.0 / p_lay_away)), 2)

    overround = round((1.0 / back_home + 1.0 / back_away) - 1.0, 4)
    return back_home, lay_home, back_away, lay_away, overround


def synthesize_volumes(
    vol_base: float,
    prev_p_home: Optional[float],
    p_home: float,
    progress: float,
) -> Tuple[float, float, float, float]:
    """Deterministic volumes: time-of-match profile x activity proportional to |delta price|.

    No i.i.d. jitter. Volume is a function of information that exists in the tick stream
    (how much the price just moved and how far through the match we are), so the volume
    features are informative about *activity* but, by construction, not about the next
    price change or the result. Returns (back_home, lay_home, back_away, lay_away).
    """
    progress = min(1.0, max(0.0, progress))
    profile = 0.7 + 1.2 * (2.0 * progress - 1.0) ** 2  # busy at the start and the death
    delta = abs(p_home - prev_p_home) if prev_p_home is not None else 0.0
    activity = 1.0 + 20.0 * delta
    v = vol_base * profile * activity
    return round(v, 2), round(v * 0.9, 2), round(v, 2), round(v * 0.9, 2)
