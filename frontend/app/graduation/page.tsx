"use client";

import { useApi } from "@/hooks/useApi";
import { GraduationProgress } from "@/components/GraduationProgress";

interface GraduationStatusResponse {
  ready: boolean;
  consecutive_days: number;
  required_days: number;
  criteria: Array<{
    name: string;
    value: number;
    threshold: number;
    met: boolean;
  }>;
}

export default function GraduationPage() {
  const { data, loading } = useApi<GraduationStatusResponse>(
    "/graduation/status",
    30000
  );

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Graduation</h1>
      <p className="text-zinc-400 mb-8">
        Agent readiness for live trading — all criteria must pass for 14
        consecutive days
      </p>

      {loading && !data ? (
        <p className="text-zinc-500">Loading graduation status...</p>
      ) : data ? (
        <GraduationProgress
          consecutiveDays={data.consecutive_days}
          requiredDays={data.required_days}
          criteria={data.criteria}
          ready={data.ready}
        />
      ) : (
        <p className="text-zinc-500">
          No graduation data available. Training must be in progress.
        </p>
      )}
    </div>
  );
}
