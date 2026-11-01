"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect } from "react";
import { useApi } from "@/hooks/useApi";
import { API_BASE } from "@/lib/api";

interface VirtualBet {
  id: number;
  placed_at: string | null;
  match_id: string;
  action: string;
  team: string | null;
  odds: number | null;
  stake: number | null;
  settled_at: string | null;
  outcome: string | null;
  profit_loss: number | null;
  agent_version: string | null;
}

interface BetsResponse {
  count: number;
  bets: VirtualBet[];
  error?: string;
}

interface ActiveMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  is_live: boolean;
  last_update: string;
}

interface ActiveMatchesResponse {
  matches: ActiveMatch[];
}

interface OrchestratorState {
  state: string;
  model_version: number;
  curriculum_stage: string;
  updated_at: string | null;
}

export default function MatchWatchPage() {
  const params = useParams();
  const router = useRouter();
  const matchId = typeof params.id === "string" ? params.id : "";

  const { data: betsData } = useApi<BetsResponse>(
    matchId
      ? `/agent/bets?match_id=${encodeURIComponent(matchId)}&limit=50`
      : "/agent/bets?match_id=_&limit=50",
    2500,
  );

  const { data: activeData } = useApi<ActiveMatchesResponse>(
    "/matches/active",
    5000,
  );

  const { data: orchState } = useApi<OrchestratorState>(
    "/advisor/state",
    10000,
  );

  const setWatchedMatch = useCallback(async () => {
    if (!matchId) return;
    try {
      await fetch(
        `${API_BASE}/demo/watch?match_id=${encodeURIComponent(matchId)}`,
        { method: "PUT" },
      );
    } catch (err) {
      console.error("Failed to set watched match", err);
    }
  }, [matchId]);

  useEffect(() => {
    if (matchId) setWatchedMatch();
  }, [matchId, setWatchedMatch]);

  const handleStopWatching = async () => {
    try {
      await fetch(`${API_BASE}/demo/watch`, { method: "DELETE" });
    } catch {
      // Ignore
    }
    router.push("/matches");
  };

  const match = activeData?.matches?.find((m) => m.match_id === matchId);
  const bets = betsData?.bets ?? [];
  const uniqueTeams = Array.from(new Set(bets.map((b) => b.team).filter(Boolean))) as string[];
  const teamHome = match?.team_home ?? uniqueTeams[0] ?? (matchId.startsWith("cric_") ? "Team Home" : "—");
  const teamAway = match?.team_away ?? uniqueTeams[1] ?? (matchId.startsWith("cric_") ? "Team Away" : "—");
  const competition = match?.competition || (matchId.startsWith("cric_") ? "Official International Match" : matchId.startsWith("bf_") ? "Betfair Historical Series" : "Live Cricket Match");
  
  const totalMatchPnL = bets.reduce((acc, b) => acc + (b.profit_loss ?? 0), 0);
  const settledBets = bets.filter((b) => b.settled_at);

  const currentState = orchState?.state ?? "accumulating";
  const isAgentReady =
    currentState === "online_training" ||
    currentState === "virtual_trading" ||
    currentState === "graduated";
  const isTraining = currentState === "online_training";
  const isGraduated = currentState === "graduated";


  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/matches"
              className="text-sm text-zinc-500 hover:text-zinc-300"
            >
              ← Back to Matches
            </Link>
          </div>
          <h1 className="text-2xl font-bold mb-1">Watch Virtual Bets</h1>
          <p className="text-zinc-400 text-sm">
            {teamHome} vs {teamAway} — {competition}
          </p>
          <div className="flex gap-4 mt-2 text-xs">
            <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono font-medium">
              ID: {matchId}
            </span>
            <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">
              Total Bets: <b className="text-zinc-100">{bets.length}</b>
            </span>
            <span className={`px-2 py-0.5 rounded font-mono font-bold ${totalMatchPnL >= 0 ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/40" : "bg-red-950/60 text-red-400 border border-red-800/40"}`}>
              Match P&L: {totalMatchPnL >= 0 ? "+$" : "-$"}{Math.abs(Math.round(totalMatchPnL)).toLocaleString()}
            </span>
          </div>
        </div>
        <button
          onClick={handleStopWatching}
          className="px-4 py-2 text-sm font-medium rounded-md bg-zinc-700 hover:bg-zinc-600 text-zinc-200 transition-colors"
        >
          Stop Watching
        </button>
      </div>

      {/* Agent status */}
      <div className="mb-6 p-4 rounded-lg bg-zinc-800/50 border border-zinc-700">
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span>
            <span className="text-zinc-500">State:</span>{" "}
            <span className="capitalize">{currentState.replaceAll("_", " ")}</span>
          </span>
          {isTraining ? (
            <span className="text-cyan-400">
              Agent is learning — placing virtual bets while training
            </span>
          ) : isAgentReady ? (
            <span className="text-emerald-400">
              Agent is placing virtual bets on this match
            </span>
          ) : (
            <span className="text-amber-400">
              Agent not ready for live betting yet. Training must start first.
            </span>
          )}
          {isGraduated && (
            <span className="text-cyan-400">
              Graduated — you can follow the model&apos;s bets
            </span>
          )}
        </div>
      </div>

      {/* Bets feed */}
      <div className="stat-card">
        <h2 className="text-lg font-semibold mb-4">Virtual Bets</h2>
        {betsData?.error ? (
          <p className="text-red-400 text-sm">{betsData.error}</p>
        ) : bets.length === 0 ? (
          <p className="text-zinc-500">
            No virtual bets yet. Bets appear here as the model places them when
            odds change.
          </p>
        ) : (
          <div className="space-y-2 max-h-[50vh] overflow-y-auto">
            {bets.map((bet) => (
              <div
                key={bet.id}
                className="flex items-center justify-between py-2 px-3 rounded-md bg-zinc-800/50 border border-zinc-700/50"
              >
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">
                    {bet.action} {bet.team ?? ""} @ {bet.odds?.toFixed(2) ?? "—"}
                  </span>
                  <span className="text-xs text-zinc-500">
                    {bet.placed_at
                      ? new Date(bet.placed_at).toLocaleString()
                      : "—"}
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-sm font-mono">
                    Stake: ${Math.round(bet.stake ?? 0).toLocaleString()}
                  </span>
                  {bet.settled_at && (
                    <div className="text-xs mt-0.5 font-mono font-bold">
                      {bet.outcome === "win" ? (
                        <span className="text-emerald-400">
                          WON +${Math.abs(Math.round(bet.profit_loss ?? 0)).toLocaleString()}
                        </span>
                      ) : bet.outcome === "loss" ? (
                        <span className="text-red-400">
                          LOST -${Math.abs(Math.round(bet.profit_loss ?? 0)).toLocaleString()}
                        </span>
                      ) : (
                        <span className="text-zinc-500">—</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
