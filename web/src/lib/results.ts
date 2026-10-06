/** Filtering and counting results and findings. Pure: the screens only decide what to show. */
import type { Finding, TestResult } from "../api/types";

export interface ResultFilters {
  status: string;
  category: string;
  severity: string;
  q: string;
}

export const NO_RESULT_FILTERS: ResultFilters = { status: "", category: "", severity: "", q: "" };

/** Statuses in the order a reader wants to see them: what needs attention first. */
export const STATUS_ORDER = ["failed", "error", "timeout", "blocked", "skipped", "passed"] as const;

export function filterResults(results: TestResult[], filters: ResultFilters): TestResult[] {
  const needle = filters.q.trim().toLowerCase();
  return results.filter(
    (r) =>
      (!filters.status || r.status === filters.status) &&
      (!filters.category || r.category === filters.category) &&
      (!filters.severity || r.severity === filters.severity) &&
      (!needle || r.test_id.toLowerCase().includes(needle) || r.test_name.toLowerCase().includes(needle)),
  );
}

export function statusCounts(results: TestResult[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const r of results) counts[r.status] = (counts[r.status] ?? 0) + 1;
  return counts;
}

export function distinctValues(results: TestResult[], pick: (r: TestResult) => string | null): string[] {
  return [...new Set(results.map(pick).filter((v): v is string => Boolean(v)))].sort((a, b) => a.localeCompare(b));
}

const SEVERITY_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

export function severityRank(severity: string | null | undefined): number {
  return severity ? (SEVERITY_RANK[severity] ?? -1) : -1;
}

export function severityCounts(findings: Finding[]): Record<string, number> {
  const counts: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const f of findings) if (f.status !== "false_positive") counts[f.severity] = (counts[f.severity] ?? 0) + 1;
  return counts;
}

export interface FindingFilters {
  severity: string;
  category: string;
  kind: "all" | "security" | "quality";
  showRejected: boolean;
  q: string;
}

export const NO_FINDING_FILTERS: FindingFilters = { severity: "", category: "", kind: "all", showRejected: false, q: "" };

/** The ones that matter most first: severity, then security before quality, then the title so the order is stable. */
export function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort(
    (a, b) => severityRank(b.severity) - severityRank(a.severity) || Number(b.is_security) - Number(a.is_security) || a.title.localeCompare(b.title),
  );
}

export function filterFindings(findings: Finding[], f: FindingFilters): Finding[] {
  const needle = f.q.trim().toLowerCase();
  return sortFindings(findings).filter(
    (x) =>
      (f.showRejected || x.status !== "false_positive") &&
      (!f.severity || x.severity === f.severity) &&
      (!f.category || x.category === f.category) &&
      (f.kind === "all" || (f.kind === "security") === x.is_security) &&
      (!needle || x.title.toLowerCase().includes(needle) || x.test_id.toLowerCase().includes(needle) || x.observed.toLowerCase().includes(needle)),
  );
}

/** How a test ended, for the one-line summary above a result. */
export function outcomeLine(r: TestResult): string {
  if (r.status === "blocked") return r.blocked_reason ?? "Blocked: a prerequisite was not met, so the test did not run.";
  if (r.status === "skipped") return "Skipped: the run ended before this test started.";
  if (r.status === "passed") return "All checks passed.";
  const failed = r.attempts.flatMap((a) => a.assertions).filter((x) => !x.passed && !x.evaluator_error);
  if (failed.length > 0) return failed[0].message;
  const error = r.attempts.find((a) => a.error)?.error;
  return error ?? "The test did not meet its expectations.";
}
