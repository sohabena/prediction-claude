"""
Graduation system: evaluates if the RL agent is ready for live trading.
All criteria must be met for 14 consecutive days.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from shared.constants import GRADUATION_CRITERIA, GRADUATION_REQUIRED_DAYS
from shared.logging import setup_logging
from shared.schemas import GraduationCriterion, GraduationStatus

logger = setup_logging("rl_graduation")


class GraduationEvaluator:
    """
    Evaluates if RL agent is ready for live trading.

    All criteria must be met for 14 consecutive days.
    """

    def __init__(self) -> None:
        self.criteria = GRADUATION_CRITERIA
        self.required_days = GRADUATION_REQUIRED_DAYS
        self._daily_results: list[dict[str, Any]] = []
        self._consecutive_pass_days = 0

    def evaluate(
        self,
        win_rate: float,
        roi: float,
        sharpe_ratio: float,
        max_drawdown: float,
        profitable_days: int,
        total_bets: int,
    ) -> GraduationStatus:
        """
        Evaluate all graduation criteria.

        Args:
            win_rate: Rolling win rate over last 200 bets.
            roi: Rolling ROI over last 200 bets.
            sharpe_ratio: Rolling Sharpe ratio over last 30 days.
            max_drawdown: Max drawdown over last 30 days.
            profitable_days: Number of profitable days in last 14.
            total_bets: Total bets in last 30 days.

        Returns:
            GraduationStatus with all criteria evaluations.
        """
        results: list[GraduationCriterion] = []
        all_met = True

        metric_values = {
            "win_rate": win_rate,
            "roi": roi,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": 1.0 - max_drawdown,  # Invert: lower drawdown is better
            "profitable_days": float(profitable_days),
            "bet_volume": float(total_bets),
        }

        for name, config in self.criteria.items():
            threshold = config["threshold"]
            value = metric_values.get(name, 0.0)

            # For max_drawdown, check if BELOW threshold
            if name == "max_drawdown":
                met = max_drawdown <= threshold
                value = max_drawdown
            else:
                met = value >= threshold

            results.append(
                GraduationCriterion(
                    name=name,
                    value=value,
                    threshold=threshold,
                    met=met,
                )
            )

            if not met:
                all_met = False

        # Track consecutive days
        self._daily_results.append({
            "date": datetime.now(timezone.utc).date().isoformat(),
            "all_met": all_met,
            "criteria": {r.name: r.met for r in results},
        })

        if all_met:
            self._consecutive_pass_days += 1
        else:
            self._consecutive_pass_days = 0

        ready = self._consecutive_pass_days >= self.required_days

        status = GraduationStatus(
            ready=ready,
            consecutive_days=self._consecutive_pass_days,
            required_days=self.required_days,
            criteria=results,
        )

        logger.info(
            "graduation_evaluated",
            ready=ready,
            consecutive_days=self._consecutive_pass_days,
            all_met=all_met,
            criteria={r.name: f"{r.value:.3f}/{r.threshold:.3f}" for r in results},
        )

        return status

    def get_progress_pct(self) -> float:
        """Get overall graduation progress as percentage."""
        if self.required_days == 0:
            return 100.0
        return min(100.0, (self._consecutive_pass_days / self.required_days) * 100.0)
