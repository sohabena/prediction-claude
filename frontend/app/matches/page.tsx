"use client";

import { useApi } from "@/hooks/useApi";
import { useWebSocket } from "@/hooks/useWebSocket";

interface ActiveMatchesResponse {
  matches: Array<{
    match_id: string;
    team_home: string;
    team_away: string;
    competition: string;
    is_live: boolean;
    last_update: string;
  }>;
}

export default function MatchesPage() {
  const { data, loading } = useApi<ActiveMatchesResponse>(
    "/matches/active",
    5000
  );
  const { isConnected, lastMessage } = useWebSocket();

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Live Matches</h1>
      <p className="text-zinc-400 mb-2">
        Active cricket matches being tracked by the scraper
      </p>
      <p className="text-xs text-zinc-600 mb-8">
        WebSocket:{" "}
        <span className={isConnected ? "text-emerald-500" : "text-red-500"}>
          {isConnected ? "Connected" : "Disconnected"}
        </span>
      </p>

      {loading && !data ? (
        <p className="text-zinc-500">Loading matches...</p>
      ) : data?.matches?.length ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.matches.map((match) => (
            <div
              key={match.match_id}
              className="stat-card flex flex-col gap-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {match.competition}
                </span>
                {match.is_live && (
                  <span className="flex items-center gap-1 text-xs text-red-400">
                    <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
                    LIVE
                  </span>
                )}
              </div>

              <div>
                <p className="text-lg font-semibold">{match.team_home}</p>
                <p className="text-xs text-zinc-500">vs</p>
                <p className="text-lg font-semibold">{match.team_away}</p>
              </div>

              <div className="text-xs text-zinc-600">
                Last update:{" "}
                {match.last_update
                  ? new Date(match.last_update).toLocaleTimeString()
                  : "—"}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="stat-card text-center py-12">
          <p className="text-zinc-500 text-lg">No active matches</p>
          <p className="text-zinc-600 text-sm mt-2">
            Matches will appear here when the scraper detects live events on
            LotusBook.
          </p>
        </div>
      )}
    </div>
  );
}
