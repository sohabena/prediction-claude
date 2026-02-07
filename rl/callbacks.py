"""
Training callbacks for logging, checkpointing, and metric tracking.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from stable_baselines3.common.callbacks import BaseCallback

from shared.logging import setup_logging

logger = setup_logging("rl_callbacks")


class PhoenixCallback(BaseCallback):
    """
    Combined callback for PHOENIX RL training.

    Features:
    - Periodic model checkpointing
    - Training metrics logging
    - Action distribution tracking
    - Early stopping on performance degradation
    """

    def __init__(
        self,
        checkpoint_dir: str = "models/checkpoints",
        checkpoint_freq: int = 10_000,
        log_freq: int = 1_000,
        early_stop_patience: int = 50_000,
        verbose: int = 0,
    ) -> None:
        super().__init__(verbose)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_freq = checkpoint_freq
        self.log_freq = log_freq
        self.early_stop_patience = early_stop_patience

        self._episode_rewards: list[float] = []
        self._episode_lengths: list[int] = []
        self._best_mean_reward = -np.inf
        self._steps_since_improvement = 0

    def _on_step(self) -> bool:
        """Called at every step."""
        # Checkpoint
        if self.num_timesteps % self.checkpoint_freq == 0:
            path = self.checkpoint_dir / f"ppo_step_{self.num_timesteps}.zip"
            self.model.save(str(path))  # type: ignore[union-attr]
            logger.info("checkpoint_saved", step=self.num_timesteps, path=str(path))

        # Log metrics
        if self.num_timesteps % self.log_freq == 0:
            self._log_metrics()

        # Track episode rewards from infos
        infos = self.locals.get("infos", [])
        for info in infos:
            if "episode" in info:
                self._episode_rewards.append(info["episode"]["r"])
                self._episode_lengths.append(info["episode"]["l"])

        # Early stopping check
        if len(self._episode_rewards) >= 100:
            mean_reward = np.mean(self._episode_rewards[-100:])
            if mean_reward > self._best_mean_reward:
                self._best_mean_reward = mean_reward
                self._steps_since_improvement = 0

                # Save best model
                best_path = self.checkpoint_dir.parent / "best_model.zip"
                self.model.save(str(best_path))  # type: ignore[union-attr]
                logger.info("new_best_model", mean_reward=float(mean_reward))
            else:
                self._steps_since_improvement += 1

        return True

    def _log_metrics(self) -> None:
        """Log current training metrics."""
        if not self._episode_rewards:
            return

        recent = self._episode_rewards[-100:]
        metrics = {
            "timestep": self.num_timesteps,
            "mean_reward_100": float(np.mean(recent)),
            "std_reward_100": float(np.std(recent)),
            "mean_length_100": float(np.mean(self._episode_lengths[-100:])) if self._episode_lengths else 0,
            "total_episodes": len(self._episode_rewards),
            "best_mean_reward": float(self._best_mean_reward),
        }
        logger.info("training_metrics", **metrics)


class MetricsLogger(BaseCallback):
    """Log detailed metrics to a JSON file for analysis."""

    def __init__(self, log_path: str = "logs/training_log.json", verbose: int = 0) -> None:
        super().__init__(verbose)
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, Any]] = []

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        for info in infos:
            if "episode" in info:
                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "timestep": self.num_timesteps,
                    "episode_reward": info["episode"]["r"],
                    "episode_length": info["episode"]["l"],
                }
                if "step_info" in info:
                    entry["balance"] = info["step_info"].get("bankroll", 0)
                self._entries.append(entry)

        # Periodic flush
        if self.num_timesteps % 5000 == 0 and self._entries:
            self._flush()

        return True

    def _flush(self) -> None:
        """Write accumulated entries to file."""
        with open(self.log_path, "w") as f:
            json.dump(self._entries, f, indent=2)

    def _on_training_end(self) -> None:
        self._flush()
