"use client";

import Link from "next/link";
import { useApi } from "@/hooks/useApi";
import { StatCard } from "@/components/StatCard";

interface VirtualBet {
  id: number;
  placed_at: string;
  match_id: string;
  action: string;
  team: string;
  odds: number;
  stake: number;
  outcome: string | null;
  profit_loss: number;
  agent_version: string | null;
}

interface BetsResponse {
  count: number;
  bets: VirtualBet[];
}

interface PerformanceResponse {
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
}

const ACTION_COLORS: Record<string, string> = {
  BACK_HOME_SM: "bg-emerald-900/40 text-emerald-400 border-emerald-700/50",
  BACK_HOME_LG: "bg-emerald-900/60 text-emerald-300 border-emerald-600/50",
  BACK_AWAY_SM: "bg-blue-900/40 text-blue-400 border-blue-700/50",
  BACK_AWAY_LG: "bg-blue-900/60 text-blue-300 border-blue-600/50",
  LAY_HOME_SM: "bg-red-900/40 text-red-400 border-red-700/50",
  LAY_HOME_LG: "bg-red-900/60 text-red-300 border-red-600/50",
  LAY_AWAY_SM: "bg-amber-900/40 text-amber-400 border-amber-700/50",
  LAY_AWAY_LG: "bg-amber-900/60 text-amber-300 border-amber-600/50",
  HOLD: "bg-zinc-800 text-zinc-400 border-zinc-700",
};

export default function TradingPage() {
  const { data: perf } = useApi<PerformanceResponse>(
    "/agent/performance",
    10000
  );
  const { data: bets } = useApi<BetsResponse>("/agent/bets?limit=50", 5000);
  const { data: orchState } = useApi<OrchestratorState>(
    "/advisor/state",
    15000
  );

  const currentState = orchState?.state ?? "unknown";
  const isActive =
    currentState === "online_training" ||
    currentState === "virtual_trading" ||
    currentState === "graduated";

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Virtual Trading</h1>
      <p className="text-zinc-400 mb-8">
        Virtual bet history and performance tracking
      </p>

      {/* Agent status banner */}
      <div
        className={`mb-6 p-3 rounded-lg border text-sm ${
          isActive
            ? "bg-emerald-950/30 border-emerald-800/50 text-emerald-400"
            : "bg-zinc-800/50 border-zinc-700 text-zinc-400"
        }`}
      >
        <span className="font-medium capitalize">
          {currentState.replaceAll("_", " ")}
        </span>
        {" — "}
        {isActive
          ? `Agent is actively placing virtual bets (model v${orchState?.model_version ?? 0})`
          : "Agent is not yet placing bets. Training must progress further."}
      </div>

      {/* Performance cards */}
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
            perf && perf.settled_bets > 0
              ? `${(perf.win_rate * 100).toFixed(1)}%`
              : perf && perf.pending_bets > 0
                ? "Awaiting results"
                : "—"
          }
          color={perf && perf.win_rate > 0.55 ? "green" : "yellow"}
        />
        <StatCard
          label="Total P&L"
          value={
            perf && perf.settled_bets > 0
              ? `${perf.total_pnl >= 0 ? "+" : ""}${perf.total_pnl.toFixed(0)}`
              : perf && perf.pending_bets > 0
                ? "Awaiting results"
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
        {bets && bets.count > 0 ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-zinc-500 border-b border-zinc-800">
                <th className="pb-2 pr-4">Time</th>
                <th className="pb-2 pr-4">Match</th>
                <th className="pb-2 pr-4">Action</th>
                <th className="pb-2 pr-4">Team</th>
                <th className="pb-2 pr-4">Odds</th>
                <th className="pb-2 pr-4">Stake</th>
                <th className="pb-2 pr-4">Status</th>
                <th className="pb-2">P&L</th>
              </tr>
            </thead>
            <tbody>
              {bets.bets.map((bet, i) => (
                <tr
                  key={bet.id || i}
                  className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
                >
                  <td className="py-2 pr-4 text-zinc-400 whitespace-nowrap">
                    {bet.placed_at
                      ? new Date(bet.placed_at).toLocaleTimeString()
                      : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    <Link
                      href={`/matches/${bet.match_id}/watch`}
                      className="text-cyan-500 hover:text-cyan-400 text-xs underline-offset-2 hover:underline"
                    >
                      {bet.match_id.replace("lb_", "")}
                    </Link>
                  </td>
                  <td className="py-2 pr-4">
                    <span
                      className={`px-2 py-0.5 rounded text-xs border ${ACTION_COLORS[bet.action] ?? "bg-zinc-800 text-zinc-400"}`}
                    >
                      {bet.action}
                    </span>
                  </td>
                  <td className="py-2 pr-4">{bet.team}</td>
                  <td className="py-2 pr-4 font-mono">
                    {bet.odds?.toFixed(2)}
                  </td>
                  <td className="py-2 pr-4 font-mono">
                    {bet.stake?.toFixed(0)}
                  </td>
                  <td className="py-2 pr-4">
                    <span
                      className={`text-xs font-medium ${
                        bet.outcome === "win"
                          ? "text-emerald-400"
                          : bet.outcome === "loss"
                            ? "text-red-400"
                            : "text-amber-400"
                      }`}
                    >
                      {bet.outcome === "win"
                        ? "WON"
                        : bet.outcome === "loss"
                          ? "LOST"
                          : "PENDING"}
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
                    {bet.outcome === "win" || bet.outcome === "loss"
                      ? `${bet.profit_loss > 0 ? "+" : ""}${bet.profit_loss?.toFixed(0) ?? "0"}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-center py-12">
            <p className="text-zinc-500 text-lg mb-2">No virtual bets yet</p>
            <p className="text-zinc-600 text-sm">
              {isActive
                ? "Bets will appear here as the agent places them on live matches."
                : "The agent needs to reach online training or virtual trading before placing bets."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
