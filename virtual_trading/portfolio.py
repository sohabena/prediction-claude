"""
Portfolio manager: tracks the agent's financial state, open positions, and P&L.

Exposure is measured in worst-case LIABILITY (stake for a back, stake*(odds-1) for a
lay), never in raw stake. ``PortfolioState.total_exposure`` is the gross sum of the
liabilities of all open bets.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Literal, Optional

from shared.constants import (
    CIRCUIT_BREAKER_CONSECUTIVE,
    CIRCUIT_BREAKER_COOLDOWN_MINUTES,
    MAX_DAILY_LOSS_PERCENT,
    MAX_TOTAL_EXPOSURE_PERCENT,
    RESERVATION_TTL_SECONDS,
)
from shared.logging import setup_logging
from shared.schemas import BettingAction, PortfolioState, VirtualBet

logger = setup_logging("portfolio")

_LAY_ACTIONS = frozenset({
    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
    BettingAction.LAY_AWAY_SM, BettingAction.LAY_AWAY_LG,
})
_HOME_ACTIONS = frozenset({
    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
})


@dataclass
class BetProposal:
    """A sized, reserved bet that has not been committed yet."""

    match_id: str
    action: BettingAction
    team: str
    side: Literal["back", "lay"]
    odds: float
    stake: float
    liability: float          # stake for back, stake*(odds-1) for lay
    reduces_liability: bool   # True if it lowers the match's worst-case loss
    confidence: float
    reservation_id: str


def action_side(action: BettingAction) -> Literal["back", "lay"]:
    """Return 'lay' for LAY_* actions, else 'back'."""
    return "lay" if action in _LAY_ACTIONS else "back"


def action_is_home(action: BettingAction) -> bool:
    """True when the action targets the home selection."""
    return action in _HOME_ACTIONS


def liability_for(side: str, stake: float, odds: float) -> float:
    """Worst-case loss of a bet: stake for back, stake*(odds-1) for lay."""
    return stake * (odds - 1.0) if side == "lay" else stake


def bet_liability(bet: VirtualBet) -> float:
    """Liability of a VirtualBet."""
    return liability_for(action_side(bet.action), bet.stake, bet.odds)


def outcome_pnls(action: BettingAction, odds: float, stake: float) -> tuple[float, float]:
    """P&L (before commission) if (home wins, away wins) for one bet leg."""
    on_home = action_is_home(action)
    if action_side(action) == "back":
        win, lose = stake * (odds - 1.0), -stake
    else:
        win, lose = -stake * (odds - 1.0), stake  # 'win' = selection wins
    return (win, lose) if on_home else (lose, win)


def worst_loss(book: tuple[float, float]) -> float:
    """Worst-case loss (>= 0) of a (pnl_home_wins, pnl_away_wins) book."""
    return max(0.0, -min(book))


class PortfolioManager:
    """
    Manages the RL agent's virtual portfolio.

    Tracks:
    - Balance, P&L, liability exposure and in-flight reservations
    - Open positions
    - Win/loss streaks
    - UTC day / ISO-week rollover of daily and weekly P&L
    - Circuit breaker with a cooldown
    """

    def __init__(
        self,
        initial_balance: float = 100_000.0,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._state = PortfolioState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
        )
        self._open_bets: dict[str, VirtualBet] = {}
        self._settled_bets: list[VirtualBet] = []
        self._daily_start_balance: float = initial_balance
        self._weekly_start_balance: float = initial_balance
        self._weekly_pnl: float = 0.0
        self._peak_balance: float = initial_balance
        self._last_bet_time: float = 0.0
        self._circuit_breaker_active: bool = False
        self._last_loss_at: Optional[datetime] = None
        self._bet_times: list[datetime] = []
        self._reservations: dict[str, BetProposal] = {}
        self._reservation_times: dict[str, datetime] = {}
        self._match_teams: dict[str, tuple[str, str]] = {}
        now = self._clock()
        self._day_key = now.date()
        self._week_key = now.isocalendar()[:2]

    # ------------------------------------------------------------------ time
    def now(self) -> datetime:
        """Current (injectable) time, after applying day/week rollover."""
        now = self._clock()
        self._maybe_roll_periods(now)
        return now

    def _maybe_roll_periods(self, now: datetime) -> None:
        """Reset daily / weekly P&L when the UTC day / ISO week changes."""
        day = now.date()
        week = now.isocalendar()[:2]
        if day != self._day_key:
            self._day_key = day
            self._daily_start_balance = self._state.current_balance
            self._state.daily_pnl = 0.0
            logger.info("daily_rollover", balance=self._state.current_balance)
        if week != self._week_key:
            self._week_key = week
            self._weekly_start_balance = self._state.current_balance
            self._weekly_pnl = 0.0
            logger.info("weekly_rollover", balance=self._state.current_balance)

    # ------------------------------------------------------------ properties
    @property
    def state(self) -> PortfolioState:
        """Get current portfolio state."""
        self._maybe_roll_periods(self._clock())
        self._state.time_since_last_bet = time.time() - self._last_bet_time if self._last_bet_time else 0.0
        return self._state

    @property
    def open_bets(self) -> dict[str, VirtualBet]:
        """Get currently open bets."""
        return self._open_bets

    @property
    def weekly_pnl(self) -> float:
        """Realised P&L since the start of the current ISO week."""
        self._maybe_roll_periods(self._clock())
        return self._weekly_pnl

    @property
    def daily_start_balance(self) -> float:
        """Balance at the start of the current UTC day."""
        self._maybe_roll_periods(self._clock())
        return self._daily_start_balance

    @property
    def weekly_start_balance(self) -> float:
        """Balance at the start of the current ISO week."""
        self._maybe_roll_periods(self._clock())
        return self._weekly_start_balance

    @property
    def is_circuit_breaker_active(self) -> bool:
        """True while the breaker is tripped; auto-clears after the cooldown."""
        if self._circuit_breaker_active and self._last_loss_at is not None:
            elapsed = self._clock() - self._last_loss_at
            if elapsed >= timedelta(minutes=CIRCUIT_BREAKER_COOLDOWN_MINUTES):
                self._circuit_breaker_active = False
                self._state.consecutive_streak = 0
                logger.info("circuit_breaker_cleared")
        return self._circuit_breaker_active

    @property
    def reservations(self) -> dict[str, BetProposal]:
        """Live (un-expired) liability reservations by reservation id."""
        self._expire_reservations()
        return self._reservations

    # ---------------------------------------------------------- reservations
    def register_match(self, match_id: str, team_home: str, team_away: str) -> None:
        """Remember which teams play in a match (for per-team exposure)."""
        self._match_teams[match_id] = (team_home, team_away)

    def match_teams(self, match_id: str) -> tuple[str, str]:
        """Return (home, away) names registered for a match, or ('', '')."""
        return self._match_teams.get(match_id, ("", ""))

    def reserve(self, proposal: BetProposal) -> None:
        """Reserve a proposal's liability so later checks count it."""
        self._expire_reservations()
        self._reservations[proposal.reservation_id] = proposal
        self._reservation_times[proposal.reservation_id] = self._clock()

    def release(self, reservation_id: str) -> None:
        """Free a reservation (no-op if unknown)."""
        self._reservations.pop(reservation_id, None)
        self._reservation_times.pop(reservation_id, None)

    def _expire_reservations(self) -> None:
        now = self._clock()
        for rid in [
            r for r, t in self._reservation_times.items()
            if (now - t).total_seconds() > RESERVATION_TTL_SECONDS
        ]:
            logger.warning("reservation_expired", reservation_id=rid)
            self.release(rid)

    # ----------------------------------------------------------------- books
    def match_books(
        self,
        extra: Iterable[BetProposal] = (),
        exclude_reservation: Optional[str] = None,
    ) -> dict[str, tuple[float, float]]:
        """Per-match (pnl if home wins, pnl if away wins) over open bets,
        live reservations (except ``exclude_reservation``) and ``extra`` proposals."""
        books: dict[str, list[float]] = {}

        def add(match_id: str, action: BettingAction, odds: float, stake: float) -> None:
            h, a = outcome_pnls(action, odds, stake)
            cur = books.setdefault(match_id, [0.0, 0.0])
            cur[0] += h
            cur[1] += a

        for b in self._open_bets.values():
            add(b.match_id, b.action, b.odds, b.stake)
        for rid, p in self.reservations.items():
            if rid != exclude_reservation:
                add(p.match_id, p.action, p.odds, p.stake)
        for p in extra:
            add(p.match_id, p.action, p.odds, p.stake)
        return {m: (v[0], v[1]) for m, v in books.items()}

    def bets_in_last_hour(self, exclude_reservation: Optional[str] = None) -> int:
        """Committed bets in the last hour plus in-flight reservations."""
        now = self._clock()
        self._bet_times = [t for t in self._bet_times if (now - t).total_seconds() < 3600]
        pending = sum(1 for rid in self.reservations if rid != exclude_reservation)
        return len(self._bet_times) + pending

    # ------------------------------------------------------------- placement
    def can_place_bet(self, stake: float, liability: Optional[float] = None) -> tuple[bool, str]:
        """
        Backstop check for a new bet (the RiskManager is the primary gate).

        Args:
            stake: Bet stake.
            liability: Worst-case loss; defaults to ``stake`` (back bet).

        Returns:
            (allowed, reason) tuple.
        """
        self._maybe_roll_periods(self._clock())
        liab = stake if liability is None else liability

        if self.is_circuit_breaker_active:
            return False, "Circuit breaker active"

        # Check daily loss limit
        daily_loss = (self._daily_start_balance - self._state.current_balance) / max(self._daily_start_balance, 1.0)
        if daily_loss >= MAX_DAILY_LOSS_PERCENT:
            return False, f"Daily loss limit reached: {daily_loss:.1%}"

        # Check total liability
        new_exposure = (self._state.total_exposure + liab) / max(self._state.current_balance, 1.0)
        if new_exposure > MAX_TOTAL_EXPOSURE_PERCENT:
            return False, f"Exposure limit: {new_exposure:.1%} > {MAX_TOTAL_EXPOSURE_PERCENT:.0%}"

        # Check single-bet liability
        if liab > self._state.current_balance * 0.1:
            return False, f"Liability too large: {liab:.0f}"

        return True, "OK"

    def open_position(self, bet: VirtualBet, reduces_liability: bool = False) -> bool:
        """Record a new open bet position.

        ``reduces_liability`` bets skip the halting backstops (breaker, daily loss,
        exposure) because they only lower the book's worst case.
        """
        liab = bet_liability(bet)
        if not reduces_liability:
            allowed, reason = self.can_place_bet(bet.stake, liab)
            if not allowed:
                logger.warning("bet_rejected", reason=reason, match_id=bet.match_id)
                return False
        else:
            self._maybe_roll_periods(self._clock())

        bet_id = f"{bet.match_id}_{uuid.uuid4().hex}"
        bet.id = bet_id
        self._open_bets[bet_id] = bet

        self._state.open_positions += 1
        self._state.total_exposure += liab
        self._state.total_bets += 1
        self._last_bet_time = time.time()
        self._bet_times.append(self._clock())

        logger.info(
            "position_opened",
            bet_id=bet_id,
            action=bet.action.name,
            odds=bet.odds,
            stake=bet.stake,
            liability=liab,
        )
        return True

    def close_position(self, bet_id: str, pnl: float) -> None:
        """Close a position with realized P&L."""
        if bet_id not in self._open_bets:
            logger.warning("bet_not_found", bet_id=bet_id)
            return

        now = self._clock()
        self._maybe_roll_periods(now)
        bet = self._open_bets.pop(bet_id)
        bet.profit_loss = pnl
        bet.settled_at = datetime.now(timezone.utc)

        self._settled_bets.append(bet)

        # Update state
        self._state.open_positions -= 1
        self._state.total_exposure = max(0.0, self._state.total_exposure - bet_liability(bet))
        self._state.current_balance += pnl
        self._state.session_pnl += pnl
        self._state.daily_pnl += pnl
        self._weekly_pnl += pnl

        # Update streak
        if pnl > 0:
            self._state.total_wins += 1
            self._state.consecutive_streak = max(1, self._state.consecutive_streak + 1)
        elif pnl < 0:
            self._state.consecutive_streak = min(-1, self._state.consecutive_streak - 1)
            self._last_loss_at = now

        # Check circuit breaker
        if self._state.consecutive_streak <= -CIRCUIT_BREAKER_CONSECUTIVE:
            if not self._circuit_breaker_active:
                logger.warning("circuit_breaker_triggered", streak=self._state.consecutive_streak)
            self._circuit_breaker_active = True

        # Update peak
        self._peak_balance = max(self._peak_balance, self._state.current_balance)

        logger.info(
            "position_closed",
            bet_id=bet_id,
            pnl=pnl,
            balance=self._state.current_balance,
        )

    def get_drawdown(self) -> float:
        """Current drawdown from peak."""
        if self._peak_balance <= 0:
            return 0.0
        return 1.0 - (self._state.current_balance / self._peak_balance)

    def reset_daily(self) -> None:
        """Manually reset daily tracking (rollover is automatic; kept for compatibility)."""
        self._daily_start_balance = self._state.current_balance
        self._state.daily_pnl = 0.0
        self._circuit_breaker_active = False
        logger.info("daily_reset", balance=self._state.current_balance)

    def get_recent_win_rate(self, n: int = 20) -> float:
        """Win rate over last N settled bets."""
        recent = self._settled_bets[-n:]
        if not recent:
            return 0.0
        wins = sum(1 for b in recent if b.profit_loss > 0)
        return wins / len(recent)
