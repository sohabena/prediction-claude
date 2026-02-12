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

interface ProgressData {
  current_step: number;
  total_steps: number;
  pct_complete: number;
  mean_reward_100?: number;
  episodes?: number;
  status?: string;
  updated_at: string | null;
}

interface SummaryResponse {
  progress?: ProgressData | null;
  total_timesteps?: number;
  total_episodes?: number;
  recent_win_rate?: number;
  recent_roi?: number;
  recent_sharpe?: number;
  latest_version?: string;
}

export default function TrainingPage() {
  const { data, loading } = useApi<MetricsResponse>(
    "/training/metrics?limit=200",
    10000
  );

  const { data: summary } = useApi<SummaryResponse>("/training/summary", 3000);
  const progress = summary?.progress ?? null;

  const metrics = data?.metrics?.slice().reverse() ?? [];
  const latest = metrics[metrics.length - 1];

  const showProgress =
    progress &&
    progress.total_steps > 0 &&
    (progress.current_step > 0 ||
      progress.status === "completed" ||
      progress.status === "running");

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Training</h1>
      <p className="text-zinc-400 mb-8">RL Agent training progress and metrics</p>

      {showProgress && (
        <div className="mb-8 p-4 rounded-lg bg-zinc-800/50 border border-zinc-700">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium">
              Offline training: {progress.current_step.toLocaleString()} /{" "}
              {progress.total_steps.toLocaleString()} steps (
              {progress.pct_complete.toFixed(1)}%)
            </span>
            {progress.mean_reward_100 != null && (
              <span className="text-xs text-zinc-500">
                Mean reward (last 100): {progress.mean_reward_100.toFixed(2)}
              </span>
            )}
          </div>
          <div className="h-2 bg-zinc-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-cyan-500 transition-all duration-500"
              style={{ width: `${Math.min(100, progress.pct_complete)}%` }}
            />
          </div>
          <p className="text-xs text-zinc-500 mt-1">
            Each step = one environment step. 500,000 steps ≈ 10–30 min depending on
            hardware.
          </p>
        </div>
      )}

      {loading && !data ? (
        <p className="text-zinc-500">Loading metrics...</p>
      ) : metrics.length === 0 ? (
        <div className="stat-card text-center py-16">
          <p className="text-zinc-400 text-lg mb-2">No training data yet</p>
          <p className="text-zinc-600 text-sm max-w-md mx-auto">
            Training metrics will appear here once the agent begins offline or online training.
            The system needs to accumulate enough match data first, then training starts automatically.
          </p>
          <div className="mt-6 flex justify-center gap-8 text-sm text-zinc-500">
            <div>
              <p className="text-xs uppercase tracking-wider mb-1">Timesteps</p>
              <p className="text-xl font-bold text-zinc-300">{summary?.total_timesteps?.toLocaleString() ?? "0"}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider mb-1">Episodes</p>
              <p className="text-xl font-bold text-zinc-300">{summary?.total_episodes?.toLocaleString() ?? "0"}</p>
            </div>
          </div>
        </div>
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
