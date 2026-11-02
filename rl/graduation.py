"""
Graduation system: evaluates if the RL agent is ready for live trading.
All criteria must be met for 14 consecutive days.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from shared.constants import GRADUATION_CRITERIA, GRADUATION_REQUIRED_DAYS
from shared.logging import setup_logging
from shared.schemas import GraduationCriterion, GraduationStatus

logger = setup_logging("rl_graduation")

DEFAULT_STATE_PATH = "models/graduation_state.json"


class GraduationEvaluator:
    """
    Evaluates if RL agent is ready for live trading.

    All criteria must be met for 14 consecutive days.
    """

    def __init__(
        self,
        state_path: str | Path | None = DEFAULT_STATE_PATH,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.criteria = GRADUATION_CRITERIA
        self.required_days = GRADUATION_REQUIRED_DAYS
        self.state_path = Path(state_path) if state_path else None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._daily_results: list[dict[str, Any]] = []
        self._consecutive_pass_days = 0
        self._last_pass_date: Optional[str] = None
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._consecutive_pass_days = int(state.get("consecutive_days", 0))
            self._last_pass_date = state.get("last_pass_date")
            self._daily_results = list(state.get("daily_results", []))
        except (OSError, ValueError) as e:
            logger.warning("graduation_state_load_failed", error=str(e))

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "consecutive_days": self._consecutive_pass_days,
                    "last_pass_date": self._last_pass_date,
                    "daily_results": self._daily_results[-60:],
                }, f, indent=2)
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.warning("graduation_state_save_failed", error=str(e))

    def evaluate(
        self,
        win_rate: float,
        roi: float,
        sharpe_ratio: float,
        max_drawdown: float,
        profitable_days: int,
        total_bets: int,
        avg_clv: float = 0.0,
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
            avg_clv: Average Closing Line Value over last 200 bets.

        Returns:
            GraduationStatus with all criteria evaluations.
        """
        results: list[GraduationCriterion] = []
        all_met = True

        metric_values = {
            "win_rate": win_rate,
            "roi": roi,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "profitable_days": float(profitable_days),
            "bet_volume": float(total_bets),
            "avg_clv": avg_clv,
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

        # Track consecutive calendar days (UTC) on which all criteria passed.
        today_dt = self._now_fn().astimezone(timezone.utc).date()
        today = today_dt.isoformat()
        self._daily_results.append({
            "date": today,
            "all_met": all_met,
            "criteria": {r.name: r.met for r in results},
        })

        if not all_met:
            self._consecutive_pass_days = 0
            self._last_pass_date = None
        elif self._last_pass_date != today:
            last = datetime.fromisoformat(self._last_pass_date).date() if self._last_pass_date else None
            if last is not None and (today_dt - last).days == 1:
                self._consecutive_pass_days += 1
            else:
                self._consecutive_pass_days = 1  # first pass, or a gap in days
            self._last_pass_date = today
        self._save_state()

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
