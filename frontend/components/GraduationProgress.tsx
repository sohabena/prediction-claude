"use client";

interface Criterion {
  name: string;
  value: number;
  threshold: number;
  met: boolean;
}

interface GraduationProgressProps {
  consecutiveDays: number;
  requiredDays: number;
  criteria: Criterion[];
  ready: boolean;
}

export function GraduationProgress({
  consecutiveDays,
  requiredDays,
  criteria,
  ready,
}: GraduationProgressProps) {
  const progressPct = Math.min(100, (consecutiveDays / requiredDays) * 100);

  return (
    <div className="space-y-6">
      {/* Overall progress */}
      <div className="stat-card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-semibold">Graduation Progress</h3>
          <span
            className={`px-3 py-1 rounded-full text-xs font-medium ${
              ready
                ? "bg-emerald-500/20 text-emerald-400"
                : "bg-amber-500/20 text-amber-400"
            }`}
          >
            {ready ? "READY" : "IN PROGRESS"}
          </span>
        </div>

        <div className="mb-2">
          <div className="flex justify-between text-sm text-zinc-400 mb-1">
            <span>
              {consecutiveDays} / {requiredDays} consecutive days
            </span>
            <span>{progressPct.toFixed(0)}%</span>
          </div>
          <div className="h-3 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                ready ? "bg-emerald-500" : "bg-phoenix-500"
              }`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      </div>

      {/* Individual criteria */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {criteria.map((criterion) => (
          <div
            key={criterion.name}
            className={`p-4 rounded-lg border ${
              criterion.met
                ? "border-emerald-800 bg-emerald-950/30"
                : "border-zinc-800 bg-zinc-900"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm text-zinc-400 capitalize">
                {criterion.name.replace("_", " ")}
              </span>
              <span
                className={
                  criterion.met ? "text-emerald-400" : "text-zinc-500"
                }
              >
                {criterion.met ? "PASS" : "FAIL"}
              </span>
            </div>
            <div className="mt-1">
              <span className="text-xl font-bold">
                {criterion.value.toFixed(3)}
              </span>
              <span className="text-xs text-zinc-500 ml-2">
                / {criterion.threshold}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
