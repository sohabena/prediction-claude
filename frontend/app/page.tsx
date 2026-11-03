"use client";

import { useEffect, useState } from "react";
import { useApi } from "@/hooks/useApi";
import { API_BASE } from "@/lib/api";
import type {
  DataMatchesResponse,
  PaperEvidence,
  PaperLive,
  TraderCycle,
} from "@/app/today/types";
import { MatchCard } from "@/components/today/MatchCard";
import {
  fmtDuration,
  fmtStartsIn,
  fmtUtcHMS,
  fmtUtcStamp,
  parseTs,
} from "@/components/today/format";

const LIVE_REFRESH_MS = 15_000;
const EVIDENCE_REFRESH_MS = 300_000;
const MATCHES_REFRESH_MS = 60_000;
/** Same threshold as the backend (FRESH_CYCLE = 5 minutes). */
const STALE_CYCLE_SECONDS = 300;

/** Current time in ms, ticking every second so ages and countdowns stay live. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function CycleLine({ cycle, nowMs }: { cycle: TraderCycle | null; nowMs: number }) {
  if (!cycle || !cycle.cycle_at) {
    return (
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
        No trader cycle has been recorded yet, so the trader may not be running.
      </div>
    );
  }
  const cycleMs = parseTs(cycle.cycle_at);
  const ageSec = cycleMs === null ? null : (nowMs - cycleMs) / 1000;
  const stale = cycle.stale || ageSec === null || ageSec > STALE_CYCLE_SECONDS;
  const ageText = ageSec === null ? "age unknown" : `${fmtDuration(ageSec)} ago`;

  if (stale) {
    return (
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
        <strong>Stale trader cycle:</strong> the last cycle was {ageText} ({fmtUtcHMS(cycle.cycle_at)} UTC). The
        trader may have stopped.
      </div>
    );
  }
  return (
    <p className="text-sm text-zinc-400 tabular-nums">
      Last trader cycle: <span className="text-zinc-200">{fmtUtcHMS(cycle.cycle_at)} UTC</span>, {ageText}
      {cycle.reference ? <span className="text-zinc-500"> · reference {cycle.reference}</span> : null}
    </p>
  );
}

function EmptyState({
  matches,
  nowMs,
}: {
  matches: { data: DataMatchesResponse | null; loading: boolean; error: string | null };
  nowMs: number;
}) {
  const upcoming = (matches.data?.matches ?? [])
    .flatMap((m) => {
      const startMs = parseTs(m.start);
      return m.state === "pre-match" && startMs !== null && startMs > nowMs ? [{ m, startMs }] : [];
    })
    .sort((a, b) => a.startMs - b.startMs)[0];

  let next: string;
  if (upcoming) {
    const { m, startMs } = upcoming;
    const comp = m.competition ? ` · ${m.competition}` : "";
    next = `Next match: ${m.team_home} v ${m.team_away}${comp}, starts ${fmtUtcStamp(m.start)} (${fmtStartsIn(startMs, nowMs)}).`;
  } else if (!matches.data && matches.loading) {
    next = "Looking up the next match start…";
  } else if (!matches.data && matches.error) {
    next = `Could not load the next match start (${matches.error}).`;
  } else {
    next = "No pre-match is listed in the next 3 days.";
  }

  return (
    <div className="rounded-xl border border-zinc-800/60 bg-zinc-900/60 p-6 backdrop-blur-md">
      <p className="text-lg font-semibold text-zinc-100">No match is being traded right now</p>
      <p className="mt-2 text-sm text-zinc-400">{next}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      {[0, 1].map((i) => (
        <div key={i} className="h-56 animate-pulse rounded-xl border border-zinc-800/60 bg-zinc-900/60" />
      ))}
    </div>
  );
}

export default function TodayPage() {
  const live = useApi<PaperLive>("/paper/live", LIVE_REFRESH_MS);
  const evidence = useApi<PaperEvidence>("/paper/evidence", EVIDENCE_REFRESH_MS);
  const matches = useApi<DataMatchesResponse>("/data/matches?days=3", MATCHES_REFRESH_MS);
  const nowMs = useNow(1000);

  const data = live.data;
  // Show the banner unless R3 is known to have passed (unknown counts as not passed).
  const r3Passed = evidence.data?.r3_passed === true;

  return (
    <div className="w-full min-w-0 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="mb-2 text-3xl font-bold">Today</h1>
          <p className="text-zinc-400">
            What the paper trader holds right now: each match traded, its prices and P&amp;L by variant against the cap,
            and the last trades.
          </p>
        </div>
        {data ? (
          <p className="text-xs tabular-nums text-zinc-500">
            Updated {fmtUtcHMS(data.generated_at)} UTC · refreshes every 15 s
          </p>
        ) : null}
      </header>

      {!r3Passed ? (
        <div role="status" className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          <strong>No real-money signals:</strong> no variant has passed R3 yet. Everything here is paper (virtual)
          betting.
          {evidence.error && !evidence.data ? (
            <span className="mt-1 block text-xs text-amber-300/80">
              The R3 status could not be loaded ({evidence.error}); retrying every 5 minutes.
            </span>
          ) : null}
        </div>
      ) : null}

      {data ? (
        <>
          <CycleLine cycle={data.cycle} nowMs={nowMs} />

          {live.error ? (
            <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
              The last refresh failed ({live.error}). Showing data from {fmtUtcHMS(data.generated_at)} UTC; retrying
              every 15 s.
            </div>
          ) : null}

          {data.matches.length === 0 ? (
            <EmptyState matches={matches} nowMs={nowMs} />
          ) : (
            <section className="space-y-4" aria-label="Matches being traded">
              <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">
                {data.matches.length} match{data.matches.length === 1 ? "" : "es"} being traded
              </h2>
              {data.matches.map((m) => (
                <MatchCard key={m.match_id} match={m} nowMs={nowMs} />
              ))}
            </section>
          )}
        </>
      ) : live.error ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-5">
          <p className="font-semibold text-red-300">The paper trader API is not responding.</p>
          <p className="mt-1 text-sm text-zinc-300">
            Could not load {API_BASE}/paper/live ({live.error}). The page retries every 15 s. Check that the backend is
            running.
          </p>
        </div>
      ) : (
        <LoadingState />
      )}
    </div>
  );
}
