"""
DQN Agent wrapper for the cricket betting RL system.
Alternative to PPO -- may perform better for HOLD-dominant, sparse-reward problems.

Uses Stable-Baselines3 DQN with prioritized experience replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
from stable_baselines3 import DQN
from stable_baselines3.common.callbacks import BaseCallback

from shared.constants import ACTION_SPACE_SIZE, OBSERVATION_SIZE
from shared.logging import setup_logging

logger = setup_logging("rl_agent_dqn")


class PhoenixDQNAgent:
    """
    DQN agent wrapper with PHOENIX-specific defaults.

    Key advantages over PPO for this problem:
    - Prioritized experience replay emphasizes rare profitable trades
    - Off-policy learning is more sample-efficient
    - Value-based approach may handle HOLD-dominant regime better

    Key disadvantage:
    - Only supports discrete action spaces (which we have)
    - Can be less stable during training
    """

    def __init__(
        self,
        env: Any,
        learning_rate: float = 1e-4,
        buffer_size: int = 100_000,
        batch_size: int = 64,
        gamma: float = 0.99,
        tau: float = 0.005,
        exploration_fraction: float = 0.2,
        exploration_initial_eps: float = 1.0,
        exploration_final_eps: float = 0.05,
        train_freq: int = 4,
        gradient_steps: int = 1,
        target_update_interval: int = 1000,
        tensorboard_log: str = "./logs/dqn_cricket/",
        device: str = "auto",
    ) -> None:
        self.model = DQN(
            policy="MlpPolicy",
            env=env,
            policy_kwargs={
                "net_arch": [256, 256, 128],
                "activation_fn": torch.nn.ReLU,
            },
            learning_rate=learning_rate,
            buffer_size=buffer_size,
            batch_size=batch_size,
            gamma=gamma,
            tau=tau,
            exploration_fraction=exploration_fraction,
            exploration_initial_eps=exploration_initial_eps,
            exploration_final_eps=exploration_final_eps,
            train_freq=train_freq,
            gradient_steps=gradient_steps,
            target_update_interval=target_update_interval,
            tensorboard_log=tensorboard_log,
            verbose=1,
            device=device,
        )
        logger.info(
            "dqn_agent_created",
            obs_size=OBSERVATION_SIZE,
            action_size=ACTION_SPACE_SIZE,
            device=str(self.model.device),
        )

    def predict(
        self, observation: np.ndarray, deterministic: bool = False
    ) -> tuple[int, Optional[np.ndarray]]:
        """Predict action from observation."""
        action, states = self.model.predict(observation, deterministic=deterministic)
        return int(action), states

    def learn(
        self,
        total_timesteps: int,
        callback: Optional[BaseCallback] = None,
        progress_bar: bool = True,
    ) -> None:
        """Train the agent."""
        logger.info("dqn_training_started", total_timesteps=total_timesteps)
        self.model.learn(
            total_timesteps=total_timesteps,
            callback=callback,
            progress_bar=progress_bar,
        )
        logger.info("dqn_training_completed", total_timesteps=total_timesteps)

    def save(self, path: str | Path) -> None:
        """Save model to disk."""
        self.model.save(str(path))
        logger.info("dqn_model_saved", path=str(path))

    @classmethod
    def load(cls, path: str | Path, env: Any) -> "PhoenixDQNAgent":
        """Load a saved model."""
        instance = cls.__new__(cls)
        instance.model = DQN.load(str(path), env=env)
        logger.info("dqn_model_loaded", path=str(path))
        return instance

    def get_action_distribution(self, observation: np.ndarray) -> np.ndarray:
        """
        Get Q-values as a proxy for action distribution.

        For DQN, we return softmax over Q-values as pseudo-probabilities.
        """
        obs_tensor = torch.as_tensor(observation).float().unsqueeze(0)
        obs_tensor = obs_tensor.to(self.model.device)
        with torch.no_grad():
            q_values = self.model.q_net(obs_tensor).cpu().numpy().flatten()
        # Softmax to get pseudo-probabilities
        exp_q = np.exp(q_values - np.max(q_values))  # Stability
        return exp_q / exp_q.sum()
