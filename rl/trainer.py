"""
Training orchestrator: manages offline pre-training, online fine-tuning, and evaluation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from stable_baselines3.common.env_checker import check_env
from stable_baselines3.common.evaluation import evaluate_policy

from rl.agent import PhoenixAgent
from rl.agent_dqn import PhoenixDQNAgent
from rl.callbacks import MetricsLogger, PhoenixCallback
from rl.curriculum import CurriculumManager
from rl.environment import CricketBettingEnv
from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("rl_trainer")


class Trainer:
    """
    Orchestrates the full RL training pipeline.

    Phases:
    1. Offline Pre-Training (historical data)
    2. Online Fine-Tuning (live data, virtual bets)
    3. Evaluation and Graduation
    """

    def __init__(self, data: Optional[list[list[dict[str, Any]]]] = None) -> None:
        self.settings = get_settings()
        self._data = data or []
        self._agent: Optional[PhoenixAgent | PhoenixDQNAgent] = None
        self._env: Optional[CricketBettingEnv] = None
        self._curriculum = CurriculumManager()
        self._algorithm = self.settings.rl.algorithm  # "ppo" or "dqn"

    def create_env(
        self, data: Optional[list[dict[str, Any]]] = None
    ) -> CricketBettingEnv:
        """Create and validate a new environment."""
        env = CricketBettingEnv(
            data=data or self._data,
            initial_bankroll=float(self.settings.rl.starting_bankroll),
        )

        # Validate environment
        try:
            check_env(env, warn=True)
            logger.info("environment_validated")
        except Exception as e:
            logger.warning("env_check_warning", error=str(e))

        return env

    def train_offline(
        self,
        total_timesteps: Optional[int] = None,
        checkpoint_dir: str = "models/checkpoints",
    ) -> PhoenixAgent:
        """
        Phase 1: Offline pre-training on historical data.

        Args:
            total_timesteps: Total training steps (default from config).
            checkpoint_dir: Where to save checkpoints.

        Returns:
            Trained agent.
        """
        timesteps = total_timesteps or self.settings.rl.total_timesteps
        logger.info("offline_training_started", timesteps=timesteps)

        # Create environment with curriculum stage 1 data
        stage_data = self._curriculum.get_stage_data(self._data)
        self._env = self.create_env(stage_data)

        # Create agent based on configured algorithm
        if self._algorithm == "dqn":
            self._agent = PhoenixDQNAgent(
                env=self._env,
                learning_rate=self.settings.rl.learning_rate,
                batch_size=self.settings.rl.batch_size,
            )
            logger.info("using_dqn_agent")
        else:
            self._agent = PhoenixAgent(
                env=self._env,
                learning_rate=self.settings.rl.learning_rate,
                n_steps=self.settings.rl.n_steps,
                batch_size=self.settings.rl.batch_size,
            )
            logger.info("using_ppo_agent")

        # Callbacks
        callbacks = [
            PhoenixCallback(
                checkpoint_dir=checkpoint_dir,
                checkpoint_freq=10_000,
            ),
            MetricsLogger(),
        ]

        # Train
        self._agent.learn(total_timesteps=timesteps, callback=callbacks)

        # Save final model
        save_path = Path(checkpoint_dir) / f"ppo_offline_{timesteps}.zip"
        self._agent.save(save_path)

        logger.info("offline_training_completed", path=str(save_path))
        return self._agent

    def evaluate(
        self,
        agent: Optional[PhoenixAgent] = None,
        n_eval_episodes: int = 50,
    ) -> dict[str, float]:
        """
        Evaluate agent performance.

        Returns:
            Dict with mean_reward, std_reward, and custom metrics.
        """
        agent = agent or self._agent
        if agent is None:
            raise ValueError("No agent to evaluate")

        eval_env = self.create_env()
        mean_reward, std_reward = evaluate_policy(
            agent.model,
            eval_env,
            n_eval_episodes=n_eval_episodes,
            deterministic=True,
        )

        metrics = {
            "mean_reward": float(mean_reward),
            "std_reward": float(std_reward),
            "n_episodes": n_eval_episodes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("evaluation_completed", **metrics)
        return metrics

    def load_and_continue(
        self,
        model_path: str,
        total_timesteps: int = 100_000,
    ) -> PhoenixAgent:
        """
        Phase 2: Load a trained model and continue training on new data.

        Args:
            model_path: Path to saved model.
            total_timesteps: Additional steps to train.

        Returns:
            Fine-tuned agent.
        """
        logger.info("continuing_training", model_path=model_path, timesteps=total_timesteps)

        self._env = self.create_env()
        self._agent = PhoenixAgent.load(model_path, self._env)

        callbacks = [
            PhoenixCallback(checkpoint_freq=5_000),
            MetricsLogger(log_path="logs/finetune_log.json"),
        ]

        self._agent.learn(total_timesteps=total_timesteps, callback=callbacks)
        return self._agent
