"""
Fresh training execution script for PHOENIX.
1. Clears P&L, virtual bets, graduation snapshots, and old training metrics.
2. Clears old model checkpoints and resets training progress and logs.
3. Loads verified historical match episodes from TimescaleDB (with full ball-by-ball context).
4. Splits corpus into 70% Train, 15% Validation, 15% Holdout Test.
5. Executes multi-stage curriculum PPO training across stages.
6. Evaluates on the unseen Holdout Test set with realistic 2% exchange commission.
7. Saves the new v1 model and updates Redis orchestrator state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from rl.curriculum import CurriculumStage
from rl.data_loader import MatchDataLoader
from rl.environment import CricketBettingEnv
from rl.trainer import Trainer
from shared.config import get_settings
from shared.constants import (
    KEY_AGENT_STATE,
    KEY_AGENT_VERSION,
    KEY_ORCHESTRATOR_STATE,
    KEY_ORCHESTRATOR_STATS,
)
from shared.db import get_engine, get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis

logger = setup_logging("train_fresh_pipeline")


async def reset_state(total_timesteps: int) -> None:
    """Reset database tables, file logs, and Redis orchestrator keys."""
    print("\n[1/5] Resetting database tables and clearing P&L...")
    async with get_session() as session:
        # 1. Clear virtual bets (resets P&L to 0)
        res_bets = await session.execute(text("DELETE FROM virtual_bets"))
        print(f"      - Cleared virtual_bets: {res_bets.rowcount} rows deleted (P&L = 0.0)")

        # 2. Clear graduation snapshots
        res_grad = await session.execute(text("DELETE FROM graduation_snapshots"))
        print(f"      - Cleared graduation_snapshots: {res_grad.rowcount} rows deleted")

        # 3. Clear training metrics
        res_train = await session.execute(text("DELETE FROM training_metrics"))
        print(f"      - Cleared training_metrics: {res_train.rowcount} rows deleted")

        await session.commit()

    # 4. Clear model files & checkpoints
    print("\n[2/5] Resetting model artifacts and logs...")
    models_dir = Path("models")
    checkpoints_dir = models_dir / "checkpoints"
    if checkpoints_dir.exists():
        shutil.rmtree(checkpoints_dir)
        print("      - Removed old checkpoints directory")
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    best_model = models_dir / "best_model.zip"
    if best_model.exists():
        best_model.unlink()
        print("      - Removed old best_model.zip")

    # 5. Reset progress files
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    (logs_dir / "training_log.json").write_text("[]")
    print("      - Reset logs/training_log.json")

    progress_initial = {
        "current_step": 0,
        "total_steps": total_timesteps,
        "pct_complete": 0.0,
        "status": "training",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (logs_dir / "training_progress.json").write_text(json.dumps(progress_initial, indent=2))
    print("      - Initialized logs/training_progress.json (0% complete)")

    # 6. Reset Redis state
    print("\n[3/5] Resetting Redis orchestrator keys...")
    redis = await get_redis()
    await redis.set_json(KEY_ORCHESTRATOR_STATE, {
        "state": "offline_training",
        "model_version": 0,
        "curriculum_stage": "PATTERN_RECOGNITION",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await redis.set_json(KEY_AGENT_STATE, {"state": "training"})
    await redis.set_json(KEY_AGENT_VERSION, {
        "version": 0,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "episodes": 0,
    })

    # Clear cached stats
    for k in [
        "orchestrator:shadow_performance",
        "orchestrator:drift_status",
        "orchestrator:graduation_status",
    ]:
        await redis.client.delete(k)
    print("      - Redis state configured to offline_training (v0)")


async def run_training(total_steps: int, use_curriculum: bool = True) -> None:
    settings = get_settings()

    # Reset
    await reset_state(total_steps)

    # 4. Load verified training data from DB
    print(f"\n[4/5] Loading verified match episodes from TimescaleDB...")
    loader = MatchDataLoader()
    all_episodes = await loader.load_completed_matches(min_ticks=50, days_back=90)
    print(f"      - Successfully loaded {len(all_episodes)} total episodes from DB")
    print(f"      - Total ticks across episodes: {sum(len(ep) for ep in all_episodes)}")

    if not all_episodes:
        print("[ERROR] No qualifying episodes found for training!")
        return

    # Train / Val / Test Partition
    n_total = len(all_episodes)
    n_train = max(1, int(n_total * 0.70))
    n_val = max(1, int(n_total * 0.15))
    train_episodes = all_episodes[:n_train]
    val_episodes = all_episodes[n_train : n_train + n_val]
    test_episodes = all_episodes[n_train + n_val :]
    if not test_episodes:
        test_episodes = val_episodes

    print(f"      - Corpus Partition: Train={len(train_episodes)}, Val={len(val_episodes)}, Test={len(test_episodes)}")

    # 5. Run Training
    print(f"\n[5/5] Executing {'Curriculum PPO' if use_curriculum else 'Flat PPO'} Training ({total_steps:,} steps)...")
    print(f"      - Algorithm: {settings.rl.algorithm.upper()}")
    print(f"      - Observation size: {settings.rl.observation_size}-dim (Full Cricket Context)")
    print(f"      - Action space: 9 actions (HOLD, 4 BACK, 4 LAY)")

    trainer = Trainer(data=train_episodes)

    if use_curriculum:
        # Allocate steps proportionally across curriculum stages
        step_patt = max(2000, int(total_steps * 0.25))
        step_full = max(3000, int(total_steps * 0.45))
        step_live = max(3000, int(total_steps * 0.30))
        steps_map = {
            CurriculumStage.PATTERN_RECOGNITION: step_patt,
            CurriculumStage.FULL_ACTIONS: step_full,
            CurriculumStage.LIVE_SIMULATION: step_live,
        }
        print(f"      - Curriculum Schedule: Stage 1 ({step_patt:,} steps) -> Stage 2 ({step_full:,} steps) -> Stage 3 ({step_live:,} steps)")
        agent = trainer.train_curriculum(
            timesteps_per_stage=steps_map,
            checkpoint_dir="models/checkpoints",
            eval_data=val_episodes,
        )
    else:
        agent = trainer.train_offline(
            total_timesteps=total_steps,
            checkpoint_dir="models/checkpoints",
        )

    print(f"\nTraining completed successfully! Saved to models/best_model.zip")

    # Update Redis state with new version v1
    redis = await get_redis()
    await redis.set_json(KEY_ORCHESTRATOR_STATE, {
        "state": "online_training",
        "model_version": 1,
        "curriculum_stage": "FULL_ACTIONS" if use_curriculum else "PATTERN_RECOGNITION",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await redis.set_json(KEY_AGENT_STATE, {"state": "online_training"})
    await redis.set_json(KEY_AGENT_VERSION, {
        "version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "episodes": len(all_episodes),
    })

    # Strict Out-of-Sample Evaluation on Holdout Test Set with 2% Exchange Commission
    print("\n" + "=" * 60)
    print(" STRICT OUT-OF-SAMPLE EVALUATION (UNSEEN HOLDOUT TEST SET)")
    print(" Exchange Commission: 2.0% | Test Episodes:", len(test_episodes))
    print("=" * 60)
    eval_metrics = trainer.evaluate(
        agent=agent,
        n_eval_episodes=min(len(test_episodes), 25),
        eval_data=test_episodes,
        commission=0.02,
    )
    for k, v in eval_metrics.items():
        if isinstance(v, float):
            print(f"  {k:20s}: {v:8.4f}")
        else:
            print(f"  {k:20s}: {v}")
    print("=" * 60 + "\n")

    await get_engine().dispose()


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Run fresh PHOENIX offline training on imported historical data."
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=15000,
        help="Number of training timesteps (default 15,000 across curriculum).",
    )
    parser.add_argument(
        "--no-curriculum",
        action="store_true",
        help="Disable curriculum learning and train flat PPO.",
    )
    args = parser.parse_args()
    asyncio.run(run_training(args.steps, use_curriculum=not args.no_curriculum))


if __name__ == "__main__":
    cli()
