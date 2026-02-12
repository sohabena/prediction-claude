"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useApi } from "@/hooks/useApi";
import { useWebSocket } from "@/hooks/useWebSocket";
import { API_BASE } from "@/lib/api";
import { ManualResultModal } from "@/components/ManualResultModal";

interface ValidationResult {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  recommendation: "approve" | "review" | "reject";
  recommendation_reason: string;
  quality_report: {
    quality_score: number;
    total_ticks: number;
    valid_ticks: number;
    passed: boolean;
    completeness: number;
    odds_jump_count: number;
    duplicate_count: number;
    missing_odds_pct: number;
    issues: string[];
  };
  detailed_stats: {
    total_ticks: number;
    live_ticks: number;
    prematch_ticks: number;
    duration_minutes: number;
    has_volume_data: number;
    has_score_data: number;
    missing_lay_home: number;
    missing_lay_away: number;
    home_odds_range: { min: number | null; max: number | null; unique_prices: number };
    away_odds_range: { min: number | null; max: number | null; unique_prices: number };
  };
}

const REC_COLORS: Record<string, string> = {
  approve: "text-emerald-400 bg-emerald-500/15",
  review: "text-amber-400 bg-amber-500/15",
  reject: "text-red-400 bg-red-500/15",
};

// ─── Types ──────────────────────────────────────────────────

interface TrainingMatch {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  training_status: string;
  scrape_status: string;
  auto_approved: boolean;
  approved_at: string | null;
  created_at: string | null;
  tick_count: number;
}

interface TrainingStatusResponse {
  matches: TrainingMatch[];
  total: number;
  summary: { pending: number; approved: number; rejected: number };
  scrape_summary: { discovered: number; scrape_approved: number; scrape_rejected: number };
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

type ViewTab = "scrape" | "training";

// ─── Helpers ────────────────────────────────────────────────

const SCRAPE_COLORS: Record<string, string> = {
  discovered: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  scrape_approved: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  scrape_rejected: "bg-red-500/20 text-red-400 border-red-500/30",
};

const TRAINING_COLORS: Record<string, string> = {
  approved: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  pending: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  rejected: "bg-red-500/20 text-red-400 border-red-500/30",
};

const SCRAPE_LABELS: Record<string, string> = {
  discovered: "Discovered",
  scrape_approved: "Approved",
  scrape_rejected: "Skipped",
};

function ScrapeStatusBadge({ status, auto, isLive }: { status: string; auto: boolean; isLive?: boolean }) {
  // For approved matches: show "Scraping" only when live, otherwise "Approved"
  let label = SCRAPE_LABELS[status] ?? status;
  let colorClass = SCRAPE_COLORS[status] ?? "bg-zinc-700 text-zinc-300";
  if (status === "scrape_approved" && !isLive) {
    label = "Approved";
    colorClass = "bg-blue-500/20 text-blue-400 border-blue-500/30";
  }

  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${colorClass}`}
    >
      {label}
      {auto && status === "scrape_approved" && (
        <span className="text-[10px] opacity-70 ml-0.5">Auto</span>
      )}
    </span>
  );
}

function TrainingStatusBadge({ status, auto }: { status: string; auto: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${TRAINING_COLORS[status] ?? "bg-zinc-700 text-zinc-300"}`}
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
  const [viewTab, setViewTab] = useState<ViewTab>("scrape");
  const [scrapeFilter, setScrapeFilter] = useState<string>("all");
  const [trainingFilter, setTrainingFilter] = useState<string>("all");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [validating, setValidating] = useState<string | null>(null);
  const [validationResults, setValidationResults] = useState<Record<string, ValidationResult>>({});
  const [manualResultMatch, setManualResultMatch] = useState<TrainingMatch | null>(null);

  const { data, loading, refresh } = useApi<TrainingStatusResponse>(
    "/matches/training-status",
    8000,
  );

  const { data: activeData } = useApi<ActiveMatchesResponse>(
    "/matches/active",
    5000,
  );

  const { isConnected } = useWebSocket();

  const liveIds = new Set(
    activeData?.matches?.filter((m) => m.is_live).map((m) => m.match_id) ?? [],
  );

  // ── Filtered matches ──────────────────────────────────────

  const allMatches = data?.matches ?? [];

  const scrapeMatches = allMatches.filter((m) =>
    scrapeFilter === "all" ? true : m.scrape_status === scrapeFilter,
  );

