"""Session-runs model (S48): calibrated distribution of runs a batting side scores in a fixed over window.

For a live innings state (legal balls bowled, runs, wickets) and a window end ``K`` overs, the model predicts the
distribution of runs scored from now until legal ball ``K * 6``. That gives ``p_at_least(state, K, line)`` for
session ("runs by over K") lines and fair prices for both sides of a line.

Design (S38 lesson applied):

* The conditional mean is a log-link negative-binomial GLM with an exposure offset ``log(balls_left / 6)``, so the
  expected runs scale with the balls that remain. It is linear in its features and L2-penalised. There are no trees,
  so a match-constant feature (venue par, XI strength, gender) can only shift the mean linearly and cannot be
  memorised per match.
* The dispersion is fitted on training seasons and then re-calibrated on the calibration season with the mean fixed
  (see ``SessionRunsModel.calibrate``).
* Training rows and live queries use the same ``design`` function, so the feature definition cannot drift.

Conventions (confirm against S47 before pricing anything real):

* A session line ``L`` is priced on the integer threshold ``T = ceil(L)``. YES wins if runs ``>= T``; NO wins if
  runs ``< T``. ``line_threshold`` is the only place this rule lives.
* A quoted rate ``r`` is a price per ``RateConvention.scale`` (default 100): buying YES or NO at ``r`` costs
  ``r / scale`` per unit and pays one unit when it wins. ``edge`` is the fair win probability minus that price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

WINDOW_OVERS: Dict[str, Tuple[int, ...]] = {"t20": (6, 10, 15, 20), "odi": (10, 20, 30, 40, 50)}
MAX_OVERS: Dict[str, int] = {"t20": 20, "odi": 50}
POWERPLAY_OVERS: Dict[str, int] = {"t20": 6, "odi": 10}
# Current run rate is shrunk towards the venue par with this many pseudo-overs of par-rate balls.
PRIOR_OVERS = 2.0
XI_SCALE = 10.0
ALPHA_L2 = 1e-4
MAX_WICKETS = 9
DISMISSAL_EXCLUDED = frozenset({"retired hurt", "retired not out"})
KMAX_CRPS = 600
_LOG_CLIP = 30.0
FEATURE_NAMES: Tuple[str, ...] = (
    "intercept", "log_rate_over_par", "log_par", "balls_left_frac", "powerplay", "xi_diff_scaled", "female",
    *(f"wickets_{i}" for i in range(1, MAX_WICKETS + 1)),
)


def line_threshold(line: float) -> int:
    """Integer run threshold for a session line: YES wins if runs >= this value."""
    return int(math.ceil(float(line)))


@dataclass(frozen=True)
class RateConvention:
    """How quoted session rates map to prices (configurable until S47 confirms the exchange convention)."""

    scale: float = 100.0


DEFAULT_CONVENTION = RateConvention()


@dataclass(frozen=True)
class InningsTrace:
    """Cumulative runs and wickets indexed by legal balls bowled (index 0 is the innings start)."""

    runs_after: np.ndarray  # float64, length legal_balls + 1
    wkts_after: np.ndarray  # int64, length legal_balls + 1
    legal_balls: int


@dataclass(frozen=True)
class SessionState:
    """Live innings state at the moment of a query. ``par_rr`` is runs per over from the venue prior (as of)."""

    family: str
    balls_done: int
    runs: float
    wickets: int
    par_rr: float
    xi_diff: float = 0.0
    female: bool = False


def trace_innings(innings: Mapping[str, Any]) -> InningsTrace:
    """Reduce one Cricsheet innings to its legal-ball trace.

    Wides and no-balls are not legal balls but their runs count. Byes and leg byes count as runs.
    ``retired hurt`` and ``retired not out`` are not wickets.
    """
    runs_after: List[float] = [0.0]
    wkts_after: List[int] = [0]
    cum = 0.0
    wickets = 0
    legal = 0
    for over in innings.get("overs") or []:
        for d in over.get("deliveries") or []:
            runs = d.get("runs") or {}
            cum += float(runs.get("total") or 0)
            for w in d.get("wickets") or []:
                if str(w.get("kind", "")).strip().lower() not in DISMISSAL_EXCLUDED:
                    wickets += 1
            extras = d.get("extras") or {}
            if "wides" in extras or "noballs" in extras:
                continue
            legal += 1
            runs_after.append(cum)
            wkts_after.append(wickets)
    return InningsTrace(np.asarray(runs_after, dtype=float), np.asarray(wkts_after, dtype=np.int64), legal)


def design(
    family: str,
    balls_done: Any,
    runs: Any,
    wickets: Any,
    window_balls: Any,
    par_rr: Any,
    xi_diff: Any,
    female: Any,
) -> Tuple[np.ndarray, np.ndarray]:
    """Design matrix ``X`` and exposure offset for the GLM. Scalars or equal-length arrays.

    Columns: intercept, log(rate / par), log(par), balls-left fraction of the innings, powerplay flag,
    wicket dummies 1..9, XI difference (scaled), female flag. The offset is ``log(balls_left / 6)``.
    """
    bd = np.atleast_1d(np.asarray(balls_done, dtype=float))
    rs = np.atleast_1d(np.asarray(runs, dtype=float))
    wk = np.atleast_1d(np.asarray(wickets, dtype=np.int64))
    wb = np.atleast_1d(np.asarray(window_balls, dtype=float))
    par = np.atleast_1d(np.asarray(par_rr, dtype=float))
    xi = np.atleast_1d(np.asarray(xi_diff, dtype=float))
    fem = np.atleast_1d(np.asarray(female, dtype=float))
    bl = wb - bd
    if np.any(bl <= 0):
        raise ValueError("balls_done must be before the window end")
    max_balls = float(MAX_OVERS[family] * 6)
    pp_balls = float(POWERPLAY_OVERS[family] * 6)
    rate = (rs + PRIOR_OVERS * par) / (bd / 6.0 + PRIOR_OVERS)
    rate = np.maximum(rate, 1e-3)
    par_safe = np.maximum(par, 1e-3)
    wicket_cols = (wk[:, None] == np.arange(1, MAX_WICKETS + 1)[None, :]).astype(float)
    cols = [
        np.ones_like(bd),
        np.log(rate / par_safe),
        np.log(par_safe),
        bl / max_balls,
        (bd < pp_balls).astype(float),
        xi / XI_SCALE,
        fem,
    ]
    X = np.column_stack(cols + [wicket_cols[:, i] for i in range(MAX_WICKETS)])
    offset = np.log(bl / 6.0)
    return X, offset


def _nll_grad(
    params: np.ndarray,
    X: np.ndarray,
    off: np.ndarray,
    y: np.ndarray,
    fit_beta: bool,
    fit_theta: bool,
    beta_fixed: Optional[np.ndarray],
    theta_fixed: float,
) -> Tuple[float, np.ndarray]:
    p = X.shape[1]
    idx = 0
    if fit_beta:
        beta = params[:p]
        idx = p
    else:
        beta = beta_fixed
    theta = float(params[idx]) if fit_theta else theta_fixed
    eta = np.clip(X @ beta + off, -_LOG_CLIP, _LOG_CLIP)
    mu = np.exp(eta)
    r = math.exp(theta)
    ll = (
        special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1.0)
        + r * theta - r * np.log(r + mu) + y * eta - y * np.log(r + mu)
    )
    n = float(len(y))
    penal = 0.5 * ALPHA_L2 * float(np.sum(beta[1:] ** 2))
    nll = -float(np.mean(ll)) + penal
    grads: List[np.ndarray] = []
    if fit_beta:
        g_eta = -(y - (r + y) * mu / (r + mu)) / n
        g_beta = X.T @ g_eta
        g_beta[1:] += ALPHA_L2 * beta[1:]
        grads.append(g_beta)
    if fit_theta:
        dl_dr = (
            special.digamma(y + r) - special.digamma(r) + math.log(r) + 1.0
            - np.log(r + mu) - r / (r + mu) - y / (r + mu)
        )
        grads.append(np.array([-float(np.sum(r * dl_dr)) / n]))
    return nll, (np.concatenate(grads) if grads else np.empty(0))


@dataclass
class SessionRunsModel:
    """Negative-binomial runs-remaining model for one family."""

    family: str
    beta: Optional[np.ndarray] = None
    theta: Optional[float] = None
    trained_through: str = ""
    calibrated_on: str = ""
    n_train_rows: int = 0
    n_calib_rows: int = 0
    feature_names: List[str] = field(default_factory=list)

    # ------------------------------------------------------------ fitting
    def fit(self, X: np.ndarray, off: np.ndarray, y: np.ndarray) -> "SessionRunsModel":
        """Fit mean and dispersion jointly on the training rows."""
        if len(y) == 0:
            raise ValueError("no training rows")
        p = X.shape[1]
        mean_exp = float(np.mean(np.exp(off)))
        b0 = math.log(max(float(np.mean(y)), 1e-3) / mean_exp)
        x0 = np.zeros(p + 1)
        x0[0] = b0
        x0[-1] = math.log(2.0)
        res = optimize.minimize(
            _nll_grad, x0, args=(X, off, y, True, True, None, 0.0), jac=True, method="L-BFGS-B",
            options={"maxiter": 500},
        )
        self.beta = np.asarray(res.x[:p], dtype=float)
        self.theta = float(res.x[p])
        self.n_train_rows = int(len(y))
        return self

    def calibrate(self, X: np.ndarray, off: np.ndarray, y: np.ndarray, *, season: str = "") -> "SessionRunsModel":
        """Re-fit only the dispersion on a calibration season, with the mean held fixed."""
        if self.beta is None:
            raise RuntimeError("fit before calibrate")
        if len(y) == 0:
            return self
        res = optimize.minimize_scalar(
            lambda th: _nll_grad(np.empty(0), X, off, y, False, False, self.beta, th)[0],
            bounds=(math.log(0.05), math.log(1000.0)), method="bounded",
        )
        self.theta = float(res.x)
        self.calibrated_on = season
        self.n_calib_rows = int(len(y))
        return self

    # ------------------------------------------------------------ prediction
    def _check(self) -> None:
        if self.beta is None or self.theta is None:
            raise RuntimeError("model is not fitted")

    def mean_and_size(self, X: np.ndarray, off: np.ndarray) -> Tuple[np.ndarray, float]:
        self._check()
        mu = np.exp(np.clip(X @ self.beta + off, -_LOG_CLIP, _LOG_CLIP))
        return mu, math.exp(self.theta)  # type: ignore[arg-type]

    def nb_params(self, X: np.ndarray, off: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Per-row scipy ``nbinom`` parameters ``(n, p)`` with ``n = r`` and ``p = r / (r + mu)``."""
        mu, r = self.mean_and_size(X, off)
        return np.full_like(mu, r), r / (r + mu)

    def p_at_least_rows(self, X: np.ndarray, off: np.ndarray, threshold: Any) -> np.ndarray:
        """P(runs >= threshold) per row, threshold an integer array or scalar."""
        n, p = self.nb_params(X, off)
        th = np.asarray(threshold, dtype=int)
        return stats.nbinom.sf(th - 1, n, p)

    def state_design(self, state: SessionState, window_overs: int) -> Tuple[np.ndarray, np.ndarray]:
        if window_overs not in WINDOW_OVERS[state.family] and window_overs > MAX_OVERS[state.family]:
            raise ValueError(f"window {window_overs} overs is outside {state.family} range")
        window_balls = window_overs * 6
        if state.balls_done >= window_balls:
            raise ValueError("window already finished for this state")
        return design(
            state.family, state.balls_done, state.runs, state.wickets, window_balls,
            state.par_rr, state.xi_diff, float(state.female),
        )

    def p_at_least(self, state: SessionState, window_overs: int, line: float) -> float:
        """P(runs scored from this state to the window end >= line). Monotone non-increasing in ``line``."""
        X, off = self.state_design(state, window_overs)
        thr = line_threshold(line)
        if thr <= 0:
            return 1.0
        return float(self.p_at_least_rows(X, off, thr)[0])

    def quantiles(self, state: SessionState, window_overs: int, taus: Sequence[float]) -> List[int]:
        X, off = self.state_design(state, window_overs)
        n, p = self.nb_params(X, off)
        return [int(stats.nbinom.ppf(t, n, p)[0]) for t in taus]


