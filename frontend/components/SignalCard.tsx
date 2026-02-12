"use client";

interface ActionProbabilities {
  [action: string]: number;
}

interface Signal {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
  recommended_action: string;
  confidence: number;
  action_probabilities: ActionProbabilities;
  timestamp: string;
}

interface SignalCardProps {
  signal: Signal;
}

const actionColorMap: Record<string, string> = {
  BACK_HOME_SM: "bg-emerald-600",
  BACK_HOME_LG: "bg-emerald-500",
  BACK_AWAY_SM: "bg-blue-600",
  BACK_AWAY_LG: "bg-blue-500",
  LAY_HOME_SM: "bg-amber-600",
  LAY_HOME_LG: "bg-amber-500",
  LAY_AWAY_SM: "bg-orange-600",
  LAY_AWAY_LG: "bg-orange-500",
  HOLD: "bg-zinc-600",
};

const actionLabelMap: Record<string, string> = {
  BACK_HOME_SM: "Back Home (Small)",
  BACK_HOME_LG: "Back Home (Large)",
  BACK_AWAY_SM: "Back Away (Small)",
  BACK_AWAY_LG: "Back Away (Large)",
  LAY_HOME_SM: "Lay Home (Small)",
  LAY_HOME_LG: "Lay Home (Large)",
  LAY_AWAY_SM: "Lay Away (Small)",
  LAY_AWAY_LG: "Lay Away (Large)",
  HOLD: "Hold",
};

export function SignalCard({ signal }: SignalCardProps) {
  const confidencePct = Math.round(signal.confidence * 100);
  const actionColor = actionColorMap[signal.recommended_action] || "bg-zinc-600";
  const actionLabel = actionLabelMap[signal.recommended_action] || signal.recommended_action;

  const confidenceColor =
    confidencePct >= 70
      ? "text-emerald-400"
      : confidencePct >= 50
      ? "text-amber-400"
      : "text-red-400";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 space-y-4">
      {/* Match header */}
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-semibold text-zinc-100">
            {signal.team_home} vs {signal.team_away}
          </h3>
          <p className="text-xs text-zinc-500 mt-1">{signal.competition}</p>
        </div>
        <span
          className={`px-3 py-1 rounded-full text-xs font-medium text-white ${actionColor}`}
        >
          {actionLabel}
        </span>
      </div>

      {/* Confidence */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-zinc-500 uppercase tracking-wider">
            Confidence
          </span>
          <span className={`text-sm font-bold ${confidenceColor}`}>
            {confidencePct}%
          </span>
        </div>
        <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${actionColor}`}
            style={{ width: `${confidencePct}%` }}
          />
        </div>
      </div>

      {/* Action probability distribution */}
      <div>
        <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">
          Action Distribution
        </p>
        <div className="space-y-1">
          {Object.entries(signal.action_probabilities)
            .sort(([, a], [, b]) => b - a)
            .map(([action, prob]) => {
              const pct = Math.round(prob * 100);
              const isRecommended = action === signal.recommended_action;
              return (
                <div key={action} className="flex items-center gap-2">
                  <span
                    className={`text-xs w-32 truncate ${
                      isRecommended ? "text-zinc-200 font-medium" : "text-zinc-500"
                    }`}
                  >
                    {actionLabelMap[action] || action}
                  </span>
                  <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${
                        isRecommended ? actionColor : "bg-zinc-600"
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-xs text-zinc-500 w-8 text-right">
                    {pct}%
                  </span>
                </div>
              );
            })}
        </div>
      </div>

      {/* Timestamp */}
      <p className="text-xs text-zinc-600 text-right">
        {new Date(signal.timestamp).toLocaleTimeString()}
      </p>
    </div>
  );
}
