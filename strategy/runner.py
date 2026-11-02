"""Live runner for the backtested strategy (S40): ticks in, signals out, paper bets tracked.

The runner drives the same ``strategy.base.Strategy`` contract the S39 backtester uses, with the same fill rule
(``backtesting.execution.plan_fill``) and the manual-latency model (``live_execution``). A backtest replays a full
match; the runner receives ticks one at a time, so an order is planned as soon as its check tick arrives. Both
paths see the same tick dicts in the same order, so they produce the same intents and the same fills (parity test
in ``tests/unit/test_s40_signal_engine.py``).

Nothing here places a real bet. A fill is recorded through the virtual engine (``engine.commit``) as a paper
bet tagged ``signal:<strategy>:<version>``.
"""
from __future__ import annotations

import math
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backtesting.execution import ExecutionModel, Fill, plan_fill
from shared.constants import MANUAL_LATENCY_SECONDS, MAX_DATA_AGE_SECONDS, SIGNAL_VALID_SECONDS
from shared.logging import setup_logging
from shared.schemas import BettingAction, VirtualBet
from strategy.base import MatchedBet, MarketFeed, OrderIntent, Position, Strategy
from strategy.signals import Signal, SignalBus
from virtual_trading.engine import BetRejectedError
from virtual_trading.portfolio import BetProposal, liability_for

logger = setup_logging("strategy_runner")

_META_KEYS = (
    "market_id", "match_id", "family", "match_format", "competition", "team_home", "team_away",
    "data_tier", "start", "competition_tier",
)
_ACTIONS = {
    ("BACK", "home"): BettingAction.BACK_HOME_SM,
    ("BACK", "away"): BettingAction.BACK_AWAY_SM,
    ("LAY", "home"): BettingAction.LAY_HOME_SM,
    ("LAY", "away"): BettingAction.LAY_AWAY_SM,
}


