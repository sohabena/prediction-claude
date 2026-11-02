"""
PPO Agent wrapper for the cricket betting RL system.
Wraps Stable-Baselines3 PPO with PHOENIX-specific configuration.
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback

from shared.constants import ACTION_SPACE_SIZE, OBSERVATION_SIZE
from shared.logging import setup_logging

logger = setup_logging("rl_agent")


class PhoenixAgent:
    """
    PPO agent wrapper with PHOENIX-specific defaults.

    Handles model creation, loading, saving, and prediction.
    """

    def __init__(
        self,
        env: Any,
        learning_rate: float = 3e-4,
        n_steps: int = 2048,
        batch_size: int = 64,
        n_epochs: int = 10,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_range: float = 0.2,
        ent_coef: float = 0.01,
        vf_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        tensorboard_log: Optional[str] = None,
        device: str = "auto",
        net_arch: Optional[Any] = None,
        policy_kwargs: Optional[dict[str, Any]] = None,
        n_envs: int = 1,
        seed: Optional[int] = None,
    ) -> None:
        """``n_steps`` is the total rollout size; with ``n_envs`` > 1 each env
        collects ``n_steps // n_envs`` steps so the rollout size is preserved."""
        self._obs_norm: Optional[dict[str, Any]] = None
        pk: dict[str, Any] = {
            "net_arch": net_arch or {"pi": [256, 256, 128], "vf": [256, 256, 128]},
            "activation_fn": torch.nn.ReLU,
        }
        if policy_kwargs:
            pk.update(policy_kwargs)
        n_steps = max(1, int(n_steps) // max(1, int(n_envs)))
        self.model = PPO(
            policy="MlpPolicy",
            env=env,
            policy_kwargs=pk,
            seed=seed,
            learning_rate=learning_rate,
            n_steps=n_steps,
            batch_size=batch_size,
            n_epochs=n_epochs,
            gamma=gamma,
            gae_lambda=gae_lambda,
            clip_range=clip_range,
            ent_coef=ent_coef,
            vf_coef=vf_coef,
            max_grad_norm=max_grad_norm,
            tensorboard_log=tensorboard_log,
            verbose=1,
            device=device,
        )
        logger.info(
            "agent_created",
            obs_size=OBSERVATION_SIZE,
            action_size=ACTION_SPACE_SIZE,
            device=str(self.model.device),
        )

    def predict(
        self, observation: np.ndarray, deterministic: bool = False
    ) -> tuple[int, Optional[np.ndarray]]:
        """Predict action from observation."""
        action, states = self.model.predict(
            self._normalize(observation), deterministic=deterministic
        )
        return int(action), states

    def learn(
        self,
        total_timesteps: int,
        callback: Optional[BaseCallback] = None,
        progress_bar: bool = False,
        reset_num_timesteps: bool = False,
    ) -> None:
        """Train the agent."""
        logger.info("training_started", total_timesteps=total_timesteps)
        self.model.learn(
            total_timesteps=total_timesteps,
            callback=callback,
            progress_bar=progress_bar,
            reset_num_timesteps=reset_num_timesteps,
        )
        logger.info("training_completed", total_timesteps=total_timesteps)

    def save(self, path: str | Path) -> None:
        """Save model to disk."""
        self.model.save(str(path))
        logger.info("model_saved", path=str(path))

    def _normalize(self, observation: np.ndarray) -> np.ndarray:
        """Apply VecNormalize observation statistics (identity if none loaded)."""
        if self._obs_norm is None:
            return observation
        n = self._obs_norm
        out = (np.asarray(observation, dtype=np.float64) - n["mean"]) / np.sqrt(n["var"] + n["eps"])
        return np.clip(out, -n["clip"], n["clip"]).astype(np.float32)

    @staticmethod
    def default_normalize_path(path: str | Path) -> Path:
        """``<key>_vecnorm.pkl`` next to a model named ``<key>[_vN|_candidate].zip``."""
        p = Path(path)
        stem = re.sub(r"_(v\d+|candidate)$", "", p.stem)
        return p.with_name(f"{stem}_vecnorm.pkl")

    @classmethod
    def load(
        cls, path: str | Path, env: Any, normalize_path: Optional[str | Path] = None
    ) -> "PhoenixAgent":
        """Load a saved model; applies VecNormalize obs stats if a vecnorm file exists."""
        instance = cls.__new__(cls)
        instance._obs_norm = None
        instance.model = PPO.load(str(path), env=env)
        norm_file = Path(normalize_path) if normalize_path else cls.default_normalize_path(path)
        if norm_file.exists():
            with open(norm_file, "rb") as f:
                vn = pickle.load(f)
            if getattr(vn, "norm_obs", True):
                instance._obs_norm = {
                    "mean": np.asarray(vn.obs_rms.mean, dtype=np.float64),
                    "var": np.asarray(vn.obs_rms.var, dtype=np.float64),
                    "eps": float(vn.epsilon),
                    "clip": float(vn.clip_obs),
                }
            logger.info("vecnorm_loaded", path=str(norm_file))
        logger.info("model_loaded", path=str(path))
        return instance

    def get_action_distribution(self, observation: np.ndarray) -> np.ndarray:
        """Get the probability distribution over actions."""
        obs_tensor = torch.as_tensor(self._normalize(observation)).float().unsqueeze(0)
        obs_tensor = obs_tensor.to(self.model.device)
        with torch.no_grad():
            dist = self.model.policy.get_distribution(obs_tensor)
            probs = dist.distribution.probs.cpu().numpy().flatten()
        return probs
