"use client";

import { useState } from "react";
import type { MissingResultItem, MissingResultsResponse, ResultWritten } from "@/app/data/types";
import { ManualResultModal, type ManualResultTarget } from "@/components/ManualResultModal";
import { fmtAge, fmtUtcTime, parseIso } from "./format";
import { ErrorBanner, Muted, Section } from "./ui";

export function MissingResults({
  data,
  loading,
  error,
  now,
  onWritten,
}: {
  data: MissingResultsResponse | null;
  loading: boolean;
  error: string | null;
  now: number | null;
  onWritten: (result: ResultWritten) => void;
}) {
  const [target, setTarget] = useState<ManualResultTarget | null>(null);
  const items = data?.items ?? [];
  const today = now !== null ? new Date(now) : new Date();

  const ageSeconds = (item: MissingResultItem): number => {
    const last = parseIso(item.last_tick);
    if (last && now !== null) return (now - last.getTime()) / 1000;
    return item.age_hours * 3600;
  };

  return (
    <Section
      title="Missing results"
      subtitle={
        data
          ? `Real matches silent for over ${data.stale_after_hours} h with no decided result, from the last ${data.lookback_days} days. Each one needs a result from a public scorecard.`
          : "Real matches silent for a long time with no decided result."
      }
      right={
        data ? (
          <span
            className={`inline-flex rounded-md border px-2 py-0.5 text-xs font-semibold ${
              data.count > 0
                ? "border-red-500/40 bg-red-500/10 text-red-400"
                : "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
            }`}
          >
            {data.count} missing
          </span>
        ) : null
      }
    >
      {error && <ErrorBanner message={error} stale={!!data} />}

      {loading && !data ? (
        <Muted>Loading the missing results...</Muted>
      ) : items.length === 0 ? (
        data ? (
          <Muted>No real match is waiting for a result.</Muted>
        ) : null
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="bg-zinc-950/60 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-3 py-2 font-medium">Teams</th>
                <th className="px-3 py-2 font-medium">Competition</th>
                <th className="px-3 py-2 font-medium">Start (UTC)</th>
                <th className="px-3 py-2 font-medium">Last tick (UTC)</th>
                <th className="px-3 py-2 font-medium">Silent for</th>
                <th className="px-3 py-2 font-medium">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {items.map((item) => (
                <tr key={item.match_id} className="align-top hover:bg-zinc-800/30">
                  <td className="px-3 py-2">
                    <p className="text-zinc-100">{item.team_home}</p>
                    <p className="text-zinc-400">v {item.team_away}</p>
                    <p className="font-mono text-[11px] text-zinc-600">{item.match_id}</p>
                  </td>
                  <td className="px-3 py-2 text-zinc-300">{item.competition || "-"}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300">
                    {fmtUtcTime(item.match_start_time, today)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300">
                    {fmtUtcTime(item.last_tick, today)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-amber-400">{fmtAge(ageSeconds(item))}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() =>
                        setTarget({
                          match_id: item.match_id,
                          team_home: item.team_home,
                          team_away: item.team_away,
                          competition: item.competition,
                        })
                      }
                      className="whitespace-nowrap rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-500"
                    >
                      Enter result
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {target && (
        <ManualResultModal
          target={target}
          onClose={() => setTarget(null)}
          onWritten={(result) => onWritten(result)}
        />
      )}
    </Section>
  );
}
