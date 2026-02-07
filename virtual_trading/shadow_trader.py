"""
Shadow Trader: continues placing virtual bets post-graduation.

After the RL agent graduates, the shadow trader runs alongside advisor mode to:
1. Validate the agent's real-world performance continuously
2. Track virtual P&L as if every recommendation was followed
3. Detect performance drift (degrading win rate, ROI, or Sharpe ratio)
4. Trigger auto-demotion back to virtual trading when drift is detected
5. Calibrate signal confidence against actual outcomes
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import numpy as np

from shared.config import get_settings
from shared.constants import (
    KEY_SHADOW_PERFORMANCE,
    KEY_DRIFT_STATUS,
    DRIFT_WIN_RATE_FLOOR,
    DRIFT_ROI_FLOOR,
    DRIFT_LOOKBACK_BETS,
    DRIFT_LOOKBACK_DAYS,
    DRIFT_DEMOTION_CONSECUTIVE_DAYS,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import BetOutcome

logger = setup_logging("shadow_trader")


class ShadowBet:
    """A virtual shadow bet placed post-graduation."""

    __slots__ = (
        "match_id", "action", "team", "odds", "stake", "confidence",
        "placed_at", "settled_at", "outcome", "profit_loss",
    )

    def __init__(
        self,
        match_id: str,
        action: str,
        team: str,
        odds: float,
        stake: float,
        confidence: float,
    ) -> None:
        self.match_id = match_id
        self.action = action
        self.team = team
        self.odds = odds
        self.stake = stake
        self.confidence = confidence
        self.placed_at = datetime.now(timezone.utc)
        self.settled_at: Optional[datetime] = None
        self.outcome: str = "pending"
        self.profit_loss: float = 0.0

    def settle(self, outcome: str, pnl: float) -> None:
        """Settle the shadow bet with an outcome."""
        self.outcome = outcome
        self.profit_loss = pnl
        self.settled_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "action": self.action,
            "team": self.team,
            "odds": self.odds,
            "stake": self.stake,
            "confidence": self.confidence,
            "placed_at": self.placed_at.isoformat(),
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "outcome": self.outcome,
            "profit_loss": self.profit_loss,
        }


class DriftStatus:
    """Tracks whether the agent's performance is drifting below thresholds."""

    def __init__(self) -> None:
        self.is_drifting: bool = False
        self.drift_detected_at: Optional[datetime] = None
        self.consecutive_drift_days: int = 0
        self.should_demote: bool = False
        self.violations: list[str] = []
        self.metrics: dict[str, float] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_drifting": self.is_drifting,
            "drift_detected_at": self.drift_detected_at.isoformat() if self.drift_detected_at else None,
            "consecutive_drift_days": self.consecutive_drift_days,
            "should_demote": self.should_demote,
            "violations": self.violations,
            "metrics": self.metrics,
        }


