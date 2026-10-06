/** The API's types, generated from its OpenAPI document (`npm run gen:api`) and made convenient to read.
 *
 * The server always sends the fields that have defaults, but OpenAPI cannot say so for them, so the generated types mark
 * them optional. `Resp` turns a response type into what actually arrives: every field present, `null` kept where it is
 * allowed. Request bodies use the generated types as they are. */
import type { components } from "./schema";

type S = components["schemas"];

export type Resp<T> = T extends (infer U)[]
  ? Resp<U>[]
  : T extends Record<string, unknown>
    ? { [K in keyof T]-?: Resp<Exclude<T[K], undefined>> }
    : T;

// -------------------------------------------------------------------------------------------------------- requests
export type RunRequest = S["RunRequest"];
export type PlanRequest = S["PlanRequest"];
export type DiscoverRequest = S["DiscoverRequest"];
export type TargetSpec = S["TargetSpec"];
export type JobOptions = S["JobOptions"];
export type JobOverrides = S["JobOverrides"];
export type CredentialCreate = S["CredentialCreate"];
export type ReviewRequest = S["ReviewRequest"];
export type ReportCreate = S["ReportCreate"];

// ------------------------------------------------------------------------------------------------------- responses
export type Health = Resp<S["Health"]>;
export type Environment = Resp<S["EnvironmentOut"]>;
export type EnvironmentCheck = Resp<S["EnvironmentCheck"]>;
export type Settings = Resp<S["SettingsOut"]>;
export type Project = Resp<S["ProjectOut"]>;
export type Target = Resp<S["TargetOut"]>;
export type AgentProfile = Resp<S["AgentProfile"]>;
export type Discovery = Resp<S["DiscoverResponse"]>;
export type Document = Resp<S["DocumentOut"]>;
export type RunAccepted = Resp<S["RunAccepted"]>;
export type RunSummary = Resp<S["RunSummary"]>;
export type RunDetail = Resp<S["RunDetail"]>;
export type RunProgress = Resp<S["RunProgress"]>;
export type CancelResponse = Resp<S["CancelResponse"]>;
export type PlanOut = Resp<S["PlanOut"]>;
export type TestPlan = Resp<S["TestPlan"]>;
export type PlannedTest = Resp<S["PlannedTest"]>;
export type TestCase = Resp<S["TestCase"]>;
export type SkillMatch = Resp<S["SkillMatch"]>;
export type CoverageEntry = Resp<S["CoverageEntry"]>;
export type TestResult = Resp<S["TestResult"]>;
export type AttemptResult = Resp<S["AttemptResult"]>;
export type Finding = Resp<S["Finding"]>;
export type Scorecard = Resp<S["Scorecard"]>;
export type TraceSummary = Resp<S["TraceSummary"]>;
export type Trace = Resp<S["TraceOut"]>;
export type RunEvent = Resp<S["EventOut"]>;
export type Review = Resp<S["ReviewOut"]>;
export type Report = Resp<S["ReportOut"]>;
export type ReportFile = Resp<S["ReportFileOut"]>;
export type Comparison = Resp<S["Comparison"]>;
export type TestDelta = Resp<S["TestDelta"]>;
export type Artifact = Resp<S["ArtifactOut"]>;
export type Provider = Resp<S["ProviderOut"]>;
export type ProviderCheck = Resp<S["ProviderCheck"]>;
export type Model = Resp<S["ModelOut"]>;
export type SkillSummary = Resp<S["SkillSummary"]>;
export type SkillDetail = Resp<S["SkillDetail"]>;
export type Credential = Resp<S["CredentialOut"]>;
export type ScoringProfile = Resp<S["ScoringProfileOut"]>;
export type ViewLink = Resp<S["ViewLink"]>;
export type PlanWarning = Resp<S["PlanWarning"]>;
export type CategoryScore = Resp<S["CategoryScore"]>;
export type AssertionResult = Resp<S["AssertionResult"]>;
export type JudgeResult = Resp<S["JudgeResult"]>;
export type CategoryDelta = Resp<S["CategoryDelta"]>;
export type MetricDelta = Resp<S["MetricDelta"]>;
export type FindingDelta = Resp<S["FindingDelta"]>;
export type CredentialKind = NonNullable<CredentialCreate["kind"]>;
export type ReviewDecision = ReviewRequest["decision"];
export type ReportFormat = ReportFile["format"];

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

/** Run states in which nothing more will happen. */
export const TERMINAL_STATUSES = [
  "completed",
  "failed",
  "cancelled",
  "stopped_due_to_cost",
  "stopped_due_to_timeout",
  "stopped_due_to_step_limit",
] as const;

export function isTerminal(status: string): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(status);
}
