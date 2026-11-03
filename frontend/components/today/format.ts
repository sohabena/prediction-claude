// Formatting helpers for the Today page. Output is deterministic (fixed
// locale, UTC clock) so server and client renders agree.

export function parseTs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

/** Whole money units with thousands separators; signed adds "+" for gains. */
export function fmtMoney(n: number | null | undefined, signed = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const r = Math.round(n);
  const body = Math.abs(r).toLocaleString("en-US");
  if (r < 0) return `-${body}`;
  if (signed && r > 0) return `+${body}`;
  return body;
}

export function fmtPrice(n: number | null | undefined): string {
  return n === null || n === undefined || Number.isNaN(n) ? "–" : n.toFixed(2);
}

export function fmtPct(p: number | null | undefined): string {
  return p === null || p === undefined || Number.isNaN(p) ? "–" : `${Math.round(p * 100)}%`;
}

/** HH:MM in UTC. */
export function fmtUtcHM(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? "–" : new Date(t).toISOString().slice(11, 16);
}

/** HH:MM:SS in UTC. */
export function fmtUtcHMS(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? "–" : new Date(t).toISOString().slice(11, 19);
}

/** YYYY-MM-DD HH:MM UTC. */
export function fmtUtcStamp(iso: string | null | undefined): string {
  const t = parseTs(iso);
  if (t === null) return "–";
  const s = new Date(t).toISOString();
  return `${s.slice(0, 10)} ${s.slice(11, 16)} UTC`;
}

/** Seconds as "45 s", "12 min", "2 h 5 min". */
export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h} h` : `${h} h ${rm} min`;
}

/** "starts in 12 min" from a start time and the current time (ms). */
export function fmtStartsIn(startMs: number, nowMs: number): string {
  const diffMs = startMs - nowMs;
  if (diffMs <= 0) return "starting";
  const minutes = Math.ceil(diffMs / 60000);
  if (minutes < 60) return `starts in ${minutes} min`;
  const h = Math.floor(minutes / 60);
  const rm = minutes % 60;
  return rm === 0 ? `starts in ${h} h` : `starts in ${h} h ${rm} min`;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined) return "text-zinc-500";
  if (n > 0) return "text-emerald-400";
  if (n < 0) return "text-red-400";
  return "text-zinc-400";
}

/** Placebo and market_only variants are controls, not strategies. */
export function isControl(v: { variant: string; placebo: boolean }): boolean {
  const name = v.variant.toLowerCase();
  return v.placebo || name.includes("market_only") || name.startsWith("placebo");
}
