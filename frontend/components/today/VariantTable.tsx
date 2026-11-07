"use client";

import type { Trade, VariantLive } from "@/app/today/types";
import { clamp, fmtMoney, fmtPrice, fmtUtcHM, isControl, pnlClass } from "./format";
import { getModelMeta, MODEL_SECTIONS, ModelSectionId } from "@/lib/modelMeta";
import { ModelInfoPopover } from "@/components/common/ModelInfoPopover";
import { ShieldCheck, TrendingUp, Zap, FlaskConical } from "lucide-react";

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

function SectionHeaderIcon({ section }: { section: ModelSectionId }) {
  if (section === "green_up") return <ShieldCheck className="h-4 w-4 text-emerald-400" />;
  if (section === "dynamics") return <Zap className="h-4 w-4 text-amber-400" />;
  if (section === "directional") return <TrendingUp className="h-4 w-4 text-sky-400" />;
  return <FlaskConical className="h-4 w-4 text-zinc-400" />;
}

export function VariantTable({ variants }: { variants: VariantLive[] }) {
  const budget = variants[0]?.budget ?? 0;
  const hardLimit = variants[0]?.hard_limit ?? 0;

  // Group variants into predefined sections
  const sectionOrder: ModelSectionId[] = ["green_up", "directional", "dynamics", "controls"];
  
  const groupedSections = sectionOrder
    .map((secId) => {
      const secDef = MODEL_SECTIONS[secId];
      const secVariants = variants.filter((v) => getModelMeta(v.variant).section === secId);
      return {
        section: secDef,
        variants: secVariants,
      };
    })
    .filter((g) => g.variants.length > 0);

  return (
    <div className="space-y-6">
      {groupedSections.map(({ section, variants: secVariants }) => (
        <div
          key={section.id}
          className={`overflow-hidden rounded-xl border ${section.borderClass} bg-zinc-950/40 shadow-sm`}
        >
          {/* Section Header */}
          <div className={`px-4 py-3 ${section.headerBg} border-b border-zinc-800/80`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <SectionHeaderIcon section={section.id} />
                <h3 className="text-sm font-semibold text-zinc-100">{section.title}</h3>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${section.badgeClass}`}>
                  {section.badge}
                </span>
              </div>
              <span className="text-xs text-zinc-400 font-medium">
                {secVariants.length} {secVariants.length === 1 ? "model" : "models"}
              </span>
            </div>
            <p className="mt-1 text-xs text-zinc-400 leading-relaxed">
              {section.description}
            </p>
          </div>

          {/* Table */}
          <div className="w-0 min-w-full overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-xs tabular-nums">
              <thead className="bg-zinc-900/60 text-[11px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800/60">
                <tr>
                  <th className="px-4 py-2.5 font-semibold min-w-[240px]">Model & Strategy</th>
                  <th className="px-3 py-2.5 text-right font-semibold">If home wins</th>
                  <th className="px-3 py-2.5 text-right font-semibold">If away wins</th>
                  <th className="px-3 py-2.5 font-semibold">Worst case</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Tickets</th>
                  <th className="px-4 py-2.5 font-semibold min-w-[200px]">Last trade</th>
                </tr>
              </thead>
              {secVariants.map((v) => {
                const control = isControl(v);
                const meta = getModelMeta(v.variant);
                const latest = v.last_trades[0];
                const recent = v.last_trades.slice(0, 5);
                const overTickets = v.tickets_used > v.ticket_budget;

                return (
                  <tbody
                    key={v.variant}
                    className={`border-t border-zinc-800/50 hover:bg-zinc-900/30 transition-colors ${control ? "opacity-60" : ""}`}
                  >
                    <tr className="align-top">
                      <td className="px-4 py-3">
                        <div className="space-y-1">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="font-semibold text-zinc-100 text-[13px]">
                              {meta.displayName}
                            </span>
                            <ModelInfoPopover meta={meta} />
                            {control ? (
                              <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-400 border border-zinc-700">
                                control
                              </span>
                            ) : null}
                          </div>
                          <div className="flex items-center gap-1.5 text-[11px] text-zinc-400 flex-wrap">
                            <span className="font-mono text-zinc-300 bg-zinc-900 px-1 py-0.5 rounded border border-zinc-800 text-[10.5px]">
                              {v.variant}
                            </span>
                            <span className="text-zinc-600">•</span>
                            <span className="text-zinc-400">{meta.shortTag}</span>
                            <span className="text-zinc-600">•</span>
                            <span className="text-zinc-500">
                              {v.fills} filled · {v.pending} pending
                            </span>
                          </div>
                        </div>
                      </td>

                      <td className={`whitespace-nowrap px-3 py-3 text-right font-semibold text-[13px] ${pnlClass(v.pnl_if_home)}`}>
                        {fmtMoney(v.pnl_if_home, true)}
                      </td>

                      <td className={`whitespace-nowrap px-3 py-3 text-right font-semibold text-[13px] ${pnlClass(v.pnl_if_away)}`}>
                        {fmtMoney(v.pnl_if_away, true)}
                      </td>

                      <td className="px-3 py-3">
                        <CapBar worst={v.worst_case} budget={v.budget} hardLimit={v.hard_limit} />
                      </td>

                      <td className={`whitespace-nowrap px-3 py-3 text-right font-medium ${overTickets ? "font-semibold text-red-400" : "text-zinc-300"}`}>
                        {v.tickets_used} / {v.ticket_budget}
                      </td>

                      <td className="px-4 py-3">
                        {latest ? <TradeLine trade={latest} /> : <span className="text-zinc-500 italic">No trades yet</span>}
                      </td>
                    </tr>

                    {recent.length > 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 pb-3 pt-0">
                          <details className="group">
                            <summary className="cursor-pointer select-none text-[11px] text-phoenix-400 hover:text-phoenix-300 flex items-center gap-1">
                              <span>Last {recent.length} trade{recent.length === 1 ? "" : "s"}</span>
                            </summary>
                            <ul className="mt-2 space-y-1.5 pl-2 border-l border-zinc-800">
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
        </div>
      ))}

      <p className="text-[11px] text-zinc-500 pt-1">
        Worst case bar: full width is the hard limit ({fmtMoney(hardLimit)}); the white tick is the budget (
        {fmtMoney(budget)}); the red edge is the hard limit.
      </p>
    </div>
  );
}
