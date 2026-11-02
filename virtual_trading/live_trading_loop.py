"""
Live virtual trading loop: runs the RL agent on live match events during VIRTUAL_TRADING.

Subscribes to match_events, computes observations, runs agent prediction,
places virtual bets via VirtualBetEngine, persists to DB, and settles via match_results.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
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
    STRATEGY_DEFAULT_MODE,
    STRATEGY_MODE_ENV,
)
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import BoundedDedupe, default_consumer_name, get_redis
from rl.environment import shift_price
from shared.schemas import BettingAction, MatchContext, OddsEvent, PortfolioState, VirtualBet
from virtual_trading.bet_persister import persist_bet, update_settled_bet
from virtual_trading.decision_gate import DecisionGate
from virtual_trading.engine import BetRejectedError, VirtualBetEngine
from virtual_trading.portfolio import BetProposal, PortfolioManager, bet_liability
from virtual_trading.risk_manager import RiskManager
from virtual_trading.settlement import SettlementEngine

logger = setup_logging("live_trading_loop")

MAX_FILL_SLIPPAGE_TICKS = 2          # max adverse Betfair ticks between decision and fill
CONTEXT_STALE_SECONDS = 10.0         # fallback context older than odds by more -> context_stale
WATCHED_ID_CACHE_SECONDS = 2.0       # cache for KEY_WATCHED_MATCH_ID
MODEL_RELOAD_INTERVAL_SECONDS = 300.0


def _strategy_mode() -> str:
    """``PHOENIX_STRATEGY``: "value" (manual-bet signals from the backtested strategy, default) or "ppo"."""
    raw = os.environ.get(STRATEGY_MODE_ENV, STRATEGY_DEFAULT_MODE).strip().lower() or STRATEGY_DEFAULT_MODE
    if raw not in ("value", "ppo"):
        logger.warning("strategy_mode_unknown", requested=raw, using=STRATEGY_DEFAULT_MODE)
        return STRATEGY_DEFAULT_MODE
    return raw


def _placement_delay() -> float:
    """Anti-detection delay for the legacy PPO placement path. Off unless ``PLACEMENT_DELAY_MODE=random``."""
    if os.environ.get("PLACEMENT_DELAY_MODE", "").strip().lower() == "random":
        return random.uniform(BET_DELAY_MIN_SECONDS, BET_DELAY_MAX_SECONDS)
    return 0.0


def _load_fair_models() -> dict[str, Any]:
    """Load the v2 fair-value model per format family (blocking; call via asyncio.to_thread)."""
    from strategy.fair_value import FAMILIES, FairValueModel, model_path

    models: dict[str, Any] = {}
    for family in FAMILIES:
        path = model_path(family, None, "v2")
        try:
            models[family] = FairValueModel.load(path)
        except FileNotFoundError:
            logger.warning("fair_model_missing", family=family, path=str(path))
        except Exception as e:
            logger.warning("fair_model_load_failed", family=family, error=type(e).__name__)
    return models


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
        # Completed match IDs (loaded from DB, prevents betting on finished matches)
        self._completed_match_ids: set[str] = set()
        # Latest odds per match (delayed fills re-read the price at placement time)
        self._latest_events: dict[str, OddsEvent] = {}
        self._watched_id: Optional[str] = None
        self._watched_checked_at: float = float('-inf')
        self._reload_task: Optional[asyncio.Task] = None
        # Streams are at-least-once: dedupe redelivered ticks by (match_id, timestamp)
        self._seen_ticks = BoundedDedupe()
        # Only decision ticks (ball events / over boundaries) reach features + policy, as in training
        self._decision_gate = DecisionGate()
        # S40: signal runner (value mode), built on the first decision tick; never places a real bet
        self._strategy_mode = _strategy_mode()
        self._runner: Optional[Any] = None

    async def start(self) -> None:
        """Start the live trading loop (subscribe and process)."""
        self._running = True
        logger.info("live_trading_loop_started", agent_version=self._agent_version)

        # Rebuild engine per-match state from DB (bet counts, stakes)
        await self._engine.rebuild_state_from_db()

        # Load completed match IDs from DB (Principle 4: belt-and-suspenders with scraper)
        await self._load_completed_matches()

        # Rebuild _open_bets from DB (Principle 6: survive restarts)
        await self._rebuild_open_bets()

        # Settle any orphaned pending bets that have match results in DB
        await self._settle_pending_from_db()

        # Load every specialist off the event loop (never lazily on the first tick)
        try:
            from rl.model_zoo import get_model_zoo

            versions = await asyncio.to_thread(get_model_zoo().preload)
            logger.info("model_zoo_preloaded", versions=versions)
            self._reload_task = asyncio.create_task(self._reload_models_periodically())
        except Exception as e:
            logger.warning("model_zoo_preload_failed", error=str(e))

        redis = await get_redis()

        try:
            # TICK_BUS=streams: consumer group "live_trading" (ack after the handler
            # finishes; a scheduled placement counts as finished). pubsub: legacy.
            async for msg in redis.consume_bus(
                "live_trading",
                default_consumer_name("live-"),
                [CHANNEL_MATCH_EVENTS, CHANNEL_MATCH_RESULTS],
            ):
                if not self._running:
                    break
                data = msg.data
                try:
                    if msg.channel == CHANNEL_MATCH_EVENTS:
                        key = (data.get("match_id"), data.get("timestamp"))
                        if None not in key and key in self._seen_ticks:
                            await redis.ack_bus(msg)
                            continue
                        await self._handle_match_event(data)
                        if None not in key:
                            self._seen_ticks.add(key)
                    elif msg.channel == CHANNEL_MATCH_RESULTS:
                        await self._handle_match_result(data)
                except Exception as e:
                    # Leave the message pending: XAUTOCLAIM redelivers, DLQ after 5 tries
                    logger.error("live_trading_loop_error", error=str(e))
                    continue
                await redis.ack_bus(msg)

        except asyncio.CancelledError:
            pass
        finally:
            if self._reload_task is not None:
                self._reload_task.cancel()
            logger.info("live_trading_loop_stopped")

    async def stop(self) -> None:
        """Stop the loop."""
        self._running = False
        if self._reload_task is not None:
            self._reload_task.cancel()
        # Cancel pending bet tasks (each releases its reservation on cancellation)
        for t in list(self._pending_bet_tasks):
            t.cancel()
        if self._pending_bet_tasks:
            await asyncio.gather(*self._pending_bet_tasks, return_exceptions=True)
        if self._pubsub:
            await self._pubsub.close()

    async def _reload_models_periodically(self) -> None:
        """Pick up retrained weights every MODEL_RELOAD_INTERVAL_SECONDS (off-loop)."""
        from rl.model_zoo import get_model_zoo

        while self._running:
            await asyncio.sleep(MODEL_RELOAD_INTERVAL_SECONDS)
            try:
                swapped = await get_model_zoo().reload_if_changed()
                if swapped:
                    logger.info("model_zoo_reloaded", keys=swapped)
            except Exception as e:
                logger.warning("model_zoo_reload_failed", error=str(e))

    async def _get_watched_match_id(self) -> Optional[str]:
        """Watched-match id from Redis, cached for WATCHED_ID_CACHE_SECONDS."""
        now = time.monotonic()
        if now - self._watched_checked_at >= WATCHED_ID_CACHE_SECONDS:
            redis = await get_redis()
            watched = await redis.client.get(KEY_WATCHED_MATCH_ID)
            if isinstance(watched, bytes):
                watched = watched.decode("utf-8")
            self._watched_id = watched or None
            self._watched_checked_at = now
        return self._watched_id

    async def _delayed_place_bet(
        self,
        proposal: BetProposal,
        decision_price: float,
        model_version: str,
    ) -> None:
        """Anti-detection delay, then fill at the price that exists NOW (or miss).

        The reservation is always released: committed on a fill, released on a miss,
        cancellation, stop, or if the match is no longer live.
        """
        match_id = proposal.match_id
        action_name = proposal.action.name
        committed = False
        try:
            delay = _placement_delay()
            if delay > 0:
                await asyncio.sleep(delay)
            if not self._running:
                return
            latest = self._latest_events.get(match_id)
            if latest is None or not latest.is_live or match_id in self._completed_match_ids:
                logger.info("bet_missed_match_not_live", match_id=match_id, action=action_name)
                return

            _team, latest_price = self._engine._resolve_action(proposal.action, latest)
            if not latest_price or latest_price <= 1.0 or not self._fill_acceptable(
                proposal.side, decision_price, latest_price
            ):
                logger.info(
                    "bet_missed_price_moved",
                    match_id=match_id,
                    action=action_name,
                    decision_odds=decision_price,
                    latest_odds=latest_price,
                    max_ticks=MAX_FILL_SLIPPAGE_TICKS,
                )
                return

            try:
                bet = self._engine.commit(proposal, fill_odds=latest_price)
            except BetRejectedError as e:
                logger.info("bet_rejected_at_fill", match_id=match_id, error=str(e))
                return
            committed = True

            bet.agent_version = model_version
            self._open_bets.setdefault(match_id, []).append((bet, bet.placed_at))
            await persist_bet(bet, agent_version=model_version)
            logger.info(
                "virtual_bet_placed",
                match_id=match_id,
                action=action_name,
                decision_odds=decision_price,
                fill_odds=bet.odds,
                delay_seconds=round(delay, 2),
                stake=round(bet.stake, 2),
                agent_version=model_version,
            )
        finally:
            if not committed:
                self._engine.release(proposal)

    def _schedule_placement(
        self, proposal: BetProposal, decision_price: float, model_version: str
    ) -> asyncio.Task:
        """Create the delayed-placement task; its reservation is released on completion
        or cancellation (even if cancelled before the coroutine's first step)."""
        task = asyncio.create_task(
            self._delayed_place_bet(proposal, decision_price, model_version)
        )
        self._pending_bet_tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._pending_bet_tasks.discard(t)
            # Idempotent: no-op if commit/finally already released it
            self._engine.release(proposal)

        task.add_done_callback(_done)
        return task

    @staticmethod
    def _fill_acceptable(side: str, decision_price: float, latest_price: float) -> bool:
        """True if latest_price is no worse than decision_price by > MAX_FILL_SLIPPAGE_TICKS.

        Back: lower odds are worse. Lay: higher odds are worse.
        """
        direction = -MAX_FILL_SLIPPAGE_TICKS if side == "back" else MAX_FILL_SLIPPAGE_TICKS
        limit = shift_price(decision_price, direction)
        if side == "back":
            return latest_price >= limit - 1e-9
        return latest_price <= limit + 1e-9

    async def _resolve_context(
        self, event: OddsEvent, embedded: Any
    ) -> Optional[MatchContext]:
        """Prefer the context embedded in the odds message; else fall back to the cache."""
        if embedded:
            try:
                return MatchContext.model_validate(embedded)
            except Exception as e:
                logger.debug("embedded_context_invalid", match_id=event.match_id, error=str(e))
        context = await get_match_context(event.match_id)
        if context is not None:
            try:
                age = (event.timestamp - context.timestamp).total_seconds()
                if age > CONTEXT_STALE_SECONDS:
                    logger.warning(
                        "context_stale", match_id=event.match_id, age_seconds=round(age, 1)
                    )
            except TypeError:
                pass  # naive vs aware timestamps; cannot compare
        return context

    async def _get_runner(self) -> Any:
        """Build the signal runner once. Fair models load off the event loop; a missing model skips its family."""
        if self._runner is None:
            from strategy.runner import StrategyRunner
            from strategy.signals import SignalBus
            from strategy.value_policy import ValueStrategy

            models = await asyncio.to_thread(_load_fair_models)

            def factory(tick: dict[str, Any]) -> Optional[Any]:
                model = models.get(tick.get("family"))
                return None if model is None else ValueStrategy(model)

            redis = await get_redis()
            self._runner = StrategyRunner(factory, self._engine, self._risk_manager, SignalBus(redis=redis))
            logger.info("signal_runner_ready", families=sorted(models))
        return self._runner

    @staticmethod
    def _tick_from_event(event: OddsEvent, context: Optional[MatchContext]) -> dict[str, Any]:
        """Strategy tick (backtest tick shape) from the live odds event and match context."""
        from strategy.fair_value import family_of

        ts = event.timestamp if event.timestamp.tzinfo else event.timestamp.replace(tzinfo=timezone.utc)
        tick: dict[str, Any] = {
            "match_id": event.match_id,
            "time": ts.isoformat(),
            "team_home": event.team_home,
            "team_away": event.team_away,
            "competition": event.competition,
            "is_live": event.is_live,
            "back_home": event.back_home,
            "lay_home": event.lay_home,
            "back_away": event.back_away,
            "lay_away": event.lay_away,
            "family": None,
            "match_format": None,
        }
        if context is not None and context.match_format:
            tick.update({
                "match_format": context.match_format,
                "family": family_of(context.match_format),
                "innings": context.innings,
                "wickets": context.wickets,
                "overs": context.overs,
                "score": context.score,
                "run_rate": context.run_rate,
                "req_run_rate": context.required_run_rate,
                "balls_remaining": context.balls_remaining,
                "batting_team": context.batting_team,
                "bowling_team": context.bowling_team,
            })
        return tick

    def _shadow_action(self, obs: Any) -> Optional[int]:
        """PPO's action for logging only. Never routed to a bet."""
        agent = self._get_agent() if self._get_agent else self._agent
        if agent is None:
            return None
        try:
            action, _ = agent.predict(obs, deterministic=True)
            return int(action)
        except Exception as e:
            logger.debug("shadow_predict_failed", error=str(e))
            return None

    async def _value_decision(self, event: OddsEvent, context: Optional[MatchContext], obs: Any) -> None:
        """Value mode: emit manual-bet signals and paper-track them. No real bet is placed."""
        shadow = self._shadow_action(obs)
        if shadow is not None:
            logger.info(
                "shadow_decision",
                match_id=event.match_id,
                action=BettingAction(shadow).name,
                mode="ppo_shadow",
                signalled=False,
            )
        runner = await self._get_runner()
        outcome = await runner.on_tick(self._tick_from_event(event, context), wall_now=time.time())
        for fill in outcome.paper:
            if fill.status == "filled" and fill.bet is not None:
                self._open_bets.setdefault(event.match_id, []).append((fill.bet, fill.bet.placed_at))
                await persist_bet(fill.bet, agent_version=fill.agent_version)
            logger.info(
                "paper_signal_outcome",
                match_id=event.match_id,
                side=fill.side,
                selection=fill.selection,
                status=fill.status,
                price=fill.price,
                stake=fill.stake,
                price_limit=fill.price_limit,
                signal_id=fill.signal_id,
            )

    async def _handle_match_event(self, data: dict[str, Any]) -> None:
        """Process a match event: compute obs, predict, place bet, persist."""
        try:
            t_received = time.time()
            t_start = time.perf_counter()
            embedded_context = data.get("context")
            data = {k: v for k, v in data.items() if k != "context"}
            event = OddsEvent.from_redis_data(data)
            if event is None:
                return

            # Latest odds per match, used by delayed fills
            self._latest_events[event.match_id] = event

            # LIVE GATE: only trade on live matches
            if not event.is_live:
                return

            # COMPLETED GATE: don't bet on matches that already have results
            # (Principle 4: defend at every layer, not just the scraper)
            if event.match_id in self._completed_match_ids:
                return

            # Watch mode: only process events for the user-selected match
            watched_id = await self._get_watched_match_id()
            if watched_id and event.match_id != watched_id:
                return

            context = await self._resolve_context(event, embedded_context)
            if not self._decision_gate.is_decision_tick(event, context):
                logger.debug("decision_tick_skipped", match_id=event.match_id)
                return
            portfolio = self._build_portfolio_state(event.match_id)
            position_state = self._build_position_state(event)
            obs = self._feature_pipeline.compute(
                match_id=event.match_id,
                event=event,
                context=context,
                portfolio=portfolio,
                position_state=position_state,
            )
            t_obs = time.perf_counter()

            # Populate feature cache for advisor fast path (orchestrator._generate_signal)
            await self._feature_store.store(event.match_id, np.asarray(obs, dtype=np.float32))

            # S40: value mode emits signals only; the PPO action is logged as a shadow decision
            if self._strategy_mode == "value":
                await self._value_decision(event, context, obs)
                return

            agent = self._get_agent() if self._get_agent else self._agent
            model_version = self._agent_version

            # Resolve format specialist from ModelZoo (already loaded; never loads here)
            try:
                from rl.model_zoo import get_model_zoo
                zoo = get_model_zoo()
                fmt_str = getattr(context, 'match_format', getattr(context, 'format', ''))
                comp_str = getattr(context, 'competition', '')
                spec_agent, spec_key = zoo.get_agent_for_match(
                    format_name=str(fmt_str),
                    event_name=str(comp_str),
                    match_name=f'{event.team_home} vs {event.team_away}',
                    team_home=event.team_home,
                    team_away=event.team_away,
                    allow_load=False,
                )
                if spec_agent is not None:
                    agent = spec_agent
                    model_version = zoo.get_agent_version(spec_key) or model_version
            except Exception as e:
                logger.debug('model_zoo_route_fallback', error=str(e))

            if agent is None:
                return

            # Left inline: median predict latency < 5 ms, asserted by
            # tests/unit/test_s06_live_path.py::test_predict_latency_median_under_5ms
            action, _ = agent.predict(obs, deterministic=True)
            t_action = time.perf_counter()
            logger.info(
                "tick_latency",
                match_id=event.match_id,
                t_scraped=event.timestamp.timestamp(),
                t_received=t_received,
                t_obs=t_received + (t_obs - t_start),
                t_action=t_received + (t_action - t_start),
                scraped_to_received_ms=round((t_received - event.timestamp.timestamp()) * 1000, 1),
                received_to_obs_ms=round((t_obs - t_start) * 1000, 2),
                obs_to_action_ms=round((t_action - t_obs) * 1000, 2),
                decision=True,
            )

            if action == BettingAction.HOLD:
                return

            # Get confidence (None when the agent exposes no distribution; never fabricated)
            confidence: Optional[float]
            try:
                action_probs = agent.get_action_distribution(obs)
                confidence = float(action_probs[action])
            except AttributeError:
                confidence = None

            # Size + reserve, then risk-check the actual proposal
            proposal = self._engine.propose(action, event, confidence=confidence)
            if proposal is None:
                return
            decision = self._risk_manager.check(proposal)
            if not decision.allowed:
                self._engine.release(proposal)
                logger.debug("bet_rejected_risk", match_id=event.match_id, violations=decision.violations)
                return

            _team, decision_price = self._engine._resolve_action(proposal.action, event)

            # Anti-detection: random delay 5-15s before placing; fills at the price then
            self._schedule_placement(proposal, decision_price or proposal.odds, model_version)

        except Exception as e:
            logger.error("handle_match_event_error", error=str(e))

    async def _handle_match_result(self, data: dict[str, Any]) -> None:
        """Settle open bets when match result is published."""
        match_id = data.get("match_id", "")
        winner = data.get("winner", "")
        result_type = data.get("result_type", "win")
        team_home = data.get("team_home", "")
        team_away = data.get("team_away", "")

        if not match_id:
            return

        # Mark match as completed regardless of whether we have open bets
        self._completed_match_ids.add(match_id)
        self._decision_gate.reset(match_id)

        if match_id not in self._open_bets:
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

    def _build_position_state(self, event: OddsEvent) -> dict:
        """Build position state for the feature pipeline (hedging awareness)."""
        net_home = 0.0
        net_away = 0.0
        best_back_home = 0.0
        best_back_away = 0.0
        best_lay_home = 0.0
        best_lay_away = 0.0

        for match_id, bet_list in self._open_bets.items():
            if match_id != event.match_id:
                continue
            for bet, _placed_at in bet_list:
                is_back = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
                )
                on_home = bet.action in (
                    BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
                    BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
                )
                if on_home:
                    if is_back:
                        net_home += bet.stake
                        if best_back_home <= 1.0 or bet.odds < best_back_home:
                            best_back_home = bet.odds
                    else:
                        net_home -= bet.stake
                        if best_lay_home <= 1.0 or bet.odds > best_lay_home:
                            best_lay_home = bet.odds
                else:
                    if is_back:
                        net_away += bet.stake
                        if best_back_away <= 1.0 or bet.odds < best_back_away:
                            best_back_away = bet.odds
                    else:
                        net_away -= bet.stake
                        if best_lay_away <= 1.0 or bet.odds > best_lay_away:
                            best_lay_away = bet.odds

        bankroll = self._portfolio.state.current_balance
        return {
            "net_home_exposure": net_home,
            "net_away_exposure": net_away,
            "bankroll": bankroll,
            "back_home_odds": event.back_home or 0.0,
            "lay_home_odds": event.lay_home or 0.0,
            "back_away_odds": event.back_away or 0.0,
            "lay_away_odds": event.lay_away or 0.0,
            "best_entry_home": best_back_home,
            "best_entry_away": best_back_away,
            "best_entry_lay_home": best_lay_home,
            "best_entry_lay_away": best_lay_away,
        }

    def _build_portfolio_state(self, match_id: Optional[str] = None) -> PortfolioState:
        """Portfolio state for the feature pipeline, as a per-match view.

        Training resets the bankroll for every match, so the observation must not see the
        cumulative live bankroll. Mapping (when match_id is given):
          initial_balance / current_balance -> per-match budget (minus this match's open
              liability for current_balance);
          open_positions / total_bets       -> this match's open bets;
          total_exposure                    -> this match's open liability;
          session_pnl, daily_pnl, wins, streak -> 0 (a fresh match, as in training);
          time_since_last_bet               -> unchanged (wall-clock based).
        Without match_id the cumulative state is returned (legacy behaviour).
        """
        state = self._portfolio.state
        if match_id is None:
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
        budget = max(self._engine._match_budget(), 1.0)
        bets = [b for b, _ in self._open_bets.get(match_id, [])]
        liability = sum(bet_liability(b) for b in bets)
        return PortfolioState(
            initial_balance=budget,
            current_balance=max(budget - liability, 0.0),
            open_positions=len(bets),
            total_exposure=liability,
            session_pnl=0.0,
            daily_pnl=0.0,
            total_bets=len(bets),
            total_wins=0,
            consecutive_streak=0,
            time_since_last_bet=state.time_since_last_bet,
        )

    async def _load_completed_matches(self) -> None:
        """Load completed match IDs from DB to prevent betting on finished matches.

        Principle 4: Defend Every Gate — this is a second gate independent of the scraper's
        completed-match filter. Even if the scraper incorrectly marks a finished match as
        live, the trading loop will refuse to bet on it.
        """
        try:
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(
                    text("SELECT match_id FROM match_results")
                )
                ids = {row[0] for row in result.fetchall()}
                self._completed_match_ids = ids
                if ids:
                    logger.info("completed_matches_loaded", count=len(ids))
        except Exception as e:
            logger.warning("load_completed_matches_error", error=str(e))

    async def _rebuild_open_bets(self) -> None:
        """Rebuild _open_bets dict from DB after a restart.

        Principle 6: In-Memory State Dies on Restart — without this, any bets placed
        before a restart can never be settled via the Redis pub/sub path.
        """
        try:
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT placed_at, match_id, action, team, odds, stake
                        FROM virtual_bets
                        WHERE settled_at IS NULL
                        ORDER BY placed_at ASC
                    """)
                )
                rows = result.fetchall()
                rebuilt = 0
                for row in rows:
                    placed_at, match_id, action_str, team, odds, stake = row
                    try:
                        action = BettingAction[action_str]
                    except (KeyError, ValueError):
                        continue

                    bet = VirtualBet(
                        match_id=match_id,
                        placed_at=placed_at,
                        action=action,
                        team=team,
                        odds=float(odds),
                        stake=float(stake),
                    )
                    if match_id not in self._open_bets:
                        self._open_bets[match_id] = []
                    self._open_bets[match_id].append((bet, placed_at))
                    rebuilt += 1

                if rebuilt:
                    logger.info("open_bets_rebuilt", count=rebuilt)
        except Exception as e:
            logger.warning("rebuild_open_bets_error", error=str(e))

    async def _settle_pending_from_db(self) -> None:
        """Settle orphaned pending bets using match results already in the DB.

        This handles the case where bets were placed, the loop restarted, and match
        results arrived while the loop was down. Without this, those bets stay
        PENDING forever (the bug visible in the screenshot).
        """
        try:
            from sqlalchemy import text

            async with get_session() as session:
                # Find pending bets that have a match result available
                result = await session.execute(
                    text("""
                        SELECT vb.placed_at, vb.match_id, vb.action, vb.team,
                               vb.odds, vb.stake,
                               mr.winner, mr.result_type
                        FROM virtual_bets vb
                        INNER JOIN match_results mr ON vb.match_id = mr.match_id
                        WHERE vb.settled_at IS NULL
                    """)
                )
                rows = result.fetchall()
                if not rows:
                    return

                settled_count = 0
                for row in rows:
                    placed_at, match_id, action_str, team, odds, stake, winner, result_type = row

                    if result_type in ("tie", "no_result", "draw", "abandoned"):
                        outcome = "void"
                        pnl = 0.0
                    else:
                        is_back = "BACK" in action_str
                        team_won = (str(team).strip().lower() == str(winner).strip().lower())

                        if is_back:
                            if team_won:
                                pnl = float(stake) * (float(odds) - 1.0)
                                outcome = "win"
                            else:
                                pnl = -float(stake)
                                outcome = "loss"
                        else:  # LAY
                            if team_won:
                                pnl = -float(stake) * (float(odds) - 1.0)
                                outcome = "loss"
                            else:
                                pnl = float(stake)
                                outcome = "win"

                    await session.execute(
                        text("""
                            UPDATE virtual_bets
                            SET settled_at = NOW(),
                                outcome = :outcome,
                                profit_loss = :pnl
                            WHERE match_id = :match_id
                              AND placed_at = :placed_at
                              AND settled_at IS NULL
                        """),
                        {
                            "match_id": match_id,
                            "placed_at": placed_at,
                            "outcome": outcome,
                            "pnl": pnl,
                        },
                    )
                    settled_count += 1

                    # Also mark this match as completed
                    self._completed_match_ids.add(match_id)

                    # Remove from _open_bets if present
                    if match_id in self._open_bets:
                        self._open_bets.pop(match_id, None)

                    logger.info(
                        "orphaned_bet_settled",
                        match_id=match_id,
                        outcome=outcome,
                        pnl=round(pnl, 2),
                    )

                if settled_count:
                    logger.info("pending_bets_settled_from_db", count=settled_count)
        except Exception as e:
            logger.warning("settle_pending_from_db_error", error=str(e))
