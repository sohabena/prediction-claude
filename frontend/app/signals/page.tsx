"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { useApi } from "@/hooks/useApi";
import { MODEL_REGISTRY, MODEL_SECTIONS, ModelMeta } from "@/lib/modelMeta";
import { ModelInfoPopover } from "@/components/common/ModelInfoPopover";
import {
  Zap,
  Volume2,
  VolumeX,
  Copy,
  Check,
  ShieldCheck,
  TrendingUp,
  Clock,
  Radio,
  Filter,
  CheckSquare,
  Square,
  Sparkles,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  Activity,
  Flame,
} from "lucide-react";

interface SignalItem {
  bet_id: string;
  variant: string;
  match_id: string;
  match_name: string;
  competition: string;
  team_home: string;
  team_away: string;
  side: "BACK" | "LAY";
  selection: "home" | "away";
  team: string;
  price: number;
  price_limit?: number | null;
  price_filled?: number | null;
  stake: number;
  liability: number;
  reason: "enter" | "add" | "reduce" | "flip" | "green_up";
  edge?: number | null;
  p_model?: number | null;
  p_market?: number | null;
  status: string;
  in_play: boolean;
  score_state?: string | null;
  batting_team?: string | null;
  is_live: boolean;
  decided_at: string;
  age_seconds?: number | null;
  is_fresh: boolean;
}

interface SignalsResponse {
  generated_at: string;
  total: number;
  signals: SignalItem[];
}

const DEFAULT_ENABLED_MODELS: Record<string, boolean> = {
  // By default, enable the 7 Guaranteed Green-Up Specialists as preferred by user
  bmodel_green_v1: true,
  bmodel_scalp_v1: true,
  bmodel_scalp_fast: true,
  bmodel_swing_v1: true,
  bmodel_deep_v1: true,
  bmodel_fav_lock_v1: true,
  w05_e06_green: true,
};

function playSignalChime() {
  try {
    const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioContextClass();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1174.66, ctx.currentTime + 0.15);
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  } catch {
    // Ignore audio autoplay restrictions
  }
}

function formatRelativeTime(seconds?: number | null): string {
  if (seconds === undefined || seconds === null) return "";
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s ago`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ago`;
}

function ReasonBadge({ reason }: { reason: string }) {
  switch (reason) {
    case "green_up":
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/50 bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-bold text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.2)]">
          🎯 GREEN UP
        </span>
      );
    case "enter":
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-sky-500/50 bg-sky-500/15 px-2.5 py-0.5 text-[11px] font-bold text-sky-400">
          ⚡ ENTER
        </span>
      );
    case "add":
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-blue-500/50 bg-blue-500/15 px-2.5 py-0.5 text-[11px] font-bold text-blue-400">
          ➕ ADD
        </span>
      );
    case "reduce":
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/50 bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-bold text-amber-400">
          🛡️ REDUCE
        </span>
      );
    case "flip":
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-purple-500/50 bg-purple-500/15 px-2.5 py-0.5 text-[11px] font-bold text-purple-400">
          🔄 FLIP
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-[10px] uppercase font-semibold text-zinc-300">
          {reason}
        </span>
      );
  }
}

