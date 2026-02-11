"""
Backtester: replays historical matches through a trained RL agent.

This is the single most important validation tool -- it answers:
"Would this agent have made money on historical data?"
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from rl.agent import PhoenixAgent
from rl.environment import CricketBettingEnv
from shared.constants import ACTION_SPACE_SIZE, OBSERVATION_SIZE
from shared.logging import setup_logging
from shared.schemas import BettingAction

logger = setup_logging("backtester")


class BacktestResult:
    """Stores the full results of a backtest run."""

    def __init__(self) -> None:
        self.bets: list[dict[str, Any]] = []
        self.episode_results: list[dict[str, Any]] = []
        self.daily_pnl: dict[str, float] = {}

    @property
    def total_bets(self) -> int:
        return len(self.bets)

    @property
    def total_wins(self) -> int:
        return sum(1 for b in self.bets if b["outcome"] == "win")

    @property
    def win_rate(self) -> float:
        return self.total_wins / max(self.total_bets, 1)

    @property
    def total_pnl(self) -> float:
        return sum(b["pnl"] for b in self.bets)

    @property
    def roi(self) -> float:
        total_staked = sum(b["stake"] for b in self.bets)
        return self.total_pnl / max(total_staked, 1.0)

    @property
    def avg_clv(self) -> float:
        clvs = [b["clv"] for b in self.bets if b.get("clv") is not None]
        return float(np.mean(clvs)) if clvs else 0.0

    @property
    def sharpe_ratio(self) -> float:
        daily = list(self.daily_pnl.values())
        if len(daily) < 5:
            return 0.0
        arr = np.array(daily)
        std = float(np.std(arr))
        if std == 0:
            return 0.0
        return float(np.mean(arr) / std * np.sqrt(252))

    @property
    def max_drawdown(self) -> float:
        daily = list(self.daily_pnl.values())
        if not daily:
            return 0.0
        cumulative = np.cumsum(daily)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        return float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "total_bets": self.total_bets,
            "total_wins": self.total_wins,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "roi": round(self.roi, 4),
            "avg_clv": round(self.avg_clv, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "episodes": len(self.episode_results),
            "trading_days": len(self.daily_pnl),
        }


class Backtester:
    """
    Replays historical matches through a trained RL agent in deterministic mode.

    Measures cumulative P&L, win rate, Sharpe, drawdown, and CLV.
    Compares against baselines: always-HOLD, random, momentum.
    """

    def __init__(
        self,
        model_path: str = "models/best_model.zip",
        initial_bankroll: float = 100_000.0,
    ) -> None:
        self.model_path = model_path
        self.initial_bankroll = initial_bankroll

    def run(
        self,
        episodes: list[Any],
        strategy: str = "agent",
    ) -> BacktestResult:
        """
        Run a backtest across all provided episodes.

        Args:
            episodes: List of episodes (each is a list of tick dicts with _meta).
            strategy: One of "agent", "hold", "random", "momentum".

        Returns:
            BacktestResult with full metrics.
        """
        result = BacktestResult()

        if strategy == "agent":
            return self._run_agent_backtest(episodes, result)
        elif strategy == "hold":
            return self._run_hold_backtest(episodes, result)
        elif strategy == "random":
            return self._run_random_backtest(episodes, result)
        elif strategy == "momentum":
            return self._run_momentum_backtest(episodes, result)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _run_agent_backtest(
        self,
        episodes: list[Any],
        result: BacktestResult,
    ) -> BacktestResult:
        """Run backtest with the trained RL agent."""
        env = CricketBettingEnv(
            data=episodes,
            initial_bankroll=self.initial_bankroll,
            settlement_mode="real",
        )

        # Load trained agent
        if not Path(self.model_path).exists():
            logger.warning("model_not_found", path=self.model_path)
            return result

        agent = PhoenixAgent.load(self.model_path, env)

        for ep_idx in range(len(episodes)):
            obs, info = env.reset()
            episode_pnl = 0.0
            episode_bets = 0
            terminated = False

            while not terminated:
                action, _ = agent.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(action)

                step_info = info.get("step_info", {})
                pnl = step_info.get("profit_loss", 0.0)
                if step_info.get("bet_settled") and pnl != 0:
                    episode_pnl += pnl
                    episode_bets += 1

                    # Record bet
                    self._record_bet(result, action, step_info, episodes, ep_idx)

            result.episode_results.append({
                "episode": ep_idx,
                "pnl": round(episode_pnl, 2),
                "bets": episode_bets,
                "final_balance": info.get("balance", 0.0),
            })

        logger.info("agent_backtest_complete", **result.summary())
        return result

    def _run_hold_backtest(
        self,
        episodes: list[Any],
        result: BacktestResult,
    ) -> BacktestResult:
        """Baseline: always HOLD (never bet). Should produce zero P&L."""
        for ep_idx in range(len(episodes)):
            result.episode_results.append({
                "episode": ep_idx, "pnl": 0.0, "bets": 0,
                "final_balance": self.initial_bankroll,
            })
        return result

    def _run_random_backtest(
        self,
        episodes: list[Any],
        result: BacktestResult,
    ) -> BacktestResult:
        """Baseline: random actions (10% bet probability per tick)."""
        rng = np.random.default_rng(42)
        env = CricketBettingEnv(
            data=episodes,
            initial_bankroll=self.initial_bankroll,
            settlement_mode="real",
        )

        for ep_idx in range(len(episodes)):
            obs, info = env.reset()
            episode_pnl = 0.0
            terminated = False

            while not terminated:
                # 90% HOLD, 10% random bet
                if rng.random() < 0.9:
                    action = 0  # HOLD
                else:
                    action = rng.integers(1, ACTION_SPACE_SIZE)

                obs, reward, terminated, truncated, info = env.step(int(action))
                step_info = info.get("step_info", {})
                pnl = step_info.get("profit_loss", 0.0)
                if step_info.get("bet_settled") and pnl != 0:
                    episode_pnl += pnl
                    self._record_bet(result, int(action), step_info, episodes, ep_idx)

            result.episode_results.append({
                "episode": ep_idx, "pnl": round(episode_pnl, 2),
            })

        logger.info("random_backtest_complete", **result.summary())
        return result

    def _run_momentum_backtest(
        self,
        episodes: list[Any],
        result: BacktestResult,
    ) -> BacktestResult:
        """Baseline: simple momentum (back team whose odds are falling)."""
        env = CricketBettingEnv(
            data=episodes,
            initial_bankroll=self.initial_bankroll,
            settlement_mode="real",
        )

        for ep_idx in range(len(episodes)):
            obs, info = env.reset()
            episode_pnl = 0.0
            prev_home_odds = None
            bet_placed = False
            terminated = False

            while not terminated:
                action = 0  # Default HOLD

                # Simple momentum: if home odds dropped by >5% from previous,
                # back home (small). If away odds dropped, back away.
                step = env._step_idx
                if step < len(env._current_episode_data) and not bet_placed:
                    tick = env._current_episode_data[step]
                    home_odds = tick.get("back_home", 0) or 0

                    if prev_home_odds and prev_home_odds > 0:
                        change = (home_odds - prev_home_odds) / prev_home_odds
                        if change < -0.05:
                            action = 1  # BACK_HOME_SM
                            bet_placed = True
                        elif change > 0.05:
                            action = 3  # BACK_AWAY_SM
                            bet_placed = True

                    prev_home_odds = home_odds

                obs, reward, terminated, truncated, info = env.step(action)
                step_info = info.get("step_info", {})
                pnl = step_info.get("profit_loss", 0.0)
                if step_info.get("bet_settled") and pnl != 0:
                    episode_pnl += pnl
                    self._record_bet(result, action, step_info, episodes, ep_idx)

            result.episode_results.append({
                "episode": ep_idx, "pnl": round(episode_pnl, 2),
            })

        logger.info("momentum_backtest_complete", **result.summary())
        return result

    def _record_bet(
        self,
        result: BacktestResult,
        action: int,
        step_info: dict[str, Any],
        episodes: list[Any],
        ep_idx: int,
    ) -> None:
        """Record a settled bet into the backtest result."""
        pnl = step_info.get("profit_loss", 0.0)
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "void"

        result.bets.append({
            "episode": ep_idx,
            "action": BettingAction(action).name if action < ACTION_SPACE_SIZE else "UNKNOWN",
            "pnl": round(pnl, 2),
            "outcome": outcome,
            "bankroll": step_info.get("bankroll", 0.0),
            "stake": abs(pnl) if pnl < 0 else pnl,  # Approximate
            "clv": None,  # CLV computed separately
        })

        # Track daily P&L
        today = datetime.now(timezone.utc).date().isoformat()
        result.daily_pnl[today] = result.daily_pnl.get(today, 0.0) + pnl

    def compare_strategies(
        self,
        episodes: list[Any],
    ) -> dict[str, dict[str, Any]]:
        """
        Run all strategies and return comparative results.

        Returns a dict mapping strategy name to its BacktestResult summary.
        """
        results: dict[str, dict[str, Any]] = {}

        for strategy in ["agent", "hold", "random", "momentum"]:
            logger.info("running_backtest", strategy=strategy)
            bt_result = self.run(episodes, strategy=strategy)
            results[strategy] = bt_result.summary()

        return results
