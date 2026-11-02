"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
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
  match_start_time: string | null;
  tick_count: number;
  back_home?: number | null;
  lay_home?: number | null;
  back_away?: number | null;
  lay_away?: number | null;
  score_text?: string | null;
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
    back_home?: number | null;
    lay_home?: number | null;
    back_away?: number | null;
    lay_away?: number | null;
    score_text?: string | null;
    scheduled_time?: string | null;
    scrape_status?: string;
    training_status?: string;
    auto_approved?: boolean;
    tick_count?: number;
    match_start_time?: string | null;
  }>;
  updated_at?: string;
}

type ViewTab = "scrape" | "training";
type FormatFilter = "all" | "t20" | "odi" | "test";

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

function getFormatLabel(matchId: string): { label: string; badgeClass: string } {
  if (matchId.includes("t20")) {
    return { label: "T20 International", badgeClass: "bg-indigo-500/15 text-indigo-400 border-indigo-500/30" };
  }
  if (matchId.includes("odi")) {
    return { label: "One Day International", badgeClass: "bg-blue-500/15 text-blue-400 border-blue-500/30" };
  }
  if (matchId.includes("test")) {
    return { label: "Test Match", badgeClass: "bg-purple-500/15 text-purple-400 border-purple-500/30" };
  }
  return { label: "Official International", badgeClass: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" };
}

// ─── Page Component ─────────────────────────────────────────

const ITEMS_PER_PAGE = 24;

export default function MatchesPage() {
  const [viewTab, setViewTab] = useState<ViewTab>("scrape");
  const [scrapeFilter, setScrapeFilter] = useState<string>("all");
  const [trainingFilter, setTrainingFilter] = useState<string>("all");
  const [formatFilter, setFormatFilter] = useState<FormatFilter>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [currentPage, setCurrentPage] = useState<number>(1);
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

  const liveIds = useMemo(() => {
    return new Set(activeData?.matches?.filter((m) => m.is_live).map((m) => m.match_id) ?? []);
  }, [activeData]);

  const allMatches = data?.matches ?? [];

  // ── Scrape Tab: Strictly Active LotusBook Matches ────
  const liveLotusMatches = useMemo(() => {
    const activeList = activeData?.matches ?? [];
    const activeIdSet = new Set(activeList.map((m) => m.match_id));
    const dbMatchMap = new Map(allMatches.map((m) => [m.match_id, m]));

    const result: TrainingMatch[] = activeList.map((active) => {
      const dbMatch = dbMatchMap.get(active.match_id);
      return {
        match_id: active.match_id,
        team_home: active.team_home,
        team_away: active.team_away,
        competition: active.competition || dbMatch?.competition || "",
        training_status: active.training_status || dbMatch?.training_status || "pending",
        scrape_status: active.scrape_status || dbMatch?.scrape_status || "discovered",
        auto_approved: active.auto_approved ?? dbMatch?.auto_approved ?? false,
        approved_at: dbMatch?.approved_at || null,
        created_at: dbMatch?.created_at || active.last_update || null,
        match_start_time: active.scheduled_time || active.match_start_time || dbMatch?.match_start_time || null,
        tick_count: active.tick_count ?? dbMatch?.tick_count ?? 0,
        back_home: active.back_home ?? null,
        lay_home: active.lay_home ?? null,
        back_away: active.back_away ?? null,
        lay_away: active.lay_away ?? null,
        score_text: active.score_text ?? null,
      };
    });

    return result;
  }, [activeData, allMatches]);

  const liveLotusSummary = useMemo(() => {
    let discovered = 0;
    let approved = 0;
    let rejected = 0;
    for (const m of liveLotusMatches) {
      if (m.scrape_status === "discovered") discovered++;
      else if (m.scrape_status === "scrape_approved") approved++;
      else if (m.scrape_status === "scrape_rejected") rejected++;
    }
    return { discovered, approved, rejected, total: liveLotusMatches.length };
  }, [liveLotusMatches]);

  const filteredScrapeMatches = useMemo(() => {
    return liveLotusMatches.filter((m) => {
      if (scrapeFilter !== "all" && m.scrape_status !== scrapeFilter) return false;
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        m.team_home.toLowerCase().includes(q) ||
        m.team_away.toLowerCase().includes(q) ||
        m.match_id.toLowerCase().includes(q) ||
        (m.competition && m.competition.toLowerCase().includes(q))
      );
    });
  }, [liveLotusMatches, scrapeFilter, searchQuery]);

  // ── Training Tab: Official Harmonized Training Corpus & Completed Matches ──
  const trainingCorpusMatches = useMemo(() => {
    const activeIdSet = new Set((activeData?.matches ?? []).map((m) => m.match_id));
    return allMatches.filter((m) => !m.match_id.startsWith("lb_") || (!activeIdSet.has(m.match_id) && m.tick_count > 0));
  }, [allMatches, activeData]);

  const formatCounts = useMemo(() => {
    let t20 = 0;
    let odi = 0;
    let test = 0;
    for (const m of trainingCorpusMatches) {
      if (m.match_id.includes("t20")) t20++;
      else if (m.match_id.includes("odi")) odi++;
      else if (m.match_id.includes("test")) test++;
    }
    return { t20, odi, test, total: trainingCorpusMatches.length };
  }, [trainingCorpusMatches]);

  const filteredTrainingMatches = useMemo(() => {
    return trainingCorpusMatches.filter((m) => {
      // Format filter
      if (formatFilter === "t20" && !m.match_id.includes("t20")) return false;
      if (formatFilter === "odi" && !m.match_id.includes("odi")) return false;
      if (formatFilter === "test" && !m.match_id.includes("test")) return false;

      // Status filter
      if (trainingFilter !== "all" && m.training_status !== trainingFilter) return false;

      // Search query
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        m.team_home.toLowerCase().includes(q) ||
        m.team_away.toLowerCase().includes(q) ||
        m.match_id.toLowerCase().includes(q) ||
        (m.competition && m.competition.toLowerCase().includes(q))
      );
    });
  }, [trainingCorpusMatches, formatFilter, trainingFilter, searchQuery]);

  // Pagination calculation
  const totalPages = Math.ceil(filteredTrainingMatches.length / ITEMS_PER_PAGE) || 1;
  const paginatedTrainingMatches = useMemo(() => {
    const start = (currentPage - 1) * ITEMS_PER_PAGE;
    return filteredTrainingMatches.slice(start, start + ITEMS_PER_PAGE);
  }, [filteredTrainingMatches, currentPage]);

  // Reset page when filter or search changes
  const handleSearchChange = (val: string) => {
    setSearchQuery(val);
    setCurrentPage(1);
  };

  const handleFormatFilterChange = (val: FormatFilter) => {
    setFormatFilter(val);
    setCurrentPage(1);
  };

  const handleTrainingFilterChange = (val: string) => {
    setTrainingFilter(val);
    setCurrentPage(1);
  };

  // ── Actions ───────────────────────────────────────────────

  const handleScrapeAction = useCallback(
    async (matchId: string, action: "approve-scrape" | "reject-scrape") => {
      setActionLoading(matchId);
      try {
        const res = await fetch(`${API_BASE}/matches/${matchId}/${action}`, {
          method: "PATCH",
        });
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
        const res = await fetch(`${API_BASE}/matches/${matchId}/${action}`, {
          method: "PATCH",
        });
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
        const resultData: ValidationResult = await res.json();
        setValidationResults((prev) => ({ ...prev, [matchId]: resultData }));
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

  // ── Render ────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold mb-1">Match Management</h1>
          <p className="text-zinc-400 text-sm">
            Live bookmaker scraping approval &amp; historical training corpus management.
          </p>
          <p className="text-xs text-zinc-500 mt-1 flex items-center gap-2">
            <span>WebSocket Feed:</span>
            <span className={`inline-flex items-center gap-1 font-semibold ${isConnected ? "text-emerald-400" : "text-red-400"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? "bg-emerald-400" : "bg-red-400"}`} />
              {isConnected ? "Connected" : "Disconnected"}
            </span>
          </p>
        </div>

        {/* Global summary badges */}
        <div className="flex flex-wrap gap-2 text-xs font-medium">
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-zinc-900 border border-zinc-800">
            <span className="text-zinc-400">Live LotusBook:</span>
            <span className="text-blue-400 font-bold">{liveLotusSummary.discovered} Discovered</span>
            <span className="text-zinc-600">/</span>
            <span className="text-emerald-400 font-bold">{liveLotusSummary.approved} Scraping</span>
            <span className="text-zinc-600">/</span>
            <span className="text-zinc-500">{liveLotusSummary.rejected} Skipped</span>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-zinc-900 border border-zinc-800">
            <span className="text-zinc-400">Specialist Zoo Corpus:</span>
            <span className="text-emerald-400 font-bold">19,494 Matches</span>
            <span className="text-zinc-600">/</span>
            <span className="text-indigo-400 font-semibold">12 Formats (3.03M Ticks)</span>
          </div>
        </div>
      </div>

      {/* Top Navigation Tabs */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6 border-b border-zinc-800/80 pb-4">
        <div className="flex gap-2 bg-zinc-900 p-1 rounded-xl border border-zinc-800">
          <button
            onClick={() => {
              setViewTab("scrape");
              setSearchQuery("");
            }}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-all ${
              viewTab === "scrape"
                ? "bg-blue-600 text-white shadow-md shadow-blue-500/20"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            🏏 Live LotusBook Matches ({liveLotusMatches.length})
          </button>
          <button
            onClick={() => {
              setViewTab("training");
              setSearchQuery("");
              setCurrentPage(1);
            }}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-all ${
              viewTab === "training"
                ? "bg-indigo-600 text-white shadow-md shadow-indigo-500/20"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            🧠 Training Corpus ({trainingCorpusMatches.length.toLocaleString()})
          </button>
        </div>

        {/* Global Search Box */}
        <div className="relative w-full sm:w-72">
          <input
            type="text"
            placeholder="Search teams, tournament, ID..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
          />
          {searchQuery && (
            <button
              onClick={() => handleSearchChange("")}
              className="absolute right-2.5 top-2 text-xs text-zinc-500 hover:text-zinc-300"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* ═══ TAB 1: LIVE LOTUSBOOK MATCHES ═══ */}
      {viewTab === "scrape" && (
        <>
          <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-3 mb-4">
            <p className="text-zinc-400 text-xs">
              Matches discovered in real time from <code className="text-zinc-300">https://lotusbook.cc/cricket</code>.
              Approve matches to capture live odds ticks and in-play scorecard context.
            </p>
            <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg border border-zinc-800 w-fit">
              {(["all", "discovered", "scrape_approved", "scrape_rejected"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setScrapeFilter(f)}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                    scrapeFilter === f
                      ? "bg-zinc-800 text-white"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {f === "all" ? "All" : SCRAPE_LABELS[f] ?? f}
                </button>
              ))}
            </div>
          </div>

          {loading && !data ? (
            <p className="text-zinc-500 text-sm py-8 text-center">Loading live LotusBook matches...</p>
          ) : filteredScrapeMatches.length ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filteredScrapeMatches.map((match) => {
                const isLive = liveIds.has(match.match_id);
                const isDiscovered = match.scrape_status === "discovered";
                const isScraping = match.scrape_status === "scrape_approved";
                const isSkipped = match.scrape_status === "scrape_rejected";
                const busy = actionLoading === match.match_id;

                return (
                  <div
                    key={match.match_id}
                    className={`stat-card flex flex-col gap-3 relative overflow-hidden ${
                      isDiscovered
                        ? "border-l-2 border-l-blue-500/70"
                        : isScraping
                          ? "border-l-2 border-l-emerald-500/70"
                          : "border-l-2 border-l-zinc-700/50 opacity-75"
                    }`}
                  >
                    {/* Top Row: Competition & Status Badges */}
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-zinc-400 font-medium truncate max-w-[60%]">
                        {match.competition || "Live Cricket"}
                      </span>
                      <div className="flex items-center gap-2">
                        {isLive && (
                          <span className="flex items-center gap-1 text-xs text-red-400 font-bold">
                            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
                            LIVE
                          </span>
                        )}
                        <ScrapeStatusBadge status={match.scrape_status} auto={match.auto_approved} isLive={isLive} />
                      </div>
                    </div>

                    {/* Team Names */}
                    <div>
                      <p className="text-base font-bold text-zinc-100">{match.team_home}</p>
                      <p className="text-xs text-zinc-500 my-0.5 font-medium">vs</p>
                      <p className="text-base font-bold text-zinc-100">{match.team_away}</p>
                    </div>

                    {/* Live Score & Odds Box */}
                    {(match.score_text || match.back_home || match.lay_home) && (
                      <div className="bg-zinc-950/70 rounded-lg p-2 border border-zinc-800/80">
                        {match.score_text && (
                          <div className="text-xs font-semibold text-emerald-400 mb-1 flex items-center justify-between">
                            <span>Score: {match.score_text}</span>
                            {isLive && <span className="text-[10px] text-zinc-500 uppercase">In-Play</span>}
                          </div>
                        )}
                        {(match.back_home || match.lay_home || match.back_away || match.lay_away) && (
                          <div className="grid grid-cols-2 gap-1.5 text-center text-xs">
                            <div className="bg-zinc-900/90 border border-zinc-800/60 rounded px-1.5 py-1">
                              <span className="text-[10px] text-zinc-400 block truncate font-medium">{match.team_home}</span>
                              <div className="flex justify-around items-center mt-0.5 font-mono">
                                <span className="text-blue-400 font-bold" title="Back odds">B {match.back_home ? match.back_home.toFixed(2) : "-"}</span>
                                <span className="text-pink-400 font-bold" title="Lay odds">L {match.lay_home ? match.lay_home.toFixed(2) : "-"}</span>
                              </div>
                            </div>
                            <div className="bg-zinc-900/90 border border-zinc-800/60 rounded px-1.5 py-1">
                              <span className="text-[10px] text-zinc-400 block truncate font-medium">{match.team_away}</span>
                              <div className="flex justify-around items-center mt-0.5 font-mono">
                                <span className="text-blue-400 font-bold" title="Back odds">B {match.back_away ? match.back_away.toFixed(2) : "-"}</span>
                                <span className="text-pink-400 font-bold" title="Lay odds">L {match.lay_away ? match.lay_away.toFixed(2) : "-"}</span>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Match Metadata & Start Times */}
                    <div className="flex items-center justify-between text-xs text-zinc-500 pt-1 border-t border-zinc-800/60">
                      <span>
                        {isScraping && isLive
                          ? `${match.tick_count.toLocaleString()} ticks collected`
                          : isScraping && !isLive
                            ? match.tick_count > 0
                              ? `${match.tick_count.toLocaleString()} ticks collected`
                              : "Active in scraper queue"
                          : isDiscovered
                            ? "Awaiting your approval"
                            : "Filtered out (SRL/Non-Intl)"}
                      </span>
                      <div className="text-right">
                        {isLive ? (
                          <span className="text-red-400 font-semibold flex items-center justify-end gap-1">
                            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
                            In-Play Live
                          </span>
                        ) : match.match_start_time ? (
                          <span className="text-amber-400/90 font-medium">
                            Starts: {new Date(match.match_start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        ) : (
                          <span>{match.match_id}</span>
                        )}
                      </div>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-2 mt-auto pt-2 border-t border-zinc-800">
                      {isDiscovered && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleScrapeAction(match.match_id, "approve-scrape")}
                            className="flex-1 text-xs font-semibold py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Approve Scraping"}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleScrapeAction(match.match_id, "reject-scrape")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-800 hover:bg-red-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Skip"}
                          </button>
                        </>
                      )}
                      {isScraping && (
                        <div className="flex gap-2 w-full">
                          <Link
                            href={`/matches/${match.match_id}/watch`}
                            className="flex-1 text-xs font-semibold py-1.5 text-center rounded-md bg-blue-600/80 hover:bg-blue-600 text-white transition-colors"
                          >
                            📺 Watch Live Ticks
                          </Link>
                          <button
                            disabled={busy}
                            onClick={() => handleScrapeAction(match.match_id, "reject-scrape")}
                            className="px-3 text-xs font-medium py-1.5 rounded-md bg-zinc-800 hover:bg-red-600/80 text-zinc-400 hover:text-white transition-colors disabled:opacity-50"
                            title="Stop scraping this match"
                          >
                            {busy ? "..." : "Stop"}
                          </button>
                        </div>
                      )}
                      {isSkipped && (
                        <button
                          disabled={busy}
                          onClick={() => handleScrapeAction(match.match_id, "approve-scrape")}
                          className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-800 hover:bg-emerald-600/80 text-zinc-400 hover:text-white transition-colors disabled:opacity-50"
                        >
                          {busy ? "..." : "Approve Scraping"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="stat-card text-center py-12">
              <p className="text-zinc-400 text-base font-semibold">No LotusBook matches match your filter</p>
              <p className="text-zinc-500 text-xs mt-1">
                The live scraper monitors LotusBook constantly. Matches will appear when detected.
              </p>
            </div>
          )}
        </>
      )}

      {/* ═══ TAB 2: TRAINING CORPUS ═══ */}
      {viewTab === "training" && (
        <>
          {/* Subheader and Filters */}
          <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-3 mb-4">
            <p className="text-zinc-400 text-xs">
              Official harmonized international cricket corpus (1,000 matches across T20I, ODI, and Test — 190,254 ticks).
            </p>

            <div className="flex flex-wrap gap-2 items-center">
              {/* Format Filter */}
              <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg border border-zinc-800 text-xs">
                <button
                  onClick={() => handleFormatFilterChange("all")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    formatFilter === "all" ? "bg-zinc-800 text-white font-bold" : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  All Formats ({formatCounts.total})
                </button>
                <button
                  onClick={() => handleFormatFilterChange("t20")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    formatFilter === "t20" ? "bg-indigo-600/40 text-indigo-300 font-bold" : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  T20I ({formatCounts.t20})
                </button>
                <button
                  onClick={() => handleFormatFilterChange("odi")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    formatFilter === "odi" ? "bg-blue-600/40 text-blue-300 font-bold" : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  ODI ({formatCounts.odi})
                </button>
                <button
                  onClick={() => handleFormatFilterChange("test")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    formatFilter === "test" ? "bg-purple-600/40 text-purple-300 font-bold" : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  Test ({formatCounts.test})
                </button>
              </div>

              {/* Status Filter */}
              <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg border border-zinc-800 text-xs">
                {(["all", "approved", "pending", "rejected"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => handleTrainingFilterChange(f)}
                    className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                      trainingFilter === f ? "bg-zinc-800 text-white font-bold" : "text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Pagination Controls Top */}
          <div className="flex items-center justify-between text-xs text-zinc-400 bg-zinc-900/60 px-4 py-2 rounded-lg border border-zinc-800 mb-4">
            <span>
              Showing <b className="text-zinc-200">{Math.min(filteredTrainingMatches.length, (currentPage - 1) * ITEMS_PER_PAGE + 1)}</b> to{" "}
              <b className="text-zinc-200">{Math.min(filteredTrainingMatches.length, currentPage * ITEMS_PER_PAGE)}</b> of{" "}
              <b className="text-zinc-200">{filteredTrainingMatches.length.toLocaleString()}</b> matches
            </span>
            <div className="flex items-center gap-2">
              <button
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 transition-colors"
              >
                ◀ Prev
              </button>
              <span>
                Page <b className="text-zinc-100">{currentPage}</b> of <b>{totalPages}</b>
              </span>
              <button
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 transition-colors"
              >
                Next ▶
              </button>
            </div>
          </div>

          {/* Cards Grid */}
          {loading && !data ? (
            <p className="text-zinc-500 text-sm py-8 text-center">Loading training matches...</p>
          ) : paginatedTrainingMatches.length ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {paginatedTrainingMatches.map((match) => {
                const isLive = liveIds.has(match.match_id);
                const isPending = match.training_status === "pending";
                const isApproved = match.training_status === "approved";
                const isRejected = match.training_status === "rejected";
                const busy = actionLoading === match.match_id;
                const format = getFormatLabel(match.match_id);

                return (
                  <div
                    key={match.match_id}
                    className="stat-card flex flex-col gap-3 relative overflow-hidden"
                  >
                    {/* Top Row: Format Pill & Status */}
                    <div className="flex items-center justify-between gap-2">
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded border ${format.badgeClass}`}>
                        {format.label}
                      </span>
                      <div className="flex items-center gap-2">
                        {isLive && (
                          <span className="flex items-center gap-1 text-xs text-red-400 font-bold">
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

                    {/* Tournament / Competition */}
                    {match.competition && (
                      <p className="text-xs text-zinc-400 font-medium truncate -mb-1">
                        🏆 {match.competition}
                      </p>
                    )}

                    {/* Team Names */}
                    <div>
                      <p className="text-base font-bold text-zinc-100 leading-tight">{match.team_home}</p>
                      <p className="text-xs text-zinc-500 my-0.5 font-medium">vs</p>
                      <p className="text-base font-bold text-zinc-100 leading-tight">{match.team_away}</p>
                    </div>

                    {/* Meta */}
                    <div className="flex items-center justify-between text-xs text-zinc-500 pt-1 border-t border-zinc-800/60">
                      <span className="font-medium text-zinc-400">
                        {match.tick_count.toLocaleString()} ticks
                      </span>
                      <span className="text-zinc-500 font-mono text-[11px]">
                        {match.match_id}
                      </span>
                    </div>

                    {/* Validation Result Diagnostic Panel */}
                    {validationResults[match.match_id] && (
                      <div className="bg-zinc-900/90 rounded-lg p-3 text-xs space-y-2 border border-zinc-700/60">
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
                              <div className={`px-2 py-1 rounded text-center font-semibold text-xs ${REC_COLORS[v.recommendation] ?? ""}`}>
                                {v.recommendation.toUpperCase()}: {v.recommendation_reason}
                              </div>
                              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-400 text-[11px]">
                                <span>Live ticks: <b className="text-zinc-200">{s.live_ticks}</b></span>
                                <span>Duration: <b className="text-zinc-200">{s.duration_minutes}m</b></span>
                                <span>Completeness: <b className="text-zinc-200">{(q.completeness * 100).toFixed(0)}%</b></span>
                                <span>Odds jumps: <b className="text-zinc-200">{q.odds_jump_count}</b></span>
                                <span>Home odds: <b className="text-zinc-200">{s.home_odds_range.min ?? "?"}-{s.home_odds_range.max ?? "?"}</b></span>
                                <span>Away odds: <b className="text-zinc-200">{s.away_odds_range.min ?? "?"}-{s.away_odds_range.max ?? "?"}</b></span>
                              </div>
                              {q.issues.length > 0 && (
                                <div className="text-red-400 text-[11px] pt-1 border-t border-zinc-800">
                                  {q.issues.map((issue, i) => <div key={i}>• {issue}</div>)}
                                </div>
                              )}
                            </>
                          );
                        })()}
                      </div>
                    )}

                    {/* Action Buttons */}
                    <div className="flex gap-2 mt-auto pt-2 border-t border-zinc-800">
                      <button
                        disabled={validating === match.match_id}
                        onClick={() => handleValidate(match.match_id)}
                        className="flex-1 text-xs font-semibold py-1.5 rounded-md bg-indigo-600/80 hover:bg-indigo-600 text-white transition-colors disabled:opacity-50"
                      >
                        {validating === match.match_id ? "Validating..." : "Validate"}
                      </button>

                      {isPending && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleTrainingAction(match.match_id, "approve")}
                            className="flex-1 text-xs font-semibold py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-50"
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
                        <>
                          <Link
                            href={`/matches/${match.match_id}/watch`}
                            className="text-xs font-medium py-1.5 px-2.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
                            title="Inspect virtual bets on this match"
                          >
                            Trades
                          </Link>
                          <button
                            onClick={() => setManualResultMatch(match)}
                            className="text-xs font-medium py-1.5 px-2 rounded-md bg-amber-600/80 hover:bg-amber-600 text-white transition-colors"
                            title="Submit match result manually"
                          >
                            📝 Result
                          </button>
                        </>
                      )}

                      {isRejected && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => handleTrainingAction(match.match_id, "approve")}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-800 hover:bg-emerald-600/80 text-zinc-300 hover:text-white transition-colors disabled:opacity-50"
                          >
                            {busy ? "..." : "Re-approve"}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleDelete(match.match_id)}
                            className="flex-1 text-xs font-medium py-1.5 rounded-md bg-zinc-800 hover:bg-red-600/80 text-zinc-400 hover:text-red-300 transition-colors disabled:opacity-50"
                            title="Permanently delete match data"
                          >
                            {busy ? "..." : "Delete"}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="stat-card text-center py-12">
              <p className="text-zinc-400 text-base font-semibold">No training matches match your query</p>
              <p className="text-zinc-500 text-xs mt-1">
                Try selecting &quot;All Sources&quot; or clearing your search term.
              </p>
            </div>
          )}

          {/* Pagination Controls Bottom */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 mt-6 text-xs text-zinc-400">
              <button
                disabled={currentPage <= 1}
                onClick={() => {
                  setCurrentPage((p) => Math.max(1, p - 1));
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                className="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 transition-colors font-medium"
              >
                ◀ Previous Page
              </button>
              <span>
                Page <b className="text-zinc-100">{currentPage}</b> of <b>{totalPages}</b>
              </span>
              <button
                disabled={currentPage >= totalPages}
                onClick={() => {
                  setCurrentPage((p) => Math.min(totalPages, p + 1));
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                className="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 transition-colors font-medium"
              >
                Next Page ▶
              </button>
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
