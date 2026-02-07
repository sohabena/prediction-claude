"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/StatCard";
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
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_odds: number;
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [summaryRes, perfRes] = await Promise.all([
          fetch(`${API_BASE}/training/summary`),
          fetch(`${API_BASE}/agent/performance`),
        ]);
        setSummary(await summaryRes.json());
        setPerf(await perfRes.json());
      } catch (err) {
        console.error("Failed to load dashboard data", err);
      }
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">PHOENIX Dashboard</h1>
      <p className="text-zinc-400 mb-8">
        Cricket Betting RL System — Real-time Overview
      </p>

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
          value={perf?.total_bets?.toLocaleString() ?? "—"}
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
