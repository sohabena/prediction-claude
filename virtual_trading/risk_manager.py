"""
Risk manager: enforces all risk limits (in liability terms) and provides risk assessments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.constants import (
    CIRCUIT_BREAKER_CONSECUTIVE,
    MAX_BET_LIABILITY_PCT,
    MAX_BETS_PER_HOUR,
    MAX_BET_PERCENT,
    MAX_DAILY_LOSS_PERCENT,
    MAX_DRAWDOWN_PERCENT,
    MAX_MATCH_EXPOSURE_PERCENT,
    MAX_MATCH_LIABILITY_PCT,
    MAX_TEAM_LIABILITY_PCT,
    MAX_TOTAL_EXPOSURE_PERCENT,
    MAX_TOTAL_LIABILITY_PCT,
    MAX_WEEKLY_LOSS_PERCENT,
)
from shared.logging import setup_logging
from virtual_trading.portfolio import BetProposal, PortfolioManager, worst_loss

logger = setup_logging("risk_manager")

_EPS = 1e-6

# S32: max worst-case loss summed over the open matches of one competition, as a
# fraction of the balance. Defined here because shared/constants.py is not owned by S32.
MAX_COMPETITION_LIABILITY_PCT = 0.15  # half the total cap, so it binds first on a busy competition day


@dataclass
class RiskDecision:
    """Result of a risk check."""

    allowed: bool
    violations: list[str] = field(default_factory=list)


class RiskManager:
    """
    Enforces risk limits for the virtual trading system.

    Checks (all on worst-case liability, including in-flight reservations):
    - Per-bet liability
    - Per-match worst-case loss
    - Per-team worst-case loss across all open matches
    - Total worst-case loss
    - Per-competition worst-case loss (S32; only for matches registered with a competition)
    - Daily loss (realised P&L plus open worst-case losses, S32), weekly loss, drawdown,
      circuit breaker (halt new risk)
    - Hourly bet frequency
    Liability-reducing proposals pass the exposure caps and the halting breakers.
    """

    def __init__(self, portfolio: PortfolioManager) -> None:
        self.portfolio = portfolio

    def check(self, proposal: BetProposal) -> RiskDecision:
        """Check a sized proposal against every limit using its actual liability."""
        pf = self.portfolio
        pf.now()  # applies day / week rollover
        state = pf.state
        balance = max(state.current_balance, 1.0)
        violations: list[str] = []
        rid = proposal.reservation_id

        # Per-bet liability (applies to every proposal, including hedges)
        bet_cap = MAX_BET_LIABILITY_PCT * balance
        if proposal.liability > bet_cap + _EPS:
            violations.append(
                f"Bet liability {proposal.liability:.0f} exceeds {MAX_BET_LIABILITY_PCT:.1%} of {balance:.0f}"
            )

        if not proposal.reduces_liability:
            # Exposure caps, evaluated on the net worst-case book after this bet
            after = pf.match_books(extra=[proposal], exclude_reservation=rid)
            worst = {m: worst_loss(b) for m, b in after.items()}

            match_loss = worst.get(proposal.match_id, 0.0)
            if match_loss > MAX_MATCH_LIABILITY_PCT * balance + _EPS:
                violations.append(
                    f"Match liability {match_loss / balance:.1%} > {MAX_MATCH_LIABILITY_PCT:.0%}"
                )

            for team in set(pf.match_teams(proposal.match_id)) - {""}:
                team_loss = sum(
                    worst.get(m, 0.0)
                    for m in after
                    if team in pf.match_teams(m)
                )
                if team_loss > MAX_TEAM_LIABILITY_PCT * balance + _EPS:
                    violations.append(
                        f"Team '{team}' liability {team_loss / balance:.1%} > {MAX_TEAM_LIABILITY_PCT:.0%}"
                    )

            total_loss = sum(worst.values())
            if total_loss > MAX_TOTAL_LIABILITY_PCT * balance + _EPS:
                violations.append(
                    f"Total liability {total_loss / balance:.1%} > {MAX_TOTAL_LIABILITY_PCT:.0%}"
                )

            competition = pf.match_competition(proposal.match_id)
            if competition is not None:  # unknown competition = no competition cap
                comp_loss = pf.competition_open_liability(
                    competition, extra=[proposal], exclude_reservation=rid
                )
                if comp_loss > MAX_COMPETITION_LIABILITY_PCT * balance + _EPS:
                    violations.append(
                        f"Competition '{competition}' liability {comp_loss / balance:.1%} "
                        f"> {MAX_COMPETITION_LIABILITY_PCT:.0%}"
                    )

            # Halting breakers (liability-reducing bets are exempt). The daily loss counts
            # realised P&L plus current open worst-case losses (excluding this proposal).
            daily_loss = pf.daily_loss_fraction(exclude_reservation=rid)
            if daily_loss >= MAX_DAILY_LOSS_PERCENT:
                violations.append(f"Daily loss {daily_loss:.1%} >= {MAX_DAILY_LOSS_PERCENT:.0%}")

            weekly_loss = -pf.weekly_pnl / max(pf.weekly_start_balance, 1.0)
            if weekly_loss >= MAX_WEEKLY_LOSS_PERCENT:
                violations.append(f"Weekly loss {weekly_loss:.1%} >= {MAX_WEEKLY_LOSS_PERCENT:.0%}")

            drawdown = pf.get_drawdown()
            if drawdown >= MAX_DRAWDOWN_PERCENT:
                violations.append(f"Drawdown {drawdown:.1%} >= {MAX_DRAWDOWN_PERCENT:.0%}")

            if pf.is_circuit_breaker_active:
                violations.append(
                    f"Circuit breaker: {CIRCUIT_BREAKER_CONSECUTIVE} consecutive losses"
                )

            hourly = pf.bets_in_last_hour(exclude_reservation=rid)
            if hourly >= MAX_BETS_PER_HOUR:
                violations.append(f"Hourly limit: {hourly} >= {MAX_BETS_PER_HOUR}")

        allowed = not violations
        if not allowed:
            logger.warning("risk_check_failed", match_id=proposal.match_id, violations=violations)
        return RiskDecision(allowed=allowed, violations=violations)

    def check_all(self, proposed_stake: float, match_id: str) -> dict[str, Any]:
        """Deprecated stake-based shim (kept for the live loop until S06 uses ``check``).

        Treats the stake as the liability of a back bet; still enforces the breakers,
        per-bet, match and total liability caps via the portfolio's current book.
        """
        pf = self.portfolio
        pf.now()
        state = pf.state
        balance = max(state.current_balance, 1.0)
        violations: list[str] = []

        if proposed_stake > balance * max(MAX_BET_PERCENT, MAX_BET_LIABILITY_PCT):
            violations.append(f"Stake {proposed_stake:.0f} exceeds per-bet limit of {balance:.0f}")

        books = pf.match_books()
        match_loss = worst_loss(books.get(match_id, (0.0, 0.0)))
        if (match_loss + proposed_stake) / balance > MAX_MATCH_EXPOSURE_PERCENT:
            violations.append("Match exposure limit")
        if (sum(worst_loss(b) for b in books.values()) + proposed_stake) / balance > MAX_TOTAL_EXPOSURE_PERCENT:
            violations.append("Total exposure limit")

        if pf.daily_loss_fraction() >= MAX_DAILY_LOSS_PERCENT:
            violations.append("Daily loss limit")
        if -pf.weekly_pnl / max(pf.weekly_start_balance, 1.0) >= MAX_WEEKLY_LOSS_PERCENT:
            violations.append("Weekly loss limit")
        if pf.get_drawdown() >= MAX_DRAWDOWN_PERCENT:
            violations.append("Drawdown limit")
        if pf.bets_in_last_hour() >= MAX_BETS_PER_HOUR:
            violations.append("Hourly limit")
        if pf.is_circuit_breaker_active:
            violations.append("Circuit breaker")

        return {"allowed": not violations, "violations": violations}

    def record_bet(self) -> None:
        """Deprecated no-op: the portfolio records bet times on commit."""

    def get_risk_summary(self) -> dict[str, Any]:
        """Get current risk metrics."""
        pf = self.portfolio
        state = pf.state
        balance = max(state.current_balance, 1)
        return {
            "exposure_pct": state.total_exposure / balance,
            "daily_pnl_pct": state.daily_pnl / max(pf.daily_start_balance, 1),
            "weekly_pnl_pct": pf.weekly_pnl / max(pf.weekly_start_balance, 1),
            "drawdown": pf.get_drawdown(),
            "open_positions": state.open_positions,
            "bets_this_hour": pf.bets_in_last_hour(),
            "circuit_breaker": pf.is_circuit_breaker_active,
            "consecutive_streak": state.consecutive_streak,
        }
