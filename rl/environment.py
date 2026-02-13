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

    Observation: 48-dim float vector (data-only features)
        Groups: odds(7) + momentum(6) + market(5) + match_stats(7)
              + portfolio(5) + position(4) + volume(4) + bookmaker(4)
              + format(4) + timing(2)
    Action: 9 discrete actions (HOLD + 4 BACK + 4 LAY)
    Reward: Multi-component (P&L, patience, risk penalties, Sharpe bonus)
    """

    metadata = {"render_modes": ["human", "ansi"]}

    def __init__(
        self,
        data: Optional[list[dict[str, Any]]] = None,
        initial_bankroll: float = 100_000.0,
        render_mode: Optional[str] = None,
        settlement_mode: str = "real",
        max_bets_per_episode: int = 10,
    ) -> None:
        """
        Args:
            data: List of episodes. Each episode is a list of tick dicts.
                  Each episode may have metadata at index 0 containing
                  '_meta' key with 'winner', 'team_home', 'team_away'.
            initial_bankroll: Starting bankroll.
            render_mode: Rendering mode.
            settlement_mode: "real" uses actual match result, "simulated"
                           uses random outcome based on implied probability
                           (legacy mode for unit tests / curriculum stage 1).
            max_bets_per_episode: Hard cap on bets per episode. Forces the
                                 agent to be selective. Expert bettors make
                                 only 3-10 bets per match.
        """
        super().__init__()

        self.render_mode = render_mode
        self.initial_bankroll = initial_bankroll
        self.settlement_mode = settlement_mode

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

        # Match result metadata for current episode
        self._match_winner: str = ""
        self._match_home_team: str = ""
        self._match_away_team: str = ""
        self._match_result_type: str = ""

        # Feature pipeline
        self.feature_pipeline = FeaturePipeline()

        # Reward function
        self.reward_fn = RewardFunction()

        # State
        self._portfolio: Optional[PortfolioState] = None
        self._open_bets: list[VirtualBet] = []
        self._episode_returns: list[float] = []
        self._bets_this_hour: int = 0
        self._bets_this_episode: int = 0
        self.max_bets_per_episode = max_bets_per_episode
        self._peak_balance: float = initial_bankroll
        self._prev_unrealized_pnl: float = 0.0  # For mark-to-market delta

        # Position tracking for hedging/trading strategy
        # Net exposure per team: positive = long (backed), negative = short (laid)
        self._net_exposure_home: float = 0.0
        self._net_exposure_away: float = 0.0
        # Best entry odds for hedge profit calculation
        self._best_back_entry_home: float = 0.0  # lowest back odds on home
        self._best_back_entry_away: float = 0.0  # lowest back odds on away
        self._best_lay_entry_home: float = 0.0   # highest lay odds on home
        self._best_lay_entry_away: float = 0.0   # highest lay odds on away

        # Action masking for curriculum (None = all actions allowed)
        self._allowed_actions: Optional[list[int]] = None

    def set_allowed_actions(self, allowed: Optional[list[int]]) -> None:
        """Set allowed actions for curriculum stage (None = all allowed)."""
        self._allowed_actions = allowed

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
        self._bets_this_episode = 0
        self._peak_balance = self.initial_bankroll
        self._prev_unrealized_pnl = 0.0
        self._net_exposure_home = 0.0
        self._net_exposure_away = 0.0
        self._best_back_entry_home = 0.0
        self._best_back_entry_away = 0.0
        self._best_lay_entry_home = 0.0
        self._best_lay_entry_away = 0.0
        self._step_idx = 0

        # Select episode data
        if self._data:
            raw_episode = self._data[self._episode_idx % len(self._data)]
            self._episode_idx += 1

            # Extract episode metadata (match result) if present
            self._match_winner = ""
            self._match_home_team = ""
            self._match_away_team = ""
            self._match_result_type = ""

            if isinstance(raw_episode, dict):
                # Episode is a dict with 'ticks' and 'meta' keys
                meta = raw_episode.get("_meta", {})
                self._match_winner = meta.get("winner", "")
                self._match_home_team = meta.get("team_home", "")
                self._match_away_team = meta.get("team_away", "")
                self._match_result_type = meta.get("result_type", "win")
                self._current_episode_data = raw_episode.get("ticks", [])
            elif isinstance(raw_episode, list) and raw_episode:
                # Legacy format: list of tick dicts, check first tick for _meta
                if "_meta" in raw_episode[0]:
                    meta = raw_episode[0]["_meta"]
                    self._match_winner = meta.get("winner", "")
                    self._match_home_team = meta.get("team_home", "")
                    self._match_away_team = meta.get("team_away", "")
                    self._match_result_type = meta.get("result_type", "win")
                else:
                    # Infer home/away from first tick
                    self._match_home_team = raw_episode[0].get("team_home", "")
                    self._match_away_team = raw_episode[0].get("team_away", "")
                self._current_episode_data = raw_episode
            else:
                self._current_episode_data = raw_episode if isinstance(raw_episode, list) else []
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

        # Apply curriculum action mask: invalid actions become HOLD
        if self._allowed_actions is not None and action not in self._allowed_actions:
            action = 0

        # Enforce bet budget: once exhausted, all bets become HOLD
        if action != 0 and self._bets_this_episode >= self.max_bets_per_episode:
            action = 0

        betting_action = BettingAction(action)
        step_info: dict[str, Any] = {}

        # 1. Apply action — track potential profit/loss and hedging
        bet_potential_profit = 0.0
        bet_potential_loss = 0.0
        bet_placed = False
        hedge_locked_pnl = 0.0  # P&L locked by hedging (both sides covered)
        is_hedge = False
        if betting_action != BettingAction.HOLD:
            bet = self._place_bet(betting_action)
            if bet:
                self._open_bets.append(bet)
                self._bets_this_hour += 1
                self._bets_this_episode += 1
                bet_placed = True
                is_back = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
                )
                bet_on_home = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
                )
                if is_back:
                    bet_potential_profit = bet.stake * (bet.odds - 1.0)
                    bet_potential_loss = bet.stake
                else:  # LAY
                    bet_potential_profit = bet.stake
                    bet_potential_loss = bet.stake * (bet.odds - 1.0)

                # Update position tracking and detect hedges
                hedge_locked_pnl, is_hedge = self._update_position(
                    bet, is_back, bet_on_home
                )

        # 2. Advance to next tick
        self._step_idx += 1
        terminated = self._step_idx >= len(self._current_episode_data)
        truncated = False

        # 3. Mark-to-market: compute unrealized P&L change for open bets
        current_unrealized = self._compute_unrealized_pnl()
        unrealized_pnl_delta = current_unrealized - self._prev_unrealized_pnl
        self._prev_unrealized_pnl = current_unrealized

        # 4. Compute CLV for open bets (before settlement)
        clv_improvement = self._compute_open_bets_clv()

        # 5. Settle bets (simplified: settle when match ends or after N steps)
        settled_pnl = self._settle_bets()

        # Update portfolio
        self._portfolio.current_balance += settled_pnl
        self._portfolio.session_pnl += settled_pnl
        self._portfolio.daily_pnl += settled_pnl
        if settled_pnl != 0:
            self._episode_returns.append(settled_pnl / self.initial_bankroll)
            # Reset unrealized tracking after settlement
            self._prev_unrealized_pnl = self._compute_unrealized_pnl()

        # Track peak for drawdown
        self._peak_balance = max(self._peak_balance, self._portfolio.current_balance)

        # 6. Compute reward
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
            "portfolio": self._portfolio if terminated else None,
            "clv_improvement": clv_improvement,
            # Potential profit/loss signals
            "bet_placed": bet_placed,
            "bet_potential_profit": bet_potential_profit,
            "bet_potential_loss": bet_potential_loss,
            # Mark-to-market unrealized P&L delta
            "unrealized_pnl_delta": unrealized_pnl_delta,
            "unrealized_pnl_total": current_unrealized,
            "open_bet_count": len(self._open_bets),
            # Hedging signals
            "is_hedge": is_hedge,
            "hedge_locked_pnl": hedge_locked_pnl,
            "net_exposure_home": self._net_exposure_home,
            "net_exposure_away": self._net_exposure_away,
        }

        reward = self.reward_fn.compute(action, step_info)

        # 6. Get observation
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
        else:  # LG actions
            stake_pct = LARGE_STAKE_PERCENT

        stake = self._portfolio.current_balance * stake_pct

        # Determine team and odds
        if action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
            team = event.team_home
            odds = event.back_home or 0.0
        elif action in (BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
            team = event.team_away
            odds = event.back_away or 0.0
        elif action in (BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG):
            team = event.team_home
            odds = event.lay_home or 0.0
        elif action in (BettingAction.LAY_AWAY_SM, BettingAction.LAY_AWAY_LG):
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
        """
        Settle open bets.

        settlement_mode="real": Uses actual match result (who won) from episode
            metadata. The bet is only settled when the episode ends (match over).
            This is the correct mode for training on historical data.
        settlement_mode="simulated": Legacy mode using random outcome based on
            implied probability. Used for unit tests and curriculum stage 1.
        """
        # In "real" mode, only settle at episode end (match completion)
        is_episode_end = self._step_idx >= len(self._current_episode_data)

        if self.settlement_mode == "real" and not is_episode_end:
            return 0.0  # Hold bets open until match completes

        # For ties/no_result/abandoned, void all bets
        if (
            self.settlement_mode == "real"
            and self._match_result_type in ("tie", "no_result", "draw", "abandoned")
        ):
            for bet in self._open_bets:
                bet.outcome = BetOutcome.VOID
                bet.profit_loss = 0.0
                bet.settled_at = datetime.now(timezone.utc)
                self._portfolio.open_positions -= 1  # type: ignore[union-attr]
                self._portfolio.total_exposure -= bet.stake  # type: ignore[union-attr]
            self._open_bets.clear()
            return 0.0

        total_pnl = 0.0
        settled: list[int] = []

        for i, bet in enumerate(self._open_bets):
            won = self._determine_bet_outcome(bet)

            is_back_bet = bet.action in (
                BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
            )

            if won:
                # Win
                if is_back_bet:
                    pnl = bet.stake * (bet.odds - 1.0)
                else:
                    pnl = bet.stake  # Lay win = keep stake
                bet.outcome = BetOutcome.WIN
                self._portfolio.total_wins += 1  # type: ignore[union-attr]
                self._portfolio.consecutive_streak = max(1, self._portfolio.consecutive_streak + 1)  # type: ignore[union-attr]
            else:
                # Loss
                if is_back_bet:
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

    def _determine_bet_outcome(self, bet: VirtualBet) -> bool:
        """
        Determine if a bet won, using real match result or simulation.

        Returns True if the bet won, False if it lost.
        """
        if self.settlement_mode == "real" and self._match_winner:
            # Real outcome: use actual match winner
            is_back = bet.action in (
                BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
            )
            bet_on_home = bet.action in (
                BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
            )

            # Did home team win?
            home_won = self._match_winner == self._match_home_team

            if is_back:
                # BACK bet wins if the backed team won
                return (bet_on_home and home_won) or (not bet_on_home and not home_won)
            else:
                # LAY bet wins if the laid team LOST
                return (bet_on_home and not home_won) or (not bet_on_home and home_won)
        else:
            # Simulated outcome: random based on implied probability (legacy)
            win_prob = 1.0 / bet.odds if bet.odds > 1.0 else 0.5
            return bool(self.np_random.random() < win_prob)

    def _get_observation(self) -> np.ndarray:
        """Get current observation vector."""
        if self._step_idx >= len(self._current_episode_data):
            return np.zeros(OBSERVATION_SIZE, dtype=np.float32)

        tick = self._current_episode_data[self._step_idx]
        event = self._tick_to_event(tick)

        context = self._tick_to_context(tick)

        position_state = self._build_position_state(tick)

        obs = self.feature_pipeline.compute(
            match_id=event.match_id,
            event=event,
            context=context,
            portfolio=self._portfolio,
            position_state=position_state,
        )
        return obs

    def _get_info(self) -> dict[str, Any]:
        """Get auxiliary info dict. Use 'episode_idx' not 'episode' to avoid
        conflicting with Monitor's episode dict {"r": reward, "l": length}."""
        return {
            "step": self._step_idx,
            "episode_idx": self._episode_idx,
            "balance": self._portfolio.current_balance if self._portfolio else 0.0,
            "open_bets": len(self._open_bets),
        }

    def _build_position_state(self, tick: dict[str, Any]) -> dict:
        """Build position state dict for the feature pipeline."""
        bankroll = self._portfolio.current_balance if self._portfolio else self.initial_bankroll
        return {
            "net_home_exposure": self._net_exposure_home,
            "net_away_exposure": self._net_exposure_away,
            "bankroll": bankroll,
            "back_home_odds": tick.get("back_home") or 0.0,
            "lay_home_odds": tick.get("lay_home") or 0.0,
            "back_away_odds": tick.get("back_away") or 0.0,
            "lay_away_odds": tick.get("lay_away") or 0.0,
            "best_entry_home": self._best_back_entry_home,
            "best_entry_away": self._best_back_entry_away,
            "best_entry_lay_home": self._best_lay_entry_home,
            "best_entry_lay_away": self._best_lay_entry_away,
        }

    def _update_position(
        self, bet: VirtualBet, is_back: bool, bet_on_home: bool
    ) -> tuple[float, bool]:
        """
        Update net position tracking when a bet is placed.
        Detects hedges: when a new bet REDUCES net exposure on a team.

        Returns:
            (locked_pnl, is_hedge) -- locked_pnl > 0 means guaranteed profit
            was locked by this hedge, regardless of match outcome.
        """
        stake = bet.stake
        odds = bet.odds
        locked_pnl = 0.0
        is_hedge = False

        if bet_on_home:
            old_exposure = self._net_exposure_home
            if is_back:
                # BACK home: increase long exposure
                self._net_exposure_home += stake
                # Track best (lowest) back entry odds
                if self._best_back_entry_home <= 1.0 or odds < self._best_back_entry_home:
                    self._best_back_entry_home = odds
            else:
                # LAY home: decrease long exposure (or go short)
                self._net_exposure_home -= stake
                if self._best_lay_entry_home <= 1.0 or odds > self._best_lay_entry_home:
                    self._best_lay_entry_home = odds

            # Did this bet reduce exposure? That's a hedge.
            if abs(self._net_exposure_home) < abs(old_exposure) and old_exposure != 0:
                is_hedge = True
                # Compute locked profit from the hedged portion
                hedged_amount = abs(old_exposure) - abs(self._net_exposure_home)
                if is_back and self._best_lay_entry_home > 1.0:
                    # Was short (laid), now backing to hedge
                    # Profit if back odds > lay entry odds
                    if odds > self._best_lay_entry_home:
                        locked_pnl = hedged_amount * (odds - self._best_lay_entry_home) / odds
                elif not is_back and self._best_back_entry_home > 1.0:
                    # Was long (backed), now laying to hedge
                    # Profit if lay odds < back entry odds
                    if odds < self._best_back_entry_home:
                        locked_pnl = hedged_amount * (self._best_back_entry_home - odds) / odds
        else:
            # Away team
            old_exposure = self._net_exposure_away
            if is_back:
                self._net_exposure_away += stake
                if self._best_back_entry_away <= 1.0 or odds < self._best_back_entry_away:
                    self._best_back_entry_away = odds
            else:
                self._net_exposure_away -= stake
                if self._best_lay_entry_away <= 1.0 or odds > self._best_lay_entry_away:
                    self._best_lay_entry_away = odds

            if abs(self._net_exposure_away) < abs(old_exposure) and old_exposure != 0:
                is_hedge = True
                hedged_amount = abs(old_exposure) - abs(self._net_exposure_away)
                if is_back and self._best_lay_entry_away > 1.0:
                    if odds > self._best_lay_entry_away:
                        locked_pnl = hedged_amount * (odds - self._best_lay_entry_away) / odds
                elif not is_back and self._best_back_entry_away > 1.0:
                    if odds < self._best_back_entry_away:
                        locked_pnl = hedged_amount * (self._best_back_entry_away - odds) / odds

        return locked_pnl, is_hedge

    def _compute_unrealized_pnl(self) -> float:
        """
        Mark-to-market: compute unrealized P&L of all open bets using current odds.

        For BACK bets: if current odds < entry odds, implied probability increased
            → the market now agrees more with our bet → positive unrealized value.
        For LAY bets: if current odds > entry odds, implied probability decreased
            → the selection is less likely to win → positive unrealized value.
        """
        if not self._open_bets or self._step_idx >= len(self._current_episode_data):
            return 0.0

        tick = self._current_episode_data[min(self._step_idx, len(self._current_episode_data) - 1)]
        total_unrealized = 0.0

        for bet in self._open_bets:
            entry = bet.odds
            if entry <= 1.0:
                continue

            is_back = bet.action in (
                BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
            )
            # Get current market odds for this bet's side
            if bet.action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
                current = tick.get("back_home") or 0.0
            elif bet.action in (BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG):
                current = tick.get("back_away") or 0.0
            elif bet.action in (BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG):
                current = tick.get("lay_home") or 0.0
            else:  # LAY_AWAY
                current = tick.get("lay_away") or 0.0

            if current <= 1.0:
                continue

            if is_back:
                # BACK bet unrealized: edge = (1/current - 1/entry) * stake * entry
                # Positive when current < entry (odds shortened in our favor)
                unrealized = ((1.0 / current) - (1.0 / entry)) * bet.stake * entry
            else:
                # LAY bet unrealized: edge = (1/entry - 1/current) * stake * entry
                # Positive when current > entry (odds drifted, selection less likely)
                unrealized = ((1.0 / entry) - (1.0 / current)) * bet.stake * entry

            total_unrealized += unrealized

        return total_unrealized

    def _compute_open_bets_clv(self) -> float:
        """
        Compute total CLV improvement for open bets (odds moved in our favor).
        BACK: positive when current < entry. LAY: positive when current > entry.
        """
        if not self._open_bets or self._step_idx >= len(self._current_episode_data):
            return 0.0

        tick = self._current_episode_data[min(self._step_idx, len(self._current_episode_data) - 1)]
        total_clv = 0.0

        for bet in self._open_bets:
            entry = bet.odds
            if entry <= 1.0:
                continue

            is_back = bet.action in (
                BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
            )
            if is_back:
                if bet.action in (BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG):
                    current = tick.get("back_home") or 0.0
                else:
                    current = tick.get("back_away") or 0.0
                if current > 1.0 and current < entry:
                    total_clv += (1.0 / entry) - (1.0 / current)
            else:
                if bet.action in (BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG):
                    current = tick.get("lay_home") or 0.0
                else:
                    current = tick.get("lay_away") or 0.0
                if current > 1.0 and current > entry:
                    total_clv += (1.0 / current) - (1.0 / entry)

        return total_clv

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
