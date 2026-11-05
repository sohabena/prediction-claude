// Number formatting for the Evidence page. CLV is a probability difference, shown in percentage points (pp).

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "n/a";
  return Math.round(n).toLocaleString("en-US");
}

export function fmtPp(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const v = x * 100;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)} pp`;
}

export function fmtPpBare(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  return (x * 100).toFixed(digits);
}

export function fmtMoney(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const r = Math.round(x);
  const sign = r > 0 ? "+" : "";
  return `${sign}${r.toLocaleString("en-US")}`;
}

export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  return `${(x * 100).toFixed(digits)}%`;
}

export function fmtDecimal(x: number | null | undefined, digits = 4): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const sign = x > 0 ? "+" : "";
  return `${sign}${x.toFixed(digits)}`;
}

// "[lo, hi]" in the given unit, or a plain note when the interval is not computed yet.
export function fmtInterval(
  lo: number | null | undefined,
  hi: number | null | undefined,
  fmt: (x: number) => string
): string {
  if (lo === null || lo === undefined || hi === null || hi === undefined) return "95% CI not yet";
  return `95% CI ${fmt(lo)} to ${fmt(hi)}`;
}

function plural(n: number | null | undefined, one: string, many: string): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

export function fmtMatchesBets(matches: number | null | undefined, bets: number | null | undefined): string {
  return `${plural(matches, "match", "matches")} / ${plural(bets, "bet", "bets")}`;
}

export function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";
}
