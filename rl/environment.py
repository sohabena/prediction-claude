"""
CricketBettingEnv: Custom Gymnasium environment for cricket betting RL.

Episode = one complete cricket match (or window of odds data).
Step = each odds tick (~3-5 seconds).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from features.pipeline import FeaturePipeline
from rl.reward import RewardFunction
from shared.constants import (
    ACTION_SPACE_SIZE,
    LARGE_STAKE_PERCENT,
    OBSERVATION_SIZE,
    SMALL_STAKE_PERCENT,
)
from shared.logging import setup_logging
from shared.schemas import (
    BetOutcome,
    BettingAction,
    MatchContext,
    OddsEvent,
    PortfolioState,
    VirtualBet,
)

logger = setup_logging("rl_environment")


class CricketBettingEnv(gym.Env):
    """
    Custom Gymnasium environment for cricket betting RL.

    Observation: 66-dim float vector (data-only features)
    Action: 7 discrete actions (HOLD + 6 bet types)
    Reward: Multi-component (P&L, patience, risk penalties, Sharpe bonus)
    """

    metadata = {"render_modes": ["human", "ansi"]}

    def __init__(
        self,
        data: Optional[list[dict[str, Any]]] = None,
        initial_bankroll: float = 100_000.0,
        render_mode: Optional[str] = None,
    ) -> None:
        super().__init__()

        self.render_mode = render_mode
        self.initial_bankroll = initial_bankroll

        # Spaces
        self.action_space = spaces.Discrete(ACTION_SPACE_SIZE)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(OBSERVATION_SIZE,),
            dtype=np.float32,
        )

        # Data
        self._data = data or []
        self._current_episode_data: list[dict[str, Any]] = []
        self._step_idx = 0
        self._episode_idx = 0

        # Feature pipeline
        self.feature_pipeline = FeaturePipeline()

        # Reward function
        self.reward_fn = RewardFunction()

        # State
        self._portfolio: Optional[PortfolioState] = None
        self._open_bets: list[VirtualBet] = []
        self._episode_returns: list[float] = []
        self._bets_this_hour: int = 0
        self._peak_balance: float = initial_bankroll

    def reset(
        self, seed: Optional[int] = None, options: Optional[dict[str, Any]] = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Start a new episode (new match)."""
        super().reset(seed=seed)

        # Initialize portfolio
        self._portfolio = PortfolioState(
            initial_balance=self.initial_bankroll,
            current_balance=self.initial_bankroll,
        )
        self._open_bets = []
        self._episode_returns = []
        self._bets_this_hour = 0
        self._peak_balance = self.initial_bankroll
        self._step_idx = 0

        # Select episode data
        if self._data:
            self._current_episode_data = self._data[self._episode_idx % len(self._data)]
            self._episode_idx += 1
        else:
            self._current_episode_data = []

        # Get initial observation
        obs = self._get_observation()
        info = self._get_info()

        return obs, info

    def step(
        self, action: int
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """
        Execute one timestep.

        1. Apply action (place bet or hold)
        2. Advance to next odds tick
        3. Settle any completed bets
        4. Compute reward
        5. Check if episode done
        """
        assert self._portfolio is not None

        betting_action = BettingAction(action)
        step_info: dict[str, Any] = {}

        # 1. Apply action
        if betting_action != BettingAction.HOLD:
            bet = self._place_bet(betting_action)
            if bet:
                self._open_bets.append(bet)
                self._bets_this_hour += 1

        # 2. Advance to next tick
        self._step_idx += 1
        terminated = self._step_idx >= len(self._current_episode_data)
        truncated = False

        # 3. Settle bets (simplified: settle when match ends or after N steps)
        settled_pnl = self._settle_bets()

        # Update portfolio
        self._portfolio.current_balance += settled_pnl
        self._portfolio.session_pnl += settled_pnl
        self._portfolio.daily_pnl += settled_pnl
        if settled_pnl != 0:
            self._episode_returns.append(settled_pnl / self.initial_bankroll)

        # Track peak for drawdown
        self._peak_balance = max(self._peak_balance, self._portfolio.current_balance)

        # 4. Compute reward
        current_odds_change = self._get_odds_change()
        drawdown = 1.0 - (self._portfolio.current_balance / self._peak_balance)

        step_info = {
            "action": action,
            "bankroll": self._portfolio.current_balance,
            "odds_change": current_odds_change,
            "bets_last_hour": self._bets_this_hour,
            "exposure_pct": self._portfolio.exposure_pct,
            "current_drawdown": drawdown,
            "profit_loss": settled_pnl,
            "bet_settled": settled_pnl != 0,
            "episode_done": terminated,
            "episode_returns": self._episode_returns,
        }

        reward = self.reward_fn.compute(action, step_info)

        # 5. Get observation
        obs = self._get_observation()
        info = self._get_info()
        info["step_info"] = step_info

        return obs, float(reward), terminated, truncated, info

    def _place_bet(self, action: BettingAction) -> Optional[VirtualBet]:
        """Create a virtual bet based on the action."""
        assert self._portfolio is not None

        if self._step_idx >= len(self._current_episode_data):
            return None

        tick = self._current_episode_data[self._step_idx]
        event = self._tick_to_event(tick)

        # Determine stake
        if action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_AWAY_SM,
                       BettingAction.LAY_HOME_SM, BettingAction.LAY_AWAY_SM):
            stake_pct = SMALL_STAKE_PERCENT
        else:
            stake_pct = LARGE_STAKE_PERCENT

        stake = self._portfolio.current_balance * stake_pct

        # Determine team and odds
        if action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
            team = event.team_home
            odds = event.back_home or 0.0
        elif action in (BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
            team = event.team_away
            odds = event.back_away or 0.0
        elif action == BettingAction.LAY_HOME_SM:
            team = event.team_home
            odds = event.lay_home or 0.0
        elif action == BettingAction.LAY_AWAY_SM:
            team = event.team_away
            odds = event.lay_away or 0.0
        else:
            return None

        if odds <= 1.0 or stake <= 0:
            return None

        # Update exposure
        self._portfolio.open_positions += 1
        self._portfolio.total_exposure += stake
        self._portfolio.total_bets += 1
        self._portfolio.time_since_last_bet = 0.0

        return VirtualBet(
            match_id=event.match_id,
            placed_at=datetime.now(timezone.utc),
            action=action,
            team=team,
            odds=odds,
            stake=stake,
        )

    def _settle_bets(self) -> float:
        """Settle open bets. Simplified: random outcome based on odds."""
        total_pnl = 0.0
        settled: list[int] = []

        for i, bet in enumerate(self._open_bets):
            # Simplified settlement: use implied probability from odds
            win_prob = 1.0 / bet.odds if bet.odds > 1.0 else 0.5

            if self.np_random.random() < win_prob:
                # Win
                if bet.action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                                   BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
                    pnl = bet.stake * (bet.odds - 1.0)
                else:
                    pnl = bet.stake  # Lay win = keep stake
                bet.outcome = BetOutcome.WIN
                self._portfolio.total_wins += 1  # type: ignore[union-attr]
                self._portfolio.consecutive_streak = max(1, self._portfolio.consecutive_streak + 1)  # type: ignore[union-attr]
            else:
                # Loss
                if bet.action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                                   BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
                    pnl = -bet.stake
                else:
                    pnl = -bet.stake * (bet.odds - 1.0)  # Lay loss
                bet.outcome = BetOutcome.LOSS
                self._portfolio.consecutive_streak = min(-1, self._portfolio.consecutive_streak - 1)  # type: ignore[union-attr]

            bet.profit_loss = pnl
            bet.settled_at = datetime.now(timezone.utc)
            total_pnl += pnl
            settled.append(i)

            # Update exposure
            self._portfolio.open_positions -= 1  # type: ignore[union-attr]
            self._portfolio.total_exposure -= bet.stake  # type: ignore[union-attr]

        # Remove settled bets
        for i in reversed(settled):
            self._open_bets.pop(i)

        return total_pnl

    def _get_observation(self) -> np.ndarray:
        """Get current observation vector."""
        if self._step_idx >= len(self._current_episode_data):
            return np.zeros(OBSERVATION_SIZE, dtype=np.float32)

        tick = self._current_episode_data[self._step_idx]
        event = self._tick_to_event(tick)

        context = self._tick_to_context(tick)

        obs = self.feature_pipeline.compute(
            match_id=event.match_id,
            event=event,
            context=context,
            portfolio=self._portfolio,
        )
        return obs

    def _get_info(self) -> dict[str, Any]:
        """Get auxiliary info dict."""
        return {
            "step": self._step_idx,
            "episode": self._episode_idx,
            "balance": self._portfolio.current_balance if self._portfolio else 0.0,
            "open_bets": len(self._open_bets),
        }

    def _get_odds_change(self) -> float:
        """Get the magnitude of odds change from previous tick."""
        if self._step_idx < 1 or self._step_idx >= len(self._current_episode_data):
            return 0.0

        curr = self._current_episode_data[self._step_idx]
        prev = self._current_episode_data[self._step_idx - 1]

        curr_home = curr.get("back_home", 0) or 0
        prev_home = prev.get("back_home", 0) or 0

        return abs(curr_home - prev_home)

    def _tick_to_event(self, tick: dict[str, Any]) -> OddsEvent:
        """Convert a raw tick dict to OddsEvent."""
        return OddsEvent(
            match_id=tick.get("match_id", "unknown"),
            timestamp=tick.get("timestamp", datetime.now(timezone.utc)),
            team_home=tick.get("team_home", "Home"),
            team_away=tick.get("team_away", "Away"),
            competition=tick.get("competition", ""),
            back_home=tick.get("back_home"),
            lay_home=tick.get("lay_home"),
            back_draw=tick.get("back_draw"),
            lay_draw=tick.get("lay_draw"),
            back_away=tick.get("back_away"),
            lay_away=tick.get("lay_away"),
            is_live=tick.get("is_live", True),
        )

    def _tick_to_context(self, tick: dict[str, Any]) -> Optional[MatchContext]:
        """Convert tick dict to MatchContext if data available."""
        if "overs" not in tick:
            return None
        return MatchContext(
            match_id=tick.get("match_id", "unknown"),
            timestamp=tick.get("timestamp", datetime.now(timezone.utc)),
            is_live=tick.get("is_live", True),
            score=tick.get("score", 0),
            wickets=tick.get("wickets", 0),
            overs=tick.get("overs", 0.0),
            run_rate=tick.get("run_rate", 0.0),
            required_run_rate=tick.get("req_run_rate", 0.0),
            innings=tick.get("innings", 1),
            balls_remaining=tick.get("balls_remaining", 0),
        )

    def render(self) -> Optional[str]:
        """Render the environment state."""
        if self.render_mode == "ansi" and self._portfolio:
            return (
                f"Step {self._step_idx} | "
                f"Balance: {self._portfolio.current_balance:.0f} | "
                f"P&L: {self._portfolio.session_pnl:.0f} | "
                f"Open: {len(self._open_bets)} | "
                f"Win Rate: {self._portfolio.win_rate:.1%}"
            )
        return None
