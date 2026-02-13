"""
Training callbacks for logging, checkpointing, and metric tracking.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from stable_baselines3.common.callbacks import BaseCallback

from shared.logging import setup_logging

logger = setup_logging("rl_callbacks")


def _iter_infos(infos: Any) -> list:
    """Yield info dicts from infos (handles list or dict from VecEnv)."""
    if isinstance(infos, dict):
        return list(infos.values())
    if isinstance(infos, (list, tuple)):
        return list(infos)
    return []


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

        # Track episode rewards from infos (VecEnv may pass dict or list)
        raw_infos = self.locals.get("infos", [])
        for info in _iter_infos(raw_infos):
            if isinstance(info, dict) and "episode" in info:
                ep = info["episode"]
                if isinstance(ep, dict) and "r" in ep:
                    self._episode_rewards.append(ep["r"])
                    self._episode_lengths.append(ep.get("l", 0))

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


class TrainingProgressCallback(BaseCallback):
    """
    Writes current training progress to a JSON file for the dashboard.
    Enables the Training page to show "X / total steps" progress.
    """

    def __init__(
        self,
        total_timesteps: int,
        progress_path: str = "logs/training_progress.json",
        update_freq: int = 5_000,
        verbose: int = 0,
    ) -> None:
        super().__init__(verbose)
        self._total_timesteps = total_timesteps
        self.progress_path = Path(progress_path)
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        self.update_freq = update_freq
        self._episode_rewards: list[float] = []

    def _on_training_start(self) -> None:
        """Write initial progress so dashboard shows training started."""
        try:
            progress = {
                "current_step": 0,
                "total_steps": self._total_timesteps,
                "pct_complete": 0.0,
                "status": "running",
                "mean_reward_100": 0.0,
                "episodes": 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.progress_path, "w") as f:
                json.dump(progress, f, indent=2)
        except OSError:
            pass

    def _on_step(self) -> bool:
        raw_infos = self.locals.get("infos", [])
        for info in _iter_infos(raw_infos):
            if isinstance(info, dict) and "episode" in info:
                ep = info["episode"]
                if isinstance(ep, dict) and "r" in ep:
                    self._episode_rewards.append(ep["r"])

        if self.num_timesteps % self.update_freq == 0:
            mean_reward = float(np.mean(self._episode_rewards[-100:])) if self._episode_rewards else 0.0
            progress = {
                "current_step": self.num_timesteps,
                "total_steps": self._total_timesteps,
                "pct_complete": round(100 * self.num_timesteps / self._total_timesteps, 1),
                "status": "running",
                "mean_reward_100": mean_reward,
                "episodes": len(self._episode_rewards),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                with open(self.progress_path, "w") as f:
                    json.dump(progress, f, indent=2)
            except OSError:
                pass

        return True

    def _on_training_end(self) -> None:
        """Mark training as complete."""
        try:
            progress = {
                "current_step": self._total_timesteps,
                "total_steps": self._total_timesteps,
                "pct_complete": 100.0,
                "status": "completed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.progress_path, "w") as f:
                json.dump(progress, f, indent=2)
        except OSError:
            pass


class DatabaseMetricsCallback(BaseCallback):
    """Write training metrics to the training_metrics DB table.

    This is the bridge between RL training and the dashboard's /training/metrics endpoint.
    """

    def __init__(
        self,
        flush_freq: int = 5_000,
        agent_version: str = "",
        verbose: int = 0,
    ) -> None:
        super().__init__(verbose)
        self.flush_freq = flush_freq
        self.agent_version = agent_version
        self._episode_rewards: list[float] = []
        self._episode_lengths: list[int] = []
        self._pending_rows: list[dict[str, Any]] = []

    def _on_step(self) -> bool:
        raw_infos = self.locals.get("infos", [])
        for info in _iter_infos(raw_infos):
            if isinstance(info, dict) and "episode" in info:
                ep = info["episode"]
                if isinstance(ep, dict) and "r" in ep:
                    self._episode_rewards.append(ep["r"])
                    self._episode_lengths.append(ep.get("l", 0))

                    # Collect per-episode row
                    recent_rewards = self._episode_rewards[-100:]
                    win_rate = 0.0
                    roi = 0.0
                    if isinstance(info.get("step_info"), dict):
                        win_rate = info["step_info"].get("win_rate", 0.0)
                        roi = info["step_info"].get("roi", 0.0)

                    self._pending_rows.append({
                        "time": datetime.now(timezone.utc),
                        "episode": len(self._episode_rewards),
                        "total_timesteps": self.num_timesteps,
                        "episode_reward": float(ep["r"]),
                        "episode_length": int(ep.get("l", 0)),
                        "win_rate": float(win_rate),
                        "roi": float(roi),
                        "sharpe_ratio": 0.0,
                        "max_drawdown": 0.0,
                        "policy_loss": 0.0,
                        "value_loss": 0.0,
                        "entropy": 0.0,
                        "agent_version": self.agent_version,
                    })

        if self.num_timesteps % self.flush_freq == 0 and self._pending_rows:
            self._flush_to_db()

        return True

    def _flush_to_db(self) -> None:
        """Flush pending rows to DB synchronously via a fresh event loop.

        Training runs in a thread pool without an asyncio event loop,
        so we always create a dedicated loop for the DB write.
        """
        rows = self._pending_rows.copy()
        self._pending_rows.clear()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._write_rows(rows))
        except Exception as e:
            logger.warning("db_metrics_flush_error", error=str(e), count=len(rows))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    @staticmethod
    async def _write_rows(rows: list[dict[str, Any]]) -> None:
        from shared.db import get_session
        from sqlalchemy import text
        try:
            async with get_session() as session:
                for row in rows:
                    await session.execute(
                        text("""
                            INSERT INTO training_metrics
                            (time, episode, total_timesteps, episode_reward, episode_length,
                             win_rate, roi, sharpe_ratio, max_drawdown,
                             policy_loss, value_loss, entropy, agent_version)
                            VALUES (:time, :episode, :total_timesteps, :episode_reward, :episode_length,
                                    :win_rate, :roi, :sharpe_ratio, :max_drawdown,
                                    :policy_loss, :value_loss, :entropy, :agent_version)
                        """),
                        row,
                    )
            logger.debug("db_metrics_flushed", count=len(rows))
        except Exception as e:
            logger.warning("db_metrics_flush_error", error=str(e), count=len(rows))

    def _on_training_end(self) -> None:
        if self._pending_rows:
            self._flush_to_db()


class MetricsLogger(BaseCallback):
    """Log detailed metrics to a JSON file for analysis."""

    def __init__(self, log_path: str = "logs/training_log.json", verbose: int = 0) -> None:
        super().__init__(verbose)
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, Any]] = []

    def _on_step(self) -> bool:
        raw_infos = self.locals.get("infos", [])
        for info in _iter_infos(raw_infos):
            if isinstance(info, dict) and "episode" in info:
                ep = info["episode"]
                if not isinstance(ep, dict) or "r" not in ep:
                    continue
                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "timestep": self.num_timesteps,
                    "episode_reward": ep["r"],
                    "episode_length": ep.get("l", 0),
                }
                if "step_info" in info and isinstance(info["step_info"], dict):
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
