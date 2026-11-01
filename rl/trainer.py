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
from rl.callbacks import DatabaseMetricsCallback, MetricsLogger, PhoenixCallback, TrainingProgressCallback
from rl.curriculum import CurriculumManager, CurriculumStage
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

        # Create environment with curriculum stage data and action mask
        stage_data = self._curriculum.get_stage_data(self._data)
        self._env = self.create_env(stage_data)
        self._env.set_allowed_actions(self._curriculum.get_allowed_actions())

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
            TrainingProgressCallback(total_timesteps=timesteps),
            MetricsLogger(),
            DatabaseMetricsCallback(
                flush_freq=5_000,
                agent_version=str(getattr(self, '_model_version', '')),
            ),
        ]

        # Train
        self._agent.learn(total_timesteps=timesteps, callback=callbacks)

        # Save final model to checkpoint dir
        save_path = Path(checkpoint_dir) / f"ppo_offline_{timesteps}.zip"
        self._agent.save(save_path)

        # Also save to the configured model_path so live trading / incremental
        # training can locate the latest model reliably.
        best_path = Path(self.settings.rl.model_path)
        best_path.parent.mkdir(parents=True, exist_ok=True)
        self._agent.save(best_path)

        logger.info("offline_training_completed", path=str(save_path), best=str(best_path))
        return self._agent


    def train_curriculum(
        self,
        stages: Optional[list[CurriculumStage]] = None,
        timesteps_per_stage: Optional[dict[CurriculumStage, int]] = None,
        checkpoint_dir: str = "models/checkpoints",
        eval_data: Optional[list[list[dict[str, Any]]]] = None,
    ) -> PhoenixAgent:
        """
        Progressive multi-stage curriculum training.
        
        Stages:
        1. PATTERN_RECOGNITION: Actions masked to HOLD and small BACK. Learn patience.
        2. FULL_ACTIONS: All 9 actions enabled. Directional value trading.
        3. LIVE_SIMULATION: Realistic execution with exchange commission.
        """
        stages = stages or [
            CurriculumStage.PATTERN_RECOGNITION,
            CurriculumStage.FULL_ACTIONS,
            CurriculumStage.LIVE_SIMULATION,
        ]
        default_steps = {
            CurriculumStage.PATTERN_RECOGNITION: 5_000,
            CurriculumStage.FULL_ACTIONS: 10_000,
            CurriculumStage.LIVE_SIMULATION: 10_000,
            CurriculumStage.ADVERSARIAL: 5_000,
        }
        steps_map = timesteps_per_stage or default_steps

        # Create base environment
        self._env = self.create_env(self._data)

        # Initialize agent if needed
        if self._agent is None:
            if self._algorithm == "dqn":
                self._agent = PhoenixDQNAgent(
                    env=self._env,
                    learning_rate=self.settings.rl.learning_rate,
                    batch_size=self.settings.rl.batch_size,
                )
            else:
                self._agent = PhoenixAgent(
                    env=self._env,
                    learning_rate=self.settings.rl.learning_rate,
                    n_steps=self.settings.rl.n_steps,
                    batch_size=self.settings.rl.batch_size,
                )

        total_elapsed_timesteps = 0
        stage_results: dict[str, Any] = {}

        for stage in stages:
            stage_steps = steps_map.get(stage, 5_000)
            self._curriculum.current_stage = stage
            allowed_actions = self._curriculum.get_allowed_actions()
            self._env.set_allowed_actions(allowed_actions)
            
            logger.info(
                "curriculum_stage_started",
                stage=stage.name,
                timesteps=stage_steps,
                allowed_actions=allowed_actions,
            )

            # Stage callbacks
            callbacks = [
                TrainingProgressCallback(total_timesteps=stage_steps),
                MetricsLogger(),
            ]

            # Train stage
            self._agent.learn(
                total_timesteps=stage_steps,
                callback=callbacks,
                progress_bar=False,
                reset_num_timesteps=False,
            )
            total_elapsed_timesteps += stage_steps

            # Checkpoint per stage
            stage_ckpt = Path(checkpoint_dir) / f"ppo_stage_{stage.name.lower()}_{stage_steps}.zip"
            stage_ckpt.parent.mkdir(parents=True, exist_ok=True)
            self._agent.save(stage_ckpt)

            # Evaluate if eval_data provided
            if eval_data:
                eval_metrics = self.evaluate(
                    agent=self._agent,
                    eval_data=eval_data,
                    n_eval_episodes=min(len(eval_data), 20),
                )
                stage_results[stage.name] = eval_metrics
                logger.info("curriculum_stage_evaluated", stage=stage.name, **eval_metrics)

        # Save final best model
        best_path = Path(self.settings.rl.model_path)
        best_path.parent.mkdir(parents=True, exist_ok=True)
        self._agent.save(best_path)
        logger.info("curriculum_training_completed", best_path=str(best_path))

        return self._agent

    def evaluate(
        self,
        agent: Optional[PhoenixAgent] = None,
        n_eval_episodes: int = 50,
        eval_data: Optional[list[list[dict[str, Any]]]] = None,
        commission: float = 0.02,
    ) -> dict[str, float]:
        """
        Evaluate agent performance.

        Returns:
            Dict with mean_reward, std_reward, win_rate, roi, and custom metrics.
        """
        agent = agent or self._agent
        if agent is None:
            raise ValueError("No agent to evaluate")

        eval_data_to_use = eval_data or self._data
        eval_env = CricketBettingEnv(
            data=eval_data_to_use,
            initial_bankroll=float(self.settings.rl.starting_bankroll),
            settlement_mode="real",
            exchange_commission=commission,
        )
        mean_reward, std_reward = evaluate_policy(
            agent.model,
            eval_env,
            n_eval_episodes=n_eval_episodes,
            deterministic=True,
        )

        # Run custom eval to collect win_rate and roi from episode outcomes
        win_rate, roi = self._eval_episode_stats(eval_env, agent, n_eval_episodes)

        metrics = {
            "mean_reward": float(mean_reward),
            "std_reward": float(std_reward),
            "win_rate": float(win_rate),
            "roi": float(roi),
            "n_episodes": n_eval_episodes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("evaluation_completed", **metrics)
        return metrics

    def _eval_episode_stats(
        self,
        env: CricketBettingEnv,
        agent: "PhoenixAgent | PhoenixDQNAgent",
        n_episodes: int,
    ) -> tuple[float, float]:
        """Run episodes and collect win_rate and roi from portfolio state."""
        total_wins = 0
        total_bets = 0
        total_pnl = 0.0
        total_stake = 0.0

        for _ in range(n_episodes):
            obs, _ = env.reset()
            done = False
            while not done:
                action, _ = agent.predict(obs, deterministic=True)
                obs, _, terminated, truncated, info = env.step(action)
                done = terminated or truncated
            # After episode, collect from this episode's portfolio
            if hasattr(env, "_portfolio") and env._portfolio:
                p = env._portfolio
                ep_wins = getattr(p, "total_wins", 0)
                ep_bets = getattr(p, "total_bets", 0)
                ep_pnl = getattr(p, "session_pnl", 0.0)
                total_wins += ep_wins
                total_bets += ep_bets
                total_pnl += ep_pnl
                # Stake: each bet is ~1-3% of bankroll
                total_stake += max(ep_bets * env.initial_bankroll * 0.02, 1e-6)

        win_rate = total_wins / max(total_bets, 1)
        roi = total_pnl / max(total_stake, 1e-6)
        return win_rate, roi

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
        settings = get_settings()
        if settings.rl.algorithm == "dqn":
            self._agent = PhoenixDQNAgent.load(model_path, self._env)
        else:
            self._agent = PhoenixAgent.load(model_path, self._env)

        callbacks = [
            PhoenixCallback(checkpoint_freq=5_000),
            MetricsLogger(log_path="logs/finetune_log.json"),
        ]

        self._agent.learn(total_timesteps=total_timesteps, callback=callbacks)

        # Save to configured model_path so orchestrator/live trading loads the
        # updated model after restart (mirrors train_offline behaviour).
        best_path = Path(self.settings.rl.model_path)
        best_path.parent.mkdir(parents=True, exist_ok=True)
        self._agent.save(best_path)
        logger.info("incremental_model_saved", path=str(best_path))

        return self._agent
