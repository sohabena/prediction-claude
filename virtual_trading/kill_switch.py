"""S42 signal kill switch: suspends signal emission for one strategy version, or globally.

It has no orders to cancel: nothing places bets. A suspended version emits no signals, and the risk manager
blocks new (liability-adding) paper bets while the global scope is active.

Trip conditions (each one is evaluated and persisted as it fires):
- daily paper loss: realised P&L settled today on one version <= -KILL_DAILY_LOSS of the allocated bankroll;
- rolling CLV: the last KILL_CLV_WINDOW_BETS paper bets with a reference price (``CLV_REFERENCE_SOURCE``, S45)
  have mean CLV < 0 and a bootstrap 95% CI upper bound < 0 (same CLV definition as trust, see ``strategy.trust``);
- stale data: no Betfair tick for KILL_STALE_SECONDS on a match with open paper positions (global scope,
  because a dead feed is a system fault).

State is kept in Redis under ``kill:<strategy:version>`` and ``kill:__all__``. A trip is never cleared by code;
only ``reset`` (a manual operator call) clears it. Loading fails closed: if the state cannot be read, the
global scope is suspended.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from shared.constants import (
    CLV_REFERENCE_SOURCE,
    KEY_KILL_PREFIX,
    KILL_ALL_SCOPE,
    KILL_CLV_WINDOW_BETS,
    KILL_DAILY_LOSS,
    KILL_STALE_SECONDS,
)
from shared.logging import setup_logging
from shared.metrics import portfolio_clv
from strategy.trust import annotate_reference

logger = setup_logging("kill_switch")

_FAIL_CLOSED_REASON = "kill state unreadable at load; suspended until manual reset"


def _utc_day(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    return date.fromisoformat(str(value)[:10])


class KillSwitch:
    """Signal suspension with Redis persistence. ``blocked`` is synchronous and reads the in-memory state."""

    def __init__(
        self,
        redis: Any = None,
        *,
        now_fn: Any = None,
        daily_loss: float = KILL_DAILY_LOSS,
        clv_window: int = KILL_CLV_WINDOW_BETS,
        stale_seconds: float = KILL_STALE_SECONDS,
        n_bootstrap: int = 10_000,
        reference_source: str = CLV_REFERENCE_SOURCE,
    ) -> None:
        self._redis = redis
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._daily_loss = daily_loss
        self._clv_window = clv_window
        self._stale_seconds = stale_seconds
        self._n_bootstrap = n_bootstrap
        self._reference_source = reference_source
        self._active: dict[str, str] = {}  # scope -> reason

    @staticmethod
    def key_for(scope: str) -> str:
        return f"{KEY_KILL_PREFIX}{scope}"

    # ------------------------------------------------------------ state
    def blocked(self, strategy_version: Optional[str] = None) -> Optional[str]:
        """Reason signals are suspended (for ``strategy_version`` or globally), or None when not suspended."""
        if KILL_ALL_SCOPE in self._active:
            return f"global: {self._active[KILL_ALL_SCOPE]}"
        if strategy_version and strategy_version in self._active:
            return self._active[strategy_version]
        return None

    async def load(self, scopes: Iterable[str] = ()) -> None:
        """Restore persisted suspensions for the global scope and the given versions."""
        for scope in dict.fromkeys([KILL_ALL_SCOPE, *scopes]):
            if self._redis is None:
                continue
            try:
                data = await self._redis.get_json(self.key_for(scope))
            except Exception as e:
                logger.error("kill_state_load_failed", scope=scope, error=type(e).__name__)
                self._active.setdefault(KILL_ALL_SCOPE, _FAIL_CLOSED_REASON)
                continue
            if data and data.get("active"):
                self._active[scope] = str(data.get("reason", ""))

    async def trip(self, scope: str, reason: str) -> bool:
        """Suspend ``scope``. Returns True when this call newly tripped it."""
        if scope in self._active:
            return False
        self._active[scope] = reason
        await self._persist(scope, {"active": True, "reason": reason, "tripped_at": self._now().isoformat()})
        logger.warning("kill_switch_tripped", scope=scope, reason=reason)
        return True

    async def reset(self, scope: str, *, operator: str) -> None:
        """Clear a suspension. Manual only (operator CLI). Nothing calls this automatically."""
        if not operator.strip():
            raise ValueError("reset requires the operator's name")
        self._active.pop(scope, None)
        await self._persist(scope, {"active": False, "reset_by": operator, "reset_at": self._now().isoformat()})
        logger.warning("kill_switch_reset", scope=scope, operator=operator)

    async def _persist(self, scope: str, data: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set_json(self.key_for(scope), data)
        except Exception as e:
            logger.error("kill_state_persist_failed", scope=scope, error=type(e).__name__)

    # ------------------------------------------------------------ evidence
    async def evaluate_version(
        self,
        strategy_version: str,
        bets: Sequence[Mapping[str, Any]],
        ticks_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        allocated_bankroll: float,
    ) -> Optional[str]:
        """Trip ``strategy_version`` on daily loss or rolling CLV. Returns the reason when it trips now."""
        annotated = annotate_reference(bets, ticks_by_match, source=self._reference_source)
        today = self._now().astimezone(timezone.utc).date()

        todays_pnl = sum(
            float(b["profit_loss"])
            for b in annotated
            if b.get("settled_at") is not None
            and b.get("profit_loss") is not None
            and _utc_day(b["settled_at"]) == today
        )
        if allocated_bankroll > 0 and todays_pnl <= -self._daily_loss * allocated_bankroll:
            reason = (
                f"daily paper loss {todays_pnl:.2f} <= -{self._daily_loss:.0%} of allocated bankroll"
            )
            if await self.trip(strategy_version, reason):
                return reason

        clv_rows = sorted(
            (b for b in annotated if b.get("reference_clv") is not None),
            key=lambda b: b["placed_at"],
        )[-self._clv_window:]
        if len(clv_rows) >= self._clv_window:
            clv = portfolio_clv(clv_rows, field="reference_clv", n_bootstrap=self._n_bootstrap)
            if clv["mean_clv"] < 0.0 and clv["ci95_upper"] < 0.0:
                reason = (
                    f"rolling reference CLV ({self._reference_source}) over {len(clv_rows)} bets: "
                    f"mean {clv['mean_clv']:+.4f}, CI upper {clv['ci95_upper']:+.4f} < 0"
                )
                if await self.trip(strategy_version, reason):
                    return reason
        return None

    async def evaluate_feed(
        self,
        last_tick_at: Mapping[str, datetime],
        open_matches: Iterable[str],
    ) -> Optional[str]:
        """Trip the global scope when an open match has had no live tick for ``stale_seconds``.

        ``last_tick_at`` comes from the feed the live loop actually receives (LotusBook today).
        """
        now = self._now()
        for match_id in sorted(set(open_matches)):
            last = last_tick_at.get(match_id)
            if last is None or (now - last).total_seconds() > self._stale_seconds:
                reason = (
                    f"no live tick for match {match_id} in {self._stale_seconds:.0f}s "
                    f"with open paper positions"
                )
                if await self.trip(KILL_ALL_SCOPE, reason):
                    return reason
                return None
        return None
