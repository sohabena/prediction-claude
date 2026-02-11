"""
Backtesting report generator with bootstrap confidence intervals.

Provides statistical significance testing for backtest results.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from shared.logging import setup_logging

logger = setup_logging("backtest_report")


def bootstrap_ci(
    data: list[float],
    n_bootstrap: int = 10_000,
    ci: float = 0.95,
    statistic: str = "mean",
) -> dict[str, float]:
    """
    Compute bootstrap confidence interval for a statistic.

    Args:
        data: Sample data.
        n_bootstrap: Number of bootstrap resamples.
        ci: Confidence level (default 95%).
        statistic: "mean", "sharpe", or "median".

    Returns:
        Dict with 'estimate', 'ci_lower', 'ci_upper', 'significant'.
    """
    if len(data) < 5:
        return {
            "estimate": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "significant": False,
        }

    arr = np.array(data)
    rng = np.random.default_rng(42)

    # Compute point estimate
    if statistic == "sharpe":
        point_est = _compute_sharpe(arr)
    elif statistic == "median":
        point_est = float(np.median(arr))
    else:
        point_est = float(np.mean(arr))

    # Bootstrap resamples
    bootstrap_stats = np.zeros(n_bootstrap)
    for i in range(n_bootstrap):
        resample = rng.choice(arr, size=len(arr), replace=True)
        if statistic == "sharpe":
            bootstrap_stats[i] = _compute_sharpe(resample)
        elif statistic == "median":
            bootstrap_stats[i] = float(np.median(resample))
        else:
            bootstrap_stats[i] = float(np.mean(resample))

    # Percentile CI
    alpha = (1 - ci) / 2
    ci_lower = float(np.percentile(bootstrap_stats, alpha * 100))
    ci_upper = float(np.percentile(bootstrap_stats, (1 - alpha) * 100))

    # Significant if CI doesn't cross zero
    significant = (ci_lower > 0) or (ci_upper < 0)

    return {
        "estimate": round(point_est, 6),
        "ci_lower": round(ci_lower, 6),
        "ci_upper": round(ci_upper, 6),
        "significant": significant,
    }


def _compute_sharpe(returns: np.ndarray) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(returns) < 2:
        return 0.0
    std = float(np.std(returns))
    if std == 0:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(252))


def generate_report(
    backtest_summary: dict[str, Any],
    bet_pnls: list[float],
    daily_pnls: list[float],
    clvs: list[float] | None = None,
) -> dict[str, Any]:
    """
    Generate a full statistical report from backtest results.

    Args:
        backtest_summary: Summary dict from BacktestResult.summary().
        bet_pnls: List of per-bet P&L values.
        daily_pnls: List of daily P&L values.
        clvs: List of CLV values per bet (if available).

    Returns:
        Report dict with point estimates and bootstrap CIs.
    """
    report: dict[str, Any] = {
        "summary": backtest_summary,
        "confidence_intervals": {},
    }

    # Bootstrap CI on mean bet P&L
    report["confidence_intervals"]["mean_pnl"] = bootstrap_ci(
        bet_pnls, statistic="mean"
    )

    # Bootstrap CI on Sharpe ratio
    report["confidence_intervals"]["sharpe_ratio"] = bootstrap_ci(
        daily_pnls, statistic="sharpe"
    )

    # Bootstrap CI on win rate (convert to 0/1)
    win_flags = [1.0 if p > 0 else 0.0 for p in bet_pnls]
    report["confidence_intervals"]["win_rate"] = bootstrap_ci(
        win_flags, statistic="mean"
    )

    # Bootstrap CI on CLV
    if clvs and len(clvs) >= 5:
        report["confidence_intervals"]["avg_clv"] = bootstrap_ci(
            clvs, statistic="mean"
        )

    # Overall assessment
    sharpe_ci = report["confidence_intervals"]["sharpe_ratio"]
    pnl_ci = report["confidence_intervals"]["mean_pnl"]

    report["assessment"] = {
        "profitable": pnl_ci["significant"] and pnl_ci["ci_lower"] > 0,
        "risk_adjusted_edge": sharpe_ci["significant"] and sharpe_ci["ci_lower"] > 0,
        "ready_for_live": (
            sharpe_ci.get("ci_lower", 0) > 0
            and pnl_ci.get("ci_lower", 0) > 0
        ),
    }

    logger.info("report_generated", assessment=report["assessment"])
    return report