def fair_rates(
    model: SessionRunsModel,
    state: SessionState,
    window_overs: int,
    line: float,
    convention: RateConvention = DEFAULT_CONVENTION,
) -> Dict[str, float]:
    """Fair YES and NO prices (per ``convention.scale``) and decimal odds for a session line."""
    p_yes = model.p_at_least(state, window_overs, line)
    p_no = 1.0 - p_yes
    return {
        "threshold": float(line_threshold(line)),
        "p_yes": p_yes,
        "p_no": p_no,
        "yes_price": convention.scale * p_yes,
        "no_price": convention.scale * p_no,
        "yes_decimal": (1.0 / p_yes) if p_yes > 0 else math.inf,
        "no_decimal": (1.0 / p_no) if p_no > 0 else math.inf,
    }


def edge(side: str, quoted_rate: float, p_yes: float, convention: RateConvention = DEFAULT_CONVENTION) -> float:
    """Fair win probability minus the quoted price of ``side`` (positive = value). ``side`` is YES or NO."""
    s = side.strip().upper()
    if s == "YES":
        fair = p_yes
    elif s == "NO":
        fair = 1.0 - p_yes
    else:
        raise ValueError(f"side must be YES or NO, got {side!r}")
    return fair - float(quoted_rate) / convention.scale


