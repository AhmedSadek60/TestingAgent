/** What this browser tab has seen go wrong, kept so that a person reporting a problem can choose to share it.
 *
 * It is a small bounded buffer in memory: nothing is stored, nothing is sent until the person asks for the browser's details to
 * be collected and reviews them. It holds the console's errors and warnings, uncaught errors and unhandled rejections, and the
 * requests to AgentLab that failed (the method, the path without its query, the status and the server's request id: never a body). */
import type { FailedRequest } from "../api/client";
import type { BrowserDiagnostics } from "../api/types";

export interface ConsoleRecord {
  time: string;
  level: "error" | "warn";
  message: string;
  source?: string;
  line?: number;
}

export interface NetworkRecord {
  time: string;
  method: string;
  url: string;
  status: number | null;
  error?: string;
  request_id?: string;
  duration_ms?: number;
}

const LIMIT = 50;
const MESSAGE_LIMIT = 1_500;
const consoleRecords: ConsoleRecord[] = [];
const networkRecords: NetworkRecord[] = [];
let installed = false;

function push<T>(list: T[], item: T): void {
  list.push(item);
  if (list.length > LIMIT) list.splice(0, list.length - LIMIT);
}

function text(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

export function recordConsole(level: "error" | "warn", values: unknown[], source?: string, line?: number): void {
  push(consoleRecords, {
    time: new Date().toISOString(),
    level,
    message: values.map(text).join(" ").slice(0, MESSAGE_LIMIT),
    ...(source ? { source: source.slice(0, 280) } : {}),
    ...(typeof line === "number" ? { line } : {}),
  });
}

export function recordFailure(failure: FailedRequest): void {
  // the server's own request ids are 8 to 40 letters and digits; anything else is not sent
  const requestId = failure.requestId && /^[A-Za-z0-9]{8,40}$/.test(failure.requestId) ? failure.requestId : undefined;
  push(networkRecords, {
    time: new Date().toISOString(),
    method: failure.method.toUpperCase().slice(0, 10),
    url: failure.path.split("?")[0]!.slice(0, 480),
    status: failure.status,
    ...(failure.error ? { error: failure.error } : {}),
    ...(requestId ? { request_id: requestId } : {}),
    duration_ms: Math.max(0, Math.min(3_600_000, Math.round(failure.durationMs))),
  });
}

/** Start listening, once. Returns a function that stops (for tests). */
export function startBrowserErrorBuffer(): () => void {
  if (installed) return () => undefined;
  installed = true;
  const originalError = console.error;
  const originalWarn = console.warn;
  console.error = (...values: unknown[]) => {
    recordConsole("error", values);
    originalError.apply(console, values);
  };
  console.warn = (...values: unknown[]) => {
    recordConsole("warn", values);
    originalWarn.apply(console, values);
  };
  const onError = (event: ErrorEvent) => recordConsole("error", [event.message], event.filename, event.lineno);
  const onRejection = (event: PromiseRejectionEvent) => recordConsole("error", ["Unhandled rejection:", event.reason]);
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    console.error = originalError;
    console.warn = originalWarn;
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    installed = false;
  };
}

export function browserSnapshot(): BrowserDiagnostics {
  return {
    console: consoleRecords.map((r) => ({ ...r })),
    network: networkRecords.map((r) => ({ ...r })),
  };
}

export function browserCounts(): { console: number; network: number } {
  return { console: consoleRecords.length, network: networkRecords.length };
}

export function clearBrowserBuffer(): void {
  consoleRecords.length = 0;
  networkRecords.length = 0;
}
