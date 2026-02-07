"use client";

import { useApi } from "@/hooks/useApi";
import { StatCard } from "@/components/StatCard";

interface BetsResponse {
  count: number;
  bets: Array<{
    id: number;
    placed_at: string;
    match_id: string;
    action: string;
    team: string;
    odds: number;
    stake: number;
    outcome: string | null;
    profit_loss: number;
  }>;
}

interface PerformanceResponse {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_odds: number;
  avg_stake: number;
}

export default function TradingPage() {
  const { data: perf } = useApi<PerformanceResponse>(
    "/agent/performance",
    10000
  );
  const { data: bets } = useApi<BetsResponse>("/agent/bets?limit=50", 5000);

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Virtual Trading</h1>
      <p className="text-zinc-400 mb-8">
        Virtual bet history and performance tracking
      </p>

      {/* Performance cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
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

      {/* Bet table */}
      <div className="chart-container overflow-x-auto">
        <h3 className="text-sm font-medium text-zinc-400 mb-4">
          Recent Bets ({bets?.count ?? 0})
        </h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-zinc-500 border-b border-zinc-800">
              <th className="pb-2 pr-4">Time</th>
              <th className="pb-2 pr-4">Action</th>
              <th className="pb-2 pr-4">Team</th>
              <th className="pb-2 pr-4">Odds</th>
              <th className="pb-2 pr-4">Stake</th>
              <th className="pb-2 pr-4">Outcome</th>
              <th className="pb-2">P&L</th>
            </tr>
          </thead>
          <tbody>
            {bets?.bets?.map((bet, i) => (
              <tr
                key={bet.id || i}
                className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
              >
                <td className="py-2 pr-4 text-zinc-400">
                  {bet.placed_at
                    ? new Date(bet.placed_at).toLocaleTimeString()
                    : "—"}
                </td>
                <td className="py-2 pr-4">
                  <span className="px-2 py-0.5 bg-zinc-800 rounded text-xs">
                    {bet.action}
                  </span>
                </td>
                <td className="py-2 pr-4">{bet.team}</td>
                <td className="py-2 pr-4">{bet.odds?.toFixed(2)}</td>
                <td className="py-2 pr-4">{bet.stake?.toFixed(0)}</td>
                <td className="py-2 pr-4">
                  <span
                    className={`text-xs ${
                      bet.outcome === "win"
                        ? "text-emerald-400"
                        : bet.outcome === "loss"
                          ? "text-red-400"
                          : "text-zinc-500"
                    }`}
                  >
                    {bet.outcome?.toUpperCase() ?? "PENDING"}
                  </span>
                </td>
                <td
                  className={`py-2 font-mono ${
                    bet.profit_loss > 0
                      ? "text-emerald-400"
                      : bet.profit_loss < 0
                        ? "text-red-400"
                        : "text-zinc-500"
                  }`}
                >
                  {bet.profit_loss > 0 ? "+" : ""}
                  {bet.profit_loss?.toFixed(0) ?? "0"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