  const trainingMatches = allMatches.filter((m) => {
    if (m.scrape_status !== "scrape_approved") return false;
    if (trainingFilter === "all") return true;
    return m.training_status === trainingFilter;
  });

  // ── Actions ───────────────────────────────────────────────

  const handleScrapeAction = useCallback(
    async (matchId: string, action: "approve-scrape" | "reject-scrape") => {
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

  const handleTrainingAction = useCallback(
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

  const handleValidate = useCallback(
    async (matchId: string) => {
      setValidating(matchId);
      try {
        const res = await fetch(`${API_BASE}/matches/${matchId}/validate`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ValidationResult = await res.json();
        setValidationResults((prev) => ({ ...prev, [matchId]: data }));
      } catch (err) {
        console.error("Validation failed", err);
      } finally {
        setValidating(null);
      }
    },
    [],
  );

  const handleDelete = useCallback(
    async (matchId: string) => {
      if (!confirm("Delete this rejected match and all its data? This cannot be undone.")) {
        return;
      }
      setActionLoading(matchId);
      try {
        const res = await fetch(`${API_BASE}/matches/${matchId}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const msg = typeof err?.detail === "string"
            ? err.detail
            : Array.isArray(err?.detail)
              ? err.detail.map((d: { msg?: string }) => d.msg).join(", ")
              : `HTTP ${res.status}`;
          throw new Error(msg);
        }
        refresh();
      } catch (err) {
        console.error("Failed to delete match", err);
        alert(err instanceof Error ? err.message : "Failed to delete match");
      } finally {
        setActionLoading(null);
      }
    },
    [refresh],
  );

  // ── Summary ───────────────────────────────────────────────

  const summary = data?.summary ?? { pending: 0, approved: 0, rejected: 0 };
  const scrapeSummary = data?.scrape_summary ?? { discovered: 0, scrape_approved: 0, scrape_rejected: 0 };

  // ── Render ────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold mb-1">Match Management</h1>
          <p className="text-zinc-400 text-sm">
            Two-phase approval: first approve which matches to scrape, then approve collected data for training.
          </p>
          <p className="text-xs text-zinc-600 mt-1">
            WebSocket:{" "}
            <span className={isConnected ? "text-emerald-500" : "text-red-500"}>
              {isConnected ? "Connected" : "Disconnected"}
            </span>
          </p>
        </div>

        {/* Summary pills */}
        <div className="flex flex-col gap-1.5 text-xs font-medium">
          <div className="flex gap-2">
            <span className="px-2.5 py-1 rounded-full bg-blue-500/15 text-blue-400">
              {scrapeSummary.discovered} Discovered
            </span>
            <span className="px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400">
              {scrapeSummary.scrape_approved} Scraping
            </span>
            <span className="px-2.5 py-1 rounded-full bg-zinc-500/15 text-zinc-400">
              {scrapeSummary.scrape_rejected} Skipped
            </span>
          </div>
          <div className="flex gap-2">
            <span className="px-2.5 py-1 rounded-full bg-amber-500/15 text-amber-400">
              {summary.pending} Train Pending
            </span>
            <span className="px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400">
              {summary.approved} Train Approved
            </span>
            <span className="px-2.5 py-1 rounded-full bg-red-500/15 text-red-400">
              {summary.rejected} Train Rejected
            </span>
          </div>
        </div>
      </div>

      {/* View tabs: Scrape Approval vs Training Approval */}
      <div className="flex gap-1 mb-4 bg-zinc-800/50 p-1 rounded-lg w-fit">
        <button
          onClick={() => setViewTab("scrape")}
          className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
            viewTab === "scrape"
              ? "bg-blue-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Step 1: Scrape Approval
        </button>
        <button
          onClick={() => setViewTab("training")}
          className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
            viewTab === "training"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Step 2: Training Approval
        </button>
      </div>

      {/* ═══ SCRAPE APPROVAL TAB ═══ */}
      {viewTab === "scrape" && (
        <>
          <p className="text-zinc-500 text-sm mb-3">
            Matches discovered on LotusBook. Approve matches to collect odds data when they go live.
          </p>
          <div className="flex gap-1 mb-6 bg-zinc-800/50 p-1 rounded-lg w-fit">
            {(["all", "discovered", "scrape_approved", "scrape_rejected"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setScrapeFilter(f)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                  scrapeFilter === f
                    ? "bg-zinc-700 text-white"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f === "all" ? "All" : SCRAPE_LABELS[f] ?? f}
              </button>
            ))}
          </div>

          {loading && !data ? (
            <p className="text-zinc-500">Loading matches...</p>
          ) : scrapeMatches.length ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {scrapeMatches.map((match) => {
                const isLive = liveIds.has(match.match_id);
                const isDiscovered = match.scrape_status === "discovered";
                const isScraping = match.scrape_status === "scrape_approved";
                const isSkipped = match.scrape_status === "scrape_rejected";
                const busy = actionLoading === match.match_id;

                return (
                  <div
                    key={match.match_id}
                    className={`stat-card flex flex-col gap-3 relative overflow-hidden ${
                      isDiscovered ? "border-l-2 border-l-blue-500/50" :
                      isScraping ? "border-l-2 border-l-emerald-500/50" :
                      "border-l-2 border-l-zinc-700/50 opacity-60"
                    }`}
                  >
                    {/* Top row */}
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
                        <ScrapeStatusBadge status={match.scrape_status} auto={match.auto_approved} isLive={isLive} />
                      </div>
                    </div>

                    {/* Teams */}
                    <div>
                      <p className="text-lg font-semibold leading-tight">{match.team_home}</p>
                      <p className="text-xs text-zinc-500 my-0.5">vs</p>
                      <p className="text-lg font-semibold leading-tight">{match.team_away}</p>
                    </div>

                    {/* Meta */}
                    <div className="flex items-center justify-between text-xs text-zinc-600">
                      <span>
                        {isScraping && isLive
                          ? `${match.tick_count.toLocaleString()} ticks collected`
                          : isScraping && !isLive
                            ? match.tick_count > 0
                              ? `${match.tick_count.toLocaleString()} ticks collected`
                              : "Waiting for live match"
                          : isDiscovered
                            ? "Awaiting approval"
                            : "Skipped"}
                      </span>
                      <span>
                        {match.created_at
                          ? new Date(match.created_at).toLocaleDateString()
                          : "—"}
                      </span>
                    </div>

                    {/* Action buttons */}
                    <div className="flex gap-2 mt-auto pt-2 border-t border-zinc-800">
                      {isDiscovered && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleScrapeAction(match.match_id, "approve-scrape")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-emerald-600/80 hover:bg-emerald-600 text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Approve Match"}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleScrapeAction(match.match_id, "reject-scrape")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-red-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Skip"}
                          </button>
                        </>
                      )}
                      {isScraping && (
                        <button
                          disabled={busy}
                          onClick={() => handleScrapeAction(match.match_id, "reject-scrape")}
                          className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-red-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                        >
                          {busy ? "..." : "Revoke Approval"}
                        </button>
                      )}
                      {isSkipped && (
                        <button
                          disabled={busy}
                          onClick={() => handleScrapeAction(match.match_id, "approve-scrape")}
                          className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-emerald-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                        >
                          {busy ? "..." : "Approve Match"}
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
                Matches will appear here once the scraper detects events on LotusBook.
              </p>
            </div>
          )}
        </>
      )}

      {/* ═══ TRAINING APPROVAL TAB ═══ */}
      {viewTab === "training" && (
        <>
          <p className="text-zinc-500 text-sm mb-3">
            Matches with collected data. Validate quality and approve for RL training.
          </p>
          <div className="flex gap-1 mb-6 bg-zinc-800/50 p-1 rounded-lg w-fit">
            {(["all", "pending", "approved", "rejected"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setTrainingFilter(f)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                  trainingFilter === f
                    ? "bg-zinc-700 text-white"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>

          {loading && !data ? (
            <p className="text-zinc-500">Loading matches...</p>
          ) : trainingMatches.length ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {trainingMatches.map((match) => {
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
                    {/* Top row */}
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
                        <TrainingStatusBadge
                          status={match.training_status}
                          auto={match.auto_approved}
                        />
                      </div>
                    </div>

                    {/* Teams */}
                    <div>
                      <p className="text-lg font-semibold leading-tight">{match.team_home}</p>
                      <p className="text-xs text-zinc-500 my-0.5">vs</p>
                      <p className="text-lg font-semibold leading-tight">{match.team_away}</p>
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

                    {/* Validation result panel */}
                    {validationResults[match.match_id] && (
                      <div className="bg-zinc-900/80 rounded-lg p-3 text-xs space-y-2 border border-zinc-700/50">
                        {(() => {
                          const v = validationResults[match.match_id];
                          const q = v.quality_report;
                          const s = v.detailed_stats;
                          return (
                            <>
                              <div className="flex items-center justify-between">
                                <span className="font-semibold text-zinc-300">Quality Score</span>
                                <span className={`font-bold text-sm ${
                                  q.quality_score >= 0.85 ? "text-emerald-400" :
                                  q.quality_score >= 0.60 ? "text-amber-400" : "text-red-400"
                                }`}>
                                  {(q.quality_score * 100).toFixed(0)}%
                                </span>
                              </div>
                              <div className={`px-2 py-1 rounded text-center font-medium ${REC_COLORS[v.recommendation] ?? ""}`}>
                                {v.recommendation.toUpperCase()}: {v.recommendation_reason}
                              </div>
                              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-zinc-400">
                                <span>Live ticks: <b className="text-zinc-200">{s.live_ticks}</b></span>
                                <span>Pre-match: <b className="text-zinc-200">{s.prematch_ticks}</b></span>
                                <span>Duration: <b className="text-zinc-200">{s.duration_minutes}m</b></span>
                                <span>Completeness: <b className="text-zinc-200">{(q.completeness * 100).toFixed(0)}%</b></span>
                                <span>Missing lay H: <b className="text-zinc-200">{s.missing_lay_home}</b></span>
                                <span>Missing lay A: <b className="text-zinc-200">{s.missing_lay_away}</b></span>
                                <span>Odds jumps: <b className="text-zinc-200">{q.odds_jump_count}</b></span>
                                <span>Duplicates: <b className="text-zinc-200">{q.duplicate_count}</b></span>
                                <span>Home odds: <b className="text-zinc-200">{s.home_odds_range.min ?? "?"}-{s.home_odds_range.max ?? "?"} ({s.home_odds_range.unique_prices})</b></span>
                                <span>Away odds: <b className="text-zinc-200">{s.away_odds_range.min ?? "?"}-{s.away_odds_range.max ?? "?"} ({s.away_odds_range.unique_prices})</b></span>
                              </div>
                              {q.issues.length > 0 && (
                                <div className="text-red-400">
                                  {q.issues.map((issue, i) => <div key={i}>{issue}</div>)}
                                </div>
                              )}
                            </>
                          );
                        })()}
                      </div>
                    )}

                    {/* Action buttons */}
                    <div className="flex gap-2 mt-auto pt-2 border-t border-zinc-800">
                      <button
                        disabled={validating === match.match_id}
                        onClick={() => handleValidate(match.match_id)}
                        className="flex-1 text-xs font-medium py-1.5 rounded-md bg-indigo-600/80 hover:bg-indigo-600 text-white transition-colors disabled:opacity-50"
                      >
                        {validating === match.match_id ? "Validating..." : "Validate"}
                      </button>
                      {isPending && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleTrainingAction(match.match_id, "approve")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-emerald-600/80 hover:bg-emerald-600 text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Approve"}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleTrainingAction(match.match_id, "reject")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-red-600/80 hover:bg-red-600 text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Reject"}
                          </button>
                        </>
                      )}
                      {isApproved && (
                        <button
                          disabled={busy}
                          onClick={() => handleTrainingAction(match.match_id, "reject")}
                          className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-red-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                        >
                          {busy ? "..." : "Revoke"}
                        </button>
                      )}
                      {isRejected && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleTrainingAction(match.match_id, "approve")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-emerald-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Re-approve"}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleDelete(match.match_id)}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-700 hover:bg-red-600/80 text-zinc-300 hover:text-red-300 transition-colors disabled:opacity-50"
                            title="Permanently delete match data"
                          >
                            {busy ? "..." : "Delete"}
                          </button>
                        </>
                      )}
                      {/* Manual result submission for approved matches */}
                      {isApproved && (
                        <button
                          onClick={() => setManualResultMatch(match)}
                          className="text-xs font-medium py-1.5 px-2 rounded-md bg-amber-600/80 hover:bg-amber-600 text-white transition-colors"
                          title="Submit match result manually"
                        >
                          📝 Result
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="stat-card text-center py-12">
              <p className="text-zinc-500 text-lg">No matches with collected data</p>
              <p className="text-zinc-600 text-sm mt-2">
                Approve matches for scraping in Step 1 first. They will appear here once data is collected.
              </p>
            </div>
          )}
        </>
      )}

      {/* Manual Result Modal */}
      {manualResultMatch && (
        <ManualResultModal
          matchId={manualResultMatch.match_id}
          teamHome={manualResultMatch.team_home}
          teamAway={manualResultMatch.team_away}
          onClose={() => setManualResultMatch(null)}
          onSuccess={() => {
            setManualResultMatch(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}
