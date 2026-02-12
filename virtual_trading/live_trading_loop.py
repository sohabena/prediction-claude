"""
Live virtual trading loop: runs the RL agent on live match events during VIRTUAL_TRADING.

Subscribes to match_events, computes observations, runs agent prediction,
places virtual bets via VirtualBetEngine, persists to DB, and settles via match_results.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, Optional

import numpy as np

from features.context_resolver import get_match_context
from features.pipeline import FeaturePipeline
from features.store import FeatureStore
from shared.constants import (
    BET_DELAY_MAX_SECONDS,
    BET_DELAY_MIN_SECONDS,
    CHANNEL_MATCH_EVENTS,
    CHANNEL_MATCH_RESULTS,
    KEY_WATCHED_MATCH_ID,
)
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import BettingAction, OddsEvent, PortfolioState, VirtualBet
from virtual_trading.bet_persister import persist_bet, update_settled_bet
from virtual_trading.engine import VirtualBetEngine
from virtual_trading.portfolio import PortfolioManager
from virtual_trading.risk_manager import RiskManager
from virtual_trading.settlement import SettlementEngine

logger = setup_logging("live_trading_loop")


class LiveVirtualTradingLoop:
    """
    Lives virtual trading during VIRTUAL_TRADING state.

    Flow:
    1. Subscribe to match_events (Redis)
    2. For each event: build OddsEvent -> compute obs -> agent.predict -> engine.execute_action
    3. Persist bets to virtual_bets
    4. Subscribe to match_results and settle open bets
    """

    def __init__(
        self,
        agent: Any,
        initial_balance: float = 100_000.0,
        agent_version: str = "0",
        on_match_settled: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
        get_agent: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._agent = agent  # Fallback if get_agent not provided
        self._get_agent = get_agent
        self._agent_version = agent_version
        self._on_match_settled = on_match_settled
        self._portfolio = PortfolioManager(initial_balance=initial_balance)
        self._risk_manager = RiskManager(self._portfolio)
        self._engine = VirtualBetEngine(self._portfolio, slippage_pct=0.005, rejection_rate=0.05)
        self._settlement = SettlementEngine(self._portfolio)
        self._feature_pipeline = FeaturePipeline()
        self._feature_store = FeatureStore()
        self._running = False
        self._pubsub: Optional[Any] = None

        # Map match_id -> list of (VirtualBet, placed_at) for settlement
        self._open_bets: dict[str, list[tuple[VirtualBet, datetime]]] = {}
        # Pending bet tasks (for cancellation on stop)
        self._pending_bet_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start the live trading loop (subscribe and process)."""
        self._running = True
        logger.info("live_trading_loop_started", agent_version=self._agent_version)

        # Rebuild engine per-match state from DB (bet counts, stakes)
        await self._engine.rebuild_state_from_db()

        redis = await get_redis()
        self._pubsub = await redis.subscribe(CHANNEL_MATCH_EVENTS, CHANNEL_MATCH_RESULTS)

        try:
            async for raw_message in self._pubsub.listen():
                if not self._running:
                    break
                if raw_message.get("type") != "message":
                    continue

                channel = raw_message.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")
                data = raw_message.get("data")
                if not data:
                    continue

                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                try:
                    if channel == CHANNEL_MATCH_EVENTS:
                        await self._handle_match_event(data)
                    elif channel == CHANNEL_MATCH_RESULTS:
                        await self._handle_match_result(data)
                except Exception as e:
                    logger.error("live_trading_loop_error", error=str(e))

        except asyncio.CancelledError:
            pass
        finally:
            logger.info("live_trading_loop_stopped")

    async def stop(self) -> None:
        """Stop the loop."""
        self._running = False
        # Cancel pending bet tasks
        for t in list(self._pending_bet_tasks):
            t.cancel()
        if self._pending_bet_tasks:
            await asyncio.gather(*self._pending_bet_tasks, return_exceptions=True)
        if self._pubsub:
            await self._pubsub.close()

    async def _delayed_persist_bet(
        self, bet: VirtualBet, match_id: str, action_name: str
    ) -> None:
        """Apply anti-detection delay then persist bet."""
        delay = random.uniform(BET_DELAY_MIN_SECONDS, BET_DELAY_MAX_SECONDS)
        await asyncio.sleep(delay)
        if not self._running:
            return
        bet.agent_version = self._agent_version
        if match_id not in self._open_bets:
            self._open_bets[match_id] = []
        self._open_bets[match_id].append((bet, bet.placed_at))
        await persist_bet(bet, agent_version=self._agent_version)
        logger.info(
            "virtual_bet_placed",
            match_id=match_id,
            action=action_name,
            odds=bet.odds,
            stake=round(bet.stake, 2),
        )

    async def _handle_match_event(self, data: dict[str, Any]) -> None:
        """Process a match event: compute obs, predict, place bet, persist."""
        try:
            event = OddsEvent.from_redis_data(data)
            if event is None:
                return

            # LIVE GATE: only trade on live matches
            if not event.is_live:
                return

            # Watch mode: only process events for the user-selected match
            redis = await get_redis()
            watched_id = await redis.client.get(KEY_WATCHED_MATCH_ID)
            if watched_id and event.match_id != watched_id:
                return

            context = await get_match_context(event.match_id)
            portfolio = self._build_portfolio_state()
            obs = self._feature_pipeline.compute(
                match_id=event.match_id,
                event=event,
                context=context,
                portfolio=portfolio,
            )

            # Populate feature cache for advisor fast path (orchestrator._generate_signal)
            await self._feature_store.store(event.match_id, np.asarray(obs, dtype=np.float32))

            agent = self._get_agent() if self._get_agent else self._agent
            if agent is None:
                return
            action, _ = agent.predict(obs, deterministic=True)

            if action == BettingAction.HOLD:
                return

            # Risk check
            stake_pct = 0.03 if "LG" in BettingAction(action).name else 0.01
            proposed_stake = self._portfolio.state.current_balance * stake_pct
            risk = self._risk_manager.check_all(proposed_stake, event.match_id)
            if not risk["allowed"]:
                logger.debug("bet_rejected_risk", match_id=event.match_id, violations=risk["violations"])
                return

            bet = self._engine.execute_action(action, event)
            if bet is None:
                return

            self._risk_manager.record_bet()

            # Anti-detection: random delay 5-15s before placing (never bet immediately after odds change)
            task = asyncio.create_task(
                self._delayed_persist_bet(bet, event.match_id, BettingAction(action).name)
            )
            self._pending_bet_tasks.add(task)
            task.add_done_callback(self._pending_bet_tasks.discard)

        except Exception as e:
            logger.error("handle_match_event_error", error=str(e))

    async def _handle_match_result(self, data: dict[str, Any]) -> None:
        """Settle open bets when match result is published."""
        match_id = data.get("match_id", "")
        winner = data.get("winner", "")
        result_type = data.get("result_type", "win")
        team_home = data.get("team_home", "")
        team_away = data.get("team_away", "")

        if not match_id or match_id not in self._open_bets:
            return

        # Fetch closing odds for CLV calculation
        closing_home, closing_away, closing_lay_home, closing_lay_away = await self._fetch_closing_odds(match_id)

        bets_to_settle = self._open_bets.pop(match_id, [])

        for bet, placed_at in bets_to_settle:
            try:
                if result_type in ("tie", "no_result", "draw", "abandoned"):
                    self._settlement.void_bet(bet)
                    await update_settled_bet(
                        match_id=match_id,
                        placed_at=placed_at,
                        outcome="void",
                        profit_loss=0.0,
                    )
                    continue

                # Determine the correct closing odds for this bet's side
                bet_on_home = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
                )
                is_back = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
                )
                if is_back:
                    closing = closing_home if bet_on_home else closing_away
                else:
                    closing = closing_lay_home if bet_on_home else closing_lay_away

                pnl = self._settlement.settle_on_match_result(bet, winner, closing_odds=closing)

                await update_settled_bet(
                    match_id=match_id,
                    placed_at=placed_at,
                    outcome=bet.outcome.value if bet.outcome else "loss",
                    profit_loss=pnl,
                    closing_odds=bet.closing_odds,
                    clv=bet.clv,
                )

                logger.info(
                    "virtual_bet_settled",
                    match_id=match_id,
                    outcome=bet.outcome.value if bet.outcome else "unknown",
                    pnl=round(pnl, 2),
                    clv=round(bet.clv, 4) if bet.clv else None,
                )

            except Exception as e:
                logger.error("settle_bet_error", match_id=match_id, error=str(e))

        # Notify for online learning (episode replay buffer)
        if self._on_match_settled and bets_to_settle:
            loser = (
                team_away if winner == team_home else team_home
                if winner
                else ""
            )
            result_meta = {
                "winner": winner,
                "loser": loser,
                "team_home": team_home,
                "team_away": team_away,
                "result_type": result_type,
            }
            try:
                await self._on_match_settled(match_id, result_meta)
            except Exception as e:
                logger.warning("on_match_settled_error", match_id=match_id, error=str(e))


    async def _fetch_closing_odds(
        self, match_id: str,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """Fetch closing odds (last tick) for CLV calculation."""
        try:
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT back_home, back_away, lay_home, lay_away
                        FROM odds_ticks
                        WHERE match_id = :match_id
                        ORDER BY time DESC
                        LIMIT 1
                    """),
                    {"match_id": match_id},
                )
                row = result.fetchone()
                if row:
                    return (
                        float(row[0]) if row[0] is not None else None,
                        float(row[1]) if row[1] is not None else None,
                        float(row[2]) if row[2] is not None else None,
                        float(row[3]) if row[3] is not None else None,
                    )
        except Exception as e:
            logger.debug("closing_odds_fetch_error", match_id=match_id, error=str(e))
        return None, None, None, None

    def _build_portfolio_state(self) -> PortfolioState:
        """Build portfolio state for feature pipeline."""
        state = self._portfolio.state
        return PortfolioState(
            initial_balance=state.initial_balance,
            current_balance=state.current_balance,
            open_positions=state.open_positions,
            total_exposure=state.total_exposure,
            session_pnl=state.session_pnl,
            daily_pnl=state.daily_pnl,
            total_bets=state.total_bets,
            total_wins=state.total_wins,
            consecutive_streak=state.consecutive_streak,
            time_since_last_bet=state.time_since_last_bet,
        )
