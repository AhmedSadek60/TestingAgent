/** Facts about a run that several screens need. */
import type { RunDetail, RunSummary } from "../api/types";
import { secondsBetween } from "./format";

export const ACTIVE_STATUSES = ["pending", "running", "cancelling"] as const;

export function isActive(status: string): boolean {
  return (ACTIVE_STATUSES as readonly string[]).includes(status);
}

export function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 8) : id;
}

/** How long the run took (or has taken so far). */
export function runSeconds(run: Pick<RunSummary, "started_at" | "finished_at" | "created_at">, now: Date = new Date()): number | null {
  return secondsBetween(run.started_at ?? run.created_at, run.finished_at, now);
}

/** The message of the error a run ended with, whatever shape the server put it in. */
export function errorMessage(error: object | null | undefined): string | null {
  if (!error) return null;
  const e = error as { message?: unknown; kind?: unknown };
  if (typeof e.message === "string" && e.message) return typeof e.kind === "string" ? `${e.message} (${e.kind})` : e.message;
  try {
    return JSON.stringify(error);
  } catch {
    return "The run failed.";
  }
}

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** The limits the run actually used: the manifest records the effective configuration (with this run's overrides); the
 * run row holds the server's defaults at the time it was queued. */
export function effectiveLimits(run: Pick<RunDetail, "manifest" | "limits">): { costUsd: number | null; seconds: number | null; tokens: number | null } {
  const manifest = run.manifest as { config?: { limits?: Record<string, unknown> } };
  const limits = manifest.config?.limits ?? (run.limits as Record<string, unknown>);
  return { costUsd: num(limits?.max_cost_usd), seconds: num(limits?.max_execution_time_seconds), tokens: num(limits?.max_tokens) };
}

export function runTitle(run: Pick<RunSummary, "target" | "suite" | "kind" | "id">): string {
  const target = run.target ?? "Unnamed target";
  return run.kind === "plan" ? `${target} · plan` : `${target} · ${run.suite}`;
}

/** Where a run is opened: plans have their own screen. */
export function runPath(run: Pick<RunSummary, "id" | "kind">): string {
  return run.kind === "plan" ? `/plans/${run.id}` : `/runs/${run.id}`;
}
