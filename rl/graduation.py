"""
Graduation system: evaluates if the RL agent is ready for live trading.
All criteria must be met for 14 consecutive days.

S42: the consecutive-day streak is tracked per version (``version`` argument). A version change
starts a fresh streak, and several days' evaluations on one calendar day count once. Calls without a
version use the unversioned streak ``""``, which is the legacy PPO behaviour.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from shared.constants import GRADUATION_CRITERIA, GRADUATION_REQUIRED_DAYS
from shared.logging import setup_logging
from shared.schemas import GraduationCriterion, GraduationStatus

logger = setup_logging("rl_graduation")

DEFAULT_STATE_PATH = "models/graduation_state.json"
_UNVERSIONED = ""


def _empty_streak() -> dict[str, Any]:
    return {"consecutive_days": 0, "last_pass_date": None, "daily_results": []}


class GraduationEvaluator:
    """
    Evaluates if RL agent is ready for live trading.

    All criteria must be met for 14 consecutive days. ``criteria`` defaults to
    ``GRADUATION_CRITERIA`` (legacy PPO path); the S42 trust gate passes ``TRUST_CRITERIA``.
    """

    def __init__(
        self,
        state_path: str | Path | None = DEFAULT_STATE_PATH,
        now_fn: Callable[[], datetime] | None = None,
        criteria: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.criteria = dict(criteria) if criteria is not None else GRADUATION_CRITERIA
        self.required_days = GRADUATION_REQUIRED_DAYS
        self.state_path = Path(state_path) if state_path else None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._streaks: dict[str, dict[str, Any]] = {}
        self._load_state()

    def _streak(self, version: Optional[str]) -> dict[str, Any]:
        key = version or _UNVERSIONED
        if key not in self._streaks:
            self._streaks[key] = _empty_streak()
        return self._streaks[key]

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if "versions" in state:
                for key, streak in state["versions"].items():
                    self._streaks[key] = {
                        "consecutive_days": int(streak.get("consecutive_days", 0)),
                        "last_pass_date": streak.get("last_pass_date"),
                        "daily_results": list(streak.get("daily_results", [])),
                    }
            else:  # legacy single-streak file: the unversioned streak
                self._streaks[_UNVERSIONED] = {
                    "consecutive_days": int(state.get("consecutive_days", 0)),
                    "last_pass_date": state.get("last_pass_date"),
                    "daily_results": list(state.get("daily_results", [])),
                }
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
                    "versions": {
                        key: {
                            "consecutive_days": s["consecutive_days"],
                            "last_pass_date": s["last_pass_date"],
                            "daily_results": s["daily_results"][-60:],
                        }
                        for key, s in self._streaks.items()
                    },
                }, f, indent=2)
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.warning("graduation_state_save_failed", error=str(e))

    def evaluate(
        self,
        win_rate: Optional[float] = None,
        roi: float = 0.0,
        sharpe_ratio: float = 0.0,
        max_drawdown: float = 0.0,
        profitable_days: int = 0,
        total_bets: int = 0,
        avg_clv: float = 0.0,
        clv_ci_lower: Optional[float] = None,
        n_matches: Optional[int] = None,
        version: Optional[str] = None,
    ) -> GraduationStatus:
        """
        Evaluate all graduation criteria (data-driven from ``GRADUATION_CRITERIA``).

        Args:
            win_rate: Accepted for backward compatibility and ignored.
            roi: Net P&L / turnover (stake) over the window.
            sharpe_ratio: Non-annualised per-calendar-day Sharpe over the window.
            max_drawdown: Max drawdown as a fraction of peak equity.
            profitable_days: Profitable calendar days in the last 14.
            total_bets: Settled bets in the window.
            avg_clv: Mean CLV (probability points) of the live model's bets.
            clv_ci_lower: Bootstrap 95% CI lower bound of CLV. None = not measured (fails).
            n_matches: Distinct matches bet. None = not measured (fails).
            version: Streak key (a strategy version). None = the unversioned streak.

        Returns:
            GraduationStatus with all criteria evaluations.
        """
        metric_values: dict[str, Optional[float]] = {
            "roi": roi,
            "roi_on_turnover": roi,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "profitable_days": float(profitable_days),
            "bet_volume": float(total_bets),
            "avg_clv": avg_clv,
            "clv_ci_lower": clv_ci_lower,
            "min_matches": None if n_matches is None else float(n_matches),
        }
        return self.evaluate_metrics(metric_values, version=version)

    def evaluate_metrics(
        self,
        metric_values: Mapping[str, Optional[float]],
        version: Optional[str] = None,
    ) -> GraduationStatus:
        """Evaluate ``self.criteria`` against named metric values (None = not measured, fails).

        A criterion whose name is missing from ``metric_values`` is also treated as not measured.
        """
        results: list[GraduationCriterion] = []
        all_met = True

        for name, config in self.criteria.items():
            threshold = config["threshold"]
            op = config.get("op", "le" if name == "max_drawdown" else "ge")
            raw = metric_values.get(name)
            value = 0.0 if raw is None else float(raw)

            if raw is None:
                met = False  # an unmeasured metric never passes
            elif op == "le":
                met = value <= threshold
            elif op == "gt":
                met = value > threshold
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

        # Track consecutive calendar days (UTC) on which all criteria passed, per version.
        streak = self._streak(version)
        today_dt = self._now_fn().astimezone(timezone.utc).date()
        today = today_dt.isoformat()
        streak["daily_results"].append({
            "date": today,
            "all_met": all_met,
            "criteria": {r.name: r.met for r in results},
        })

        if not all_met:
            streak["consecutive_days"] = 0
            streak["last_pass_date"] = None
        elif streak["last_pass_date"] != today:
            last_raw = streak["last_pass_date"]
            last = datetime.fromisoformat(last_raw).date() if last_raw else None
            if last is not None and (today_dt - last).days == 1:
                streak["consecutive_days"] += 1
            else:
                streak["consecutive_days"] = 1  # first pass, or a gap in days
            streak["last_pass_date"] = today
        self._save_state()

        consecutive = streak["consecutive_days"]
        ready = consecutive >= self.required_days

        status = GraduationStatus(
            ready=ready,
            consecutive_days=consecutive,
            required_days=self.required_days,
            criteria=results,
        )

        logger.info(
            "graduation_evaluated",
            version=version or _UNVERSIONED,
            ready=ready,
            consecutive_days=consecutive,
            all_met=all_met,
            criteria={r.name: f"{r.value:.3f}/{r.threshold:.3f}" for r in results},
        )

        return status

    def get_progress_pct(self, version: Optional[str] = None) -> float:
        """Get overall graduation progress as percentage for one version (default: unversioned)."""
        if self.required_days == 0:
            return 100.0
        days = self._streak(version)["consecutive_days"]
        return min(100.0, (days / self.required_days) * 100.0)
