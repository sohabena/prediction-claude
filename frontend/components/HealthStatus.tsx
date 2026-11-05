"use client";

import { useApi } from "@/hooks/useApi";

interface HealthResponse {
  status: string;
  redis: string;
  database: string;
  scraper_status: string;
  scraper_last_update_seconds: number | null;
  agent_state: string;
  agent_version: string;
  timestamp: string;
}

export function HealthStatus() {
  const { data, loading } = useApi<HealthResponse>("/health", 10000);

  if (loading && !data) return null;

  const isHealthy = data?.status === "healthy";
  const redisOk = data?.redis === "connected";
  const dbOk = data?.database === "connected";

  return (
    <div className="flex items-center gap-3 text-xs">
      <div className="flex items-center gap-1.5">
        <div
          className={`w-2 h-2 rounded-full ${
            isHealthy ? "bg-emerald-500" : "bg-red-500"
          }`}
        />
        <span className="text-zinc-400">API</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div
          className={`w-2 h-2 rounded-full ${
            redisOk ? "bg-emerald-500" : "bg-red-500"
          }`}
        />
        <span className="text-zinc-400">Redis</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div
          className={`w-2 h-2 rounded-full ${
            dbOk ? "bg-emerald-500" : "bg-red-500"
          }`}
        />
        <span className="text-zinc-400">DB</span>
      </div>
      {data?.scraper_last_update_seconds != null && (
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${
              data.scraper_last_update_seconds < 60
                ? "bg-emerald-500"
                : data.scraper_last_update_seconds < 300
                  ? "bg-amber-500"
                  : "bg-red-500"
            }`}
          />
          <span className="text-zinc-400">
            Scraper ({Math.round(data.scraper_last_update_seconds)}s ago)
          </span>
        </div>
      )}
    </div>
  );
}
