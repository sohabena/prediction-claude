"use client";

import { useCallback, useState } from "react";
import { useApi } from "@/hooks/useApi";
import { useWebSocket } from "@/hooks/useWebSocket";
import { API_BASE } from "@/lib/api";

// ─── Types ──────────────────────────────────────────────────

interface TrainingMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  training_status: string; // "pending" | "approved" | "rejected"
  auto_approved: boolean;
  approved_at: string | null;
  created_at: string | null;
  tick_count: number;
}

interface TrainingStatusResponse {
  matches: TrainingMatch[];
  total: number;
  summary: { pending: number; approved: number; rejected: number };
  error?: string;
}

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

type StatusFilter = "all" | "pending" | "approved" | "rejected";

// ─── Helpers ────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  approved: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  pending: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  rejected: "bg-red-500/20 text-red-400 border-red-500/30",
};

function StatusBadge({
  status,
  auto,
}: {
  status: string;
  auto: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${STATUS_COLORS[status] ?? "bg-zinc-700 text-zinc-300"}`}
    >
      {status.charAt(0).toUpperCase() + status.slice(1)}
      {auto && status === "approved" && (
        <span className="text-[10px] opacity-70 ml-0.5">Auto</span>
      )}
    </span>
  );
}

// ─── Page ───────────────────────────────────────────────────

export default function MatchesPage() {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const endpoint =
    filter === "all"
      ? "/matches/training-status"
      : `/matches/training-status?status=${filter}`;

  const { data, loading, refresh } = useApi<TrainingStatusResponse>(
    endpoint,
    8000,
  );

  const { data: activeData } = useApi<ActiveMatchesResponse>(
    "/matches/active",
    5000,
  );

  const { isConnected } = useWebSocket();

  // Build a set of currently live match_ids
  const liveIds = new Set(
    activeData?.matches?.filter((m) => m.is_live).map((m) => m.match_id) ?? [],
  );

  // ── Actions ───────────────────────────────────────────────

  const handleAction = useCallback(
    async (matchId: string, action: "approve" | "reject") => {
      setActionLoading(matchId);
      try {
        const res = await fetch(
          `${API_BASE}/matches/${matchId}/${action}`,
          { method: "PATCH" },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        refresh();
      } catch (err) {
        console.error(`Failed to ${action} match`, err);
      } finally {
        setActionLoading(null);
      }
    },
    [refresh],
  );

  // ── Summary bar ───────────────────────────────────────────

  const summary = data?.summary ?? { pending: 0, approved: 0, rejected: 0 };

  // ── Render ────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold mb-1">Matches &amp; Training Approval</h1>
          <p className="text-zinc-400 text-sm">
            Approve or reject scraped matches before they feed the RL training
            pipeline.
          </p>
          <p className="text-xs text-zinc-600 mt-1">
            WebSocket:{" "}
            <span className={isConnected ? "text-emerald-500" : "text-red-500"}>
              {isConnected ? "Connected" : "Disconnected"}
            </span>
          </p>
        </div>

        {/* Summary pills */}
        <div className="flex gap-2 text-xs font-medium">
          <span className="px-2.5 py-1 rounded-full bg-amber-500/15 text-amber-400">
            {summary.pending} Pending
          </span>
          <span className="px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400">
            {summary.approved} Approved
          </span>
          <span className="px-2.5 py-1 rounded-full bg-red-500/15 text-red-400">
            {summary.rejected} Rejected
          </span>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex gap-1 mb-6 bg-zinc-800/50 p-1 rounded-lg w-fit">
        {(["all", "pending", "approved", "rejected"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              filter === f
                ? "bg-zinc-700 text-white"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Match cards */}
      {loading && !data ? (
        <p className="text-zinc-500">Loading matches...</p>
      ) : data?.matches?.length ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.matches.map((match) => {
            const isLive = liveIds.has(match.match_id);
            const isPending = match.training_status === "pending";
            const isApproved = match.training_status === "approved";
            const isRejected = match.training_status === "rejected";
            const busy = actionLoading === match.match_id;

            return (
              <div
                key={match.match_id}
                className="stat-card flex flex-col gap-3 relative overflow-hidden"
              >
                {/* Top row: competition + badges */}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-zinc-500 truncate max-w-[60%]">
                    {match.competition || "Unknown"}
                  </span>
                  <div className="flex items-center gap-2">
                    {isLive && (
                      <span className="flex items-center gap-1 text-xs text-red-400">
                        <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
                        LIVE
                      </span>
                    )}
                    <StatusBadge
                      status={match.training_status}
                      auto={match.auto_approved}
                    />
                  </div>
                </div>

                {/* Teams */}
                <div>
                  <p className="text-lg font-semibold leading-tight">
                    {match.team_home}
                  </p>
                  <p className="text-xs text-zinc-500 my-0.5">vs</p>
                  <p className="text-lg font-semibold leading-tight">
                    {match.team_away}
                  </p>
                </div>

                {/* Meta */}
                <div className="flex items-center justify-between text-xs text-zinc-600">
                  <span>{match.tick_count.toLocaleString()} ticks</span>
                  <span>
                    {match.created_at
                      ? new Date(match.created_at).toLocaleDateString()
                      : "—"}
                  </span>
                </div>

                {/* Action buttons */}
                <div className="flex gap-2 mt-auto pt-2 border-t border-zinc-800">
                  {isPending && (
                    <>
                      <button
                        disabled={busy}
                        onClick={() => handleAction(match.match_id, "approve")}
                        className="flex-1 text-xs font-medium py-1.5 rounded-md bg-emerald-600/80 hover:bg-emerald-600 text-white transition-colors disabled:opacity-50"
                      >
                        {busy ? "..." : "Approve"}
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => handleAction(match.match_id, "reject")}
                        className="flex-1 text-xs font-medium py-1.5 rounded-md bg-red-600/80 hover:bg-red-600 text-white transition-colors disabled:opacity-50"
                      >
                        {busy ? "..." : "Reject"}
                      </button>
                    </>
                  )}
                  {isApproved && (
                    <button
                      disabled={busy}
                      onClick={() => handleAction(match.match_id, "reject")}
                      className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-red-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                    >
                      {busy ? "..." : "Revoke"}
                    </button>
                  )}
                  {isRejected && (
                    <button
                      disabled={busy}
                      onClick={() => handleAction(match.match_id, "approve")}
                      className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-emerald-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                    >
                      {busy ? "..." : "Re-approve"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="stat-card text-center py-12">
          <p className="text-zinc-500 text-lg">No matches found</p>
          <p className="text-zinc-600 text-sm mt-2">
            {filter !== "all"
              ? `No ${filter} matches. Try a different filter.`
              : "Matches will appear here once the scraper detects events on LotusBook."}
          </p>
        </div>
      )}
    </div>
  );
}
