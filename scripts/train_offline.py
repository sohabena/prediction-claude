"""
Run full offline training directly (bypassing orchestrator).

This ensures:
- Fresh model (no stale weights)
- Full 500K steps
- Real match results used for settlement
- Fixed reward function applied
"""

import asyncio
import shutil
from pathlib import Path

from rl.data_loader import MatchDataLoader
from rl.trainer import Trainer
from shared.config import get_settings
from shared.logging import setup_logging

logger = setup_logging("train_offline")


async def main():
    settings = get_settings()

    # 1. Clear old models
    models_dir = Path("models")
    checkpoints_dir = models_dir / "checkpoints"
    if checkpoints_dir.exists():
        shutil.rmtree(checkpoints_dir)
        print("Cleared old checkpoints")
    best = models_dir / "best_model.zip"
    if best.exists():
        best.unlink()
        print("Cleared old best_model.zip")

    # Clear old training log
    log_path = Path("logs/training_log.json")
    if log_path.exists():
        log_path.write_text("[]")
        print("Cleared training log")

    # 2. Load data with real match results
    loader = MatchDataLoader()
    episodes = await loader.load_completed_matches()
    print(f"\nLoaded {len(episodes)} episodes, {sum(len(e) for e in episodes)} ticks")

    # Count real outcomes
    real_outcomes = sum(
        1 for ep in episodes
        if ep and "_meta" in ep[0] and ep[0]["_meta"].get("has_real_outcome")
    )
    print(f"Episodes with real outcomes: {real_outcomes}/{len(episodes)}")

    if not episodes:
        print("ERROR: No training data available!")
        return

    # 3. Train
    total_steps = settings.rl.total_timesteps
    print(f"\nStarting offline training: {total_steps} steps")
    print(f"Algorithm: {settings.rl.algorithm}")
    print(f"Observation size: {settings.rl.observation_size}")

    trainer = Trainer(data=episodes)
    agent = trainer.train_offline(
        total_timesteps=total_steps,
        checkpoint_dir="models/checkpoints",
    )

    print(f"\nTraining complete! Model saved to models/best_model.zip")

    # 4. Quick evaluation
    print("\nRunning quick evaluation (20 episodes)...")
    metrics = trainer.evaluate(agent, n_eval_episodes=20)
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
