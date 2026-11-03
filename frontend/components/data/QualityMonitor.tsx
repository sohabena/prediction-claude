"use client";

import type { HealthResponse, QualityCheck } from "@/app/data/types";
import { checkTone, fmtAge, fmtCheckName, fmtUtcFull, fmtValue, parseIso } from "./format";
import { Badge, ErrorBanner, Muted, Section } from "./ui";

const LONG_DETAIL = 110;

function CheckCard({ check }: { check: QualityCheck }) {
  const tone = checkTone(check.status);
  const detail = check.detail ?? "";
  const long = detail.length > LONG_DETAIL;
  return (
    <li className="min-w-0 rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 break-words text-sm font-medium text-zinc-200">{fmtCheckName(check.name)}</p>
        <Badge className={tone.badge}>{tone.label}</Badge>
      </div>
      <p className={`mt-1 font-mono text-sm ${tone.text}`}>value {fmtValue(check.value)}</p>
      {detail &&
        (long ? (
          <details className="group mt-2">
            <summary className="cursor-pointer select-none break-words text-xs text-zinc-400 hover:text-zinc-200 list-none">
              <span className="group-open:hidden">{detail.slice(0, LONG_DETAIL).trimEnd()}... show more</span>
              <span className="hidden group-open:inline">show less</span>
            </summary>
            <p className="mt-2 break-words text-xs leading-relaxed text-zinc-400">{detail}</p>
          </details>
        ) : (
          <p className="mt-2 break-words text-xs leading-relaxed text-zinc-400">{detail}</p>
        ))}
    </li>
  );
}

export function QualityMonitor({
  health,
  loading,
  error,
  now,
}: {
  health: HealthResponse | null;
  loading: boolean;
  error: string | null;
  now: number | null;
}) {
  const counts = { ok: 0, warn: 0, fail: 0 };
  for (const c of health?.checks ?? []) {
    if (c.status === "ok") counts.ok += 1;
    else if (c.status === "warn") counts.warn += 1;
    else if (c.status === "fail") counts.fail += 1;
  }

  const ts = parseIso(health?.ts ?? null);
  const ageSeconds =
    ts && now !== null ? (now - ts.getTime()) / 1000 : (health?.age_seconds ?? null);
  const overall = health?.overall ? checkTone(health.overall) : null;

  return (
    <Section
      title="Quality monitor"
      subtitle="The latest run of the data quality monitor. Each check is OK, WARN or FAIL; the detail says what it measured."
      right={
        overall && health?.available ? <Badge className={overall.badge}>Overall {overall.label}</Badge> : null
      }
    >
      {error && <ErrorBanner message={error} stale={!!health} />}

      {loading && !health ? (
        <Muted>Loading the quality monitor...</Muted>
      ) : !health || !health.available ? (
        <Muted>No quality-monitor run has been recorded yet.</Muted>
      ) : (
        <>
          <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <dt className="text-xs uppercase tracking-widest text-zinc-500">Run time</dt>
              <dd className="mt-1 font-mono text-zinc-200">{fmtUtcFull(health.ts)}</dd>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <dt className="text-xs uppercase tracking-widest text-zinc-500">Age of the run</dt>
              <dd className="mt-1 text-zinc-200">{fmtAge(ageSeconds)}</dd>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <dt className="text-xs uppercase tracking-widest text-zinc-500">Checks</dt>
              <dd className="mt-1 text-zinc-200">
                <span className="text-emerald-400">{counts.ok} OK</span>
                {" · "}
                <span className="text-amber-400">{counts.warn} WARN</span>
                {" · "}
                <span className="text-red-400">{counts.fail} FAIL</span>
                {health.window_hours !== null && (
                  <span className="text-zinc-500"> · window {fmtValue(health.window_hours)} h</span>
                )}
              </dd>
            </div>
          </dl>

          {health.checks.length === 0 ? (
            <Muted>The last run recorded no checks.</Muted>
          ) : (
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {health.checks.map((check) => (
                <CheckCard key={check.name} check={check} />
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}
