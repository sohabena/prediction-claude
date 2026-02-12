"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
import { HealthStatus } from "@/components/HealthStatus";
import { API_BASE } from "@/lib/api";

interface Summary {
  total_timesteps: number;
  total_episodes: number;
  recent_win_rate: number;
  recent_roi: number;
  recent_sharpe: number;
  latest_version: string;
}

interface Performance {
  total_bets: number;
  settled_bets: number;
  pending_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_odds: number;
  avg_stake: number;
}

interface OrchestratorState {
  state: string;
  model_version: number;
  curriculum_stage: string;
  updated_at: string | null;
}

interface OrchestratorStats {
  accumulation?: {
    total_matches: number;
    total_ticks: number;
    qualifying_matches: number;
    min_required: number;
    ready_to_train: boolean;
  };
  training_runs: number;
}

interface ActiveMatchesResponse {
  matches: Array<{ match_id: string; is_live: boolean }>;
}

const STATE_LABELS: Record<string, string> = {
  accumulating: "Accumulating Data",
  offline_training: "Offline Training",
  online_training: "Online Training",
  virtual_trading: "Virtual Trading",
  graduated: "Graduated",
};

const STATE_COLORS: Record<string, string> = {
  accumulating: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  offline_training: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  online_training: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  virtual_trading: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  graduated: "bg-phoenix-500/20 text-phoenix-400 border-phoenix-500/30",
};

