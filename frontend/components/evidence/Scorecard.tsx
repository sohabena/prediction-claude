import type { EvidenceResponse, ExcessCell, VariantEvidence } from "@/app/evidence/types";
import { fmtInterval, fmtMatchesBets, fmtMoney, fmtPct, fmtPp } from "./format";

function TooFew() {
  return (
    <span className="mt-1 inline-block rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-300">
      too few matches
    </span>
  );
}

// The plain sentence(s) from the review. The first one is the headline.
export function ExcessVerdict({ data }: { data: EvidenceResponse }) {
  const ex = data.excess_over_placebo;
  const headline = ex.sentence_all_bets ?? ex.sentence_pre_match ?? ex.sentence_in_play;
  const extra = [
    ex.sentence_pre_match && ex.sentence_pre_match !== headline ? { label: "Pre-match", text: ex.sentence_pre_match } : null,
    ex.sentence_in_play && ex.sentence_in_play !== headline ? { label: "In-play", text: ex.sentence_in_play } : null,
  ].filter((x): x is { label: string; text: string } => x !== null);

  return (
    <section aria-labelledby="verdict-heading" className="rounded-2xl border border-phoenix-500/30 bg-phoenix-500/5 p-5 sm:p-6">
      <p id="verdict-heading" className="text-xs font-semibold uppercase tracking-widest text-phoenix-400">
        What the evidence says
      </p>
      <p className="mt-2 text-lg font-semibold leading-snug text-zinc-100 sm:text-xl">
        {headline ?? "No verdict yet: the review has not produced a sentence."}
      </p>
      {extra.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-zinc-400">
          {extra.map((e) => (
            <li key={e.label}>
              <span className="text-zinc-500">{e.label}:</span> {e.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ExcessCellView({ cell }: { cell: ExcessCell | undefined }) {
  if (!cell || cell.matches === 0) return <span className="text-sm text-zinc-500">No bets in this segment yet</span>;
  const beats = cell.above_zero && !cell.too_few_matches;
  return (
    <div className="space-y-0.5">
      <p className={`text-base font-semibold tabular-nums ${beats ? "text-emerald-400" : "text-zinc-100"}`}>
        {fmtPp(cell.estimate)}
      </p>
      <p className="text-xs text-zinc-500 tabular-nums">{fmtInterval(cell.ci_lo, cell.ci_hi, (x) => fmtPp(x))}</p>
      <p className="text-xs text-zinc-500 tabular-nums">{fmtMatchesBets(cell.matches, cell.bets)}</p>
      {cell.too_few_matches && <TooFew />}
    </div>
  );
}

function PnlCell({ v }: { v: VariantEvidence }) {
  return (
    <div className="space-y-0.5">
      <p className="text-base font-semibold tabular-nums text-zinc-100">{fmtMoney(v.pnl.total)}</p>
      <p className="text-xs text-zinc-500 tabular-nums">
        {fmtInterval(v.pnl.ci_lo, v.pnl.ci_hi, (x) => fmtMoney(x))}
      </p>
      <p className="text-xs text-zinc-500 tabular-nums">{fmtMatchesBets(v.pnl.matches, v.pnl.bets)}</p>
    </div>
  );
}

export function StrategyTable({ data }: { data: EvidenceResponse }) {
  const ex = data.excess_over_placebo;
  const strategies = data.variants.filter((v) => !v.placebo && v.variant !== "market_only");

  return (
    <section aria-labelledby="strategy-heading" className="space-y-3">
      <div>
        <h2 id="strategy-heading" className="text-lg font-semibold text-zinc-100">Strategy variants</h2>
        <p className="mt-1 text-xs leading-relaxed text-zinc-500">
          CLV excess over {ex.control}: the variant&apos;s closing-line value minus the control&apos;s, on the same
          matches, in percentage points (pp). Confidence intervals are clustered by match. P&amp;L is in paper units.
        </p>
      </div>

      {strategies.length === 0 ? (
        <div className="stat-card text-sm text-zinc-400">No strategy variants reported yet.</div>
      ) : (
        <div className="stat-card overflow-x-auto p-0">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-3 font-semibold">Variant</th>
                <th scope="col" className="px-4 py-3 font-semibold">Pre-match CLV excess</th>
                <th scope="col" className="px-4 py-3 font-semibold">In-play CLV excess</th>
                <th scope="col" className="px-4 py-3 font-semibold">P&amp;L</th>
                <th scope="col" className="px-4 py-3 font-semibold">Fill rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {strategies.map((v) => (
                <tr key={v.variant} className="align-top">
                  <th scope="row" className="px-4 py-4 font-mono text-xs text-zinc-200 whitespace-nowrap">
                    {v.variant}
                  </th>
                  <td className="px-4 py-4">
                    <ExcessCellView cell={ex.pre_match[v.variant]} />
                  </td>
                  <td className="px-4 py-4">
                    <ExcessCellView cell={ex.in_play[v.variant]} />
                  </td>
                  <td className="px-4 py-4">
                    <PnlCell v={v} />
                  </td>
                  <td className="px-4 py-4 tabular-nums text-zinc-300">{fmtPct(v.fill_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function ControlsTable({ data }: { data: EvidenceResponse }) {
  const controls = data.variants.filter((v) => v.placebo || v.variant === "market_only");
  if (controls.length === 0) return null;

  return (
    <section aria-labelledby="controls-heading" className="space-y-3">
      <div>
        <h2 id="controls-heading" className="text-lg font-semibold text-zinc-100">Controls</h2>
        <p className="mt-1 text-xs leading-relaxed text-zinc-500">
          Not strategies. Placebos bet at random or on the favourite with the same timing and stakes; market_only only
          reduces and greens up. They show what the fill rule and price drift produce on their own.
        </p>
      </div>

      <div className="stat-card overflow-x-auto p-0">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400">
            <tr>
              <th scope="col" className="px-4 py-3 font-semibold">Control</th>
              <th scope="col" className="px-4 py-3 font-semibold">CLV</th>
              <th scope="col" className="px-4 py-3 font-semibold">P&amp;L</th>
              <th scope="col" className="px-4 py-3 font-semibold">Fill rate</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {controls.map((v) => (
              <tr key={v.variant} className="align-top">
                <th scope="row" className="px-4 py-4 font-mono text-xs text-zinc-300 whitespace-nowrap">
                  {v.variant}
                </th>
                <td className="px-4 py-4">
                  <div className="space-y-0.5">
                    <p className="font-semibold tabular-nums text-zinc-200">{fmtPp(v.clv.mean)}</p>
                    <p className="text-xs text-zinc-500 tabular-nums">
                      {fmtInterval(v.clv.ci_lo, v.clv.ci_hi, (x) => fmtPp(x))}
                    </p>
                    <p className="text-xs text-zinc-500 tabular-nums">{fmtMatchesBets(v.clv.matches, v.clv.bets)}</p>
                    {v.clv.too_few_matches && <TooFew />}
                  </div>
                </td>
                <td className="px-4 py-4">
                  <PnlCell v={v} />
                </td>
                <td className="px-4 py-4 tabular-nums text-zinc-300">{fmtPct(v.fill_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
