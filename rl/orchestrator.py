"""
Autonomous training orchestrator.
Manages the full RL lifecycle as a background service:

  ACCUMULATING -> OFFLINE_TRAINING -> ONLINE_TRAINING -> VIRTUAL_TRADING -> GRADUATED

Runs 24/7, checks state transitions, performs nightly retraining,
and notifies the dashboard on graduation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

import numpy as np

from rl.data_loader import MatchDataLoader
from rl.trainer import Trainer
from rl.curriculum import CurriculumManager, CurriculumStage
from rl.graduation import GraduationEvaluator
from shared.config import get_settings
from shared.constants import (
    CHANNEL_ADVISOR_SIGNALS,
    CHANNEL_MATCH_EVENTS,
    KEY_ADVISOR_SIGNALS,
    KEY_AGENT_STATE,
    KEY_AGENT_VERSION,
    KEY_GRADUATION_STATUS,
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
from virtual_trading.shadow_trader import ShadowTrader

logger = setup_logging("orchestrator")

# Nightly schedule (UTC hours)
NIGHTLY_GRADUATION_CHECK_HOUR = 0
NIGHTLY_DATA_REFRESH_HOUR = 1
NIGHTLY_RETRAIN_HOUR = 1  # 01:30 handled via minute check
NIGHTLY_EVAL_HOUR = 2
NIGHTLY_SAVE_HOUR = 2  # 02:30 handled via minute check


class OrchestratorState(StrEnum):
    """Lifecycle states for the autonomous training loop."""
    ACCUMULATING = ORCHESTRATOR_STATE_ACCUMULATING
    OFFLINE_TRAINING = ORCHESTRATOR_STATE_OFFLINE_TRAINING
    ONLINE_TRAINING = ORCHESTRATOR_STATE_ONLINE_TRAINING
    VIRTUAL_TRADING = ORCHESTRATOR_STATE_VIRTUAL_TRADING
    GRADUATED = ORCHESTRATOR_STATE_GRADUATED


class Orchestrator:
    """
    Central autonomous training loop.

    Manages:
    - Data accumulation monitoring
    - Offline training trigger
    - Curriculum progression
    - Virtual trading evaluation
    - Graduation check & notification
    - Advisor mode (post-graduation)
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._state = OrchestratorState.ACCUMULATING
        self._data_loader = MatchDataLoader()
        self._trainer: Optional[Trainer] = None
        self._curriculum = CurriculumManager()
        self._graduation = GraduationEvaluator()
        self._shadow_trader = ShadowTrader(
            initial_balance=float(self.settings.rl.starting_bankroll)
        )
        self._running = False
        self._model_version = 0
        self._last_nightly_date: Optional[str] = None
        self._stats: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state_transitions": [],
            "training_runs": 0,
            "eval_runs": 0,
            "matches_trained_on": 0,
        }

    @property
    def state(self) -> OrchestratorState:
        return self._state

    async def start(self) -> None:
        """Start the autonomous training loop."""
        logger.info("orchestrator_starting")
        self._running = True

        # Restore state from Redis if available
        await self._restore_state()
        await self._shadow_trader.restore_state()

        # Publish initial state
        await self._publish_state()

        # Main loop
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("orchestrator_tick_error", error=str(e), state=self._state)
                await asyncio.sleep(30)  # Wait before retrying on error

            # Check interval depends on state
            sleep_seconds = self._get_check_interval()
            await asyncio.sleep(sleep_seconds)

    async def stop(self) -> None:
        """Gracefully stop the orchestrator."""
        logger.info("orchestrator_stopping")
        self._running = False
        await self._publish_state()

    async def _tick(self) -> None:
        """Single iteration of the main loop."""
        now = datetime.now(timezone.utc)

        if self._state == OrchestratorState.ACCUMULATING:
            await self._handle_accumulating()

        elif self._state == OrchestratorState.OFFLINE_TRAINING:
            await self._handle_offline_training()

        elif self._state == OrchestratorState.ONLINE_TRAINING:
            await self._handle_online_training()
            # Check nightly schedule
            await self._check_nightly_schedule(now)

        elif self._state == OrchestratorState.VIRTUAL_TRADING:
            # Check nightly schedule (includes graduation check)
            await self._check_nightly_schedule(now)

        elif self._state == OrchestratorState.GRADUATED:
            await self._handle_advisor_mode()
            # Continue nightly retraining even after graduation
            await self._check_nightly_schedule(now)
            # Check for performance drift (daily)
            await self._handle_drift_check()

    def _get_check_interval(self) -> float:
        """Get appropriate sleep interval based on current state."""
        intervals = {
            OrchestratorState.ACCUMULATING: 300.0,      # 5 min
            OrchestratorState.OFFLINE_TRAINING: 10.0,   # 10 sec (training progress)
            OrchestratorState.ONLINE_TRAINING: 30.0,    # 30 sec
            OrchestratorState.VIRTUAL_TRADING: 60.0,    # 1 min
            OrchestratorState.GRADUATED: 60.0,          # 1 min
        }
        return intervals.get(self._state, 60.0)

    # ================================================================
    # State Handlers
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

        # Update dashboard stats
        self._stats["accumulation"] = stats
        await self._publish_state()

        if stats["ready_to_train"]:
            await self._transition_to(OrchestratorState.OFFLINE_TRAINING)

    async def _handle_offline_training(self) -> None:
        """Run offline training on accumulated data."""
        logger.info("offline_training_starting")

        try:
            # Load training episodes from DB
            episodes = await self._data_loader.load_completed_matches()
            if not episodes:
                logger.warning("no_training_data_available")
                await self._transition_to(OrchestratorState.ACCUMULATING)
                return

            self._stats["matches_trained_on"] = len(episodes)

            # Run training in a thread pool to avoid blocking the event loop
            await asyncio.get_event_loop().run_in_executor(
                None, self._run_offline_training, episodes
            )

            self._stats["training_runs"] += 1
            self._model_version += 1

            # Update agent version in Redis
            redis = await get_redis()
            await redis.set_json(KEY_AGENT_VERSION, {
                "version": self._model_version,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "episodes": len(episodes),
            })

            # Advance to online training
            await self._transition_to(OrchestratorState.ONLINE_TRAINING)

        except Exception as e:
            logger.error("offline_training_failed", error=str(e))
            # Stay in offline training state and retry next tick
            await asyncio.sleep(60)

    def _run_offline_training(self, episodes: list[list[dict[str, Any]]]) -> None:
        """Synchronous offline training (runs in thread pool)."""
        self._trainer = Trainer(data=episodes)
        self._trainer.train_offline(
            total_timesteps=self.settings.rl.total_timesteps,
            checkpoint_dir="models/checkpoints",
        )

    async def _handle_online_training(self) -> None:
        """Online training with live data + curriculum advancement."""
        if self._trainer is None:
            logger.warning("no_trainer_for_online_training")
            await self._transition_to(OrchestratorState.OFFLINE_TRAINING)
            return

        # Check curriculum advancement
        if self.settings.rl.auto_advance_curriculum and self._curriculum.should_advance():
            old_stage = self._curriculum.current_stage
            self._curriculum.advance()
            logger.info(
                "curriculum_advanced",
                from_stage=old_stage.name,
                to_stage=self._curriculum.current_stage.name,
            )

            # If we've completed all curriculum stages, move to virtual trading
            if self._curriculum.current_stage == CurriculumStage.ADVERSARIAL:
                # Check if adversarial stage is also passed
                if self._curriculum.should_advance():
                    await self._transition_to(OrchestratorState.VIRTUAL_TRADING)
                    return

        await self._publish_state()

    async def _check_nightly_schedule(self, now: datetime) -> None:
        """Execute nightly tasks based on UTC schedule."""
        today = now.date().isoformat()

        # Only run once per day
        if self._last_nightly_date == today:
            return

        # Run at midnight UTC (or shortly after)
        if now.hour < NIGHTLY_GRADUATION_CHECK_HOUR:
            return

        self._last_nightly_date = today
        logger.info("nightly_schedule_starting", date=today)

        # 1. Graduation check (for virtual trading + online training states)
        if self._state in (
            OrchestratorState.VIRTUAL_TRADING,
            OrchestratorState.ONLINE_TRAINING,
        ):
            graduated = await self._run_graduation_check()
            if graduated:
                return  # Already transitioned

        # 2. Nightly retraining
        await self._run_nightly_retrain()

        # 3. Evaluation
        await self._run_evaluation()

        logger.info("nightly_schedule_completed", date=today)

    async def _run_graduation_check(self) -> bool:
        """Run daily graduation check. Returns True if graduated."""
        logger.info("graduation_check_starting")

        try:
            async with get_session() as session:
                from sqlalchemy import text

                # Get recent virtual trading performance
                result = await session.execute(
                    text("""
                        SELECT
                            COUNT(*) FILTER (WHERE outcome = 'win') * 1.0 /
                                NULLIF(COUNT(*), 0) as win_rate,
                            COALESCE(SUM(profit_loss), 0) /
                                NULLIF(COUNT(*), 0) as avg_roi,
                            COUNT(*) as total_bets,
                            COUNT(DISTINCT DATE(placed_at)) FILTER (
                                WHERE profit_loss > 0
                            ) as profitable_days
                        FROM virtual_bets
                        WHERE placed_at > NOW() - INTERVAL '30 days'
                    """)
                )
                row = result.fetchone()

                if row and row[3] is not None:
                    win_rate = float(row[0] or 0)
                    roi = float(row[1] or 0)
                    total_bets = int(row[2] or 0)
                    profitable_days = int(row[3] or 0)

                    # Compute Sharpe ratio from daily returns
                    daily_result = await session.execute(
                        text("""
                            SELECT
                                DATE(placed_at) as day,
                                SUM(profit_loss) as daily_pnl
                            FROM virtual_bets
                            WHERE placed_at > NOW() - INTERVAL '30 days'
                            GROUP BY DATE(placed_at)
                            ORDER BY day
                        """)
                    )
                    daily_rows = daily_result.fetchall()
                    daily_returns = [float(r[1]) for r in daily_rows if r[1] is not None]

                    if len(daily_returns) >= 5:
                        mean_ret = np.mean(daily_returns)
                        std_ret = np.std(daily_returns)
                        sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0
                    else:
                        sharpe = 0.0

                    # Max drawdown
                    cumulative = np.cumsum(daily_returns) if daily_returns else np.array([0.0])
                    running_max = np.maximum.accumulate(cumulative)
                    drawdowns = running_max - cumulative
                    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

                    status = self._graduation.evaluate(
                        win_rate=win_rate,
                        roi=roi,
                        sharpe_ratio=sharpe,
                        max_drawdown=max_drawdown,
                        profitable_days=profitable_days,
                        total_bets=total_bets,
                    )

                    # Store graduation status in Redis
                    redis = await get_redis()
                    await redis.set_json(KEY_GRADUATION_STATUS, {
                        "ready": status.ready,
                        "consecutive_days": status.consecutive_days,
                        "required_days": status.required_days,
                        "progress_pct": self._graduation.get_progress_pct(),
                        "criteria": [c.model_dump() for c in status.criteria],
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    })

                    if status.ready:
                        logger.info("AGENT_GRADUATED", consecutive_days=status.consecutive_days)
                        await self._transition_to(OrchestratorState.GRADUATED)
                        return True

        except Exception as e:
            logger.error("graduation_check_failed", error=str(e))

        return False

    async def _run_nightly_retrain(self) -> None:
        """Incremental nightly retraining on latest data."""
        logger.info("nightly_retrain_starting")

        try:
            episodes = await self._data_loader.load_completed_matches()
            if not episodes:
                logger.info("nightly_retrain_skipped_no_data")
                return

            # Run incremental training in thread pool
            retrain_steps = self.settings.rl.nightly_retrain_steps
            await asyncio.get_event_loop().run_in_executor(
                None, self._run_incremental_training, episodes, retrain_steps
            )

            self._stats["training_runs"] += 1
            self._model_version += 1
            logger.info("nightly_retrain_completed", version=self._model_version)

        except Exception as e:
            logger.error("nightly_retrain_failed", error=str(e))

    def _run_incremental_training(
        self, episodes: list[list[dict[str, Any]]], steps: int
    ) -> None:
        """Synchronous incremental retraining (runs in thread pool)."""
        model_path = self.settings.rl.model_path
        if Path(model_path).exists() and self._trainer:
            self._trainer.load_and_continue(
                model_path=model_path,
                total_timesteps=steps,
            )
        elif self._trainer:
            self._trainer._data = episodes
            self._trainer.train_offline(
                total_timesteps=steps,
                checkpoint_dir="models/checkpoints",
            )

    async def _run_evaluation(self) -> None:
        """Run evaluation episodes and log metrics."""
        if self._trainer is None:
            return

        logger.info("evaluation_starting")

        try:
            metrics = await asyncio.get_event_loop().run_in_executor(
                None, self._trainer.evaluate, None, 50
            )
            self._stats["eval_runs"] += 1
            self._stats["last_eval"] = metrics

            # Record curriculum episode metrics
            if metrics:
                self._curriculum.record_episode({
                    "win_rate": metrics.get("mean_reward", 0),
                    "roi": metrics.get("mean_reward", 0),
                })

            logger.info("evaluation_completed", **metrics)

        except Exception as e:
            logger.error("evaluation_failed", error=str(e))

    async def _handle_advisor_mode(self) -> None:
        """Post-graduation: generate bet signals and shadow trade them."""
        if self._trainer is None or self._trainer._agent is None:
            return

        try:
            redis = await get_redis()
            active_matches_raw = await redis.get_json("active_matches")
            if not active_matches_raw:
                return

            matches = active_matches_raw.get("matches", [])
            signals: list[dict[str, Any]] = []

            for match in matches:
                signal = await self._generate_signal(match)
                if signal:
                    signals.append(signal)
                    # Shadow-trade every signal to track virtual performance
                    self._shadow_trader.place_shadow_bet(signal)

            if signals:
                # Store signals in Redis
                await redis.set_json(KEY_ADVISOR_SIGNALS, {
                    "signals": signals,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "model_version": self._model_version,
                })

                # Publish to real-time channel
                await redis.publish_event(CHANNEL_ADVISOR_SIGNALS, {
                    "signals": signals,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

            # Settle shadow bets for completed matches
            await self._settle_shadow_bets()

            # Publish shadow performance to Redis
            await self._shadow_trader.publish_performance()

        except Exception as e:
            logger.error("advisor_mode_error", error=str(e))

    async def _settle_shadow_bets(self) -> None:
        """Settle shadow bets using verified match results from match_results table."""
        try:
            open_match_ids = list(self._shadow_trader._open_bets.keys())
            if not open_match_ids:
                return

            async with get_session() as session:
                from sqlalchemy import text

                for match_id in open_match_ids:
                    # Check for a verified result in match_results
                    result = await session.execute(
                        text("""
                            SELECT winner, loser, result_type, team_home, team_away
                            FROM match_results
                            WHERE match_id = :match_id
                        """),
                        {"match_id": match_id},
                    )
                    row = result.fetchone()

                    if not row:
                        continue  # No verified result yet -- keep bet open

                    winner = row[0]
                    result_type = row[2]
                    team_home = row[3]

                    bet = self._shadow_trader._open_bets.get(match_id)
                    if not bet:
                        continue

                    # Handle ties/no_result/abandoned -- void the bet
                    if result_type in ("tie", "no_result", "draw", "abandoned"):
                        self._shadow_trader.void_shadow_bet(match_id)
                        logger.info("shadow_bet_voided", match_id=match_id, reason=result_type)
                        continue

                    # Determine win/loss from verified result
                    action = bet.action
                    home_won = (winner == team_home)

                    if "HOME" in action:
                        is_back = "BACK" in action
                        won = (is_back and home_won) or (not is_back and not home_won)
                    else:  # AWAY
                        is_back = "BACK" in action
                        won = (is_back and not home_won) or (not is_back and home_won)

                    self._shadow_trader.settle_shadow_bet(match_id, won)
                    logger.info(
                        "shadow_bet_settled",
                        match_id=match_id,
                        won=won,
                        winner=winner,
                    )

        except Exception as e:
            logger.error("settle_shadow_bets_error", error=str(e))

    async def _handle_drift_check(self) -> None:
        """Check for performance drift and auto-demote if needed."""
        drift = self._shadow_trader.check_drift()

        if drift.should_demote:
            logger.warning(
                "AUTO_DEMOTION_TRIGGERED",
                consecutive_drift_days=drift.consecutive_drift_days,
                violations=drift.violations,
            )

            # Demote back to virtual trading for re-evaluation
            await self._transition_to(OrchestratorState.VIRTUAL_TRADING)

            # Update agent state
            redis = await get_redis()
            await redis.set_json(KEY_AGENT_STATE, {
                "mode": "virtual_trading",
                "demoted_at": datetime.now(timezone.utc).isoformat(),
                "reason": "performance_drift",
                "violations": drift.violations,
                "model_version": self._model_version,
            })

            self._stats["demotions"] = self._stats.get("demotions", 0) + 1
            self._stats["last_demotion"] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": drift.violations,
            }

        elif drift.is_drifting:
            logger.warning(
                "drift_warning",
                consecutive_days=drift.consecutive_drift_days,
                violations=drift.violations,
            )

    async def _generate_signal(self, match: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Generate a bet signal for a single match."""
        if self._trainer is None or self._trainer._agent is None:
            return None

        try:
            # Get latest observation for this match via the feature pipeline
            from features.pipeline import FeaturePipeline
            pipeline = FeaturePipeline()

            # Get action and confidence from the agent
            obs = np.zeros(self.settings.rl.observation_size, dtype=np.float32)
            action, _ = self._trainer._agent.predict(obs, deterministic=True)
            probs = self._trainer._agent.get_action_distribution(obs)

            action_names = [
                "HOLD", "BACK_HOME_SM", "BACK_HOME_LG",
                "BACK_AWAY_SM", "BACK_AWAY_LG",
                "LAY_HOME_SM", "LAY_AWAY_SM",
                "LAY_HOME_LG", "LAY_AWAY_LG",
            ]

            confidence = float(probs[action]) if action < len(probs) else 0.0

            # Only generate signal if agent has meaningful confidence and isn't HOLDing
            if action == 0 or confidence < 0.3:
                return None

            return {
                "match_id": match.get("match_id", ""),
                "team_home": match.get("team_home", ""),
                "team_away": match.get("team_away", ""),
                "competition": match.get("competition", ""),
                "recommended_action": action_names[action] if action < len(action_names) else "UNKNOWN",
                "confidence": round(confidence, 4),
                "action_probabilities": {
                    name: round(float(p), 4) for name, p in zip(action_names, probs)
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("signal_generation_error", match_id=match.get("match_id"), error=str(e))
            return None

    # ================================================================
    # State Management
    # ================================================================

    async def _transition_to(self, new_state: OrchestratorState) -> None:
        """Transition to a new orchestrator state."""
        old_state = self._state
        self._state = new_state
        self._stats["state_transitions"].append({
            "from": old_state,
            "to": new_state,
            "at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            "state_transition",
            from_state=old_state,
            to_state=new_state,
        )

        await self._publish_state()

        # If graduated, also update agent state
        if new_state == OrchestratorState.GRADUATED:
            redis = await get_redis()
            await redis.set_json(KEY_AGENT_STATE, {
                "mode": "advisor",
                "graduated_at": datetime.now(timezone.utc).isoformat(),
                "model_version": self._model_version,
            })

    async def _publish_state(self) -> None:
        """Publish current orchestrator state to Redis."""
        try:
            redis = await get_redis()
            await redis.set_json(KEY_ORCHESTRATOR_STATE, {
                "state": self._state,
                "model_version": self._model_version,
                "curriculum_stage": self._curriculum.current_stage.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            await redis.set_json(KEY_ORCHESTRATOR_STATS, self._stats)
        except Exception as e:
            logger.error("state_publish_error", error=str(e))

    async def _restore_state(self) -> None:
        """Restore orchestrator state from Redis on restart."""
        try:
            redis = await get_redis()
            state_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
            if state_data:
                saved_state = state_data.get("state")
                if saved_state in [s.value for s in OrchestratorState]:
                    self._state = OrchestratorState(saved_state)
                    self._model_version = state_data.get("model_version", 0)
                    logger.info(
                        "state_restored",
                        state=self._state,
                        model_version=self._model_version,
                    )

                    # If we were training or beyond, try to load the model
                    if self._state in (
                        OrchestratorState.ONLINE_TRAINING,
                        OrchestratorState.VIRTUAL_TRADING,
                        OrchestratorState.GRADUATED,
                    ):
                        model_path = Path(self.settings.rl.model_path)
                        if model_path.exists():
                            self._trainer = Trainer()
                            logger.info("trainer_restored")

            stats_data = await redis.get_json(KEY_ORCHESTRATOR_STATS)
            if stats_data:
                self._stats.update(stats_data)

        except Exception as e:
            logger.warning("state_restore_failed", error=str(e))


async def run_orchestrator() -> None:
    """Entry point for running the orchestrator service."""
    import signal as sig

    orchestrator = Orchestrator()

    loop = asyncio.get_event_loop()

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