export default function SignalsPage() {
  const { data, loading, error, refresh } = useApi<SignalsResponse>("/paper/signals", 5000);

  // Enabled models state (persisted to localStorage)
  const [enabledModels, setEnabledModels] = useState<Record<string, boolean>>(DEFAULT_ENABLED_MODELS);
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [filterReason, setFilterReason] = useState<"all" | "green_up" | "enter_add">("all");
  const [filterFreshOnly, setFilterFreshOnly] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [modelFilterOpen, setModelFilterOpen] = useState(false);

  const prevSignalsCountRef = useRef(0);

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("phoenix_signals_enabled_models");
      if (saved) {
        setEnabledModels(JSON.parse(saved));
      }
      const savedAudio = localStorage.getItem("phoenix_signals_audio");
      if (savedAudio !== null) {
        setAudioEnabled(savedAudio === "true");
      }
    } catch {
      // Local storage unavailable
    }
  }, []);

  // Save to localStorage when changed
  const updateEnabledModels = (newMap: Record<string, boolean>) => {
    setEnabledModels(newMap);
    try {
      localStorage.setItem("phoenix_signals_enabled_models", JSON.stringify(newMap));
    } catch {}
  };

  const toggleModel = (variantCode: string) => {
    const updated = { ...enabledModels, [variantCode]: !enabledModels[variantCode] };
    updateEnabledModels(updated);
  };

  const toggleAudio = () => {
    const next = !audioEnabled;
    setAudioEnabled(next);
    try {
      localStorage.setItem("phoenix_signals_audio", String(next));
    } catch {}
    if (next) playSignalChime();
  };

  // Play chime when new signals arrive for enabled models
  useEffect(() => {
    if (!data?.signals) return;
    const currentEnabledCount = data.signals.filter((s) => enabledModels[s.variant]).length;
    if (audioEnabled && currentEnabledCount > prevSignalsCountRef.current && prevSignalsCountRef.current > 0) {
      playSignalChime();
    }
    prevSignalsCountRef.current = currentEnabledCount;
  }, [data, audioEnabled, enabledModels]);

  // Presets
  const applyPreset = (type: "green" | "all" | "kelly" | "dynamics" | "none") => {
    const next: Record<string, boolean> = {};
    Object.keys(MODEL_REGISTRY).forEach((code) => {
      const meta = MODEL_REGISTRY[code];
      if (type === "all") next[code] = true;
      else if (type === "none") next[code] = false;
      else if (type === "green") next[code] = meta.section === "green_up";
      else if (type === "kelly") next[code] = meta.section === "directional";
      else if (type === "dynamics") next[code] = meta.section === "dynamics";
    });
    updateEnabledModels(next);
  };

  // Filter signals
  const filteredSignals = useMemo(() => {
    if (!data?.signals) return [];
    return data.signals.filter((s) => {
      // Check if model enabled
      if (!enabledModels[s.variant]) return false;

      // Reason filter
      if (filterReason === "green_up" && s.reason !== "green_up") return false;
      if (filterReason === "enter_add" && s.reason !== "enter" && s.reason !== "add") return false;

      // Fresh filter (past 15m)
      if (filterFreshOnly && (s.age_seconds === null || s.age_seconds === undefined || s.age_seconds > 900)) return false;

      return true;
    });
  }, [data?.signals, enabledModels, filterReason, filterFreshOnly]);

  // Count signals per model in the current dataset
  const signalCountsByModel = useMemo(() => {
    const map: Record<string, number> = {};
    data?.signals?.forEach((s) => {
      map[s.variant] = (map[s.variant] || 0) + 1;
    });
    return map;
  }, [data?.signals]);

  const enabledCount = Object.values(enabledModels).filter(Boolean).length;
  const freshCount = data?.signals?.filter((s) => s.is_fresh).length || 0;

  const copySignalText = (sig: SignalItem) => {
    const text = `${sig.side} ${sig.team} @ ${sig.price.toFixed(2)} (Stake: ${Math.round(sig.stake).toLocaleString()}) - ${sig.reason.toUpperCase()}`;
    navigator.clipboard.writeText(text);
    setCopiedId(sig.bet_id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="space-y-6 pb-20">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-zinc-800/80 pb-6">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-zinc-100 flex items-center gap-2">
              <Zap className="h-7 w-7 text-phoenix-400" />
              Live Trading Signals
            </h1>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-950/40 px-2.5 py-0.5 text-xs font-semibold text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              LIVE STREAM
            </span>
          </div>
          <p className="text-sm text-zinc-400 mt-1">
            Real-time execution signals generated by active models. Turn models on/off to personalize your execution feed.
          </p>
        </div>

        {/* Global Action Bar */}
        <div className="flex items-center gap-2.5 flex-wrap">
          <button
            type="button"
            onClick={toggleAudio}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              audioEnabled
                ? "border-emerald-500/40 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/50"
                : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
            }`}
            title={audioEnabled ? "Audio chimes active for new signals" : "Click to enable audio chimes"}
          >
            {audioEnabled ? <Volume2 className="h-4 w-4 text-emerald-400" /> : <VolumeX className="h-4 w-4 text-zinc-500" />}
            <span>{audioEnabled ? "Audio Alert On" : "Audio Muted"}</span>
          </button>

          <button
            type="button"
            onClick={() => setModelFilterOpen(!modelFilterOpen)}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              modelFilterOpen
                ? "border-phoenix-500/40 bg-phoenix-950/40 text-phoenix-400"
                : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
            }`}
          >
            <Filter className="h-4 w-4" />
            <span>Model Filters ({enabledCount}/19)</span>
          </button>

          <button
            type="button"
            onClick={() => refresh()}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-3.5 backdrop-blur-sm">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 block mb-1">
            Active Signals
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-zinc-100">{filteredSignals.length}</span>
            <span className="text-xs text-zinc-500">matching filters</span>
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-3.5 backdrop-blur-sm">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400 block mb-1 flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" /> Fresh (&lt;3 min)
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-emerald-400">{freshCount}</span>
            <span className="text-xs text-zinc-500">immediate action</span>
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-3.5 backdrop-blur-sm">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 block mb-1">
            Enabled Models
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-phoenix-400">{enabledCount}</span>
            <span className="text-xs text-zinc-500">of 19 total</span>
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-3.5 backdrop-blur-sm">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 block mb-1">
            Green-Up Signals
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-emerald-400">
              {data?.signals?.filter((s) => s.reason === "green_up").length || 0}
            </span>
            <span className="text-xs text-zinc-500">profit locks</span>
          </div>
        </div>
      </div>

      {/* Model Toggle Panel (Collapsible / Expandable) */}
      {modelFilterOpen && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/90 p-5 shadow-2xl backdrop-blur-xl animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-zinc-800 pb-4 mb-5">
            <div>
              <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
                <Layers className="h-4 w-4 text-phoenix-400" />
                Select Which Models Send Signals
              </h2>
              <p className="text-xs text-zinc-400">
                Turn on only the strategies you wish to trade. Signals from disabled models will be hidden.
              </p>
            </div>

            {/* Quick Presets */}
            <div className="flex items-center gap-2 flex-wrap text-xs">
              <button
                type="button"
                onClick={() => applyPreset("green")}
                className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-2.5 py-1 font-semibold text-emerald-300 hover:bg-emerald-500/25 transition-colors"
              >
                ⭐ Green-Up Only (7)
              </button>
              <button
                type="button"
                onClick={() => applyPreset("kelly")}
                className="rounded-lg border border-sky-500/40 bg-sky-500/15 px-2.5 py-1 font-semibold text-sky-300 hover:bg-sky-500/25 transition-colors"
              >
                Directional Kelly (6)
              </button>
              <button
                type="button"
                onClick={() => applyPreset("all")}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1 font-medium text-zinc-200 hover:bg-zinc-700 transition-colors"
              >
                All Models
              </button>
              <button
                type="button"
                onClick={() => applyPreset("none")}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2.5 py-1 font-medium text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                Deselect All
              </button>
            </div>
          </div>

          {/* Grouped Model Checkboxes */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {Object.values(MODEL_SECTIONS).map((sec) => {
              const sectionModels = Object.values(MODEL_REGISTRY).filter((m) => m.section === sec.id);
              const allSectionEnabled = sectionModels.every((m) => enabledModels[m.code]);

              const toggleAllSection = () => {
                const nextState = !allSectionEnabled;
                const next = { ...enabledModels };
                sectionModels.forEach((m) => {
                  next[m.code] = nextState;
                });
                updateEnabledModels(next);
              };

              return (
                <div key={sec.id} className="rounded-xl border border-zinc-800/80 bg-zinc-950/60 p-3.5 space-y-2.5">
                  <div className="flex items-center justify-between border-b border-zinc-800/80 pb-2">
                    <span className="font-semibold text-xs text-zinc-200">{sec.title}</span>
                    <button
                      type="button"
                      onClick={toggleAllSection}
                      className="text-[11px] text-phoenix-400 hover:underline"
                    >
                      {allSectionEnabled ? "Disable All" : "Enable All"}
                    </button>
                  </div>

                  <div className="space-y-1.5">
                    {sectionModels.map((meta) => {
                      const isChecked = !!enabledModels[meta.code];
                      const count = signalCountsByModel[meta.code] || 0;

                      return (
                        <div
                          key={meta.code}
                          onClick={() => toggleModel(meta.code)}
                          className={`flex items-center justify-between p-2 rounded-lg cursor-pointer transition-colors ${
                            isChecked
                              ? "bg-zinc-900 border border-zinc-700/80 text-zinc-100"
                              : "bg-zinc-950/40 border border-transparent text-zinc-500 hover:bg-zinc-900/50"
                          }`}
                        >
                          <div className="flex items-center gap-2 overflow-hidden pr-1">
                            {isChecked ? (
                              <CheckSquare className="h-4 w-4 text-emerald-400 shrink-0" />
                            ) : (
                              <Square className="h-4 w-4 text-zinc-600 shrink-0" />
                            )}
                            <div className="truncate">
                              <div className="text-xs font-medium truncate">{meta.displayName}</div>
                              <div className="text-[10px] text-zinc-400 font-mono">{meta.code}</div>
                            </div>
                          </div>

                          <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                            {count > 0 && (
                              <span className="rounded-full bg-zinc-800 px-1.5 py-0.2 text-[10px] font-mono text-zinc-300">
                                {count}
                              </span>
                            )}
                            <ModelInfoPopover meta={meta} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Filter Chips Bar */}
      <div className="flex items-center justify-between gap-3 flex-wrap bg-zinc-900/40 p-2.5 rounded-xl border border-zinc-800/60">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-zinc-400 mr-1 flex items-center gap-1">
            <Filter className="h-3.5 w-3.5" /> Filter:
          </span>
          <button
            type="button"
            onClick={() => setFilterReason("all")}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
              filterReason === "all"
                ? "bg-zinc-800 text-zinc-100 font-semibold"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            All Action Types
          </button>
          <button
            type="button"
            onClick={() => setFilterReason("green_up")}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
              filterReason === "green_up"
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-semibold"
                : "text-zinc-400 hover:text-emerald-400"
            }`}
          >
            🎯 Green-Up Only
          </button>
          <button
            type="button"
            onClick={() => setFilterReason("enter_add")}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
              filterReason === "enter_add"
                ? "bg-sky-500/20 text-sky-300 border border-sky-500/40 font-semibold"
                : "text-zinc-400 hover:text-sky-400"
            }`}
          >
            ⚡ New Entries & Adds
          </button>
        </div>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-zinc-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={filterFreshOnly}
              onChange={(e) => setFilterFreshOnly(e.target.checked)}
              className="rounded border-zinc-700 bg-zinc-900 text-phoenix-500 focus:ring-0"
            />
            <span>Recent Only (&lt;15m)</span>
          </label>
        </div>
      </div>

      {/* Signal Cards Feed */}
      {loading && !data ? (
        <div className="text-center py-20 text-zinc-500 animate-pulse">Loading real-time signal stream...</div>
      ) : filteredSignals.length === 0 ? (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-12 text-center space-y-3">
          <div className="h-12 w-12 rounded-full bg-zinc-800/80 flex items-center justify-center mx-auto text-zinc-400">
            <Radio className="h-6 w-6 animate-pulse" />
          </div>
          <h3 className="text-base font-semibold text-zinc-200">Listening for signals...</h3>
          <p className="text-xs text-zinc-400 max-w-md mx-auto">
            {enabledCount === 0
              ? "All models are currently turned off. Click 'Model Filters' above to enable the models you want to receive signals from."
              : "No signals match your active model and filter selection. The trading loop evaluates in-play ticks every 15 seconds."}
          </p>
          {enabledCount === 0 && (
            <button
              type="button"
              onClick={() => applyPreset("green")}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2 text-xs font-bold text-zinc-950 hover:bg-emerald-400 transition-colors"
            >
              Enable Guaranteed Green-Up Specialists
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredSignals.map((sig) => {
            const meta: ModelMeta | undefined = MODEL_REGISTRY[sig.variant];
            const isLay = sig.side === "LAY";
            const isFresh = sig.is_fresh;

            return (
              <div
                key={sig.bet_id}
                className={`relative rounded-2xl border bg-zinc-900/80 p-5 shadow-xl backdrop-blur-md transition-all duration-200 hover:border-zinc-600/80 ${
                  sig.reason === "green_up"
                    ? "border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.08)]"
                    : isFresh
                    ? "border-phoenix-500/40 shadow-[0_0_20px_rgba(249,115,22,0.08)]"
                    : "border-zinc-800/90"
                }`}
              >
                {/* Card Top: Model & Timing */}
                <div className="flex items-start justify-between gap-2 border-b border-zinc-800/80 pb-3 mb-3">
                  <div className="space-y-0.5 overflow-hidden">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-semibold text-zinc-100 text-sm truncate">
                        {meta?.displayName || sig.variant}
                      </span>
                      {meta && <ModelInfoPopover meta={meta} />}
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-zinc-400">
                      <span className="font-mono text-zinc-400 bg-zinc-950 px-1 rounded border border-zinc-800">
                        {sig.variant}
                      </span>
                      <span>•</span>
                      <ReasonBadge reason={sig.reason} />
                    </div>
                  </div>

                  <div className="text-right shrink-0">
                    <div className="flex items-center gap-1 text-[11px] text-zinc-400 font-mono justify-end">
                      {isFresh && <span className="h-2 w-2 rounded-full bg-emerald-400 animate-ping inline-block" />}
                      <span>{formatRelativeTime(sig.age_seconds)}</span>
                    </div>
                    <span className="text-[10px] text-zinc-400 block mt-0.5 uppercase tracking-wider">
                      {sig.status}
                    </span>
                  </div>
                </div>

                {/* Match Banner */}
                <div className="bg-zinc-950/60 rounded-xl p-2.5 border border-zinc-800/60 mb-3 space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-zinc-200 truncate">{sig.match_name}</span>
                    {sig.is_live && (
                      <span className="rounded bg-rose-500/20 text-rose-400 border border-rose-500/30 text-[10px] font-bold px-1.5 py-0.2 shrink-0">
                        LIVE
                      </span>
                    )}
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-zinc-400">
                    <span className="truncate">{sig.competition}</span>
                    {sig.score_state && (
                      <span className="font-mono font-medium text-emerald-400 shrink-0">
                        {sig.score_state}
                      </span>
                    )}
                  </div>
                </div>

                {/* Main Order Box (Execution Callout) */}
                <div
                  className={`rounded-xl border p-3.5 mb-3 space-y-2 ${
                    isLay
                      ? "border-rose-500/30 bg-rose-950/20"
                      : "border-emerald-500/30 bg-emerald-950/20"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-black tracking-wider uppercase ${
                        isLay
                          ? "bg-rose-500 text-zinc-950 shadow-[0_0_12px_rgba(244,63,94,0.4)]"
                          : "bg-emerald-500 text-zinc-950 shadow-[0_0_12px_rgba(16,185,129,0.4)]"
                      }`}
                    >
                      {isLay ? <ArrowDownRight className="h-3.5 w-3.5" /> : <ArrowUpRight className="h-3.5 w-3.5" />}
                      {sig.side}
                    </span>

                    <div className="text-right">
                      <span className="text-[11px] text-zinc-400 uppercase tracking-wider block">Target Odds</span>
                      <span className="text-xl font-mono font-black text-zinc-100">
                        @{sig.price.toFixed(2)}
                      </span>
                    </div>
                  </div>

                  <div className="pt-1">
                    <span className="text-[11px] text-zinc-400 uppercase tracking-wider block">Selection</span>
                    <span className="text-base font-bold text-zinc-100 truncate block">
                      {sig.team}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-zinc-800/60 text-xs">
                    <div>
                      <span className="text-zinc-400 block text-[10px] uppercase">Stake</span>
                      <span className="font-mono font-bold text-zinc-200">
                        {Math.round(sig.stake).toLocaleString()} pts
                      </span>
                    </div>
                    <div className="text-right">
                      <span className="text-zinc-400 block text-[10px] uppercase">
                        {isLay ? "Max Liability" : "Potential Win"}
                      </span>
                      <span className={`font-mono font-bold ${isLay ? "text-rose-400" : "text-emerald-400"}`}>
                        {Math.round(isLay ? sig.liability : sig.stake * (sig.price - 1)).toLocaleString()} pts
                      </span>
                    </div>
                  </div>
                </div>

                {/* Edge & Probabilities */}
                <div className="flex items-center justify-between text-[11px] text-zinc-400 pt-1 pb-2 px-1">
                  <div>
                    <span>Edge: </span>
                    <span className="font-mono font-bold text-emerald-400">
                      {sig.edge !== null && sig.edge !== undefined
                        ? `${(sig.edge * 100).toFixed(1)}%`
                        : "—"}
                    </span>
                  </div>
                  <div>
                    <span>Model: </span>
                    <span className="font-mono text-zinc-200">
                      {sig.p_model ? `${Math.round(sig.p_model * 100)}%` : "—"}
                    </span>
                    <span className="mx-1 text-zinc-600">vs</span>
                    <span>Mkt: </span>
                    <span className="font-mono text-zinc-200">
                      {sig.p_market ? `${Math.round(sig.p_market * 100)}%` : "—"}
                    </span>
                  </div>
                </div>

                {/* Card Action Footer */}
                <div className="pt-2 border-t border-zinc-800/80">
                  <button
                    type="button"
                    onClick={() => copySignalText(sig)}
                    className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-zinc-700/80 bg-zinc-800/80 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-700 hover:text-white transition-colors"
                  >
                    {copiedId === sig.bet_id ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-emerald-400" />
                        <span className="text-emerald-400">Copied to Clipboard!</span>
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5" />
                        <span>Copy Order Callout</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