def build_rows(
    trace: InningsTrace,
    *,
    family: str,
    match_overs: int,
    par_rr: float,
    xi_diff: float,
    female: bool,
    stride: int = 3,
    season: int = 0,
) -> Dict[str, np.ndarray]:
    """Training rows for one innings and every window it fully covers.

    A window ``K`` is used only when the innings bowled at least ``K * 6`` legal balls (so it was not all out or
    cut short before the window end) and ``K`` is within the match's overs. Each state is the position after ``b``
    legal balls with ``b < K * 6`` (every ``stride``-th ball). The target is the runs from state ``b`` to ball
    ``K * 6``, including wides and no-balls in between.
    """
    if stride < 1:
        raise ValueError("stride must be >= 1")
    cols: Dict[str, List[np.ndarray]] = {k: [] for k in (
        "balls_done", "runs", "wickets", "window_balls", "window_overs", "target", "season")}
    for k_overs in WINDOW_OVERS[family]:
        if k_overs > match_overs or trace.legal_balls < k_overs * 6:
            continue
        wb = k_overs * 6
        b = np.arange(0, wb, stride)
        cols["balls_done"].append(b)
        cols["runs"].append(trace.runs_after[b])
        cols["wickets"].append(trace.wkts_after[b])
        cols["window_balls"].append(np.full(len(b), wb))
        cols["window_overs"].append(np.full(len(b), k_overs))
        cols["target"].append(trace.runs_after[wb] - trace.runs_after[b])
        cols["season"].append(np.full(len(b), season))
    out: Dict[str, np.ndarray] = {}
    for name, parts in cols.items():
        out[name] = np.concatenate(parts) if parts else np.empty(0, dtype=float)
    n = len(out["target"])
    out["par_rr"] = np.full(n, float(par_rr))
    out["xi_diff"] = np.full(n, float(xi_diff))
    out["female"] = np.full(n, 1.0 if female else 0.0)
    out["balls_done"] = out["balls_done"].astype(np.int64)
    out["window_balls"] = out["window_balls"].astype(np.int64)
    out["wickets"] = out["wickets"].astype(np.int64)
    return out


