"""Training handler — manages offline and incremental retraining.

Extracted from orchestrator._handle_offline_training,
_run_nightly_retrain, _run_incremental_training, _run_evaluation (RGV #4).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rl.curriculum import CurriculumManager
from rl.data_loader import MatchDataLoader
from rl.trainer import Trainer
from shared.config import get_settings
from shared.constants import KEY_AGENT_VERSION
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("lifecycle_training")


class TrainingHandler:
    """Manages offline, nightly, and live retraining cycles."""

    def __init__(
        self,
        data_loader: MatchDataLoader,
        curriculum: CurriculumManager,
    ) -> None:
        self._data_loader = data_loader
        self._curriculum = curriculum

    async def run_offline(
        self, trainer_ref: list, model_version_ref: list, stats: dict[str, Any],
    ) -> bool:
        """Run offline training. Returns True on success, False to retry/fallback."""
        settings = get_settings()
        logger.info("offline_training_starting")

        try:
            episodes = await self._data_loader.load_completed_matches()
            if not episodes:
                logger.warning("no_training_data_available")
                return False

            stats["matches_trained_on"] = len(episodes)

            n_train = max(1, int(len(episodes) * 0.70))
            train_episodes = episodes[:n_train]
            val_episodes = episodes[n_train:] if len(episodes) > n_train else train_episodes

            trainer = Trainer(data=train_episodes)
            if getattr(settings.rl, "use_curriculum", True) and len(episodes) >= 10:
                await asyncio.get_running_loop().run_in_executor(
                    None, trainer.train_curriculum,
                    None, None, "models/checkpoints", val_episodes,
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None, trainer.train_offline,
                    settings.rl.total_timesteps, "models/checkpoints",
                )

            trainer_ref[0] = trainer
            stats["training_runs"] += 1
            model_version_ref[0] += 1

            redis = await get_redis()
            await redis.set_json(KEY_AGENT_VERSION, {
                "version": model_version_ref[0],
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "episodes": len(episodes),
            })
            return True

        except Exception as e:
            logger.error("offline_training_failed", error=str(e))
            await asyncio.sleep(60)
            return False

    async def run_nightly_retrain(
        self, trainer: Trainer | None, stats: dict[str, Any],
        model_version_ref: list,
    ) -> None:
        """Incremental nightly retraining on latest data."""
        logger.info("nightly_retrain_starting")
        settings = get_settings()

        try:
            episodes = await self._data_loader.load_completed_matches()
            if not episodes:
                logger.info("nightly_retrain_skipped_no_data")
                return
            if trainer is None:
                return

            retrain_steps = settings.rl.nightly_retrain_steps
            await asyncio.get_running_loop().run_in_executor(
                None, self._run_incremental, trainer, episodes, retrain_steps,
            )
            stats["training_runs"] += 1
            model_version_ref[0] += 1
            logger.info("nightly_retrain_completed", version=model_version_ref[0])

        except Exception as e:
            logger.error("nightly_retrain_failed", error=str(e))

    async def run_evaluation(
        self, trainer: Trainer | None, stats: dict[str, Any],
    ) -> None:
        """Run evaluation episodes and log metrics."""
        if trainer is None:
            return
        logger.info("evaluation_starting")
        try:
            metrics = await asyncio.get_running_loop().run_in_executor(
                None, trainer.evaluate, None, 50,
            )
            stats["eval_runs"] += 1
            stats["last_eval"] = metrics
            if metrics:
                self._curriculum.record_episode({
                    "win_rate": metrics.get("win_rate", 0),
                    "roi": metrics.get("roi", 0),
                })
            logger.info("evaluation_completed", **metrics)
        except Exception as e:
            logger.error("evaluation_failed", error=str(e))

    async def run_live_retrain(
        self, trainer: Trainer | None,
        episodes: list[list[dict[str, Any]]],
        stats: dict[str, Any],
        model_version_ref: list,
    ) -> None:
        """Incremental training from live settled episodes."""
        if not trainer or not episodes:
            return
        settings = get_settings()
        logger.info("live_retrain_starting", episode_count=len(episodes))
        try:
            steps = settings.rl.live_retrain_steps
            await asyncio.get_running_loop().run_in_executor(
                None, self._run_incremental, trainer, episodes, steps,
            )
            stats["training_runs"] += 1
            model_version_ref[0] += 1
            logger.info("live_retrain_completed", version=model_version_ref[0])
        except Exception as e:
            logger.error("live_retrain_failed", error=str(e))

    @staticmethod
    def _run_incremental(
        trainer: Trainer, episodes: list[list[dict[str, Any]]], steps: int,
    ) -> None:
        """Synchronous incremental retraining (runs in thread pool)."""
        settings = get_settings()
        trainer._data = episodes
        model_path = settings.rl.model_path
        if Path(model_path).exists():
            trainer.load_and_continue(model_path=model_path, total_timesteps=steps)
        else:
            trainer.train_offline(total_timesteps=steps, checkpoint_dir="models/checkpoints")
