"use client";

import { useState } from "react";
import { useApi } from "@/hooks/useApi";
import { API_BASE } from "@/lib/api";
import { StatCard } from "@/components/StatCard";
import { SignalCard } from "@/components/SignalCard";

interface Signal {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  recommended_action: string;
  confidence: number;
  action_probabilities: Record<string, number>;
  timestamp: string;
}

interface SignalsResponse {
  signals: Signal[];
  generated_at: string | null;
  model_version: number;
  message?: string;
}

interface OrchestratorState {
  state: string;
  model_version: number;
  curriculum_stage: string;
  updated_at: string | null;
}

interface OrchestratorStats {
  started_at: string | null;
  state_transitions: Array<{ from: string; to: string; at: string }>;
  training_runs: number;
  eval_runs: number;
  matches_trained_on: number;
  demotions?: number;
  last_demotion?: { at: string; reason: string[] };
  accumulation?: {
    total_matches: number;
    total_ticks: number;
    qualifying_matches: number;
    min_required: number;
    ready_to_train: boolean;
  };
}

interface ShadowPerformance {
  balance: number;
  initial_balance: number;
  total_bets: number;
  total_wins: number;
  win_rate: number;
  total_pnl: number;
  roi: number;
  drawdown: number;
  peak_balance: number;
  open_bets: number;
  sharpe_ratio: number;
  recent_window: {
    n: number;
    win_rate: number;
    pnl: number;
    roi: number;
  };
  calibration: Array<{
    bucket: string;
    count: number;
    actual_win_rate: number;
  }>;
  daily_history: Array<{ date: string; pnl: number }>;
}

interface DriftStatus {
  is_drifting: boolean;
  drift_detected_at: string | null;
  consecutive_drift_days: number;
  should_demote: boolean;
  violations: string[];
  metrics: Record<string, number>;
}

interface SpecialistMetric {
  key: string;
  format_name: string;
  matches_trained: number;
  matches_tested: number;
  total_bets: number;
  avg_bets_per_match: number;
  hedged_bets: number;
  hedge_rate_pct: number;
  win_rate_pct: number;
  total_pnl: number;
  mean_roi_pct: number;
  sharpe_ratio: number;
  profit_factor: number;
}

interface SpecialistInfo {
  key: string;
  format: string;
  available: boolean;
  path: string;
  matches_trained_on: number;
  metrics?: SpecialistMetric;
}

interface SpecialistsResponse {
  specialists: Record<string, SpecialistInfo>;
  total_available: number;
  active_strategy: string;
}

const stateLabels: Record<string, string> = {
  accumulating: "Accumulating Data",
  offline_training: "Offline Training",
  online_training: "Online Training",
  virtual_trading: "Virtual Trading",
  graduated: "Graduated",
};

const stateColors: Record<string, "green" | "yellow" | "red" | "default"> = {
  accumulating: "yellow",
  offline_training: "yellow",
  online_training: "yellow",
  virtual_trading: "green",
  graduated: "green",
};

