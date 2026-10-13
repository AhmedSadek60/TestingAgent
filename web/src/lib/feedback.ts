/** Feedback & Support in the interface: the words, the forms' values and what makes them valid, how a form becomes a request,
 * and the draft a person's tab keeps while they write. Nothing here talks to the server. */
import type { SubmissionRequest } from "../api/types";
import { readStored, writeStored } from "./storage";

// ------------------------------------------------------------------------------------------------------------ words
export const IMPACT_LABELS: Record<string, string> = {
  cosmetic: "Cosmetic inconvenience",
  slows_work: "Slows down my work",
  prevents_workflow: "Prevents a particular workflow",
  blocks_project: "Blocks an important project",
  incorrect_results: "Causes incorrect evaluation results",
  data_loss_or_exposure: "Causes data loss or suspected data exposure",
  unavailable: "Makes AgentLab unavailable",
  other: "Other",
};

export const FREQUENCY_LABELS: Record<string, string> = {
  every_time: "Every time",
  frequently: "Frequently",
  occasionally: "Occasionally",
  once: "Happened once",
  unknown: "Unknown",
};

export const FEATURE_PRIORITY_LABELS: Record<string, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
  critical_business_need: "Critical business need",
};

export const PRIORITY_LABELS: Record<string, string> = { low: "Low", normal: "Normal", high: "High", critical: "Critical" };
export const RELATION_LABELS: Record<string, string> = {
  duplicate_of: "Duplicate of",
  related: "Related to",
  caused_by: "Caused by",
  implements: "Implemented by",
};
export const CATEGORY_LABELS: Record<string, string> = {
  environment: "Environment and version",
  application_logs: "Application logs",
  backend_logs: "Backend (worker) logs",
  errors: "Error details and stack traces",
  trace: "AgentLab execution trace",
  run_metadata: "Evaluation-run metadata",
  test_summary: "Test result summary",
  browser: "Browser console and network errors",
  screenshot: "Screenshot of the current page",
};
export const WINDOW_LABELS: Record<string, string> = {
  last_5_minutes: "Last 5 minutes",
  last_15_minutes: "Last 15 minutes",
  last_30_minutes: "Last 30 minutes",
  custom: "Custom time range",
};

export const label = (map: Record<string, string>, key: string | null | undefined): string => (key ? (map[key] ?? key.replace(/_/g, " ")) : "");

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The tone of a workflow status, from its name: the workflow is configurable, so the names are matched, not listed. */
export function workflowTone(status: string): "good" | "bad" | "warn" | "info" | "neutral" {
  const name = status.toLowerCase();
  if (/(completed|resolved|fix available)/.test(name)) return "good";
  if (/(declined|cannot reproduce|duplicate|closed)/.test(name)) return "neutral";
  if (/(awaiting)/.test(name)) return "warn";
  if (/(in progress|investigating|reproducing|planned|under review)/.test(name)) return "info";
  return "neutral";
}

// ------------------------------------------------------------------------------------------------------ the forms
/** What an identifier of the server's looks like: the API refuses anything else, so the form says so before sending. */
export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;

export interface Context {
  project_id: string;
  related_target_id: string;
  related_run_id: string;
  related_test_id: string;
  related_result_id: string;
  related_trace_id: string;
  related_report_id: string;
  ui_screen: string;
  worker_job_id: string;
  provider_config_id: string;
}

export const EMPTY_CONTEXT: Context = {
  project_id: "",
  related_target_id: "",
  related_run_id: "",
  related_test_id: "",
  related_result_id: "",
  related_trace_id: "",
  related_report_id: "",
  ui_screen: "",
  worker_job_id: "",
  provider_config_id: "",
};

export const CONTEXT_LABELS: Record<keyof Context, string> = {
  project_id: "Project",
  related_target_id: "Target",
  related_run_id: "Evaluation run",
  related_test_id: "Test case",
  related_result_id: "Test result",
  related_trace_id: "Trace",
  related_report_id: "Report",
  ui_screen: "Screen",
  worker_job_id: "Background job",
  provider_config_id: "Provider configuration",
};

const QUERY_NAMES: Record<keyof Context, string> = {
  project_id: "project",
  related_target_id: "target",
  related_run_id: "run",
  related_test_id: "test",
  related_result_id: "result",
  related_trace_id: "trace",
  related_report_id: "report",
  ui_screen: "screen",
  worker_job_id: "job",
  provider_config_id: "provider",
};

