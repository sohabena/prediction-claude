// Formatting helpers for the Data page. All clock times are UTC.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Parse an API timestamp. Microseconds are trimmed to milliseconds; a value without a zone is read as UTC.
export function parseIso(value: string | null | undefined): Date | null {
  if (!value) return null;
  let s = value.replace(/(\.\d{3})\d+/, "$1");
  if (!/([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

// "HH:MM" for a time on today's UTC date, otherwise "DD Mon HH:MM".
export function fmtUtcTime(value: string | null | undefined, now: Date = new Date()): string {
  const d = parseIso(value);
  if (!d) return "-";
  const hhmm = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  const sameDay =
    d.getUTCFullYear() === now.getUTCFullYear() &&
    d.getUTCMonth() === now.getUTCMonth() &&
    d.getUTCDate() === now.getUTCDate();
  return sameDay ? hhmm : `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${hhmm}`;
}

// "YYYY-MM-DD HH:MM:SS UTC" for run times.
export function fmtUtcFull(value: string | null | undefined): string {
  const d = parseIso(value);
  if (!d) return "-";
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes()
  )}:${pad(d.getUTCSeconds())} UTC`;
}

// Seconds as "12 s", "14 min", "3 h 5 min" or "2 d 4 h".
export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "-";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

// A check value for display: numbers rounded to three decimals, nulls as a dash.
export function fmtValue(value: number | string | boolean | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return value.toLocaleString("en-GB");
    return (Math.round(value * 1000) / 1000).toString();
  }
  return String(value);
}

// The name of a check as a readable label: "match_page_coverage" becomes "match page coverage".
export function fmtCheckName(name: string): string {
  return name.replace(/_/g, " ");
}

export interface Tone {
  text: string;
  badge: string;
  label: string;
}

export function checkTone(status: string): Tone {
  switch (status) {
    case "ok":
      return { text: "text-emerald-400", badge: "bg-emerald-500/10 border-emerald-500/40 text-emerald-400", label: "OK" };
    case "warn":
      return { text: "text-amber-400", badge: "bg-amber-500/10 border-amber-500/40 text-amber-400", label: "WARN" };
    case "fail":
      return { text: "text-red-400", badge: "bg-red-500/10 border-red-500/40 text-red-400", label: "FAIL" };
    default:
      return {
        text: "text-zinc-400",
        badge: "bg-zinc-700/40 border-zinc-600 text-zinc-300",
        label: (status || "unknown").toUpperCase(),
      };
  }
}

export function stateTone(state: string): string {
  switch (state) {
    case "live":
      return "bg-emerald-500/10 border-emerald-500/40 text-emerald-400";
    case "pre-match":
      return "bg-sky-500/10 border-sky-500/40 text-sky-400";
    case "finished":
      return "bg-zinc-700/40 border-zinc-600 text-zinc-300";
    default:
      return "bg-zinc-700/40 border-zinc-600 text-zinc-300";
  }
}

export function scorePipelineLabel(pipeline: string | null): string {
  if (!pipeline) return "none yet";
  if (pipeline === "legacy") return "legacy feed";
  if (pipeline === "livescore-v2") return "livescore v2";
  return pipeline;
}

// Reads a FastAPI error body: {detail: string} for our refusals, or a validation list for bad fields.
export async function readErrorDetail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" ? (item as { msg?: unknown }).msg : undefined))
      .filter((m): m is string => typeof m === "string");
    if (messages.length) return messages.join("; ");
  }
  return `HTTP ${res.status}`;
}
