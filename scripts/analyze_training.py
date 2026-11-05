"""Analyze training logs and evaluate the trained model."""

import asyncio
import collections
import json

import numpy as np

from rl.agent import PhoenixAgent
from rl.environment import CricketBettingEnv
from rl.data_loader import MatchDataLoader

ACTION_NAMES = {
    0: "HOLD", 1: "BACK_HOME_SM", 2: "BACK_HOME_LG",
    3: "BACK_AWAY_SM", 4: "BACK_AWAY_LG", 5: "LAY_HOME_SM",
    6: "LAY_AWAY_SM", 7: "LAY_HOME_LG", 8: "LAY_AWAY_LG",
}


def analyze_logs():
    """Analyze training log progression."""
    with open("logs/training_log.json") as f:
        data = json.load(f)

    n = len(data)
    print(f"=== TRAINING LOG ({n} episodes) ===")
    print(f"Step range: {data[0]['timestep']} -> {data[-1]['timestep']}")

    # Show reward progression in 5 bins
    bin_size = max(n // 5, 1)
    for i in range(min(5, n)):
        start = i * bin_size
        end = min((i + 1) * bin_size, n)
        chunk = data[start:end]
        if not chunk:
            break
        rewards = [e["episode_reward"] for e in chunk]
        balances = [e["balance"] for e in chunk]
        avg_r = sum(rewards) / len(rewards)
        pos = sum(1 for r in rewards if r > 0)
        avg_b = sum(balances) / len(balances)
        print(f"  Bin {i+1}: ep {start+1:>3d}-{end:>3d}  "
              f"avg_reward={avg_r:>8.2f}  pos={100*pos/len(rewards):>5.1f}%  "
              f"avg_balance={avg_b:>10.0f}")

    # Overall stats
    all_rewards = [e["episode_reward"] for e in data]
    all_balances = [e["balance"] for e in data]
    pos_total = sum(1 for r in all_rewards if r > 0)
    print(f"\n  Overall: mean_reward={np.mean(all_rewards):.3f}  "
          f"pos={100*pos_total/n:.1f}%  "
          f"balance_range=[{min(all_balances):.0f}, {max(all_balances):.0f}]")


async def evaluate_model():
    """Evaluate trained model with real data."""
    loader = MatchDataLoader()
    episodes = await loader.load_completed_matches()
    print(f"\n=== MODEL EVALUATION ({len(episodes)} episodes, "
          f"{sum(len(e) for e in episodes)} ticks) ===")

    # Check if match results are being used
    r_count = 0
    for ep in episodes:
        if ep and "_meta" in ep[0]:
            meta = ep[0]["_meta"]
            if meta.get("has_real_outcome"):
                r_count += 1
    print(f"  Episodes with real outcomes: {r_count}/{len(episodes)}")

    env = CricketBettingEnv(data=episodes, initial_bankroll=100000.0)
    agent = PhoenixAgent.load("models/best_model.zip", env)

    action_counts = collections.Counter()
    total_steps = 0
    ep_rewards = []
    final_balances = []

    n_eval = min(20, len(episodes))
    for _ in range(n_eval):
        obs, _ = env.reset()
        done = False
        ep_reward = 0.0
        while not done:
            action, _ = agent.predict(obs, deterministic=True)
            action_counts[int(action)] += 1
            total_steps += 1
            obs, reward, terminated, truncated, info = env.step(action)
            ep_reward += reward
            done = terminated or truncated
        ep_rewards.append(ep_reward)
        if hasattr(env, "_portfolio") and env._portfolio:
            final_balances.append(env._portfolio.current_balance)

    print(f"\n  Action Distribution ({total_steps} steps):")
    for a in sorted(action_counts.keys()):
        pct = 100 * action_counts[a] / total_steps
        bar = "#" * int(pct / 2)
        print(f"    {ACTION_NAMES.get(a, f'ACT_{a}'):>16s}: {action_counts[a]:>6d} ({pct:>5.1f}%) {bar}")

    hold_pct = 100 * action_counts.get(0, 0) / max(total_steps, 1)
    bet_pct = 100 - hold_pct
    print(f"\n  HOLD rate: {hold_pct:.1f}%  |  BET rate: {bet_pct:.1f}%")

    print(f"\n  Episode Rewards:")
    print(f"    Mean: {np.mean(ep_rewards):.3f}")
    print(f"    Std:  {np.std(ep_rewards):.3f}")
    pos = sum(1 for r in ep_rewards if r > 0)
    print(f"    Positive: {pos}/{n_eval} ({100*pos/n_eval:.0f}%)")

    if final_balances:
        avg_bal = np.mean(final_balances)
        avg_pnl = avg_bal - 100000
        print(f"\n  Portfolio:")
        print(f"    Mean final balance: {avg_bal:.0f}")
        print(f"    Avg P&L: {avg_pnl:+.0f} ({avg_pnl/1000:.2f}%)")
        print(f"    Best: {max(final_balances):.0f}  Worst: {min(final_balances):.0f}")


if __name__ == "__main__":
    analyze_logs()
    asyncio.run(evaluate_model())