def epoch_of(value: Any) -> float:
    """Epoch seconds for an ISO string, datetime or number. Naive times are taken as UTC."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def live_execution() -> ExecutionModel:
    """Manual signal execution: a fill at the first price at or after ``decision + MANUAL_LATENCY_SECONDS``.

    Same ``bet_delay_s`` as ``ExecutionModel.preset("manual")`` (S43), with ``latency_s=0``: live ticks are
    stamped at scrape time, so the 5 s processing lag that the backtest adds to exchange timestamps is already
    in the tick. On one tick stream, a signal at exchange time T fills at T + 65 s in both paths.
    """
    return ExecutionModel(name="manual_signal", latency_s=0.0, bet_delay_s=MANUAL_LATENCY_SECONDS)


@dataclass
class PaperFill:
    """Outcome of one order for paper tracking: filled, missed (price through limit), lapsed, or rejected."""

    match_id: str
    side: str
    selection: str
    status: str
    reason: str
    placed_at: float
    price_limit: Optional[float]
    signal_id: Optional[str]
    price: Optional[float] = None
    stake: Optional[float] = None
    matched_at: Optional[float] = None
    bet: Optional[VirtualBet] = None
    agent_version: str = ""
    detail: str = ""


@dataclass
class TickOutcome:
    signals: list[Signal] = field(default_factory=list)
    paper: list[PaperFill] = field(default_factory=list)


@dataclass
class _Pending:
    intent: OrderIntent
    decision_index: int
    decision_time: float
    signal: Optional[Signal]
    planned: bool = False
    check: Optional[int] = None
    fill: Optional[Fill] = None


@dataclass
class _Match:
    strategy: Strategy
    team_home: str
    team_away: str
    position: Position
    times: list[float] = field(default_factory=list)
    ticks: list[dict[str, Any]] = field(default_factory=list)
    pending: list[_Pending] = field(default_factory=list)


class StrategyRunner:
    """Runs one strategy instance per match and turns its intents into signals and paper bets.

    ``strategy_factory(tick)`` returns a fresh strategy for the tick's match, or None when no strategy can run
    (for example no fair model for the format). A strategy holds per-match state, so the runner needs a factory
    rather than one shared instance. ``bankroll`` defaults to the portfolio's current balance.
    """

    def __init__(
        self,
        strategy_factory: Callable[[dict[str, Any]], Optional[Strategy]],
        engine: Any,
        risk: Any,
        bus: SignalBus,
        *,
        execution: Optional[ExecutionModel] = None,
        bankroll: Optional[Callable[[], float]] = None,
    ) -> None:
        self._factory = strategy_factory
        self._engine = engine
        self._risk = risk
        self._bus = bus
        self._execution = execution or live_execution()
        self._bankroll = bankroll or (lambda: float(engine.portfolio.state.current_balance))
        self._matches: dict[str, _Match] = {}
        self._skipped_logged: set[str] = set()

    # ------------------------------------------------------------------ public
    async def on_tick(self, tick: dict[str, Any], *, wall_now: Optional[float] = None) -> TickOutcome:
        """Process one live tick. ``wall_now`` (epoch seconds) enables the stale-data rule; None skips it."""
        match_id = str(tick["match_id"])
        out = TickOutcome()
        st = self._matches.get(match_id)
        if st is None:
            strategy = self._factory(tick)
            if strategy is None:
                if match_id not in self._skipped_logged:
                    self._skipped_logged.add(match_id)
                    logger.info("strategy_skipped_no_model", match_id=match_id, match_format=tick.get("match_format"))
                return out
            meta = {k: tick[k] for k in _META_KEYS if k in tick}
            strategy.reset(dict(meta))
            st = _Match(
                strategy=strategy,
                team_home=str(tick.get("team_home", "")),
                team_away=str(tick.get("team_away", "")),
                position=Position(match_id=match_id, bankroll=self._bankroll()),
            )
            self._matches[match_id] = st

        t = epoch_of(tick["time"])
        if st.times and t < st.times[-1]:
            raise ValueError(f"tick for {match_id} is out of order")
        clean = {k: v for k, v in tick.items() if k != "_meta"}
        st.times.append(t)
        st.ticks.append(clean)
        k = len(st.times) - 1
        self._settle(st, t, out)

        if wall_now is not None and wall_now - t > MAX_DATA_AGE_SECONDS:
            logger.debug("strategy_stale_tick_skipped", match_id=match_id, age_s=round(wall_now - t, 1))
            st.position.open_orders = [p.intent for p in st.pending]
            return out

        st.position.feed = MarketFeed(st.ticks, st.times, k)
        st.position.bankroll = self._bankroll()
        intents = st.strategy.on_tick(dict(clean), st.position) or []
        details = list(getattr(st.strategy, "last_details", []) or [])
        for i, intent in enumerate(intents):
            detail = details[i] if i < len(details) else {}
            signal = self._signal_for(st, match_id, intent, detail, t)
            published = await self._bus.publish(signal)
            if published:
                out.signals.append(signal)
            st.pending.append(_Pending(intent, k, t, signal if published else None))
        self._settle(st, t, out)
        st.position.open_orders = [p.intent for p in st.pending]
        return out

    async def finish(self, match_id: str) -> TickOutcome:
        """End a match: fills already due are recorded, the rest lapse (no market tick after the latency)."""
        out = TickOutcome()
        st = self._matches.pop(str(match_id), None)
        if st is None:
            return out
        self._settle(st, math.inf, out)
        for p in st.pending:
            if not p.planned:
                out.paper.append(self._missed(st, p, "lapsed", "no market tick after the manual latency"))
        st.pending = []
        return out

    # ------------------------------------------------------------------ internals
    def _settle(self, st: _Match, now: float, out: TickOutcome) -> None:
        self._plan(st)
        self._reveal(st, now, out)

    def _plan(self, st: _Match) -> None:
        """Fix the fill of every order whose check tick (first tick after the latency) has arrived."""
        for p in st.pending:
            if p.planned:
                continue
            if bisect_left(st.times, p.decision_time + self._execution.bet_delay_s) >= len(st.times):
                continue
            check, fill = plan_fill(
                self._execution, st.ticks, st.times, p.decision_index, p.decision_time, p.intent,
                bankroll=self._bankroll(),
            )
            p.planned = True
            p.check = check
            p.fill = fill

    def _reveal(self, st: _Match, now: float, out: TickOutcome) -> None:
        """Move orders whose outcome is known by ``now`` into the position and the paper book."""
        still: list[_Pending] = []
        for p in st.pending:
            if not p.planned or p.check is None or st.times[p.check] > now:
                still.append(p)
                continue
            if p.fill is None:
                out.paper.append(self._missed(st, p, "missed", "price through the limit or no price at the check tick"))
                continue
            fill = p.fill
            st.position.matched.append(MatchedBet(
                side=p.intent.side,
                selection=p.intent.selection,
                price=fill.price,
                stake=fill.stake,
                placed_at=p.decision_time,
                matched_at=fill.time,
                fill_index=fill.index,
                reason=p.intent.reason,
            ))
            out.paper.append(self._paper_fill(st, p))
        st.pending = still

    def _paper_fill(self, st: _Match, p: _Pending) -> PaperFill:
        intent, fill = p.intent, p.fill
        assert fill is not None
        tag = f"signal:{st.strategy.name}:{getattr(st.strategy, 'version', '')}"
        base = dict(
            match_id=st.position.match_id, side=intent.side, selection=intent.selection,
            placed_at=p.decision_time, price_limit=intent.price_limit,
            signal_id=p.signal.id if p.signal else None, reason=intent.reason, agent_version=tag,
            price=fill.price, stake=fill.stake, matched_at=fill.time,
        )
        team = st.team_home if intent.selection == "home" else st.team_away
        side = intent.side.lower()
        proposal = BetProposal(
            match_id=st.position.match_id,
            action=_ACTIONS[(intent.side, intent.selection)],
            team=team,
            side=side,  # type: ignore[arg-type]
            odds=fill.price,
            stake=fill.stake,
            liability=liability_for(side, fill.stake, fill.price),
            reduces_liability=intent.reason.startswith("exit"),
            confidence=None,
            reservation_id=uuid.uuid4().hex,
        )
        self._engine.portfolio.reserve(proposal)
        decision = self._risk.check(proposal)
        if not decision.allowed:
            self._engine.release(proposal)
            logger.info("paper_bet_rejected_risk", match_id=base["match_id"], violations=decision.violations)
            return PaperFill(status="rejected", detail="; ".join(decision.violations), **{**base, "reason": intent.reason})
        try:
            bet = self._engine.commit(proposal, fill_odds=fill.price)
        except BetRejectedError as e:
            return PaperFill(status="rejected", detail=str(e), **{**base, "reason": intent.reason})
        self._risk.record_bet()
        bet.agent_version = tag
        return PaperFill(status="filled", bet=bet, **{**base, "reason": intent.reason})

    @staticmethod
    def _missed(st: _Match, p: _Pending, status: str, detail: str) -> PaperFill:
        intent = p.intent
        return PaperFill(
            match_id=st.position.match_id, side=intent.side, selection=intent.selection, status=status,
            reason=intent.reason, placed_at=p.decision_time, price_limit=intent.price_limit,
            signal_id=p.signal.id if p.signal else None, detail=detail,
        )

    @staticmethod
    def _signal_for(st: _Match, match_id: str, intent: OrderIntent, detail: dict[str, Any], t: float) -> Signal:
        kind = str(detail.get("kind") or ("exit" if intent.reason.startswith("exit") else "entry"))
        return Signal(
            id=Signal.new_id(),
            created_at=datetime.fromtimestamp(t, tz=timezone.utc),
            valid_until=datetime.fromtimestamp(t + SIGNAL_VALID_SECONDS, tz=timezone.utc),
            match=match_id,
            selection=intent.selection,
            side=intent.side,
            price_limit=intent.price_limit,
            suggested_stake=round(intent.stake, 2),
            fair_prob=float(detail["fair_prob"]),
            market_price=float(detail["market_price"]),
            edge=float(detail["edge"]),
            reason=intent.reason,
            strategy_version=f"{st.strategy.name}:{getattr(st.strategy, 'version', '')}",
            kind=kind,
        )
