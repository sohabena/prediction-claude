"use client";

import type { MatchesResponse, RecordedMatch } from "@/app/data/types";
import { fmtUtcTime, scorePipelineLabel, stateTone } from "./format";
import { Badge, ErrorBanner, Muted, Section } from "./ui";

export type MatchFilter = "real" | "srl" | "all";

const FILTERS: { value: MatchFilter; label: string }[] = [
  { value: "real", label: "Real" },
  { value: "srl", label: "SRL" },
  { value: "all", label: "All" },
];

function resultCell(m: RecordedMatch): { text: string; className: string } {
  switch (m.result.result_type) {
    case "win":
      return { text: `Winner: ${m.result.winner ?? "-"}`, className: "text-emerald-400" };
    case "tie":
      return { text: "Tie", className: "text-sky-400" };
    case "no_result":
      return { text: "No result", className: "text-zinc-300" };
    case "abandoned":
      return { text: "Abandoned", className: "text-zinc-300" };
  }
  switch (m.result_status) {
    case "awaiting":
      return { text: "Awaiting", className: "text-amber-400" };
    case "missing":
      return { text: "Missing", className: "text-red-400 font-semibold" };
    case "not_started":
      return { text: "Not started", className: "text-zinc-500" };
    default:
      return { text: m.result_status, className: "text-zinc-400" };
  }
}

export function RecordedMatches({
  data,
  loading,
  error,
  filter,
  onFilterChange,
  now,
}: {
  data: MatchesResponse | null;
  loading: boolean;
  error: string | null;
  filter: MatchFilter;
  onFilterChange: (f: MatchFilter) => void;
  now: number | null;
}) {
  const today = now !== null ? new Date(now) : new Date();
  const all = data?.matches ?? [];
  const matches = all.filter((m) => (filter === "real" ? !m.simulated : filter === "srl" ? m.simulated : true));

  const counts = { live: 0, "pre-match": 0, finished: 0, missing: 0, awaiting: 0 };
  for (const m of matches) {
    if (m.state === "live") counts.live += 1;
    else if (m.state === "pre-match") counts["pre-match"] += 1;
    else if (m.state === "finished") counts.finished += 1;
    if (m.result_status === "missing") counts.missing += 1;
    if (m.result_status === "awaiting") counts.awaiting += 1;
  }

  const summaryCards: { label: string; value: number; className: string }[] = [
    { label: "Matches", value: matches.length, className: "text-zinc-100" },
    { label: "Live", value: counts.live, className: "text-emerald-400" },
    { label: "Pre-match", value: counts["pre-match"], className: "text-sky-400" },
    { label: "Finished", value: counts.finished, className: "text-zinc-200" },
    { label: "Awaiting result", value: counts.awaiting, className: "text-amber-400" },
    { label: "Missing result", value: counts.missing, className: counts.missing > 0 ? "text-red-400" : "text-zinc-200" },
  ];

  return (
    <Section
      title="Recorded matches"
      subtitle={`Matches with ticks in the last ${data?.days ?? 3} days, newest state first. Times are UTC.`}
      right={
        <div role="group" aria-label="Match type" className="inline-flex rounded-lg border border-zinc-700 p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => onFilterChange(f.value)}
              aria-pressed={filter === f.value}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                filter === f.value ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      {error && <ErrorBanner message={error} stale={!!data} />}

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {summaryCards.map((c) => (
          <div key={c.label} className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
            <dt className="text-[11px] uppercase tracking-widest text-zinc-500">{c.label}</dt>
            <dd className={`mt-1 text-2xl font-bold ${c.className}`}>{c.value}</dd>
          </div>
        ))}
      </dl>

      {loading && !data ? (
        <Muted>Loading the recorded matches...</Muted>
      ) : matches.length === 0 ? (
        data ? (
          <Muted>No {filter === "all" ? "" : filter === "srl" ? "SRL " : "real "}match was recorded in the last {data.days} days.</Muted>
        ) : null
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="bg-zinc-950/60 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-3 py-2 font-medium">Teams</th>
                <th className="px-3 py-2 font-medium">Competition</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">State</th>
                <th className="px-3 py-2 font-medium">Match page</th>
                <th className="px-3 py-2 font-medium">Score pipeline</th>
                <th className="px-3 py-2 font-medium">Start</th>
                <th className="px-3 py-2 font-medium">Actual start</th>
                <th className="px-3 py-2 font-medium">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {matches.map((m) => {
                const result = resultCell(m);
                return (
                  <tr key={m.match_id} className="align-top hover:bg-zinc-800/30">
                    <td className="px-3 py-2">
                      <p className="text-zinc-100">{m.team_home}</p>
                      <p className="text-zinc-400">v {m.team_away}</p>
                    </td>
                    <td className="px-3 py-2 text-zinc-300">{m.competition || "-"}</td>
                    <td className="px-3 py-2">
                      <Badge
                        className={
                          m.simulated
                            ? "border-violet-500/40 bg-violet-500/10 text-violet-300"
                            : "border-zinc-600 bg-zinc-800 text-zinc-200"
                        }
                      >
                        {m.simulated ? "SRL" : "Real"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      <Badge className={stateTone(m.state)}>{m.state}</Badge>
                    </td>
                    <td className={`whitespace-nowrap px-3 py-2 ${m.match_page_seen ? "text-emerald-400" : "text-zinc-500"}`}>
                      {m.match_page_seen ? "seen" : "not seen"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-zinc-300">{scorePipelineLabel(m.score_pipeline)}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300">
                      {fmtUtcTime(m.start, today)}
                    </td>
                    <td
                      className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300"
                      title={m.actual_start_source ? `source: ${m.actual_start_source}` : undefined}
                    >
                      {fmtUtcTime(m.actual_start, today)}
                    </td>
                    <td className={`whitespace-nowrap px-3 py-2 ${result.className}`}>{result.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
