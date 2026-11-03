import type { LiveMatch } from "@/app/today/types";
import { fmtPct, fmtPrice, fmtStartsIn, fmtUtcHMS, parseTs } from "./format";
import { VariantTable } from "./VariantTable";

function StatusPill({ match, nowMs }: { match: LiveMatch; nowMs: number }) {
  if (match.in_play) {
    return (
      <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold tracking-wide text-emerald-300">
        <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
        IN PLAY
      </span>
    );
  }
  const startMs = parseTs(match.start);
  if (startMs !== null && startMs > nowMs) {
    return (
      <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-xs font-semibold text-amber-300">
        {fmtStartsIn(startMs, nowMs)}
      </span>
    );
  }
  return (
    <span className="rounded-full border border-zinc-700 px-3 py-1 text-xs font-semibold text-zinc-400">
      NOT IN PLAY
    </span>
  );
}

function ScoreLine({ match }: { match: LiveMatch }) {
  const sc = match.latest_score;
  if (!sc) {
    return <p className="text-sm text-zinc-500">No score yet</p>;
  }
  const runs =
    sc.score !== null && sc.wickets !== null
      ? `${sc.score}/${sc.wickets}`
      : sc.score !== null
        ? `${sc.score}`
        : null;
  const parts = [
    runs,
    sc.overs !== null ? `(${sc.overs} ov)` : null,
    sc.innings !== null ? `innings ${sc.innings}` : null,
    sc.batting_team ? `${sc.batting_team} batting` : null,
  ].filter((p): p is string => Boolean(p));

  return (
    <p className="text-sm text-zinc-200">
      <span className="font-semibold tabular-nums">{parts.length ? parts.join(" · ") : "Score not known yet"}</span>
      <span className="ml-2 text-xs text-zinc-500">score as of {fmtUtcHMS(sc.at)} UTC</span>
    </p>
  );
}

function PriceBox({
  name,
  back,
  lay,
}: {
  name: string;
  back: number | null;
  lay: number | null;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-3">
      <p className="truncate text-sm font-semibold text-zinc-200">{name}</p>
      <p className="mt-1 text-xs tabular-nums text-zinc-400">
        Back <span className="font-semibold text-sky-300">{fmtPrice(back)}</span>
        <span className="mx-2 text-zinc-600">|</span>
        Lay <span className="font-semibold text-rose-300">{fmtPrice(lay)}</span>
      </p>
    </div>
  );
}

export function MatchCard({ match, nowMs }: { match: LiveMatch; nowMs: number }) {
  return (
    <article className="min-w-0 space-y-4 rounded-xl border border-zinc-800/60 bg-zinc-900/60 p-4 shadow-[0_4px_30px_rgba(0,0,0,0.1)] backdrop-blur-md sm:p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold uppercase tracking-widest text-zinc-400">
            {match.competition ?? "Competition unknown"}
          </p>
          <h2 className="break-words text-lg font-bold text-zinc-100">
            {match.team_home} <span className="font-normal text-zinc-500">v</span> {match.team_away}
          </h2>
        </div>
        <StatusPill match={match} nowMs={nowMs} />
      </header>

      <div className="space-y-3">
        <ScoreLine match={match} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <PriceBox name={match.team_home} back={match.book.back_home} lay={match.book.lay_home} />
          <PriceBox name={match.team_away} back={match.book.back_away} lay={match.book.lay_away} />
        </div>
        <p className="text-xs tabular-nums text-zinc-400">
          Home win chance: model <span className="font-semibold text-zinc-200">{fmtPct(match.p_model)}</span>
          <span className="mx-2 text-zinc-600">|</span>
          market <span className="font-semibold text-zinc-200">{fmtPct(match.p_market)}</span>
        </p>
      </div>

      {match.variants.length === 0 ? (
        <p className="text-sm text-zinc-500">No variant has a position on this match yet.</p>
      ) : (
        <VariantTable variants={match.variants} />
      )}
    </article>
  );
}
