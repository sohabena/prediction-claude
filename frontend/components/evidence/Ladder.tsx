import type { EvidenceResponse, VariantEvidence } from "@/app/evidence/types";
import { fmtInt } from "./format";

interface Bar {
  label: string;
  value: number | null;
  target: number | null;
}

function ProgressBar({ label, value, target }: Bar) {
  const done = value !== null && target !== null && target > 0 && value >= target;
  const pct = value !== null && target !== null && target > 0 ? Math.min(100, (value / target) * 100) : 0;
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-zinc-300">{label}</span>
        <span className={`font-semibold tabular-nums ${done ? "text-emerald-400" : "text-zinc-100"}`}>
          {fmtInt(value)} of {fmtInt(target)}
        </span>
      </div>
      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-zinc-800" role="progressbar"
        aria-label={`${label}: ${fmtInt(value)} of ${fmtInt(target)}`}
        aria-valuemin={0} aria-valuemax={target ?? 0} aria-valuenow={value ?? 0}>
        <div className={`h-full rounded-full transition-all duration-500 ${done ? "bg-emerald-500" : "bg-phoenix-500"}`}
          style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Rung({ tag, title, need, bars, footnote }: {
  tag: string;
  title: string;
  need: string;
  bars: Bar[];
  footnote?: string;
}) {
  return (
    <div className="stat-card flex flex-col gap-4 min-w-0">
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-phoenix-400">{tag}</p>
        <h3 className="mt-1 text-base font-semibold text-zinc-100">{title}</h3>
        <p className="mt-1 text-sm leading-relaxed text-zinc-400">{need}</p>
      </div>
      <div className="space-y-4">
        {bars.map((b) => (
          <ProgressBar key={b.label} {...b} />
        ))}
      </div>
      {footnote && <p className="text-xs text-zinc-500">{footnote}</p>}
    </div>
  );
}

// The strategy variant closest to R3: most settled matches, then most settled bets.
function closestToR3(variants: VariantEvidence[]): VariantEvidence | null {
  const strategies = variants.filter((v) => !v.placebo && v.variant !== "market_only");
  if (strategies.length === 0) return null;
  return [...strategies].sort(
    (a, b) => b.r3.settled_matches - a.r3.settled_matches || b.r3.settled_bets - a.r3.settled_bets
  )[0];
}

export function ValidationLadder({ data }: { data: EvidenceResponse }) {
  const { progress, tradable, variants } = data;
  const closest = closestToR3(variants);
  const r3Matches = closest ? closest.r3.settled_matches : null;
  const r3Bets = closest ? closest.r3.settled_bets : null;

  return (
    <section aria-labelledby="ladder-heading" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ladder-heading" className="text-lg font-semibold text-zinc-100">Validation ladder</h2>
        <p className="text-xs text-zinc-500">
          {data.r3_passed ? "R3 passed by a variant" : "No variant has passed R3"}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Rung
          tag="R1"
          title="Model beats the market"
          need="The model's log loss beats the de-vigged market's over 200 matches with a pre-match price."
          bars={[{ label: "Matches", value: progress.r1.matches, target: progress.r1.target }]}
        />
        <Rung
          tag="R2"
          title="Edge after costs"
          need="CLV and P&L are both above zero over 150 held-out matches."
          bars={[{ label: "Matches", value: progress.r2.matches, target: progress.r2.target }]}
          footnote={progress.r2.cutoff ? `Held out from ${progress.r2.cutoff}.` : undefined}
        />
        <Rung
          tag="R3"
          title="Settled paper bets"
          need="300 settled paper bets over 30 matches, and the CLV confidence interval's lower bound above zero."
          bars={[
            { label: "Matches", value: r3Matches, target: progress.r3.target_matches },
            { label: "Settled bets", value: r3Bets, target: progress.r3.target_settled_bets },
          ]}
          footnote={
            closest
              ? `Closest variant: ${closest.variant}. CLV lower bound above zero: ${closest.r3.clv_ci_lo_above_zero ? "yes" : "no"}.`
              : "No strategy variant reported yet."
          }
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="stat-card min-w-0">
          <p className="text-xs uppercase tracking-widest font-semibold text-zinc-400 mb-2">
            Settled real matches with a tradable book
          </p>
          <p className="text-3xl font-black tracking-tight text-zinc-100 tabular-nums">
            {fmtInt(tradable.tradable_settled)}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Both sides back and lay, result settled.</p>
        </div>
        <div className="stat-card min-w-0">
          <p className="text-xs uppercase tracking-widest font-semibold text-zinc-400 mb-2">
            Last {fmtInt(tradable.recent_days)} days
          </p>
          <p className="text-3xl font-black tracking-tight text-zinc-100 tabular-nums">
            {fmtInt(tradable.tradable_settled_recent)}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Same measure, settled within the recent window.</p>
        </div>
      </div>
    </section>
  );
}