export default function DashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [orchState, setOrchState] = useState<OrchestratorState | null>(null);
  const [orchStats, setOrchStats] = useState<OrchestratorStats | null>(null);
  const [activeMatches, setActiveMatches] = useState<ActiveMatchesResponse | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [summaryRes, perfRes, stateRes, statsRes, matchesRes] = await Promise.all([
          fetch(`${API_BASE}/training/summary`),
          fetch(`${API_BASE}/agent/performance`),
          fetch(`${API_BASE}/advisor/state`),
          fetch(`${API_BASE}/advisor/stats`),
          fetch(`${API_BASE}/matches/active`),
        ]);
        setSummary(await summaryRes.json());
        setPerf(await perfRes.json());
        setOrchState(await stateRes.json());
        setOrchStats(await statsRes.json());
        setActiveMatches(await matchesRes.json());
      } catch (err) {
        console.error("Failed to load dashboard data", err);
      }
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const currentState = orchState?.state ?? "accumulating";
  const liveCount = activeMatches?.matches?.filter((m) => m.is_live).length ?? 0;
  const totalMatches = activeMatches?.matches?.length ?? 0;
  const accum = orchStats?.accumulation;

  return (
    <div>
      <div className="flex items-start justify-between mb-2">
        <h1 className="text-3xl font-bold">PHOENIX Dashboard</h1>
        <HealthStatus />
      </div>
      <p className="text-zinc-400 mb-8">
        Cricket Betting RL System — Real-time Overview
      </p>

      {/* Lifecycle State Banner */}
      <div className={`mb-6 p-4 rounded-xl border flex items-center justify-between ${STATE_COLORS[currentState] ?? "bg-zinc-800 text-zinc-300 border-zinc-700"}`}>
        <div>
          <span className="text-xs uppercase tracking-wider opacity-70">Lifecycle State</span>
          <p className="text-lg font-bold">{STATE_LABELS[currentState] ?? currentState}</p>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <div className="text-center">
            <p className="text-xs opacity-70">Live</p>
            <p className="font-bold">{liveCount}</p>
          </div>
          <div className="text-center">
            <p className="text-xs opacity-70">Tracked</p>
            <p className="font-bold">{totalMatches}</p>
          </div>
          {orchState?.model_version != null && orchState.model_version > 0 && (
            <div className="text-center">
              <p className="text-xs opacity-70">Model</p>
              <p className="font-bold">v{orchState.model_version}</p>
            </div>
          )}
        </div>
      </div>

      {/* Data Accumulation (shown when accumulating) */}
      {accum && currentState === "accumulating" && (
        <div className="mb-6 p-4 rounded-xl bg-zinc-800/50 border border-zinc-700">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-zinc-300">Data Accumulation</span>
            <span className="text-xs text-zinc-500">
              {accum.qualifying_matches} / {accum.min_required} qualifying matches
            </span>
          </div>
          <div className="h-2.5 bg-zinc-700 rounded-full overflow-hidden mb-2">
            <div
              className="h-full bg-phoenix-500 rounded-full transition-all duration-700"
              style={{ width: `${Math.min(100, (accum.qualifying_matches / Math.max(accum.min_required, 1)) * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-zinc-500">
            <span>{accum.total_ticks.toLocaleString()} total ticks</span>
            <span>{accum.ready_to_train ? "Ready to train" : `Need ${accum.min_required - accum.qualifying_matches} more`}</span>
          </div>
        </div>
      )}

      {/* Training Stats */}
      <h2 className="text-xl font-semibold mb-4 text-zinc-300">
        Training Progress
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Total Timesteps"
          value={summary?.total_timesteps?.toLocaleString() ?? "—"}
        />
        <StatCard
          label="Episodes"
          value={summary?.total_episodes?.toLocaleString() ?? "—"}
        />
        <StatCard
          label="Win Rate (1h)"
          value={
            summary?.recent_win_rate
              ? `${(summary.recent_win_rate * 100).toFixed(1)}%`
              : "—"
          }
          color={
            summary && summary.recent_win_rate > 0.55
              ? "green"
              : summary && summary.recent_win_rate > 0.5
                ? "yellow"
                : "red"
          }
        />
        <StatCard
          label="Sharpe (1h)"
          value={summary?.recent_sharpe?.toFixed(2) ?? "—"}
          color={
            summary && summary.recent_sharpe > 1.5
              ? "green"
              : summary && summary.recent_sharpe > 0
                ? "yellow"
                : "red"
          }
        />
      </div>

      {/* Trading Stats */}
      <h2 className="text-xl font-semibold mb-4 text-zinc-300">
        Virtual Trading
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Total Bets"
          value={
            perf
              ? `${perf.total_bets.toLocaleString()}${perf.pending_bets > 0 ? ` (${perf.pending_bets} pending)` : ""}`
              : "—"
          }
        />
        <StatCard
          label="Win Rate"
          value={
            perf?.win_rate ? `${(perf.win_rate * 100).toFixed(1)}%` : "—"
          }
          color={perf && perf.win_rate > 0.55 ? "green" : "yellow"}
        />
        <StatCard
          label="Total P&L"
          value={
            perf?.total_pnl !== undefined
              ? `${perf.total_pnl >= 0 ? "+" : ""}${perf.total_pnl.toFixed(0)}`
              : "—"
          }
          color={perf && perf.total_pnl > 0 ? "green" : "red"}
        />
        <StatCard
          label="Avg Odds"
          value={perf?.avg_odds?.toFixed(2) ?? "—"}
        />
      </div>

      {/* Agent Status */}
      <h2 className="text-xl font-semibold mb-4 text-zinc-300">System</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Agent Version"
          value={summary?.latest_version ?? "—"}
        />
        <StatCard
          label="ROI (1h)"
          value={
            summary?.recent_roi
              ? `${(summary.recent_roi * 100).toFixed(1)}%`
              : "—"
          }
          color={
            summary && summary.recent_roi > 0.08
              ? "green"
              : summary && summary.recent_roi > 0
                ? "yellow"
                : "red"
          }
        />
        <StatCard label="Wins / Losses" value={perf ? `${perf.wins} / ${perf.losses}` : "—"} />
        <StatCard label="Avg Stake" value={perf?.avg_stake?.toFixed(0) ?? "—"} />
      </div>
    </div>
  );
}
