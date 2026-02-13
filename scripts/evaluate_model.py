"""Evaluate trained model: action distribution, rewards, and portfolio performance."""

import asyncio
import collections
import numpy as np

from rl.agent import PhoenixAgent
from rl.environment import CricketBettingEnv
from rl.data_loader import MatchDataLoader

ACTION_NAMES = {
    0: "HOLD",
    1: "BACK_HOME_SM",
    2: "BACK_HOME_LG",
    3: "BACK_AWAY_SM",
    4: "BACK_AWAY_LG",
    5: "LAY_HOME_SM",
    6: "LAY_AWAY_SM",
    7: "LAY_HOME_LG",
    8: "LAY_AWAY_LG",
}


async def main():
    loader = MatchDataLoader()
    episodes = await loader.load_completed_matches()
    print(f"Loaded {len(episodes)} episodes, {sum(len(e) for e in episodes)} total ticks")

    env = CricketBettingEnv(data=episodes, initial_bankroll=100000.0)
    agent = PhoenixAgent.load("models/best_model.zip", env)

    action_counts = collections.Counter()
    total_steps = 0
    ep_rewards = []
    final_balances = []

    n_eval = min(20, len(episodes))
    for ep_idx in range(n_eval):
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
        bal = getattr(env, "_portfolio", None)
        if bal:
            final_balances.append(bal.current_balance)

    print(f"\n=== ACTION DISTRIBUTION ({total_steps} steps, {n_eval} episodes) ===")
    for a in sorted(action_counts.keys()):
        pct = 100 * action_counts[a] / total_steps
        bar = "#" * int(pct)
        print(f"  {ACTION_NAMES.get(a, f'ACT_{a}'):>16s}: {action_counts[a]:>6d} ({pct:>5.1f}%) {bar}")

    print(f"\n=== EPISODE REWARDS ===")
    print(f"  Mean: {np.mean(ep_rewards):.3f}")
    print(f"  Std:  {np.std(ep_rewards):.3f}")
    print(f"  Min:  {np.min(ep_rewards):.3f}")
    print(f"  Max:  {np.max(ep_rewards):.3f}")
    pos = sum(1 for r in ep_rewards if r > 0)
    print(f"  Positive: {pos}/{n_eval} ({100*pos/n_eval:.0f}%)")

    if final_balances:
        print(f"\n=== PORTFOLIO ===")
        print(f"  Final balances: {[f'{b:.0f}' for b in final_balances]}")
        print(f"  Mean final: {np.mean(final_balances):.0f}")
        avg_pnl = np.mean(final_balances) - 100000
        print(f"  Avg P&L: {avg_pnl:+.0f} ({avg_pnl/1000:.2f}%)")


if __name__ == "__main__":
    asyncio.run(main())
