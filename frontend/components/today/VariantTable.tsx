import type { Trade, VariantLive } from "@/app/today/types";
import { clamp, fmtMoney, fmtPrice, fmtUtcHM, isControl, pnlClass } from "./format";

function sideClass(side: string): string {
  if (side === "BACK") return "text-sky-300";
  if (side === "LAY") return "text-rose-300";
  return "text-zinc-300";
}

function statusClass(status: string): string {
  if (status === "filled") return "text-emerald-400";
  if (status === "pending") return "text-amber-400";
  return "text-zinc-500";
}

/** One trade on a single line: time, side, team, price, stake, status (and reason when sent). */
function TradeLine({ trade }: { trade: Trade }) {
  const filled = trade.status === "filled" && trade.price_filled !== null;
  const price = filled ? fmtPrice(trade.price_filled) : `lim ${fmtPrice(trade.price_limit)}`;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2 whitespace-nowrap">
      <span className="text-zinc-400">{fmtUtcHM(trade.decided_at)} UTC</span>
      <span className={`font-semibold ${sideClass(trade.side)}`}>{trade.side}</span>
      <span className="text-zinc-200">{trade.team}</span>
      <span className="text-zinc-300">@{price}</span>
      <span className="text-zinc-300">{fmtMoney(trade.stake)}</span>
      <span className={statusClass(trade.status)}>{trade.status}</span>
      {trade.reason ? <span className="text-zinc-500">{trade.reason}</span> : null}
    </span>
  );
}

/** Worst case as a bar: full width is the hard limit, the tick is the budget. */
export function CapBar({
  worst,
  budget,
  hardLimit,
}: {
  worst: number;
  budget: number;
  hardLimit: number;
}) {
  const scale = hardLimit > 0 ? hardLimit : budget;
  const fillPct = scale > 0 ? clamp((worst / scale) * 100, 0, 100) : 0;
  const budgetPct = scale > 0 ? clamp((budget / scale) * 100, 0, 100) : 0;
  const overBudget = worst >= budget;
  const ofBudget = budget > 0 ? Math.round((worst / budget) * 100) : 0;

  return (
    <div className="min-w-[160px]">
      <div className="flex items-baseline justify-between gap-2 tabular-nums">
        <span className={overBudget ? "font-semibold text-red-400" : "text-zinc-200"}>
          {fmtMoney(worst)}
        </span>
        <span className="text-[11px] text-zinc-500">{ofBudget}% of budget</span>
      </div>
      <div
        role="img"
        aria-label={`Worst case ${fmtMoney(worst)}; budget ${fmtMoney(budget)}; hard limit ${fmtMoney(hardLimit)}`}
        className="relative mt-1 h-2 rounded-full bg-zinc-800"
      >
        <div
          className={`absolute inset-y-0 left-0 rounded-full ${overBudget ? "bg-red-500" : "bg-phoenix-500"}`}
          style={{ width: `${fillPct}%` }}
        />
        <div
          className="absolute -inset-y-0.5 w-0.5 bg-zinc-200"
          style={{ left: `${budgetPct}%` }}
          title={`Budget ${fmtMoney(budget)}`}
        />
        <div className="absolute -inset-y-0.5 right-0 w-0.5 bg-red-400" title={`Hard limit ${fmtMoney(hardLimit)}`} />
      </div>
    </div>
  );
}

export function VariantTable({ variants }: { variants: VariantLive[] }) {
  // Strategy variants first (stable order), then controls.
  const ordered = [...variants].sort((a, b) => Number(isControl(a)) - Number(isControl(b)));
  const budget = ordered[0]?.budget ?? 0;
  const hardLimit = ordered[0]?.hard_limit ?? 0;

  return (
    <div className="space-y-2">
      {/* Scrolls horizontally inside the card only. w-0 + min-w-full keeps the
          table from widening the page on a phone. */}
      <div className="w-0 min-w-full overflow-x-auto rounded-lg border border-zinc-800/60">
        <table className="w-full min-w-[760px] text-left text-xs tabular-nums">
          <thead className="bg-zinc-950/50 text-[11px] uppercase tracking-wider text-zinc-500">
            <tr>
              <th className="px-3 py-2 font-semibold">Variant</th>
              <th className="px-3 py-2 text-right font-semibold">If home wins</th>
              <th className="px-3 py-2 text-right font-semibold">If away wins</th>
              <th className="px-3 py-2 font-semibold">Worst case</th>
              <th className="px-3 py-2 text-right font-semibold">Tickets</th>
              <th className="px-3 py-2 font-semibold">Last trade</th>
            </tr>
          </thead>
          {ordered.map((v) => {
            const control = isControl(v);
            const latest = v.last_trades[0];
            const recent = v.last_trades.slice(0, 5);
            const overTickets = v.tickets_used > v.ticket_budget;
            return (
              <tbody
                key={v.variant}
                className={`border-t border-zinc-800/60 ${control ? "opacity-50" : ""}`}
              >
                <tr className="align-top">
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className="font-mono text-zinc-100">{v.variant}</span>
                    {control ? (
                      <span className="ml-2 rounded bg-zinc-700/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-300">
                        control
                      </span>
                    ) : null}
                    <div className="text-[11px] text-zinc-500">
                      {v.fills} filled · {v.pending} pending
                    </div>
                  </td>
                  <td className={`whitespace-nowrap px-3 py-2 text-right font-semibold ${pnlClass(v.pnl_if_home)}`}>
                    {fmtMoney(v.pnl_if_home, true)}
                  </td>
                  <td className={`whitespace-nowrap px-3 py-2 text-right font-semibold ${pnlClass(v.pnl_if_away)}`}>
                    {fmtMoney(v.pnl_if_away, true)}
                  </td>
                  <td className="px-3 py-2">
                    <CapBar worst={v.worst_case} budget={v.budget} hardLimit={v.hard_limit} />
                  </td>
                  <td className={`whitespace-nowrap px-3 py-2 text-right ${overTickets ? "font-semibold text-red-400" : "text-zinc-300"}`}>
                    {v.tickets_used} / {v.ticket_budget}
                  </td>
                  <td className="px-3 py-2">
                    {latest ? <TradeLine trade={latest} /> : <span className="text-zinc-500">No trades yet</span>}
                  </td>
                </tr>
                {recent.length > 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 pb-3">
                      <details>
                        <summary className="cursor-pointer select-none text-[11px] text-phoenix-400 hover:text-phoenix-300">
                          Last {recent.length} trade{recent.length === 1 ? "" : "s"}
                        </summary>
                        <ul className="mt-2 space-y-1">
                          {recent.map((t, i) => (
                            <li key={`${t.decided_at}-${i}`}>
                              <TradeLine trade={t} />
                            </li>
                          ))}
                        </ul>
                      </details>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            );
          })}
        </table>
      </div>
      <p className="text-[11px] text-zinc-500">
        Worst case bar: full width is the hard limit ({fmtMoney(hardLimit)}); the white tick is the budget (
        {fmtMoney(budget)}); the red edge is the hard limit.
      </p>
    </div>
  );
}
