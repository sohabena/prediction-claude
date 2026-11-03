"use client";

import { useApi } from "@/hooks/useApi";
import type { EvidenceResponse } from "./types";
import { ValidationLadder } from "@/components/evidence/Ladder";
import { ExcessVerdict, StrategyTable, ControlsTable } from "@/components/evidence/Scorecard";
import { InPlayGate, FillRealism, MissingResults } from "@/components/evidence/Diagnostics";
import { fmtTimestamp } from "@/components/evidence/format";

const FIVE_MINUTES = 300000;

function LoadingSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading evidence">
      <div className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="stat-card h-44 animate-pulse bg-zinc-800/40" />
        ))}
      </div>
      <div className="stat-card h-40 animate-pulse bg-zinc-800/40" />
      <div className="stat-card h-64 animate-pulse bg-zinc-800/40" />
    </div>
  );
}

export default function EvidencePage() {
  const { data, loading, error } = useApi<EvidenceResponse>("/paper/evidence", FIVE_MINUTES);

  return (
    <div className="max-w-6xl space-y-8 min-w-0">
      <header>
        <h1 className="text-3xl font-bold mb-2">Evidence</h1>
        <p className="text-zinc-400 max-w-3xl">
          Is any strategy beating the market? Closing-line value in excess of the placebo, settled real matches with a
          tradable book, and the R1 to R3 ladder. Paper trading only.
        </p>
        {data && (
          <p className="mt-2 text-xs text-zinc-500">
            Generated {fmtTimestamp(data.generated_at)}
            {data.review_day ? ` · review day ${data.review_day}` : ""} · refreshes every 5 minutes
          </p>
        )}
      </header>

      {!data && loading && <LoadingSkeleton />}

      {!data && !loading && error && (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-5 py-4 text-sm text-red-200">
          Could not load the evidence ({error}). The API may be down. The page retries every 5 minutes.
        </div>
      )}

      {data && error && (
        <div role="alert" className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          The last refresh failed ({error}). Showing the data generated {fmtTimestamp(data.generated_at)}.
        </div>
      )}

      {data && (
        <>
          <ValidationLadder data={data} />
          <MissingResults count={data.missing_results} />
          <ExcessVerdict data={data} />
          <StrategyTable data={data} />
          <ControlsTable data={data} />
          <InPlayGate rows={data.in_play_gate} />
          <FillRealism data={data} />
        </>
      )}
    </div>
  );
}
