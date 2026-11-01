"""
Autonomous training orchestrator.
Manages the full RL lifecycle as a background service:

  ACCUMULATING -> OFFLINE_TRAINING -> ONLINE_TRAINING -> VIRTUAL_TRADING -> GRADUATED

Runs 24/7, checks state transitions, performs nightly retraining,
and notifies the dashboard on graduation.

Refactored: domain logic extracted into rl/lifecycle/ handlers.
Orchestrator is now a ~350-line state machine coordinator.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    pass

from rl.curriculum import CurriculumManager, CurriculumStage
from rl.data_loader import MatchDataLoader
from rl.graduation import GraduationEvaluator
from rl.lifecycle.advisor_handler import AdvisorHandler
from rl.lifecycle.drift_handler import DriftHandler
from rl.lifecycle.graduation_handler import GraduationHandler
from rl.lifecycle.training_handler import TrainingHandler
from rl.trainer import Trainer
from shared.config import get_settings
from shared.constants import (
    KEY_AGENT_STATE,
    KEY_AGENT_VERSION,
    KEY_ORCHESTRATOR_STATE,
    KEY_ORCHESTRATOR_STATS,
    ORCHESTRATOR_STATE_ACCUMULATING,
    ORCHESTRATOR_STATE_OFFLINE_TRAINING,
    ORCHESTRATOR_STATE_ONLINE_TRAINING,
    ORCHESTRATOR_STATE_VIRTUAL_TRADING,
    ORCHESTRATOR_STATE_GRADUATED,
)
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from features.feature_cache_consumer import FeatureCacheConsumer
from rl.episode_replay_buffer import EpisodeReplayBuffer
from virtual_trading.live_trading_loop import LiveVirtualTradingLoop
from virtual_trading.shadow_trader import ShadowTrader

logger = setup_logging("orchestrator")

# Nightly schedule (UTC hours)
NIGHTLY_GRADUATION_CHECK_HOUR = 0


class OrchestratorState(StrEnum):
    """Lifecycle states for the autonomous training loop."""
    ACCUMULATING = ORCHESTRATOR_STATE_ACCUMULATING
    OFFLINE_TRAINING = ORCHESTRATOR_STATE_OFFLINE_TRAINING
    ONLINE_TRAINING = ORCHESTRATOR_STATE_ONLINE_TRAINING
    VIRTUAL_TRADING = ORCHESTRATOR_STATE_VIRTUAL_TRADING
    GRADUATED = ORCHESTRATOR_STATE_GRADUATED


class Orchestrator:
    """
    Central autonomous training loop — now a thin state-machine coordinator.

    Domain logic lives in rl/lifecycle/ handlers:
    - DriftHandler: performance drift detection & auto-demotion
    - GraduationHandler: graduation criteria evaluation
    - AdvisorHandler: signal generation & shadow trading
    - TrainingHandler: offline/nightly/live training & evaluation
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._state = OrchestratorState.ACCUMULATING
        self._data_loader = MatchDataLoader()
        self._trainer: Optional[Trainer] = None
        self._curriculum = CurriculumManager()
        self._graduation_eval = GraduationEvaluator()
        self._shadow_trader = ShadowTrader(
            initial_balance=float(self.settings.rl.starting_bankroll)
        )
        self._running = False
        self._model_version = 0
        self._last_nightly_date: Optional[str] = None
        self._live_trading_task: Optional[asyncio.Task] = None
        self._live_trading_loop: Optional[LiveVirtualTradingLoop] = None
        self._feature_cache_task: Optional[asyncio.Task] = None
        self._feature_cache_consumer: Optional[FeatureCacheConsumer] = None
        self._episode_replay_buffer: Optional[EpisodeReplayBuffer] = None
        self._stats: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state_transitions": [],
            "training_runs": 0,
            "eval_runs": 0,
            "matches_trained_on": 0,
        }

        # Lifecycle handlers
        self._drift_handler = DriftHandler(self._shadow_trader)
        self._graduation_handler = GraduationHandler(self._graduation_eval)
        self._advisor_handler = AdvisorHandler(self._shadow_trader)
        self._training_handler = TrainingHandler(self._data_loader, self._curriculum)

    @property
    def state(self) -> OrchestratorState:
        return self._state

    # ================================================================
    # Main Loop
    # ================================================================

    async def start(self) -> None:
        """Start the autonomous training loop."""
        logger.info("orchestrator_starting")
        self._running = True
        await self._restore_state()
        await self._shadow_trader.restore_state()
        await self._start_feature_cache_consumer()

        if self._state in {OrchestratorState.ONLINE_TRAINING, OrchestratorState.VIRTUAL_TRADING}:
            await self._start_live_trading()

        await self._publish_state()

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("orchestrator_tick_error", error=str(e), state=self._state)
                await asyncio.sleep(30)
            await asyncio.sleep(self._get_check_interval())

    async def stop(self) -> None:
        """Gracefully stop the orchestrator."""
        logger.info("orchestrator_stopping")
        self._running = False
        await self._stop_live_trading()
        await self._stop_feature_cache_consumer()
        await self._publish_state()

    async def _tick(self) -> None:
        """Single iteration of the main loop."""
        if await self._check_external_state_change():
            await self._publish_state()
            return

        now = datetime.now(timezone.utc)

        if self._state == OrchestratorState.ACCUMULATING:
            await self._handle_accumulating()
        elif self._state == OrchestratorState.OFFLINE_TRAINING:
            await self._handle_offline_training()
        elif self._state == OrchestratorState.ONLINE_TRAINING:
            await self._handle_online_training()
            await self._check_nightly_schedule(now)
        elif self._state == OrchestratorState.VIRTUAL_TRADING:
            await self._check_nightly_schedule(now)
        elif self._state == OrchestratorState.GRADUATED:
            await self._advisor_handler.run(self._trainer, self._model_version)
            await self._check_nightly_schedule(now)
            should_demote = await self._drift_handler.check(
                self._model_version, self._stats,
            )
            if should_demote:
                await self._transition_to(OrchestratorState.VIRTUAL_TRADING)

    def _get_check_interval(self) -> float:
        intervals = {
            OrchestratorState.ACCUMULATING: 300.0,
            OrchestratorState.OFFLINE_TRAINING: 10.0,
            OrchestratorState.ONLINE_TRAINING: 30.0,
            OrchestratorState.VIRTUAL_TRADING: 60.0,
            OrchestratorState.GRADUATED: 60.0,
        }
        return intervals.get(self._state, 60.0)

    # ================================================================
    # State Handlers (thin delegates)
    # ================================================================

    async def _handle_accumulating(self) -> None:
        """Monitor data accumulation until we have enough matches."""
        stats = await self._data_loader.get_accumulation_stats()
        logger.info(
            "accumulation_check",
            qualifying=stats["qualifying_matches"],
            required=stats["min_required"],
            total_ticks=stats["total_ticks"],
        )
        self._stats["accumulation"] = stats
        await self._publish_state()

        stats["min_required"] = self.settings.rl.min_matches_to_train
        stats["ready_to_train"] = (stats["qualifying_matches"] or 0) >= stats["min_required"]
        if stats["ready_to_train"]:
            await self._transition_to(OrchestratorState.OFFLINE_TRAINING)

    async def _handle_offline_training(self) -> None:
        """Run offline training on accumulated data."""
        trainer_ref = [self._trainer]
        version_ref = [self._model_version]

        success = await self._training_handler.run_offline(
            trainer_ref, version_ref, self._stats,
        )

        self._trainer = trainer_ref[0]
        self._model_version = version_ref[0]

        if success:
            await self._transition_to(OrchestratorState.ONLINE_TRAINING)
        elif not success and self._trainer is None:
            await self._transition_to(OrchestratorState.ACCUMULATING)

    async def _handle_online_training(self) -> None:
        """Online training with live data + curriculum advancement."""
        if self._trainer is None:
            logger.warning("no_trainer_for_online_training")
            await self._transition_to(OrchestratorState.OFFLINE_TRAINING)
            return

        if self.settings.rl.auto_advance_curriculum and self._curriculum.should_advance():
            old_stage = self._curriculum.current_stage
            self._curriculum.advance()
            logger.info(
                "curriculum_advanced",
                from_stage=old_stage.name,
                to_stage=self._curriculum.current_stage.name,
            )
            if self._curriculum.current_stage == CurriculumStage.ADVERSARIAL:
                if self._curriculum.should_advance():
                    await self._transition_to(OrchestratorState.VIRTUAL_TRADING)
                    return
        await self._publish_state()

    async def _check_nightly_schedule(self, now: datetime) -> None:
        """Execute nightly tasks based on UTC schedule."""
        today = now.date().isoformat()
        if self._last_nightly_date == today:
            return
        if now.hour < NIGHTLY_GRADUATION_CHECK_HOUR:
            return

        self._last_nightly_date = today
        logger.info("nightly_schedule_starting", date=today)

        if self._state in (OrchestratorState.VIRTUAL_TRADING, OrchestratorState.ONLINE_TRAINING):
            graduated = await self._graduation_handler.check()
            if graduated:
                await self._transition_to(OrchestratorState.GRADUATED)
                return

        version_ref = [self._model_version]
        await self._training_handler.run_nightly_retrain(
            self._trainer, self._stats, version_ref,
        )
        self._model_version = version_ref[0]

        await self._training_handler.run_evaluation(self._trainer, self._stats)
        logger.info("nightly_schedule_completed", date=today)

    # ================================================================
    # External State Change Detection
    # ================================================================

    async def _check_external_state_change(self) -> bool:
        """Check if an external actor changed our state in Redis."""
        try:
            redis = await get_redis()
            state_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
            if not state_data:
                return False
            redis_state_str = state_data.get("state", "")
            if redis_state_str not in [s.value for s in OrchestratorState]:
                return False
            redis_state = OrchestratorState(redis_state_str)
            if redis_state != self._state:
                old = self._state
                self._state = redis_state
                self._model_version = state_data.get("model_version", self._model_version)
                logger.info("external_state_change_detected", from_state=old, to_state=redis_state)
                _live_states = {OrchestratorState.ONLINE_TRAINING, OrchestratorState.VIRTUAL_TRADING}
                if old in _live_states and redis_state not in _live_states:
                    await self._stop_live_trading()
                if redis_state in _live_states and old not in _live_states:
                    await self._start_live_trading()
                return True
        except Exception as e:
            logger.debug("external_state_check_failed", error=str(e))
        return False

    # ================================================================
    # State Transitions
    # ================================================================

    async def _transition_to(self, new_state: OrchestratorState) -> None:
        """Transition to a new orchestrator state."""
        old_state = self._state
        self._state = new_state
        self._stats["state_transitions"].append({
            "from": old_state, "to": new_state,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("state_transition", from_state=old_state, to_state=new_state)
        await self._publish_state()

        _live_states = {OrchestratorState.ONLINE_TRAINING, OrchestratorState.VIRTUAL_TRADING}
        if old_state in _live_states and new_state not in _live_states:
            await self._stop_live_trading()
        if new_state in _live_states and old_state not in _live_states:
            await self._start_live_trading()

        if new_state == OrchestratorState.GRADUATED:
            redis = await get_redis()
            await redis.set_json(KEY_AGENT_STATE, {
                "mode": "advisor",
                "graduated_at": datetime.now(timezone.utc).isoformat(),
                "model_version": self._model_version,
            })

    # ================================================================
    # Infrastructure (live trading, feature cache, state persistence)
    # ================================================================

    async def _start_live_trading(self) -> None:
        """Start the live virtual trading loop."""
        await self._stop_live_trading()
        if self._trainer is None or self._trainer._agent is None:
            model_path = Path(self.settings.rl.model_path)
            if model_path.exists():
                self._trainer = Trainer()
                env = self._trainer.create_env()
                if self.settings.rl.algorithm == "dqn":
                    from rl.agent_dqn import PhoenixDQNAgent
                    self._trainer._agent = PhoenixDQNAgent.load(model_path, env)
                else:
                    from rl.agent import PhoenixAgent
                    self._trainer._agent = PhoenixAgent.load(model_path, env)
            if self._trainer is None or self._trainer._agent is None:
                logger.warning("live_trading_skipped_no_agent")
                return

        def _on_replay_train_ready(episodes: list) -> None:
            asyncio.get_running_loop().create_task(
                self._training_handler.run_live_retrain(
                    self._trainer, episodes, self._stats,
                    [self._model_version],
                )
            )

        self._episode_replay_buffer = EpisodeReplayBuffer(
            max_episodes=50,
            train_threshold=self.settings.rl.live_retrain_threshold,
            min_ticks_per_episode=10,
            on_train_ready=_on_replay_train_ready,
        )
        self._live_trading_loop = LiveVirtualTradingLoop(
            agent=self._trainer._agent,
            initial_balance=float(self.settings.rl.starting_bankroll),
            agent_version=str(self._model_version),
            on_match_settled=self._on_live_match_settled,
            get_agent=lambda: self._trainer._agent if self._trainer else None,
        )
        self._live_trading_task = asyncio.create_task(self._live_trading_loop.start())
        logger.info("live_trading_started")

    async def _on_live_match_settled(self, match_id: str, result_meta: dict[str, Any]) -> None:
        if not self._episode_replay_buffer:
            return
        try:
            episode = await self._data_loader.load_episode_for_settled_match(
                match_id=match_id, result_meta=result_meta,
                min_ticks=10, run_quality_gate=False,
            )
            if episode:
                self._episode_replay_buffer.add(episode, match_id=match_id)
        except Exception as e:
            logger.warning("episode_load_for_replay_failed", match_id=match_id, error=str(e))

    async def _stop_live_trading(self) -> None:
        had_loop = self._live_trading_loop is not None or self._live_trading_task is not None
        if self._live_trading_loop:
            await self._live_trading_loop.stop()
            self._live_trading_loop = None
        if self._live_trading_task:
            self._live_trading_task.cancel()
            try:
                await self._live_trading_task
            except asyncio.CancelledError:
                pass
            self._live_trading_task = None
        if had_loop:
            logger.info("live_trading_stopped")

    async def _start_feature_cache_consumer(self) -> None:
        await self._stop_feature_cache_consumer()
        self._feature_cache_consumer = FeatureCacheConsumer(
            initial_balance=float(self.settings.rl.starting_bankroll),
        )
        self._feature_cache_task = asyncio.create_task(self._feature_cache_consumer.start())
        logger.info("feature_cache_consumer_started")

    async def _stop_feature_cache_consumer(self) -> None:
        if self._feature_cache_consumer:
            await self._feature_cache_consumer.stop()
            self._feature_cache_consumer = None
        if self._feature_cache_task:
            self._feature_cache_task.cancel()
            try:
                await self._feature_cache_task
            except asyncio.CancelledError:
                pass
            self._feature_cache_task = None

    async def _publish_state(self) -> None:
        try:
            redis = await get_redis()
            await redis.set_json(KEY_ORCHESTRATOR_STATE, {
                "state": self._state,
                "model_version": self._model_version,
                "curriculum_stage": self._curriculum.current_stage.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            stats_to_save = dict(self._stats)
            if stats_to_save.get("accumulation"):
                stats_to_save["accumulation"] = dict(stats_to_save["accumulation"])
                stats_to_save["accumulation"]["min_required"] = self.settings.rl.min_matches_to_train
                qual = stats_to_save["accumulation"].get("qualifying_matches", 0)
                stats_to_save["accumulation"]["ready_to_train"] = qual >= self.settings.rl.min_matches_to_train
            await redis.set_json(KEY_ORCHESTRATOR_STATS, stats_to_save)
        except Exception as e:
            logger.error("state_publish_error", error=str(e))

    async def _restore_state(self) -> None:
        try:
            redis = await get_redis()
            state_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
            if state_data:
                saved_state = state_data.get("state")
                if saved_state in [s.value for s in OrchestratorState]:
                    self._state = OrchestratorState(saved_state)
                    self._model_version = state_data.get("model_version", 0)
                    logger.info("state_restored", state=self._state, model_version=self._model_version)
                    if self._state in (
                        OrchestratorState.ONLINE_TRAINING,
                        OrchestratorState.VIRTUAL_TRADING,
                        OrchestratorState.GRADUATED,
                    ):
                        model_path = Path(self.settings.rl.model_path)
                        if model_path.exists():
                            self._trainer = Trainer()
                            env = self._trainer.create_env()
                            if self.settings.rl.algorithm == "dqn":
                                from rl.agent_dqn import PhoenixDQNAgent
                                self._trainer._agent = PhoenixDQNAgent.load(model_path, env)
                            else:
                                from rl.agent import PhoenixAgent
                                self._trainer._agent = PhoenixAgent.load(model_path, env)
                            logger.info("trainer_and_agent_restored", model_path=str(model_path))
            stats_data = await redis.get_json(KEY_ORCHESTRATOR_STATS)
            if stats_data:
                self._stats.update(stats_data)
                if self._stats.get("accumulation"):
                    self._stats["accumulation"]["min_required"] = self.settings.rl.min_matches_to_train
                    qual = self._stats["accumulation"].get("qualifying_matches", 0)
                    self._stats["accumulation"]["ready_to_train"] = qual >= self.settings.rl.min_matches_to_train
        except Exception as e:
            logger.warning("state_restore_failed", error=str(e))


async def run_orchestrator() -> None:
    """Entry point for running the orchestrator service."""
    import signal as sig

    orchestrator = Orchestrator()
    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        asyncio.ensure_future(orchestrator.stop())

    for s in (sig.SIGTERM, sig.SIGINT):
        try:
            loop.add_signal_handler(s, _shutdown)
        except NotImplementedError:
            pass

    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(run_orchestrator())
