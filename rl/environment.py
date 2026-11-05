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
    OBSERVATION_SIZE,
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


# Betfair price ladder: (upper bound exclusive-ish, step)
_TICK_LADDER: tuple[tuple[float, float], ...] = (
    (2.0, 0.01),
    (3.0, 0.02),
    (4.0, 0.05),
    (6.0, 0.1),
    (10.0, 0.2),
    (20.0, 0.5),
    (30.0, 1.0),
    (50.0, 2.0),
    (100.0, 5.0),
    (1000.0, 10.0),
)

_BACK_ACTIONS = (
    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
    BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
)
_HOME_ACTIONS = (
    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
)
_SMALL_ACTIONS = (
    BettingAction.BACK_HOME_SM, BettingAction.BACK_AWAY_SM,
    BettingAction.LAY_HOME_SM, BettingAction.LAY_AWAY_SM,
)

_VOID_RESULTS = ("tie", "no_result", "draw", "abandoned")
_EPS = 1e-9


def betfair_tick_size(price: float) -> float:
    """Return the Betfair ladder increment applying to prices at/above ``price``."""
    for upper, step in _TICK_LADDER:
        if price < upper - _EPS:
            return step
    return _TICK_LADDER[-1][1]


def shift_price(price: float, ticks: int) -> float:
    """Move ``price`` by ``ticks`` ladder increments (negative = down), clamped to 1.01..1000."""
    out = price
    for _ in range(abs(int(ticks))):
        if ticks > 0:
            out = out + betfair_tick_size(out)
        else:
            out = out - betfair_tick_size(out - 1e-6)
        out = round(min(max(out, 1.01), 1000.0), 2)
    return out


def _action_parts(action: BettingAction) -> tuple[bool, bool, bool]:
    """Return (is_back, on_home, is_small) for a bet action."""
    return action in _BACK_ACTIONS, action in _HOME_ACTIONS, action in _SMALL_ACTIONS