/** The address of the report form for what the person was looking at: only identifiers, nothing they typed or saw. */
export function reportLink(context: Partial<Context>): string {
  const params = new URLSearchParams();
  for (const key of Object.keys(QUERY_NAMES) as (keyof Context)[]) {
    const value = context[key];
    if (value) params.set(QUERY_NAMES[key], value);
  }
  const text = params.toString();
  return `/feedback/issue${text ? `?${text}` : ""}`;
}

/** The context a form was opened with. A value that is not a valid identifier is dropped: it came from an address anyone can
 * write, so it is checked like a typed one. */
export function contextFromSearch(search: string): Context {
  const params = new URLSearchParams(search);
  const out: Context = { ...EMPTY_CONTEXT };
  for (const key of Object.keys(QUERY_NAMES) as (keyof Context)[]) {
    const value = (params.get(QUERY_NAMES[key]) ?? "").trim();
    if (!value) continue;
    if (key === "ui_screen") out[key] = value.slice(0, 200);
    else if (ID_PATTERN.test(value)) out[key] = value;
  }
  return out;
}

export interface FeatureValues extends Context {
  title: string;
  category: string;
  problem_statement: string;
  description: string;
  expected_behavior: string;
  primary_use_case: string;
  requested_priority: string;
  why_important: string;
  users_affected: string;
  business_impact: string;
  use_frequency: string;
  current_workaround: string;
  proposed_solution: string;
  acceptance_criteria: string;
  related_area: string;
  dependencies: string;
}

export interface IssueValues extends Context {
  title: string;
  category: string;
  description: string;
  impact_summary: string;
  reported_impact: string;
  expected_behavior: string;
  actual_behavior: string;
  reproduction_steps: string;
  not_reproducible: boolean;
  frequency: string;
  first_observed_at: string;
  consistent: "" | "yes" | "no";
  blocks_work: "" | "yes" | "no";
  workaround: string;
  app_version: string;
  build: string;
}

export const emptyFeature = (context: Partial<Context> = {}): FeatureValues => ({
  ...EMPTY_CONTEXT,
  ...context,
  title: "",
  category: "",
  problem_statement: "",
  description: "",
  expected_behavior: "",
  primary_use_case: "",
  requested_priority: "normal",
  why_important: "",
  users_affected: "",
  business_impact: "",
  use_frequency: "",
  current_workaround: "",
  proposed_solution: "",
  acceptance_criteria: "",
  related_area: "",
  dependencies: "",
});

export const emptyIssue = (context: Partial<Context> = {}): IssueValues => ({
  ...EMPTY_CONTEXT,
  ...context,
  title: "",
  category: "",
  description: "",
  impact_summary: "",
  reported_impact: "slows_work",
  expected_behavior: "",
  actual_behavior: "",
  reproduction_steps: "",
  not_reproducible: false,
  frequency: "unknown",
  first_observed_at: "",
  consistent: "",
  blocks_work: "",
  workaround: "",
  app_version: "",
  build: "",
});

export type Problems = Partial<Record<string, string>>;

function contextProblems(values: Context): Problems {
  const problems: Problems = {};
  for (const key of Object.keys(CONTEXT_LABELS) as (keyof Context)[]) {
    const value = values[key].trim();
    if (key === "ui_screen") continue;
    if (value && !ID_PATTERN.test(value)) problems[key] = `${CONTEXT_LABELS[key]} must be an identifier: letters, digits and . : _ -`;
  }
  return problems;
}

export function featureProblems(values: FeatureValues): Problems {
  const problems = contextProblems(values);
  if (values.title.trim().length < 3) problems.title = "Give the request a short title (at least 3 characters).";
  if (!values.category) problems.category = "Choose a category.";
  if (!values.problem_statement.trim()) problems.problem_statement = "Describe the problem or opportunity.";
  return problems;
}

export function issueProblems(values: IssueValues): Problems {
  const problems = contextProblems(values);
  if (values.title.trim().length < 3) problems.title = "Give the report a short title (at least 3 characters).";
  if (!values.category) problems.category = "Choose the kind of problem it is.";
  if (!values.description.trim()) problems.description = "Describe the problem.";
  if (!values.impact_summary.trim()) problems.impact_summary = "Say briefly how this affects your work.";
  if (values.first_observed_at && Number.isNaN(Date.parse(values.first_observed_at))) problems.first_observed_at = "That is not a date and time.";
  return problems;
}

