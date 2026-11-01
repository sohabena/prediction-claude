"""
Comprehensive Benchmark & Evaluation Script for PHOENIX Offline Training.

Compares:
1. BASELINE: 15 matches, zero ball-by-ball context, in-sample evaluation, zero commission.
2. BASELINE OUT-OF-SAMPLE: Baseline model evaluated on 15 unseen holdout test matches with 2.0% commission.
3. IMPROVED: 100 matches (5 archetypes), full ball-by-ball context, 70/15/15 out-of-sample holdout test,
   with realistic exchange commission (2.0%) and execution friction.
"""

from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

# Ensure root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from scripts.generate_realistic_historical_corpus import RealisticCorpusGenerator, MatchArchetype
from rl.environment import CricketBettingEnv
from rl.agent import PhoenixAgent
from shared.constants import OBSERVATION_SIZE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("offline_benchmark")


def run_evaluation(env: CricketBettingEnv, agent: PhoenixAgent, episodes: int = 15, commission: float = 0.02) -> dict:
    """Evaluate an agent on an environment with realistic metrics."""
    rewards = []
    pnls = []
    rois = []
    win_flags = []
    total_bets_list = []
    hedged_bets_list = []
    clvs = []

    for ep in range(episodes):
        obs, info = env.reset()
        done = False
        ep_reward = 0.0
        bets_count = 0
        hedged_count = 0

        while not done:
            # Deterministic action for evaluation
            action, _ = agent.predict(obs, deterministic=True)
            action_int = int(action)
            obs, reward, terminated, truncated, step_info = env.step(action_int)
            done = terminated or truncated
            ep_reward += reward

            if action_int != 0:
                bets_count += 1
            if step_info.get("is_hedge", False):
                hedged_count += 1

        rewards.append(ep_reward)
        pnl = env._portfolio.session_pnl
        # Deduct exchange commission on positive net market winnings
        if commission > 0 and pnl > 0:
            net_pnl = pnl * (1.0 - commission)
        else:
            net_pnl = pnl
        pnls.append(net_pnl)

        roi = (net_pnl / max(env.initial_bankroll, 1.0)) * 100.0
        rois.append(roi)
        win_flags.append(1 if net_pnl > 0 else 0)
        total_bets_list.append(bets_count)
        hedged_bets_list.append(hedged_count)
        clvs.append(env._compute_open_bets_clv())

    # Compute metrics
    mean_pnl = float(np.mean(pnls))
    std_pnl = float(np.std(pnls)) if len(pnls) > 1 else 1.0
    sharpe = float((mean_pnl / std_pnl) * np.sqrt(len(pnls))) if std_pnl > 0 else 0.0

    # Max Drawdown
    cum_pnl = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cum_pnl)
    drawdowns = running_max - cum_pnl
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    win_rate = float(np.mean(win_flags) * 100.0)
    mean_roi = float(np.mean(rois))
    mean_reward = float(np.mean(rewards))
    hedge_rate = float((sum(hedged_bets_list) / max(1, sum(total_bets_list))) * 100.0)
    avg_clv = float(np.mean(clvs))

    return {
        "episodes_evaluated": episodes,
        "mean_reward": round(mean_reward, 2),
        "win_rate_pct": round(win_rate, 2),
        "mean_roi_pct": round(mean_roi, 2),
        "mean_pnl": round(mean_pnl, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "hedge_efficiency_pct": round(hedge_rate, 2),
        "avg_clv": round(avg_clv, 4),
    }


def main():
    logger.info("Starting Offline Training Benchmark & Empirical Analysis...")

    # 1. Generate 100-match Realistic Corpus
    logger.info("Generating 100-match realistic corpus across 5 archetypes with full ball-by-ball context...")
    generator = RealisticCorpusGenerator(seed=42)
    full_corpus = generator.generate_corpus(match_count=100)
    logger.info(f"Generated {len(full_corpus)} matches.")

    # Archetype breakdown
    arch_counts = {}
    for m in full_corpus:
        arch_counts[m.archetype.value] = arch_counts.get(m.archetype.value, 0) + 1
    logger.info(f"Archetype breakdown: {arch_counts}")

    # Split into 70 Train, 15 Val, 15 Holdout Test
    train_matches = full_corpus[:70]
    val_matches = full_corpus[70:85]
    test_matches = full_corpus[85:100]

    train_episodes = [m.ticks for m in train_matches]
    val_episodes = [m.ticks for m in val_matches]
    test_episodes = [m.ticks for m in test_matches]

    logger.info(f"Corpus split: Train={len(train_episodes)}, Val={len(val_episodes)}, Test={len(test_episodes)}")

    # 2. Extract baseline 15 episodes (stripped of match context to replicate previous state)
    baseline_15_episodes = []
    for m in full_corpus[:15]:
        stripped_ticks = []
        for t in m.ticks:
            # Replicate previous demo importer state (no context)
            tick_copy = {k: v for k, v in t.items() if k not in ["score", "wickets", "overs", "run_rate", "req_run_rate", "balls_remaining"]}
            stripped_ticks.append(tick_copy)
        baseline_15_episodes.append(stripped_ticks)

    # -------------------------------------------------------------
    # RUN 1: Baseline Model (15 matches, context-blind, in-sample evaluation)
    # -------------------------------------------------------------
    logger.info("--- RUN 1: Evaluating BASELINE Setup (15 matches, context-blind, in-sample) ---")
    env_baseline_train = CricketBettingEnv(
        data=baseline_15_episodes,
        settlement_mode="real",
        initial_bankroll=10000.0,
    )

    agent_baseline = PhoenixAgent(
        env=env_baseline_train,
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
    )
    logger.info("Training baseline agent for 5,000 steps...")
    agent_baseline.learn(total_timesteps=5000, progress_bar=False)

    # Evaluate Baseline In-Sample (as was previously done)
    env_baseline_insample = CricketBettingEnv(
        data=baseline_15_episodes,
        settlement_mode="real",
        initial_bankroll=10000.0,
    )
    baseline_insample_metrics = run_evaluation(env_baseline_insample, agent_baseline, episodes=15, commission=0.0)
    logger.info(f"Baseline In-Sample Metrics: {baseline_insample_metrics}")

    # Evaluate Baseline Out-of-Sample on holdout test set (stripped)
    test_stripped = []
    for m in test_matches:
        test_stripped.append([{k: v for k, v in t.items() if k not in ["score", "wickets", "overs", "run_rate", "req_run_rate", "balls_remaining"]} for t in m.ticks])
    env_baseline_oos = CricketBettingEnv(
        data=test_stripped,
        settlement_mode="real",
        initial_bankroll=10000.0,
    )
    baseline_oos_metrics = run_evaluation(env_baseline_oos, agent_baseline, episodes=15, commission=0.02)
    logger.info(f"Baseline Out-of-Sample Metrics (Realistic Friction): {baseline_oos_metrics}")

    # -------------------------------------------------------------
    # RUN 2: Improved Model (70 matches train, full cricket context, holdout test, 2% commission)
    # -------------------------------------------------------------
    logger.info("--- RUN 2: Evaluating IMPROVED Setup (70 train matches, full context, holdout test) ---")
    env_improved_train = CricketBettingEnv(
        data=train_episodes,
        settlement_mode="real",
        initial_bankroll=10000.0,
    )

    agent_improved = PhoenixAgent(
        env=env_improved_train,
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
    )
    logger.info("Training improved agent for 15,000 steps on 70 matches with full cricket context...")
    agent_improved.learn(total_timesteps=15000, progress_bar=False)

    # Evaluate Improved on Holdout Test Set (Unseen 15 matches) with 2% Commission
    env_improved_test = CricketBettingEnv(
        data=test_episodes,
        settlement_mode="real",
        initial_bankroll=10000.0,
    )
    improved_test_metrics = run_evaluation(env_improved_test, agent_improved, episodes=15, commission=0.02)
    logger.info(f"Improved Out-of-Sample Test Metrics (Realistic Friction): {improved_test_metrics}")

    # Compile benchmark results
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline_setup": {
            "dataset_size": 15,
            "has_cricket_context": False,
            "evaluation_type": "in_sample",
            "commission": 0.0,
            "metrics": baseline_insample_metrics,
        },
        "baseline_oos_breakdown": {
            "dataset_size": 15,
            "has_cricket_context": False,
            "evaluation_type": "out_of_sample_holdout",
            "commission": 0.02,
            "metrics": baseline_oos_metrics,
        },
        "improved_setup": {
            "dataset_size": 100,
            "train_size": 70,
            "val_size": 15,
            "test_size": 15,
            "has_cricket_context": True,
            "evaluation_type": "out_of_sample_holdout",
            "commission": 0.02,
            "metrics": improved_test_metrics,
        },
        "archetype_distribution": arch_counts,
    }

    out_path = Path("scratch/offline_benchmark_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Benchmark results successfully written to {out_path}")
    print("\n" + "=" * 60)
    print("BENCHMARK COMPARISON SUMMARY:")
    print("=" * 60)
    print(f"1. Previous Baseline (In-Sample, No Context, 0% Comm):")
    print(f"   Win Rate: {baseline_insample_metrics['win_rate_pct']}% | ROI: {baseline_insample_metrics['mean_roi_pct']}% | Sharpe: {baseline_insample_metrics['sharpe_ratio']}")
    print(f"2. Previous Baseline on Unseen Data (Out-of-Sample, 2% Comm):")
    print(f"   Win Rate: {baseline_oos_metrics['win_rate_pct']}% | ROI: {baseline_oos_metrics['mean_roi_pct']}% | Sharpe: {baseline_oos_metrics['sharpe_ratio']}")
    print(f"3. Improved Agent (100 Matches, Full Context, Holdout Test, 2% Comm):")
    print(f"   Win Rate: {improved_test_metrics['win_rate_pct']}% | ROI: {improved_test_metrics['mean_roi_pct']}% | Sharpe: {improved_test_metrics['sharpe_ratio']} | Hedge Efficiency: {improved_test_metrics['hedge_efficiency_pct']}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