def concat_rows(parts: Sequence[Mapping[str, np.ndarray]]) -> Dict[str, np.ndarray]:
    """Stack row blocks from ``build_rows`` (empty input gives empty arrays with the standard keys)."""
    keys = ("balls_done", "runs", "wickets", "window_balls", "window_overs", "target", "season",
            "par_rr", "xi_diff", "female")
    if not parts:
        return {k: np.empty(0) for k in keys}
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def design_rows(rows: Mapping[str, np.ndarray], family: str) -> Tuple[np.ndarray, np.ndarray]:
    """Design matrix and offset for stored training rows (same ``design`` as live queries)."""
    return design(
        family, rows["balls_done"], rows["runs"], rows["wickets"], rows["window_balls"],
        rows["par_rr"], rows["xi_diff"], rows["female"],
    )


def crps_discrete(
    n: np.ndarray, p: np.ndarray, y: np.ndarray, kmax: int = KMAX_CRPS, chunk: int = 20000
) -> float:
    """Mean discrete CRPS: sum over integers k of (F(k) - 1{y <= k})^2, truncated at ``kmax``."""
    ks = np.arange(0, kmax + 1)
    n_all = len(y)
    if n_all == 0:
        return float("nan")
    total = 0.0
    for start in range(0, n_all, chunk):
        sl = slice(start, min(start + chunk, n_all))
        F = stats.nbinom.cdf(ks[None, :], n[sl, None], p[sl, None])
        ind = (y[sl, None] <= ks[None, :]).astype(float)
        total += float(np.sum((F - ind) ** 2))
    return total / n_all


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(tau * d, (tau - 1.0) * d)))
