"use client";

import { useEffect, useState } from "react";
import { useApi } from "@/hooks/useApi";
import { QualityMonitor } from "@/components/data/QualityMonitor";
import { MissingResults } from "@/components/data/MissingResults";
import { type MatchFilter, RecordedMatches } from "@/components/data/RecordedMatches";
import type { HealthResponse, MatchesResponse, MissingResultsResponse, ResultWritten } from "./types";

const REFRESH_MS = 60_000;
const CLOCK_MS = 15_000;

export default function DataPage() {
  const [filter, setFilter] = useState<MatchFilter>("real");
  // The clock is set after mount so server and client markup agree; it drives the "age" figures between refreshes.
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => clearInterval(timer);
  }, []);

  const health = useApi<HealthResponse>("/data/health", REFRESH_MS);
  const missing = useApi<MissingResultsResponse>("/data/missing-results", REFRESH_MS);
  // Real only asks the API for real matches; SRL and All ask for everything and the table filters the rest.
  const matches = useApi<MatchesResponse>(
    `/data/matches?days=3&include_simulated=${filter === "real" ? "false" : "true"}`,
    REFRESH_MS
  );

  const onWritten = (_result: ResultWritten) => {
    missing.refresh();
    matches.refresh();
  };

  return (
    <div className="mx-auto w-full max-w-7xl min-w-0 space-y-8">
      <header>
        <h1 className="mb-2 text-3xl font-bold">Data</h1>
        <p className="text-zinc-400">
          Whether the data can be trusted: the latest quality-monitor run, real matches missing a result, and the
          recorded matches.
        </p>
      </header>

      <QualityMonitor health={health.data} loading={health.loading} error={health.error} now={now} />

      <MissingResults
        data={missing.data}
        loading={missing.loading}
        error={missing.error}
        now={now}
        onWritten={onWritten}
      />

      <RecordedMatches
        data={matches.data}
        loading={matches.loading}
        error={matches.error}
        filter={filter}
        onFilterChange={setFilter}
        now={now}
      />
    </div>
  );
}
