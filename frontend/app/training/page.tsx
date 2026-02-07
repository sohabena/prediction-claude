"use client";

import { useApi } from "@/hooks/useApi";
import { TrainingChart } from "@/components/TrainingChart";
import { StatCard } from "@/components/StatCard";

interface MetricsResponse {
  count: number;
  metrics: Array<{
    episode: number;
    episode_reward: number;
    win_rate: number;
    roi: number;
    sharpe_ratio: number;
    policy_loss: number;
    value_loss: number;
    entropy: number;
    agent_version: string;
  }>;
}

export default function TrainingPage() {
  const { data, loading } = useApi<MetricsResponse>(
    "/training/metrics?limit=200",
    10000
  );

  const metrics = data?.metrics?.slice().reverse() ?? [];
  const latest = metrics[metrics.length - 1];

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Training</h1>
      <p className="text-zinc-400 mb-8">RL Agent training progress and metrics</p>

      {loading && !data ? (
        <p className="text-zinc-500">Loading metrics...</p>
      ) : (
        <>
          {/* Summary stats */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
            <StatCard
              label="Policy Loss"
              value={latest?.policy_loss?.toFixed(4) ?? "—"}
            />
            <StatCard
              label="Value Loss"
              value={latest?.value_loss?.toFixed(4) ?? "—"}
            />
            <StatCard
              label="Entropy"
              value={latest?.entropy?.toFixed(4) ?? "—"}
            />
            <StatCard
              label="Agent Version"
              value={latest?.agent_version ?? "—"}
            />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <TrainingChart
              data={metrics}
              metric="episode_reward"
              title="Episode Reward"
            />
            <TrainingChart
              data={metrics}
              metric="win_rate"
              title="Win Rate"
            />
            <TrainingChart data={metrics} metric="roi" title="ROI" />
          </div>
        </>
      )}
    </div>
  );
}
