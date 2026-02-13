"""
Multi-component reward function for the cricket betting RL agent.

Trading strategy philosophy (capital-safety-first):
  1. PRIMARY GOAL: Keep capital safe by hedging both sides when opportunity
     appears. Lock in guaranteed profit from odds movement.
  2. SECONDARY: When confidence is high, lean directional for bigger payoff.
  3. CONTINUOUS feedback via mark-to-market unrealized P&L.
  4. FINAL confirmation via realized settlement P&L.
  5. PENALTIES for naked directional risk, overtrading, drawdown.

NO domain-specific shaping (no 'death over penalty', no 'phase-based' adjustments).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from shared.schemas import BettingAction


class RewardFunction:
    """
    Reward function aligned with a sports-trading mindset:

    Core idea: bet both sides to lock in profit from odds movement.
    Only go directional when highly confident.

    Components:
      1. Hedge Bonus — BIG reward when a hedge locks in guaranteed profit
      2. Transaction Cost — per-bet penalty (bookmaker spread is a real cost)
      3. Mark-to-Market — continuous unrealized P&L as odds move
      4. Settlement P&L — realized outcome (dominant signal)
      5. Capital Safety — penalize large naked exposure, reward balanced books
      6. Patience — meaningful reward for disciplined HOLD
      7. Risk Penalties — overtrading, drawdown
      8. Episodic — Sharpe + CLV bonuses
    """

    def __init__(
        self,
        pnl_scale: float = 10.0,
        hedge_bonus_scale: float = 15.0,
        bet_cost: float = 0.02,
        unrealized_scale: float = 1.0,
        patience_reward: float = 0.01,
        naked_exposure_penalty: float = 0.01,
        overtrading_threshold: int = 3,
        overtrading_penalty: float = 0.1,
        exposure_threshold: float = 0.5,
        exposure_penalty: float = 0.1,
        drawdown_threshold: float = 0.10,
        drawdown_penalty_scale: float = 50.0,
        sharpe_bonus_scale: float = 0.5,
        clv_bonus_scale: float = 2.0,
    ) -> None:
        self.pnl_scale = pnl_scale
        self.hedge_bonus_scale = hedge_bonus_scale
        self.bet_cost = bet_cost
        self.unrealized_scale = unrealized_scale
        self.patience_reward = patience_reward
        self.naked_exposure_penalty = naked_exposure_penalty
        self.overtrading_threshold = overtrading_threshold
        self.overtrading_penalty = overtrading_penalty
        self.exposure_threshold = exposure_threshold
        self.exposure_penalty = exposure_penalty
        self.drawdown_threshold = drawdown_threshold
        self.drawdown_penalty_scale = drawdown_penalty_scale
        self.sharpe_bonus_scale = sharpe_bonus_scale
        self.clv_bonus_scale = clv_bonus_scale

    def compute(self, action: int, step_info: dict[str, Any]) -> float:
        """
        Compute the total reward for a single step.

        Args:
            action: Integer action taken.
            step_info: Dict with step context including bankroll, P&L,
                hedge info, position info, and risk metrics.
        """
        reward = 0.0
        bankroll = max(step_info.get("bankroll", 1.0), 1.0)

        # ============================================================
        # 1. HEDGE BONUS — biggest reward for locking in guaranteed profit
        # ============================================================
        # When the agent places a bet that reduces exposure and locks in
        # profit regardless of match outcome, this is the ideal action.
        if step_info.get("is_hedge"):
            locked_pnl = step_info.get("hedge_locked_pnl", 0.0)
            if locked_pnl > 0:
                # Locked profit — this is the #1 thing we want to teach
                reward += (locked_pnl / bankroll) * self.hedge_bonus_scale
            else:
                # Hedge that didn't lock profit (closing at a loss) — small penalty
                # but still less bad than not hedging at all when losing
                reward += (locked_pnl / bankroll) * self.pnl_scale * 0.5

        # ============================================================
        # 2. TRANSACTION COST — flat per-bet penalty
        # ============================================================
        # Every bet costs money (bookmaker spread). The agent must overcome
        # this cost via good entries. Combined with the environment's hard
        # bet budget, this teaches selectivity.
        if step_info.get("bet_placed"):
            reward -= self.bet_cost

        # ============================================================
        # 3. MARK-TO-MARKET — continuous unrealized P&L feedback
        # ============================================================
        unrealized_delta = step_info.get("unrealized_pnl_delta", 0.0)
        if unrealized_delta != 0.0:
            reward += (unrealized_delta / bankroll) * self.unrealized_scale

        # ============================================================
        # 4. SETTLEMENT — realized P&L (dominant signal)
        # ============================================================
        if step_info.get("bet_settled"):
            pnl = step_info.get("profit_loss", 0.0)
            reward += (pnl / bankroll) * self.pnl_scale

        # ============================================================
        # 5. CAPITAL SAFETY — penalize large naked directional exposure
        # ============================================================
        # If the agent has big one-sided exposure without hedging,
        # apply a small continuous penalty to encourage hedging.
        net_home = abs(step_info.get("net_exposure_home", 0.0))
        net_away = abs(step_info.get("net_exposure_away", 0.0))
        # Naked exposure = max one-sided exposure as fraction of bankroll
        max_naked = max(net_home, net_away) / bankroll
        if max_naked > 0.03:  # > 3% of bankroll on one side unhedged
            reward -= (max_naked - 0.03) * self.naked_exposure_penalty

        # ============================================================
        # 6. PATIENCE — small reward for disciplined HOLD
        # ============================================================
        if action == BettingAction.HOLD:
            odds_change = abs(step_info.get("odds_change", 0.0))
            clv_potential = abs(step_info.get("clv_improvement", 0.0))
            if odds_change < 0.01 and clv_potential < 0.02:
                reward += self.patience_reward

        # ============================================================
        # 7. RISK PENALTIES — overtrading, exposure cap, drawdown
        # ============================================================

        # Overtrading
        bets_last_hour = step_info.get("bets_last_hour", 0)
        if bets_last_hour > self.overtrading_threshold:
            reward -= self.overtrading_penalty * (bets_last_hour - self.overtrading_threshold)

        # Total exposure cap
        exposure_pct = step_info.get("exposure_pct", 0.0)
        if exposure_pct > self.exposure_threshold:
            reward -= self.exposure_penalty * (exposure_pct - self.exposure_threshold)

        # Drawdown (quadratic)
        drawdown = step_info.get("current_drawdown", 0.0)
        if drawdown > self.drawdown_threshold:
            reward -= (drawdown - self.drawdown_threshold) ** 2 * self.drawdown_penalty_scale

        # ============================================================
        # 8. EPISODIC — Sharpe bonus + CLV at end of match
        # ============================================================
        if step_info.get("episode_done"):
            returns = step_info.get("episode_returns", [])
            if len(returns) >= 5:
                returns_arr = np.array(returns)
                sharpe = float(np.mean(returns_arr) / (np.std(returns_arr) + 1e-8))
                reward += max(0.0, sharpe) * self.sharpe_bonus_scale

        clv = step_info.get("clv_improvement", 0.0)
        if clv > 0:
            reward += clv * self.clv_bonus_scale

        return reward