const idOrNull = (value: string): string | null => value.trim() || null;
const yesNo = (value: "" | "yes" | "no"): boolean | null => (value === "yes" ? true : value === "no" ? false : null);

function contextFields(values: Context) {
  return {
    project_id: idOrNull(values.project_id),
    related_run_id: idOrNull(values.related_run_id),
    related_test_id: idOrNull(values.related_test_id),
    related_trace_id: idOrNull(values.related_trace_id),
    related_result_id: idOrNull(values.related_result_id),
    related_report_id: idOrNull(values.related_report_id),
    related_target_id: idOrNull(values.related_target_id),
  };
}

export function featureRequest(values: FeatureValues): SubmissionRequest {
  return {
    type: "feature",
    title: values.title.trim(),
    category: values.category,
    description: values.description.trim(),
    feature: {
      problem_statement: values.problem_statement.trim(),
      expected_behavior: values.expected_behavior.trim(),
      primary_use_case: values.primary_use_case.trim(),
      why_important: values.why_important.trim(),
      users_affected: values.users_affected.trim(),
      business_impact: values.business_impact.trim(),
      use_frequency: values.use_frequency.trim(),
      current_workaround: values.current_workaround.trim(),
      proposed_solution: values.proposed_solution.trim(),
      acceptance_criteria: values.acceptance_criteria.trim(),
      related_area: values.related_area.trim(),
      dependencies: values.dependencies.trim(),
      requested_priority: values.requested_priority as "low" | "normal" | "high" | "critical_business_need",
    },
    ...contextFields(values),
  };
}

export function issueRequest(values: IssueValues, securityCategory: string): SubmissionRequest {
  return {
    type: "issue",
    title: values.title.trim(),
    category: values.category,
    description: values.description.trim(),
    issue: {
      impact_summary: values.impact_summary.trim(),
      expected_behavior: values.expected_behavior.trim(),
      actual_behavior: values.actual_behavior.trim(),
      reproduction_steps: values.not_reproducible ? "" : values.reproduction_steps.trim(),
      not_reproducible: values.not_reproducible,
      frequency: values.frequency as "every_time" | "frequently" | "occasionally" | "once" | "unknown",
      first_observed_at: values.first_observed_at ? new Date(values.first_observed_at).toISOString() : null,
      consistent: yesNo(values.consistent),
      blocks_work: yesNo(values.blocks_work),
      workaround: values.workaround.trim(),
      reported_impact: values.reported_impact as NonNullable<NonNullable<SubmissionRequest["issue"]>["reported_impact"]>,
      ui_screen: values.ui_screen.trim(),
      worker_job_id: idOrNull(values.worker_job_id),
      provider_config_id: idOrNull(values.provider_config_id),
    },
    ...contextFields(values),
    app_version: idOrNull(values.app_version),
    build: idOrNull(values.build),
    suspected_security: values.category === securityCategory,
  };
}

// ----------------------------------------------------------------------------------------------------- the draft
const DRAFT_PREFIX = "agentlab.feedback.draft.";

/** A draft is what a person has typed so far in this tab, kept in the tab's session storage (gone when the tab is closed, and
 * when they sign out) so that a reload does not lose it. Only the form's text is kept: never a file, a diagnostic or a token. */
export function loadDraft<T extends object>(kind: string, fallback: T): T {
  const raw = readStored("session", DRAFT_PREFIX + kind);
  if (!raw) return fallback;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      const base = Object.assign({}, fallback) as Record<string, unknown>;
      const out: Record<string, unknown> = { ...base };
      for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (key in base && typeof value === typeof base[key]) out[key] = value;
      }
      return out as T;
    }
  } catch {
    /* a draft that cannot be read is no draft */
  }
  return fallback;
}

export function saveDraft(kind: string, values: object): void {
  writeStored("session", DRAFT_PREFIX + kind, JSON.stringify(values));
}

export function clearDraft(kind: string): void {
  writeStored("session", DRAFT_PREFIX + kind, null);
}

export function clearFeedbackDrafts(): void {
  for (const kind of ["feature", "issue"]) clearDraft(kind);
}

export function hasDraft(kind: string): boolean {
  return readStored("session", DRAFT_PREFIX + kind) !== null;
}