class ShadowTrader:
    """
    Continues virtual betting after graduation to monitor ongoing performance.

    Runs alongside advisor mode. Every time the agent generates a signal,
    the shadow trader records a virtual bet at the same odds. When the match
    settles, the shadow bet is settled too. This provides:

    - Live virtual P&L tracking post-graduation
    - Drift detection (performance below thresholds)
    - Auto-demotion if drift persists
    - Confidence calibration data
    """

    def __init__(self, initial_balance: float = 100_000.0) -> None:
        self.settings = get_settings()

        # Shadow portfolio
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._peak_balance = initial_balance

        # Bet tracking
        self._open_bets: dict[str, ShadowBet] = {}
        self._settled_bets: deque[ShadowBet] = deque(maxlen=2000)
        self._total_bets = 0
        self._total_wins = 0
        self._total_pnl = 0.0

        # Daily tracking
        self._daily_pnl: dict[str, float] = {}  # date_str -> pnl
        self._daily_start_balance: float = initial_balance

        # Drift detection
        self._drift_status = DriftStatus()
        self._last_drift_check_date: Optional[str] = None

    def place_shadow_bet(self, signal: dict[str, Any]) -> Optional[ShadowBet]:
        """
        Place a virtual shadow bet based on an advisor signal.

        Args:
            signal: The advisor signal dict with match_id, recommended_action,
                    confidence, team_home, team_away, etc.

        Returns:
            The ShadowBet if placed, None if skipped.
        """
        action = signal.get("recommended_action", "HOLD")
        if action == "HOLD":
            return None

        confidence = signal.get("confidence", 0.0)
        match_id = signal.get("match_id", "")

        # Skip if already have a shadow bet on this match
        if match_id in self._open_bets:
            return None

        # Determine team and stake
        is_home = "HOME" in action
        is_large = "LG" in action
        team = signal.get("team_home", "") if is_home else signal.get("team_away", "")

        stake_pct = 0.03 if is_large else 0.01
        stake = self._balance * stake_pct

        # Use approximate odds (from signal context if available, else estimate)
        odds = signal.get("odds", 2.0)

        bet = ShadowBet(
            match_id=match_id,
            action=action,
            team=team,
            odds=odds,
            stake=stake,
            confidence=confidence,
        )

        self._open_bets[match_id] = bet
        self._total_bets += 1

        logger.info(
            "shadow_bet_placed",
            match_id=match_id,
            action=action,
            stake=round(stake, 2),
            confidence=round(confidence, 4),
        )

        return bet

    def settle_shadow_bet(self, match_id: str, won: bool) -> Optional[ShadowBet]:
        """
        Settle a shadow bet when the match completes.

        Args:
            match_id: The match identifier.
            won: Whether the bet would have won.

        Returns:
            The settled ShadowBet, or None if not found.
        """
        bet = self._open_bets.pop(match_id, None)
        if bet is None:
            return None

        if won:
            pnl = bet.stake * (bet.odds - 1)
            bet.settle("win", pnl)
            self._total_wins += 1
        else:
            pnl = -bet.stake
            bet.settle("loss", pnl)

        self._balance += pnl
        self._total_pnl += pnl
        self._peak_balance = max(self._peak_balance, self._balance)

        # Track daily P&L
        today = datetime.now(timezone.utc).date().isoformat()
        self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + pnl

        self._settled_bets.append(bet)

        logger.info(
            "shadow_bet_settled",
            match_id=match_id,
            outcome=bet.outcome,
            pnl=round(pnl, 2),
            balance=round(self._balance, 2),
        )

        return bet

    def get_performance(self) -> dict[str, Any]:
        """Get current shadow trading performance metrics."""
        settled = list(self._settled_bets)
        recent_n = min(DRIFT_LOOKBACK_BETS, len(settled))
        recent = settled[-recent_n:] if recent_n > 0 else []

        # Overall metrics
        win_rate = self._total_wins / max(self._total_bets, 1)
        roi = self._total_pnl / max(self._initial_balance, 1)
        drawdown = 1.0 - (self._balance / max(self._peak_balance, 1))

        # Recent window metrics
        recent_wins = sum(1 for b in recent if b.outcome == "win")
        recent_win_rate = recent_wins / max(len(recent), 1)
        recent_pnl = sum(b.profit_loss for b in recent)
        recent_roi = recent_pnl / max(self._initial_balance, 1)

        # Confidence calibration: group by confidence buckets
        calibration = self._compute_calibration(settled)

        # Sharpe ratio from daily returns
        sharpe = self._compute_sharpe()

        # Daily P&L history (last 30 days)
        daily_history = self._get_daily_history(30)

        return {
            "balance": round(self._balance, 2),
            "initial_balance": round(self._initial_balance, 2),
            "total_bets": self._total_bets,
            "total_wins": self._total_wins,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(self._total_pnl, 2),
            "roi": round(roi, 4),
            "drawdown": round(drawdown, 4),
            "peak_balance": round(self._peak_balance, 2),
            "open_bets": len(self._open_bets),
            "recent_window": {
                "n": recent_n,
                "win_rate": round(recent_win_rate, 4),
                "pnl": round(recent_pnl, 2),
                "roi": round(recent_roi, 4),
            },
            "sharpe_ratio": round(sharpe, 4),
            "calibration": calibration,
            "daily_history": daily_history,
            "drift": self._drift_status.to_dict(),
        }

    def check_drift(self) -> DriftStatus:
        """
        Check if the agent's performance has drifted below acceptable thresholds.

        Drift is detected when rolling win rate or ROI falls below floor values.
        If drift persists for N consecutive days, auto-demotion is triggered.
        """
        today = datetime.now(timezone.utc).date().isoformat()

        # Only check once per day
        if self._last_drift_check_date == today:
            return self._drift_status

        self._last_drift_check_date = today

        settled = list(self._settled_bets)
        if len(settled) < 20:
            # Not enough data to detect drift
            self._drift_status.is_drifting = False
            self._drift_status.violations = []
            return self._drift_status

        violations: list[str] = []

        # Check recent window win rate
        recent = settled[-DRIFT_LOOKBACK_BETS:]
        recent_wins = sum(1 for b in recent if b.outcome == "win")
        recent_win_rate = recent_wins / len(recent)

        if recent_win_rate < DRIFT_WIN_RATE_FLOOR:
            violations.append(
                f"Win rate {recent_win_rate:.1%} below floor {DRIFT_WIN_RATE_FLOOR:.0%}"
            )

        # Check recent ROI
        recent_pnl = sum(b.profit_loss for b in recent)
        recent_roi = recent_pnl / max(self._initial_balance, 1)

        if recent_roi < DRIFT_ROI_FLOOR:
            violations.append(
                f"ROI {recent_roi:.1%} below floor {DRIFT_ROI_FLOOR:.0%}"
            )

        # Check Sharpe ratio
        sharpe = self._compute_sharpe()
        if sharpe < 0.5:
            violations.append(f"Sharpe ratio {sharpe:.2f} below 0.50")

        # Check max drawdown
        drawdown = 1.0 - (self._balance / max(self._peak_balance, 1))
        if drawdown > 0.20:
            violations.append(f"Drawdown {drawdown:.1%} exceeds 20%")

        # Update drift status
        is_drifting = len(violations) > 0

        if is_drifting:
            if not self._drift_status.is_drifting:
                # Drift just started
                self._drift_status.drift_detected_at = datetime.now(timezone.utc)
                self._drift_status.consecutive_drift_days = 1
            else:
                self._drift_status.consecutive_drift_days += 1
        else:
            self._drift_status.consecutive_drift_days = 0
            self._drift_status.drift_detected_at = None

        self._drift_status.is_drifting = is_drifting
        self._drift_status.violations = violations
        self._drift_status.should_demote = (
            self._drift_status.consecutive_drift_days >= DRIFT_DEMOTION_CONSECUTIVE_DAYS
        )
        self._drift_status.metrics = {
            "recent_win_rate": round(recent_win_rate, 4),
            "recent_roi": round(recent_roi, 4),
            "sharpe_ratio": round(sharpe, 4),
            "drawdown": round(drawdown, 4),
        }

        if is_drifting:
            logger.warning(
                "drift_detected",
                violations=violations,
                consecutive_days=self._drift_status.consecutive_drift_days,
                should_demote=self._drift_status.should_demote,
            )

        return self._drift_status

    async def publish_performance(self) -> None:
        """Publish shadow trading performance to Redis for the dashboard."""
        try:
            redis = await get_redis()
            perf = self.get_performance()
            await redis.set_json(KEY_SHADOW_PERFORMANCE, perf)
            await redis.set_json(KEY_DRIFT_STATUS, self._drift_status.to_dict())
        except Exception as e:
            logger.error("publish_performance_error", error=str(e))

    async def restore_state(self) -> None:
        """Restore shadow trader state from Redis on restart."""
        try:
            redis = await get_redis()
            perf = await redis.get_json(KEY_SHADOW_PERFORMANCE)
            if perf:
                self._balance = perf.get("balance", self._initial_balance)
                self._peak_balance = perf.get("peak_balance", self._initial_balance)
                self._total_bets = perf.get("total_bets", 0)
                self._total_wins = perf.get("total_wins", 0)
                self._total_pnl = perf.get("total_pnl", 0.0)
                logger.info(
                    "shadow_state_restored",
                    balance=self._balance,
                    total_bets=self._total_bets,
                )

            drift = await redis.get_json(KEY_DRIFT_STATUS)
            if drift:
                self._drift_status.is_drifting = drift.get("is_drifting", False)
                self._drift_status.consecutive_drift_days = drift.get("consecutive_drift_days", 0)
                self._drift_status.should_demote = drift.get("should_demote", False)
                self._drift_status.violations = drift.get("violations", [])
        except Exception as e:
            logger.warning("shadow_state_restore_failed", error=str(e))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_calibration(self, settled: list[ShadowBet]) -> list[dict[str, Any]]:
        """
        Compute confidence calibration: for each confidence bucket,
        what was the actual win rate?
        """
        buckets: dict[str, list[bool]] = {
            "30-40%": [],
            "40-50%": [],
            "50-60%": [],
            "60-70%": [],
            "70-80%": [],
            "80-100%": [],
        }

        for bet in settled:
            conf = bet.confidence * 100
            won = bet.outcome == "win"
            if conf < 40:
                buckets["30-40%"].append(won)
            elif conf < 50:
                buckets["40-50%"].append(won)
            elif conf < 60:
                buckets["50-60%"].append(won)
            elif conf < 70:
                buckets["60-70%"].append(won)
            elif conf < 80:
                buckets["70-80%"].append(won)
            else:
                buckets["80-100%"].append(won)

        return [
            {
                "bucket": bucket,
                "count": len(outcomes),
                "actual_win_rate": round(sum(outcomes) / max(len(outcomes), 1), 4),
            }
            for bucket, outcomes in buckets.items()
            if outcomes  # Only include buckets with data
        ]

    def _compute_sharpe(self) -> float:
        """Compute annualized Sharpe ratio from daily P&L."""
        if len(self._daily_pnl) < 5:
            return 0.0

        daily_returns = list(self._daily_pnl.values())
        mean_ret = np.mean(daily_returns)
        std_ret = np.std(daily_returns)
        if std_ret == 0:
            return 0.0
        return float(mean_ret / std_ret * np.sqrt(252))

    def _get_daily_history(self, days: int) -> list[dict[str, Any]]:
        """Get daily P&L history for the last N days."""
        today = datetime.now(timezone.utc).date()
        history: list[dict[str, Any]] = []

        for i in range(days):
            date = (today - timedelta(days=i)).isoformat()
            pnl = self._daily_pnl.get(date, 0.0)
            history.append({"date": date, "pnl": round(pnl, 2)})

        return list(reversed(history))