export default function AdvisorPage() {
  const [demoting, setDemoting] = useState(false);
  const [demoteMsg, setDemoteMsg] = useState<string | null>(null);

  const { data: signals, loading: signalsLoading } =
    useApi<SignalsResponse>("/advisor/signals", 10000);

  const { data: orchState } =
    useApi<OrchestratorState>("/advisor/state", 15000);

  const { data: orchStats } = useApi<OrchestratorStats>("/advisor/stats", 30000);

  const { data: shadowPerf } =
    useApi<ShadowPerformance>("/advisor/shadow/performance", 15000);

  const { data: driftStatus } =
    useApi<DriftStatus>("/advisor/shadow/drift", 15000);

  const { data: specialistsData } =
    useApi<SpecialistsResponse>("/advisor/specialists", 10000);

  const currentState = orchState?.state || "accumulating";
  const isGraduated = currentState === "graduated";

  const handleDemote = async () => {
    if (!confirm("Are you sure you want to demote the agent? It will need to re-graduate (14 days) before generating signals again.")) {
      return;
    }
    setDemoting(true);
    setDemoteMsg(null);
    try {
      const res = await fetch(`${API_BASE}/advisor/demote`, {
        method: "POST",
      });
      const data = await res.json();
      if (data.success) {
        setDemoteMsg("Agent demoted. It will re-enter virtual trading.");
      } else {
        setDemoteMsg(data.detail || "Demotion failed.");
      }
    } catch {
      setDemoteMsg("Network error. Is the backend running?");
    } finally {
      setDemoting(false);
    }
  };

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Advisor</h1>
      <p className="text-zinc-400 mb-8">
        {isGraduated
          ? "Agent has graduated. Showing live bet suggestions with continuous shadow validation."
          : "Agent is training. Bet suggestions will appear after graduation."}
      </p>

      {/* Drift Warning Banner */}
      {driftStatus?.is_drifting && isGraduated && (
        <div className={`rounded-xl p-4 mb-6 border ${
          driftStatus.should_demote
            ? "bg-red-950/50 border-red-800"
            : "bg-amber-950/50 border-amber-800"
        }`}>
          <div className="flex items-start gap-3">
            <span className="text-2xl">{driftStatus.should_demote ? "!!!" : "!"}</span>
            <div className="flex-1">
              <h3 className={`font-semibold ${
                driftStatus.should_demote ? "text-red-400" : "text-amber-400"
              }`}>
                {driftStatus.should_demote
                  ? "Auto-Demotion Triggered"
                  : `Performance Drift Detected (Day ${driftStatus.consecutive_drift_days}/5)`}
              </h3>
              <ul className="mt-1 space-y-0.5">
                {driftStatus.violations.map((v, i) => (
                  <li key={i} className="text-sm text-zinc-400">{v}</li>
                ))}
              </ul>
              {!driftStatus.should_demote && (
                <p className="text-xs text-zinc-500 mt-2">
                  Agent will auto-demote after 5 consecutive drift days.
                  Nightly retraining may resolve this.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Orchestrator state cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Lifecycle State"
          value={stateLabels[currentState] || currentState}
          color={stateColors[currentState] || "default"}
        />
        <StatCard
          label="Model Version"
          value={`v${orchState?.model_version || 0}`}
        />
        <StatCard
          label="Curriculum Stage"
          value={orchState?.curriculum_stage || "N/A"}
        />
        <StatCard
          label="Training Runs"
          value={String(orchStats?.training_runs || 0)}
        />
      </div>

      {/* Format Specialist Model Zoo */}
      {specialistsData && specialistsData.total_available > 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-8">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4 pb-3 border-b border-zinc-800">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-zinc-100">Format Specialist Model Zoo</h2>
                <span className="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  {specialistsData.total_available} Models Active
                </span>
              </div>
              <p className="text-xs text-zinc-400 mt-0.5">
                Two-way rate trading (20–30 bets/match, $100k budget, exchange liability control)
              </p>
            </div>
            <div className="text-right">
              <span className="text-xs text-zinc-500 uppercase tracking-wider">Strategy Architecture</span>
              <p className="text-sm font-medium text-cyan-400">Dynamic Format Router</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {Object.values(specialistsData.specialists).map((spec) => {
              const m = spec.metrics;
              const formatColor =
                spec.key === "ipl"
                  ? "border-amber-500/30 bg-amber-500/5 text-amber-400"
                  : spec.key === "t20i"
                  ? "border-purple-500/30 bg-purple-500/5 text-purple-400"
                  : spec.key === "test"
                  ? "border-rose-500/30 bg-rose-500/5 text-rose-400"
                  : "border-blue-500/30 bg-blue-500/5 text-blue-400";

              return (
                <div
                  key={spec.key}
                  className="bg-zinc-800/40 border border-zinc-800 hover:border-zinc-700 transition-colors rounded-lg p-4 flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className={`text-xs px-2 py-0.5 rounded font-medium border ${formatColor}`}>
                        {spec.key.toUpperCase()}
                      </span>
                      <span className="text-xs font-medium text-emerald-400 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                        Active
                      </span>
                    </div>

                    <h3 className="font-semibold text-sm text-zinc-200 mb-1">{spec.format}</h3>
                    <p className="text-xs text-zinc-500 mb-3">
                      Corpus: {spec.matches_trained_on} matches ({m ? m.matches_trained : 244} train / {m ? m.matches_tested : 54} test)
                    </p>

                    {m && (
                      <div className="space-y-2 mb-3 bg-zinc-900/60 p-2.5 rounded border border-zinc-800/80">
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Win Rate:</span>
                          <span className="font-bold text-emerald-400">{m.win_rate_pct.toFixed(1)}%</span>
                        </div>
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Total P&L:</span>
                          <span className="font-bold text-emerald-400">+${m.total_pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                        </div>
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Mean ROI:</span>
                          <span className="font-semibold text-emerald-400">+{m.mean_roi_pct.toFixed(1)}%</span>
                        </div>
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Sharpe Ratio:</span>
                          <span className="font-semibold text-cyan-400">{m.sharpe_ratio.toFixed(2)}</span>
                        </div>
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Bets / Match:</span>
                          <span className="font-semibold text-zinc-200">{m.avg_bets_per_match.toFixed(1)} (target: 25)</span>
                        </div>
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-zinc-400">Hedge Ratio:</span>
                          <span className="font-semibold text-zinc-300">{m.hedge_rate_pct.toFixed(1)}%</span>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="pt-2 border-t border-zinc-800/60 flex items-center justify-between text-[11px] text-zinc-500">
                    <span>Liability: &le; 2.20 lay</span>
                    <span>Budget: $100k/match</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Shadow Trading Performance (shown when graduated) */}
      {isGraduated && shadowPerf && shadowPerf.total_bets > 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Shadow Trading (Post-Graduation)</h2>
            <span className="text-xs text-zinc-500">
              Continuous virtual validation
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-4 mb-5">
            <div>
              <p className="text-xs text-zinc-500 uppercase">Balance</p>
              <p className={`text-xl font-bold ${
                shadowPerf.balance >= shadowPerf.initial_balance
                  ? "text-emerald-400" : "text-red-400"
              }`}>
                {shadowPerf.balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Shadow P&L</p>
              <p className={`text-xl font-bold ${
                shadowPerf.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"
              }`}>
                {shadowPerf.total_pnl >= 0 ? "+" : ""}
                {shadowPerf.total_pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Win Rate</p>
              <p className={`text-xl font-bold ${
                shadowPerf.win_rate >= 0.55 ? "text-emerald-400"
                  : shadowPerf.win_rate >= 0.50 ? "text-amber-400"
                  : "text-red-400"
              }`}>
                {(shadowPerf.win_rate * 100).toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">ROI</p>
              <p className={`text-xl font-bold ${
                shadowPerf.roi >= 0.08 ? "text-emerald-400"
                  : shadowPerf.roi >= 0 ? "text-amber-400"
                  : "text-red-400"
              }`}>
                {(shadowPerf.roi * 100).toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Sharpe</p>
              <p className={`text-xl font-bold ${
                shadowPerf.sharpe_ratio >= 1.5 ? "text-emerald-400"
                  : shadowPerf.sharpe_ratio >= 1.0 ? "text-amber-400"
                  : "text-red-400"
              }`}>
                {shadowPerf.sharpe_ratio.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Drawdown</p>
              <p className={`text-xl font-bold ${
                shadowPerf.drawdown <= 0.10 ? "text-emerald-400"
                  : shadowPerf.drawdown <= 0.15 ? "text-amber-400"
                  : "text-red-400"
              }`}>
                {(shadowPerf.drawdown * 100).toFixed(1)}%
              </p>
            </div>
          </div>

          {/* Recent window */}
          <div className="bg-zinc-800/50 rounded-lg p-3 mb-4">
            <p className="text-xs text-zinc-500 uppercase mb-2">
              Recent Window (Last {shadowPerf.recent_window.n} bets)
            </p>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <span className="text-xs text-zinc-500">Win Rate</span>
                <p className={`text-sm font-semibold ${
                  shadowPerf.recent_window.win_rate >= 0.55
                    ? "text-emerald-400" : shadowPerf.recent_window.win_rate >= 0.50
                    ? "text-amber-400" : "text-red-400"
                }`}>
                  {(shadowPerf.recent_window.win_rate * 100).toFixed(1)}%
                </p>
              </div>
              <div>
                <span className="text-xs text-zinc-500">P&L</span>
                <p className={`text-sm font-semibold ${
                  shadowPerf.recent_window.pnl >= 0 ? "text-emerald-400" : "text-red-400"
                }`}>
                  {shadowPerf.recent_window.pnl >= 0 ? "+" : ""}
                  {shadowPerf.recent_window.pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </p>
              </div>
              <div>
                <span className="text-xs text-zinc-500">ROI</span>
                <p className={`text-sm font-semibold ${
                  shadowPerf.recent_window.roi >= 0 ? "text-emerald-400" : "text-red-400"
                }`}>
                  {(shadowPerf.recent_window.roi * 100).toFixed(1)}%
                </p>
              </div>
            </div>
          </div>

          {/* Confidence Calibration */}
          {shadowPerf.calibration.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-zinc-500 uppercase mb-2">
                Confidence Calibration
              </p>
              <div className="space-y-1.5">
                {shadowPerf.calibration.map((cal) => (
                  <div key={cal.bucket} className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400 w-16">{cal.bucket}</span>
                    <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          cal.actual_win_rate >= 0.55 ? "bg-emerald-500"
                            : cal.actual_win_rate >= 0.50 ? "bg-amber-500"
                            : "bg-red-500"
                        }`}
                        style={{ width: `${cal.actual_win_rate * 100}%` }}
                      />
                    </div>
                    <span className="text-xs text-zinc-500 w-20 text-right">
                      {(cal.actual_win_rate * 100).toFixed(0)}% ({cal.count})
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-xs text-zinc-600 mt-1">
                Higher confidence buckets should have higher actual win rates.
              </p>
            </div>
          )}

          {/* Daily P&L mini chart */}
          {shadowPerf.daily_history.length > 0 && (
            <div>
              <p className="text-xs text-zinc-500 uppercase mb-2">
                Daily P&L (Last 30 Days)
              </p>
              <div className="flex items-end gap-0.5 h-16">
                {shadowPerf.daily_history.map((day) => {
                  const maxPnl = Math.max(
                    ...shadowPerf.daily_history.map((d) => Math.abs(d.pnl)),
                    1
                  );
                  const pct = Math.abs(day.pnl) / maxPnl;
                  return (
                    <div
                      key={day.date}
                      className="flex-1 flex flex-col justify-end h-full"
                      title={`${day.date}: ${day.pnl >= 0 ? "+" : ""}${day.pnl}`}
                    >
                      {day.pnl >= 0 ? (
                        <div
                          className="bg-emerald-500/60 rounded-t-sm w-full"
                          style={{ height: `${Math.max(pct * 100, 2)}%` }}
                        />
                      ) : (
                        <div className="flex-1" />
                      )}
                      {day.pnl < 0 && (
                        <div
                          className="bg-red-500/60 rounded-b-sm w-full"
                          style={{ height: `${Math.max(pct * 100, 2)}%` }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Accumulation progress (shown when accumulating) */}
      {orchStats?.accumulation && !isGraduated && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-8">
          <h2 className="text-lg font-semibold mb-4">Data Accumulation</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
            <div>
              <p className="text-xs text-zinc-500 uppercase">Total Matches</p>
              <p className="text-xl font-bold text-zinc-100">
                {orchStats.accumulation.total_matches}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Qualifying</p>
              <p className="text-xl font-bold text-zinc-100">
                {orchStats.accumulation.qualifying_matches} /{" "}
                {orchStats.accumulation.min_required}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Total Ticks</p>
              <p className="text-xl font-bold text-zinc-100">
                {orchStats.accumulation.total_ticks.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Ready</p>
              <p
                className={`text-xl font-bold ${
                  orchStats.accumulation.ready_to_train
                    ? "text-emerald-400"
                    : "text-amber-400"
                }`}
              >
                {orchStats.accumulation.ready_to_train ? "Yes" : "Not yet"}
              </p>
            </div>
          </div>

          {/* Progress bar */}
          <div className="w-full h-3 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-phoenix-500 rounded-full transition-all duration-700"
              style={{
                width: `${Math.min(
                  100,
                  (orchStats.accumulation.qualifying_matches /
                    Math.max(orchStats.accumulation.min_required, 1)) *
                    100
                )}%`,
              }}
            />
          </div>
          <p className="text-xs text-zinc-500 mt-2">
            Need {orchStats.accumulation.min_required} qualifying matches to
            start training
          </p>
        </div>
      )}

      {/* Signals section */}
      {isGraduated && (
        <div>
          <h2 className="text-xl font-semibold mb-4">Live Bet Signals</h2>
          {signalsLoading && !signals ? (
            <p className="text-zinc-500">Loading signals...</p>
          ) : signals && signals.signals.length > 0 ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {signals.signals.map((signal) => (
                <SignalCard key={signal.match_id} signal={signal} />
              ))}
            </div>
          ) : (
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-8 text-center">
              <p className="text-zinc-500 text-lg mb-2">
                No active signals right now
              </p>
              <p className="text-zinc-600 text-sm">
                Signals appear when the agent identifies opportunities in live
                matches.
              </p>
            </div>
          )}

          {signals?.generated_at && (
            <p className="text-xs text-zinc-600 mt-4 text-right">
              Last updated: {new Date(signals.generated_at).toLocaleString()} |
              Model v{signals.model_version}
            </p>
          )}
        </div>
      )}

      {/* Not graduated info */}
      {!isGraduated && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-8 text-center">
          <p className="text-zinc-400 text-lg mb-2">
            Agent is in <strong>{stateLabels[currentState]}</strong> phase
          </p>
          <p className="text-zinc-600 text-sm">
            Bet suggestions will appear here once the agent has graduated after
            meeting all performance criteria for 14 consecutive days.
          </p>
        </div>
      )}

      {/* Manual Demotion Controls (admin) */}
      {isGraduated && (
        <div className="mt-8 bg-zinc-900 border border-zinc-800 rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-2">Admin Controls</h2>
          <p className="text-sm text-zinc-500 mb-4">
            If you&apos;ve lost confidence in the agent, you can manually demote it
            back to virtual trading. It will need to meet all graduation criteria
            for 14 consecutive days again before returning to advisor mode.
          </p>
          <div className="flex items-center gap-4">
            <button
              onClick={handleDemote}
              disabled={demoting}
              className="px-4 py-2 bg-red-900/50 hover:bg-red-800/50 border border-red-700
                         text-red-300 rounded-lg text-sm font-medium transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {demoting ? "Demoting..." : "Demote to Virtual Trading"}
            </button>
            {demoteMsg && (
              <span className="text-sm text-zinc-400">{demoteMsg}</span>
            )}
          </div>
          {orchStats?.demotions && orchStats.demotions > 0 && (
            <p className="text-xs text-zinc-600 mt-3">
              Previous demotions: {orchStats.demotions}
              {orchStats.last_demotion && (
                <> | Last: {new Date(orchStats.last_demotion.at).toLocaleDateString()}</>
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
