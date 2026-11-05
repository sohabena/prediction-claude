import Link from "next/link";
import type { EvidenceResponse, InPlayGateRow } from "@/app/evidence/types";
import { fmtDecimal, fmtInt, fmtPct } from "./format";

const FORMAT_LABEL: Record<string, string> = {
  t20: "T20",
  odi: "ODI",
  multiday: "Multi-day",
};

function formatLabel(f: string): string {
  return FORMAT_LABEL[f.toLowerCase()] ?? f;
}

function GateRow({ row }: { row: InPlayGateRow }) {
  return (
    <tr className="align-top">
      <th scope="row" className="px-4 py-3 font-medium text-zinc-200 whitespace-nowrap">{formatLabel(row.format)}</th>
      <td className="px-4 py-3">
        <span
          className={`inline-block rounded-md border px-2 py-0.5 text-xs font-semibold ${
            row.open
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-zinc-700 bg-zinc-800/60 text-zinc-300"
          }`}
        >
          {row.open ? "Open" : "Closed"}
        </span>
      </td>
      <td className="px-4 py-3 tabular-nums text-zinc-300">{fmtInt(row.matches)}</td>
      <td className="px-4 py-3 tabular-nums text-zinc-300">{fmtDecimal(row.logloss_diff)}</td>
    </tr>
  );
}

export function InPlayGate({ rows }: { rows: InPlayGateRow[] }) {
  return (
    <section aria-labelledby="gate-heading" className="space-y-3">
      <div>
        <h2 id="gate-heading" className="text-lg font-semibold text-zinc-100">In-play model gate</h2>
        <p className="mt-1 text-xs leading-relaxed text-zinc-500">
          A format opens only with at least 15 decided matches, a log loss no worse than the market&apos;s, and a
          confidence-interval lower bound below zero. The log-loss difference is the model minus the market, so a
          negative value is better. The gate governs in-play signals; it does not restrict paper trading.
        </p>
      </div>

      {rows.length === 0 ? (
        <div className="stat-card text-sm text-zinc-400">No formats reported yet.</div>
      ) : (
        <div className="stat-card overflow-x-auto p-0">
          <table className="w-full min-w-[420px] text-left text-sm">
            <thead className="border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-3 font-semibold">Format</th>
                <th scope="col" className="px-4 py-3 font-semibold">Gate</th>
                <th scope="col" className="px-4 py-3 font-semibold">Matches</th>
                <th scope="col" className="px-4 py-3 font-semibold">Log loss vs market</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {rows.map((r) => (
                <GateRow key={r.format} row={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function FillRealism({ data }: { data: EvidenceResponse }) {
  const fr = data.fill_realism;
  return (
    <section aria-labelledby="fill-heading" className="space-y-3">
      <div>
        <h2 id="fill-heading" className="text-lg font-semibold text-zinc-100">Fill realism</h2>
        <p className="mt-1 text-xs leading-relaxed text-zinc-500">
          Fill rate: the share of intended bets that filled. Stake filled: the share of intended stake that filled.
        </p>
      </div>

      {!fr.live_size_cap && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm text-amber-200">
          The size cap is off until the size unit is verified, so fills are not yet limited to the size shown on the
          match page. Fill rates may be optimistic.
        </div>
      )}

      {fr.variants.length === 0 ? (
        <div className="stat-card text-sm text-zinc-400">No fills reported yet.</div>
      ) : (
        <div className="stat-card overflow-x-auto p-0">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-3 font-semibold">Variant</th>
                <th scope="col" className="px-4 py-3 font-semibold">Fill rate</th>
                <th scope="col" className="px-4 py-3 font-semibold">Stake filled</th>
                <th scope="col" className="px-4 py-3 font-semibold">Adverse slippage</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {fr.variants.map((v) => (
                <tr key={v.variant}>
                  <th scope="row" className="px-4 py-3 font-mono text-xs text-zinc-200 whitespace-nowrap">{v.variant}</th>
                  <td className="px-4 py-3 tabular-nums text-zinc-300">{fmtPct(v.fill_rate)}</td>
                  <td className="px-4 py-3 tabular-nums text-zinc-300">{fmtPct(v.stake_fill_rate)}</td>
                  <td className="px-4 py-3 tabular-nums text-zinc-300">
                    {v.mean_adverse_slippage_pct === null || v.mean_adverse_slippage_pct === undefined
                      ? "n/a"
                      : `${v.mean_adverse_slippage_pct.toFixed(2)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function MissingResults({ count }: { count: number }) {
  return (
    <section aria-labelledby="missing-heading" className="stat-card flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h2 id="missing-heading" className="text-sm font-semibold text-zinc-100">Result coverage</h2>
        <p className="mt-1 text-sm text-zinc-400">
          {count === 0
            ? "No real match is missing a result."
            : `${fmtInt(count)} real ${count === 1 ? "match is" : "matches are"} missing a result, so their bets are not settled.`}
        </p>
      </div>
      <Link
        href="/data"
        className="inline-flex shrink-0 items-center text-sm font-semibold text-phoenix-400 hover:text-phoenix-300 hover:underline"
      >
        Review on Data
      </Link>
    </section>
  );
}