class CricketBettingEnv(gym.Env):
    """
    Custom Gymnasium environment for cricket betting RL.

    Observation: 50-dim float vector (data-only features)
        Groups: odds(7) + momentum(6) + market(5) + match_stats(7)
              + portfolio(5) + position(4) + volume(4) + bookmaker(4)
              + format(4) + timing(2) + match_momentum(2)
    Action: 9 discrete actions (HOLD + 4 BACK + 4 LAY)
    Reward: see ``rl/reward.py``

    Execution model (exchange style): bets are filled ``fill_delay_ticks`` after the
    decision on the correct side of the book, worsened by ``slippage_ticks`` ladder
    increments; LAY bets are sized and capped by *liability*; a per-outcome book
    (P&L if home wins / if away wins) drives exposure, equity and drawdown.
    """

    metadata = {"render_modes": ["human", "ansi"]}

    def __init__(
        self,
        data: Optional[list[dict[str, Any]]] = None,
        initial_bankroll: float = 100_000.0,
        render_mode: Optional[str] = None,
        settlement_mode: str = "real",
        max_bets_per_episode: int = 30,
        exchange_commission: float = 0.0,
        small_stake_pct: float = 0.01,
        large_stake_pct: float = 0.03,
        max_liability_pct_per_bet: float = 0.05,
        max_total_liability_pct: float = 0.20,
        slippage_ticks: int = 1,
        fill_delay_ticks: int = 1,
        episode_sampling: str = "random",
        bet_budget_tolerance_pct: float = 0.20,
    ) -> None:
        """
        Args:
            data: List of episodes (list of tick dicts, or dict with 'ticks' and '_meta').
            initial_bankroll: Starting bankroll.
            render_mode: Rendering mode.
            settlement_mode: "real" uses the actual result; "simulated" draws a random
                outcome from implied probability (legacy / curriculum stage 1).
            max_bets_per_episode: Base bet cap. Up to ``bet_budget_tolerance_pct`` extra
                bets are allowed only if they strictly reduce total liability.
            exchange_commission: Commission on net positive market P&L (e.g. 0.02).
            small_stake_pct / large_stake_pct: Per-bet budget as a fraction of bankroll
                (stake for BACK, liability for LAY).
            max_liability_pct_per_bet: Hard cap on one bet's liability.
            max_total_liability_pct: Worst-case book loss limit as a fraction of bankroll.
            slippage_ticks: Ladder increments by which fills are worsened.
            fill_delay_ticks: Ticks between decision and fill.
            episode_sampling: "random" (training) or "sequential" (evaluation).
        """
        super().__init__()
        if episode_sampling not in ("random", "sequential"):
            raise ValueError("episode_sampling must be 'random' or 'sequential'")

        self.render_mode = render_mode
        self.initial_bankroll = initial_bankroll
        self.settlement_mode = settlement_mode
        self.exchange_commission = exchange_commission
        self.small_stake_pct = small_stake_pct
        self.large_stake_pct = large_stake_pct
        self.max_liability_pct_per_bet = max_liability_pct_per_bet
        self.max_total_liability_pct = max_total_liability_pct
        self.slippage_ticks = int(slippage_ticks)
        self.fill_delay_ticks = max(0, int(fill_delay_ticks))
        self.episode_sampling = episode_sampling

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
        self._data_index = -1

        # Match result metadata for current episode
        self._match_winner: str = ""
        self._match_home_team: str = ""
        self._match_away_team: str = ""
        self._match_result_type: str = ""

        # Feature pipeline
        self.feature_pipeline = FeaturePipeline()

        # Reward function
        self.reward_fn = RewardFunction(initial_bankroll=self.initial_bankroll)

        # State
        self._portfolio: Optional[PortfolioState] = None
        self._open_bets: list[VirtualBet] = []
        self._settled_bets_history: list[VirtualBet] = []
        self._episode_returns: list[float] = []
        self._bets_this_episode: int = 0
        self.max_bets_per_episode = max_bets_per_episode
        self.max_bets_tolerance_pct = bet_budget_tolerance_pct
        self.max_bets_with_tolerance = int(
            self.max_bets_per_episode * (1.0 + self.max_bets_tolerance_pct)
        )

        # Book: realised P&L (gross, before commission) per match outcome
        self._pnl_home: float = 0.0
        self._pnl_away: float = 0.0
        self._pending: list[tuple[int, BettingAction]] = []
        self._peak_equity: float = initial_bankroll
        self._prev_equity: float = initial_bankroll
        self._prev_clv: float = 0.0

        # Action masking for curriculum (None = all actions allowed)
        self._allowed_actions: Optional[list[int]] = None

    # ------------------------------------------------------------------ props
    @property
    def _net_exposure_home(self) -> float:
        """Book P&L if home wins (kept for external readers)."""
        return self._pnl_home

    @property
    def _net_exposure_away(self) -> float:
        """Book P&L if away wins (kept for external readers)."""
        return self._pnl_away

    def set_allowed_actions(self, allowed: Optional[list[int]]) -> None:
        """Set allowed actions for curriculum stage (None = all allowed)."""
        self._allowed_actions = allowed

    # ------------------------------------------------------------------ reset
    def reset(
        self, seed: Optional[int] = None, options: Optional[dict[str, Any]] = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Start a new episode (new match)."""
        super().reset(seed=seed)

        self._portfolio = PortfolioState(
            initial_balance=self.initial_bankroll,
            current_balance=self.initial_bankroll,
        )
        self.reward_fn.reset()
        self._open_bets = []
        self._pending = []
        self._episode_returns = []
        self._bets_this_episode = 0
        self._pnl_home = 0.0
        self._pnl_away = 0.0
        self._peak_equity = self.initial_bankroll
        self._prev_equity = self.initial_bankroll
        self._prev_clv = 0.0
        self._step_idx = 0

        if self._data:
            if options and options.get("episode_index") is not None:
                idx = int(options["episode_index"]) % len(self._data)
            elif self.episode_sampling == "random":
                idx = int(self.np_random.integers(len(self._data)))
            else:
                idx = self._episode_idx % len(self._data)
            self._data_index = idx
            raw_episode = self._data[idx]
            self._episode_idx += 1

            self._match_winner = ""
            self._match_home_team = ""
            self._match_away_team = ""
            self._match_result_type = ""

            if isinstance(raw_episode, dict):
                meta = raw_episode.get("_meta", {})
                self._match_winner = meta.get("winner", "")
                self._match_home_team = meta.get("team_home", "")
                self._match_away_team = meta.get("team_away", "")
                self._match_result_type = meta.get("result_type", "win")
                self._current_episode_data = raw_episode.get("ticks", [])
            elif isinstance(raw_episode, list) and raw_episode:
                first_tick = raw_episode[0]
                meta = first_tick.get("_meta", {})
                self._match_winner = meta.get("winner") or first_tick.get("winner", "")
                self._match_home_team = meta.get("team_home") or first_tick.get("team_home", "")
                self._match_away_team = meta.get("team_away") or first_tick.get("team_away", "")
                self._match_result_type = meta.get("result_type", "win")
                self._current_episode_data = raw_episode
            else:
                self._current_episode_data = raw_episode if isinstance(raw_episode, list) else []
        else:
            self._current_episode_data = []

        obs = self._get_observation()
        info = self._get_info()
        return obs, info

    # ------------------------------------------------------------------- step
    def step(
        self, action: int
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """
        Execute one timestep.

        1. Validate the action (curriculum mask, budget, liability) -> queue an order
        2. Fill due orders (decision + fill_delay_ticks), advance to the next tick
        3. Settle at episode end from the book
        4. Mark the book to market, compute reward
        """
        assert self._portfolio is not None

        action = int(action)
        if self._allowed_actions is not None and action not in self._allowed_actions:
            action = 0

        # 1. Decision: queue an order for a future tick if it is currently valid
        if action != 0:
            fill_idx = self._step_idx + self.fill_delay_ticks
            if self._plan_fill(BettingAction(action), fill_idx, len(self._pending)) is None:
                action = 0
            else:
                self._pending.append((fill_idx, BettingAction(action)))

        prev_worst = self._worst_case()
        had_bets = bool(self._open_bets)

        # 2. Fill zero-delay orders, advance, fill delayed orders
        fills = self._process_fills(self._step_idx)
        self._step_idx += 1
        terminated = self._step_idx >= len(self._current_episode_data)
        truncated = False
        if not terminated:
            fills += self._process_fills(self._step_idx)
        else:
            self._pending = []

        # 3. Settle from the book at episode end
        settled_pnl, commission, settled_any = self._settle_bets()
        if settled_any:
            self._portfolio.current_balance += settled_pnl
            self._portfolio.session_pnl += settled_pnl
            self._portfolio.daily_pnl += settled_pnl
            self._episode_returns.append(settled_pnl / self.initial_bankroll)

        # 4. Equity (bankroll + spread-aware green-up value) and drawdown
        equity = self._compute_equity()
        equity_delta = equity - self._prev_equity
        self._prev_equity = equity
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = 1.0 - equity / self._peak_equity if self._peak_equity > 0 else 0.0

        clv_now = self._compute_open_bets_clv()
        clv_delta = (clv_now - self._prev_clv) / max(self._portfolio.current_balance, 1.0)
        self._prev_clv = clv_now

        worst = self._worst_case()
        best = self._best_case()
        bet_stake = sum(b.stake for b in fills)
        bet_liability = sum(self._bet_liability(b) for b in fills)
        is_hedge = bool(fills) and had_bets and worst > prev_worst + _EPS

        step_info = {
            "action": action,
            "bet_placed": bool(fills),
            "bet_stake": bet_stake,
            "bet_liability": bet_liability,
            "bankroll": self._portfolio.current_balance,
            "equity": equity,
            "equity_delta": equity_delta,
            "peak_equity": self._peak_equity,
            "current_drawdown": drawdown,
            "total_liability": self._total_liability(),
            "worst_case_pnl": worst,
            "best_case_pnl": best,
            "clv_delta": clv_delta,
            "profit_loss": settled_pnl,
            "bet_settled": settled_any,
            "commission_paid": commission,
            "bets_this_episode": self._bets_this_episode,
            "episode_done": terminated,
            "win_rate": self._portfolio.win_rate if self._portfolio.total_bets > 0 else 0.0,
            "roi": self._portfolio.roi,
            # Extras
            "gross_liability": sum(self._bet_liability(b) for b in self._open_bets),
            "open_bet_count": len(self._open_bets),
            "portfolio": self._portfolio if terminated else None,
            "episode_returns": self._episode_returns,
            "odds_change": self._get_odds_change(),
            "exposure_pct": self._portfolio.exposure_pct,
            # Legacy keys still read by scripts/benchmark_offline_improvements.py and iterative_tuning (is_hedge)
            "is_hedge": is_hedge,
            "net_exposure_home": self._pnl_home,
            "net_exposure_away": self._pnl_away,
        }

        reward = self.reward_fn.compute(action, step_info)

        obs = self._get_observation()
        info = self._get_info()
        info["step_info"] = step_info

        return obs, float(reward), terminated, truncated, info

    # ------------------------------------------------------------ book / fills
    @staticmethod
    def _bet_liability(bet: VirtualBet) -> float:
        """Max loss of a bet: stake for BACK, stake*(odds-1) for LAY."""
        if bet.action in _BACK_ACTIONS:
            return bet.stake
        return bet.stake * (bet.odds - 1.0)

    @staticmethod
    def _book_delta(
        is_back: bool, on_home: bool, stake: float, odds: float
    ) -> tuple[float, float]:
        """P&L change (if home wins, if away wins) of one bet."""
        win = stake * (odds - 1.0) if is_back else -stake * (odds - 1.0)
        lose = -stake if is_back else stake
        return (win, lose) if on_home else (lose, win)

    def _worst_case(self) -> float:
        return min(self._pnl_home, self._pnl_away)

    def _best_case(self) -> float:
        return max(self._pnl_home, self._pnl_away)

    def _total_liability(self) -> float:
        """Net liability of the book: the worst-case loss (>= 0)."""
        return max(0.0, -self._worst_case())

    def _plan_fill(
        self, action: BettingAction, idx: int, extra_count: int = 0
    ) -> Optional[dict[str, Any]]:
        """Validate and size a bet filled at tick ``idx``; None if it cannot be placed."""
        assert self._portfolio is not None
        if action == BettingAction.HOLD or idx >= len(self._current_episode_data):
            return None
        tick = self._current_episode_data[idx]
        is_back, on_home, is_small = _action_parts(action)
        side = "home" if on_home else "away"
        raw = tick.get(f"{'back' if is_back else 'lay'}_{side}") or 0.0
        if raw <= 1.0:
            return None
        odds = shift_price(raw, -self.slippage_ticks if is_back else self.slippage_ticks)
        if odds <= 1.0:
            return None

        bankroll = self._portfolio.current_balance
        pct = self.small_stake_pct if is_small else self.large_stake_pct
        budget = min(pct, self.max_liability_pct_per_bet) * bankroll
        if budget <= 0:
            return None
        stake = budget if is_back else budget / (odds - 1.0)
        liability = budget

        d_home, d_away = self._book_delta(is_back, on_home, stake, odds)
        new_home, new_away = self._pnl_home + d_home, self._pnl_away + d_away
        new_worst = min(new_home, new_away)
        if (
            new_worst < -self.max_total_liability_pct * bankroll
            and new_worst < self._worst_case() - _EPS
        ):
            return None

        count = self._bets_this_episode + extra_count
        if count >= self.max_bets_with_tolerance:
            return None
        if count >= self.max_bets_per_episode:
            if max(0.0, -new_worst) >= self._total_liability() - _EPS:
                return None

        return {
            "tick": tick, "stake": stake, "odds": odds, "liability": liability,
            "is_back": is_back, "on_home": on_home,
        }

    def _process_fills(self, upto_idx: int) -> list[VirtualBet]:
        """Fill queued orders whose fill tick has been reached."""
        filled: list[VirtualBet] = []
        remaining: list[tuple[int, BettingAction]] = []
        for fill_idx, act in self._pending:
            if fill_idx > upto_idx:
                remaining.append((fill_idx, act))
                continue
            plan = self._plan_fill(act, fill_idx, 0)
            if plan is None:
                continue
            filled.append(self._apply_fill(
                act, plan["stake"], plan["odds"], plan["tick"],
            ))
        self._pending = remaining
        return filled

    def _apply_fill(
        self, action: BettingAction, stake: float, odds: float, tick: dict[str, Any]
    ) -> VirtualBet:
        """Record a filled bet in the book and portfolio."""
        assert self._portfolio is not None
        is_back, on_home, _ = _action_parts(action)
        event = self._tick_to_event(tick)
        bet = VirtualBet(
            match_id=event.match_id,
            placed_at=event.timestamp,
            action=action,
            team=event.team_home if on_home else event.team_away,
            odds=odds,
            stake=stake,
        )
        d_home, d_away = self._book_delta(is_back, on_home, stake, odds)
        self._pnl_home += d_home
        self._pnl_away += d_away
        self._open_bets.append(bet)
        self._bets_this_episode += 1
        self._portfolio.open_positions += 1
        self._portfolio.total_bets += 1
        self._portfolio.time_since_last_bet = 0.0
        self._portfolio.total_exposure = self._total_liability()
        return bet

    def _green_up_value(self, tick: dict[str, Any]) -> float:
        """Guaranteed P&L if the book were closed now at the prices available."""
        ph, pa = self._pnl_home, self._pnl_away
        d = ph - pa
        if abs(d) < _EPS:
            return ph

        def px(key: str) -> float:
            v = tick.get(key) or 0.0
            return v if v > 1.0 else 0.0

        options: list[float] = []
        if d > 0:
            if px("lay_home"):
                options.append(pa + d / px("lay_home"))
            if px("back_away"):
                options.append(ph - d / px("back_away"))
        else:
            e = -d
            if px("back_home"):
                options.append(pa - e / px("back_home"))
            if px("lay_away"):
                options.append(ph + e / px("lay_away"))
        return max(options) if options else min(ph, pa)

    def _compute_equity(self) -> float:
        """Bankroll plus spread-aware green-up value of the book, net of commission."""
        assert self._portfolio is not None
        bankroll = self._portfolio.current_balance
        if not self._open_bets or not self._current_episode_data:
            return bankroll
        tick = self._current_episode_data[min(self._step_idx, len(self._current_episode_data) - 1)]
        locked = self._green_up_value(tick)
        if locked > 0:
            locked *= 1.0 - self.exchange_commission
        return bankroll + locked

    def action_masks(self) -> np.ndarray:
        """Boolean mask over actions (HOLD always valid) for MaskablePPO-style use."""
        mask = np.zeros(ACTION_SPACE_SIZE, dtype=bool)
        mask[0] = True
        if self._portfolio is None:
            return mask
        fill_idx = self._step_idx + self.fill_delay_ticks
        for a in range(1, ACTION_SPACE_SIZE):
            if self._allowed_actions is not None and a not in self._allowed_actions:
                continue
            mask[a] = self._plan_fill(BettingAction(a), fill_idx, len(self._pending)) is not None
        return mask

    # ------------------------------------------------------------- settlement
    def _settle_bets(self) -> tuple[float, float, bool]:
        """
        Settle the book at episode end.

        Returns (net_profit_loss, commission_paid, any_settled). Commission is charged
        on net positive market P&L. Void results (tie/no_result/...) return stakes.
        """
        assert self._portfolio is not None
        if self._step_idx < len(self._current_episode_data) or not self._open_bets:
            return 0.0, 0.0, False

        now_ts = self._current_tick_time()
        if self.settlement_mode == "real" and self._match_result_type in _VOID_RESULTS:
            for bet in self._open_bets:
                bet.outcome = BetOutcome.VOID
                bet.profit_loss = 0.0
                bet.settled_at = now_ts
            self._clear_book()
            self._settled_bets_history = []
            return 0.0, 0.0, True

        home_won = self._determine_home_won()
        gross = 0.0
        for bet in self._open_bets:
            is_back = bet.action in _BACK_ACTIONS
            on_home = bet.action in _HOME_ACTIONS
            won = (on_home == home_won) if is_back else (on_home != home_won)
            if won:
                pnl = bet.stake * (bet.odds - 1.0) if is_back else bet.stake
                bet.outcome = BetOutcome.WIN
                self._portfolio.total_wins += 1
                self._portfolio.consecutive_streak = max(1, self._portfolio.consecutive_streak + 1)
            else:
                pnl = -bet.stake if is_back else -bet.stake * (bet.odds - 1.0)
                bet.outcome = BetOutcome.LOSS
                self._portfolio.consecutive_streak = min(-1, self._portfolio.consecutive_streak - 1)
            bet.profit_loss = pnl
            bet.settled_at = now_ts
            gross += pnl

        commission = self.exchange_commission * gross if gross > 0 else 0.0
        self._settled_bets_history.extend(self._open_bets)
        self._clear_book()
        return gross - commission, commission, True

    def _clear_book(self) -> None:
        assert self._portfolio is not None
        self._portfolio.open_positions = 0
        self._portfolio.total_exposure = 0.0
        self._open_bets = []
        self._pnl_home = 0.0
        self._pnl_away = 0.0

    def _current_tick_time(self) -> datetime:
        if not self._current_episode_data:
            return datetime.now(timezone.utc)
        tick = self._current_episode_data[min(self._step_idx, len(self._current_episode_data) - 1)]
        return self._tick_to_event(tick).timestamp

    def _determine_home_won(self) -> bool:
        """Did the home side win? Real result if known, else a random draw from implied odds."""
        if self.settlement_mode == "real" and self._match_winner:
            return self._match_winner == self._match_home_team
        tick = self._current_episode_data[-1] if self._current_episode_data else {}
        bh, ba = tick.get("back_home") or 0.0, tick.get("back_away") or 0.0
        p_home = (1.0 / bh) / ((1.0 / bh) + (1.0 / ba)) if bh > 1.0 and ba > 1.0 else 0.5
        return bool(self.np_random.random() < p_home)

    # ------------------------------------------------------------ observation
    def _get_observation(self) -> np.ndarray:
        """Get current observation vector."""
        if self._step_idx >= len(self._current_episode_data):
            return np.zeros(OBSERVATION_SIZE, dtype=np.float32)

        tick = self._current_episode_data[self._step_idx]
        event = self._tick_to_event(tick)
        context = self._tick_to_context(tick)
        position_state = self._build_position_state(tick)

        return self.feature_pipeline.compute(
            match_id=event.match_id,
            event=event,
            context=context,
            portfolio=self._portfolio,
            position_state=position_state,
        )

    def _get_info(self) -> dict[str, Any]:
        """Get auxiliary info dict. Use 'episode_idx' not 'episode' to avoid
        conflicting with Monitor's episode dict {"r": reward, "l": length}."""
        return {
            "step": self._step_idx,
            "episode_idx": self._episode_idx,
            "data_index": self._data_index,
            "balance": self._portfolio.current_balance if self._portfolio else 0.0,
            "open_bets": len(self._open_bets),
        }

    def _avg_entry(self, back: bool, home: bool) -> float:
        """Stake-weighted average entry odds of open bets on one side (0 if none)."""
        total = weighted = 0.0
        for b in self._open_bets:
            is_back, on_home, _ = _action_parts(b.action)
            if is_back == back and on_home == home:
                total += b.stake
                weighted += b.stake * b.odds
        return weighted / total if total > 0 else 0.0

    def _build_position_state(self, tick: dict[str, Any]) -> dict:
        """Build position state dict for the feature pipeline (fed from the book)."""
        bankroll = self._portfolio.current_balance if self._portfolio else self.initial_bankroll
        return {
            "net_home_exposure": self._pnl_home,
            "net_away_exposure": self._pnl_away,
            "bankroll": bankroll,
            "back_home_odds": tick.get("back_home") or 0.0,
            "lay_home_odds": tick.get("lay_home") or 0.0,
            "back_away_odds": tick.get("back_away") or 0.0,
            "lay_away_odds": tick.get("lay_away") or 0.0,
            "best_entry_home": self._avg_entry(True, True),
            "best_entry_away": self._avg_entry(True, False),
            "best_entry_lay_home": self._avg_entry(False, True),
            "best_entry_lay_away": self._avg_entry(False, False),
        }

    def _compute_open_bets_clv(self) -> float:
        """
        Stake-weighted CLV of open bets versus the current market (signed, positive =
        price moved in our favour). BACK: stake*(1/close - 1/entry); LAY is mirrored:
        stake*(1/entry - 1/close). Close price = current back price of the selection
        (lay price if back is missing).
        """
        if not self._open_bets or not self._current_episode_data:
            return 0.0
        tick = self._current_episode_data[min(self._step_idx, len(self._current_episode_data) - 1)]
        total = 0.0
        for bet in self._open_bets:
            is_back, on_home, _ = _action_parts(bet.action)
            side = "home" if on_home else "away"
            close = tick.get(f"back_{side}") or tick.get(f"lay_{side}") or 0.0
            if close <= 1.0 or bet.odds <= 1.0:
                continue
            if is_back:
                total += bet.stake * (1.0 / close - 1.0 / bet.odds)
            else:
                total += bet.stake * (1.0 / bet.odds - 1.0 / close)
        return total

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
        raw_ts = tick.get("timestamp") or tick.get("time")
        if isinstance(raw_ts, str):
            try:
                ts = datetime.fromisoformat(raw_ts)
            except Exception:
                ts = datetime.now(timezone.utc)
        elif isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.now(timezone.utc)

        return OddsEvent(
            match_id=tick.get("match_id", "unknown"),
            timestamp=ts,
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

        raw_ts = tick.get("timestamp") or tick.get("time")
        if isinstance(raw_ts, str):
            try:
                ts = datetime.fromisoformat(raw_ts)
            except Exception:
                ts = datetime.now(timezone.utc)
        elif isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.now(timezone.utc)

        return MatchContext(
            match_id=tick.get("match_id", "unknown"),
            timestamp=ts,
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
