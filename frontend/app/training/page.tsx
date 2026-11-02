"use client";

import { useState } from "react";
import { useApi } from "@/hooks/useApi";
import { API_BASE } from "@/lib/api";
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

interface SyncStatus {
  success: boolean;
  new_matches_found: number;
  updated_specialists: string[];
  timestamp: string | null;
  message?: string;
}

export default function TrainingPage() {
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const { data, loading } = useApi<MetricsResponse>(
    "/training/metrics?limit=200",
    10000
  );

  const { data: summary } = useApi<SummaryResponse>("/training/summary", 3000);
  const { data: syncStatus, refresh: mutateSync } = useApi<SyncStatus>(
    "/training/cricsheet-sync/status",
    10000
  );

  const progress = summary?.progress ?? null;
  const metrics = data?.metrics?.slice().reverse() ?? [];
  const latest = metrics[metrics.length - 1];

  const showProgress =
    progress &&
    progress.total_steps > 0 &&
    (progress.current_step > 0 ||
      progress.status === "completed" ||
      progress.status === "running");

  const handleSyncNow = async () => {
    setSyncing(true);
    setSyncMsg("Initiating Cricsheet sync & specialist retraining...");
    try {
      const res = await fetch(`${API_BASE}/training/cricsheet-sync`, {
        method: "POST",
      });
      const d = await res.json();
      setSyncMsg(d.message || "Sync task running in background.");
      setTimeout(() => {
        mutateSync?.();
      }, 5000);
    } catch {
      setSyncMsg("Network error connecting to backend.");
    } finally {
      setTimeout(() => setSyncing(false), 2000);
    }
  };

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-3xl font-bold mb-2">Training</h1>
          <p className="text-zinc-400">RL Agent training progress, model zoo synchronization, and metrics</p>
        </div>
        <button
          onClick={handleSyncNow}
          disabled={syncing}
          className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium text-sm transition-colors flex items-center gap-2 self-start sm:self-auto"
        >
          {syncing ? (
            <>
              <span className="w-2 h-2 rounded-full bg-white animate-ping"></span>
              Syncing...
            </>
          ) : (
            <>
              <span>↺</span>
              Sync with Cricsheet Now
            </>
          )}
        </button>
      </div>

      {syncMsg && (
        <div className="mb-6 p-3 rounded-lg bg-emerald-950/40 border border-emerald-800 text-emerald-300 text-sm">
          {syncMsg}
        </div>
      )}

      {/* Weekly Cricsheet Sync & Retraining Panel */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-8">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4 pb-3 border-b border-zinc-800">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-zinc-100">
                Weekly Cricsheet Synchronizer & Specialist Retraining
              </h2>
              <span className="px-2 py-0.5 text-xs font-semibold rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                Autonomous
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-0.5">
              Scheduled weekly sync: downloads newly published matches, deduplicates, and incrementally fine-tunes specialist models.
            </p>
          </div>
          <div className="text-right">
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Schedule</span>
            <p className="text-sm font-medium text-zinc-300">Every Monday @ 03:00 UTC</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-zinc-800/40 p-3.5 rounded-lg border border-zinc-800">
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Last Sync Status</span>
            <p className="text-base font-semibold text-emerald-400 mt-1">
              {syncStatus?.success ? "Success" : "Pending / Standing By"}
            </p>
            <p className="text-xs text-zinc-500 mt-0.5">
              {syncStatus?.timestamp
                ? new Date(syncStatus.timestamp).toLocaleString()
                : "Awaiting next schedule"}
            </p>
          </div>

          <div className="bg-zinc-800/40 p-3.5 rounded-lg border border-zinc-800">
            <span className="text-xs text-zinc-500 uppercase tracking-wider">New Matches Ingested</span>
            <p className="text-base font-semibold text-zinc-100 mt-1">
              +{syncStatus?.new_matches_found ?? 0} matches
            </p>
            <p className="text-xs text-zinc-500 mt-0.5">
              Deduplicated vs 19,494 catalog
            </p>
          </div>

          <div className="bg-zinc-800/40 p-3.5 rounded-lg border border-zinc-800">
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Models Fine-Tuned</span>
            <p className="text-base font-semibold text-cyan-400 mt-1">
              {syncStatus?.updated_specialists && syncStatus.updated_specialists.length > 0
                ? syncStatus.updated_specialists.map(s => s.toUpperCase()).join(", ")
                : "All Specialists Up to Date"}
            </p>
            <p className="text-xs text-zinc-500 mt-0.5">
              Incremental PPO curriculum
            </p>
          </div>
        </div>
      </div>

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
            Each step = one environment step. 500,000 steps ≈ 10–30 min depending on hardware.
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
              value={latest?.agent_version || summary?.latest_version || "v3_specialist_zoo"}
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
