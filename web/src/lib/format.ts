/** Small, pure formatting helpers: no component needs to know how a duration or a cost is written. */

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || Number.isNaN(usd)) return "n/a";
  if (usd === 0) return "$0";
  if (Math.abs(usd) < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(usd < 1 ? 3 : 2)}`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "n/a";
  if (seconds < 1) return `${Math.max(0, Math.round(seconds * 1000))} ms`; // a clock a little behind the server's must not show a negative time
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes - hours * 60} min`;
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "n/a";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "n/a";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return "n/a";
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

/** A stored timestamp (UTC, with or without an offset) as the viewer's local date and time. */
export function parseTime(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const date = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTime(iso: string | null | undefined): string {
  const date = parseTime(iso);
  if (!date) return "n/a";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  const date = parseTime(iso);
  if (!date) return "n/a";
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < -5) return "in the future";
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/** Seconds between two stored timestamps; `end` defaults to now. */
export function secondsBetween(start: string | null | undefined, end?: string | null, now: Date = new Date()): number | null {
  const a = parseTime(start);
  if (!a) return null;
  const b = end ? parseTime(end) : now;
  return b ? Math.max(0, (b.getTime() - a.getTime()) / 1000) : null;
}

export function titleCase(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
}

/** The text of a skill without the `---` header that carries its name and description (both are shown elsewhere). */
export function stripFrontMatter(text: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
  return match ? text.slice(match[0].length).trimStart() : text;
}

export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

/** `{"a": 1}` as a stable, indented string; strings pass through, anything unserialisable becomes its own description. */
export function pretty(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}
