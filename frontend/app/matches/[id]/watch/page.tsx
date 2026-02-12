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
  const teamHome = match?.team_home ?? "—";
  const teamAway = match?.team_away ?? "—";
  const competition = match?.competition ?? "Unknown";

  const currentState = orchState?.state ?? "accumulating";
  const isAgentReady =
    currentState === "online_training" ||
    currentState === "virtual_trading" ||
    currentState === "graduated";
  const isTraining = currentState === "online_training";
  const isGraduated = currentState === "graduated";

  const bets = betsData?.bets ?? [];

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
                  <span className="text-sm">
                    Stake: {bet.stake?.toFixed(0) ?? "—"}
                  </span>
                  {bet.settled_at && (
                    <div className="text-xs mt-0.5">
                      {bet.outcome === "win" ? (
                        <span className="text-emerald-400">
                          Win +{bet.profit_loss?.toFixed(0) ?? "0"}
                        </span>
                      ) : bet.outcome === "loss" ? (
                        <span className="text-red-400">
                          Loss {bet.profit_loss?.toFixed(0) ?? "0"}
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
