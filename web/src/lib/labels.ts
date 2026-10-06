/** How the interface names and colours the API's vocabulary. One place, so a status looks the same on every screen. */
import { titleCase } from "./format";

export type Tone = "good" | "bad" | "warn" | "info" | "neutral" | "critical";

const STATUS_TONE: Record<string, Tone> = {
  passed: "good",
  completed: "good",
  ok: "good",
  covered: "good",
  supported: "good",
  comparable: "good",
  improved: "good",
  resolved: "good",
  approve: "good",
  failed: "bad",
  error: "bad",
  fail: "bad",
  regressed: "bad",
  new_failure: "bad",
  timeout: "bad",
  not_covered: "bad",
  unsupported: "bad",
  blocked: "warn",
  warn: "warn",
  warning: "warn",
  partial: "warn",
  mixed: "warn",
  unstable: "warn",
  cancelled: "warn",
  cancelling: "warn",
  stopped_due_to_cost: "warn",
  stopped_due_to_timeout: "warn",
  stopped_due_to_step_limit: "warn",
  comparable_with_caveats: "warn",
  not_comparable: "bad",
  inconclusive: "warn",
  running: "info",
  pending: "neutral",
  skipped: "neutral",
  info: "info",
  draft: "neutral",
  ready: "info",
  not_applicable: "neutral",
  unchanged: "neutral",
  blocker: "bad",
};

const SEVERITY_TONE: Record<string, Tone> = { critical: "critical", high: "bad", medium: "warn", low: "info", info: "neutral" };

export function statusTone(status: string): Tone {
  return STATUS_TONE[status] ?? "neutral";
}

export function severityTone(severity: string): Tone {
  return SEVERITY_TONE[severity] ?? "neutral";
}

const STATUS_LABEL: Record<string, string> = {
  stopped_due_to_cost: "Stopped: cost limit",
  stopped_due_to_timeout: "Stopped: time limit",
  stopped_due_to_step_limit: "Stopped: step limit",
  not_covered: "Not covered",
  not_applicable: "Not applicable",
  comparable_with_caveats: "Comparable, with caveats",
  not_comparable: "Not comparable",
  new_failure: "New failure",
  still_failing: "Still failing",
  lost_coverage: "Lost coverage",
  gained_coverage: "Gained coverage",
  new_test_failed: "New test failed",
  new_test_passed: "New test passed",
  new_test_not_run: "New test not run",
  definition_changed: "Definition changed",
  false_positive: "False positive",
  false_negative: "False negative",
  override_score: "Override score",
  change_severity: "Change severity",
};

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? titleCase(status);
}

/** The orchestrator's seventeen phases, in order (spec section 9). */
export const PHASES: { id: string; label: string; detail: string }[] = [
  { id: "input_validation", label: "Input validation", detail: "Check the request and the safety settings" },
  { id: "target_ingestion", label: "Target ingestion", detail: "Read the repository, documents and endpoints" },
  { id: "target_fingerprinting", label: "Fingerprinting", detail: "Work out what kind of agent this is" },
  { id: "environment_preparation", label: "Environment", detail: "Prepare the sandbox, providers and judge" },
  { id: "skill_selection", label: "Skill selection", detail: "Choose the test skills that apply" },
  { id: "test_plan_generation", label: "Plan generation", detail: "Design the explainable test plan" },
  { id: "risk_classification", label: "Risk classification", detail: "Classify and gate each test" },
  { id: "test_execution", label: "Test execution", detail: "Run the tests against the target" },
  { id: "trace_collection", label: "Trace collection", detail: "Store the evidence of every attempt" },
  { id: "deterministic_evaluation", label: "Deterministic checks", detail: "Evaluate assertions on the traces" },
  { id: "llm_judge_evaluation", label: "Judge evaluation", detail: "Evaluate quality with the independent judge" },
  { id: "cross_test_analysis", label: "Cross-test analysis", detail: "Find patterns across tests" },
  { id: "security_analysis", label: "Security analysis", detail: "Assess the security posture" },
  { id: "reliability_analysis", label: "Reliability analysis", detail: "Assess stability across repetitions" },
  { id: "scoring", label: "Scoring", detail: "Score by category with the chosen profile" },
  { id: "report_generation", label: "Report generation", detail: "Build the report bundle" },
  { id: "artifact_packaging", label: "Packaging", detail: "Package and checksum the artifacts" },
];

export const ROOT_CAUSE_LABEL: Record<string, string> = {
  prompt_problem: "Prompt problem",
  model_limitation: "Model limitation",
  tool_selection_problem: "Tool selection",
  tool_implementation_problem: "Tool implementation",
  retrieval_problem: "Retrieval",
  data_problem: "Data",
  memory_problem: "Memory",
  authorization_problem: "Authorization",
  browser_interaction_problem: "Browser interaction",
  ui_problem: "UI",
  api_problem: "API",
  orchestration_problem: "Orchestration",
  evaluator_uncertainty: "Evaluator uncertainty",
  infrastructure_problem: "Infrastructure",
  timeout: "Timeout",
  external_dependency: "External dependency",
  security_vulnerability: "Security vulnerability",
  unknown: "Unknown",
};

export function rootCauseLabel(cause: string | null | undefined): string {
  return cause ? (ROOT_CAUSE_LABEL[cause] ?? titleCase(cause)) : "n/a";
}

/**
 * The server writes a grade as the letter plus what limits it: `F (capped by security; partial coverage)`.
 * Show the letter on its own and keep the reasons for the places that have room for them.
 */
export function splitGrade(grade: string | null | undefined): { letter: string; notes: string[] } {
  const text = (grade ?? "").trim();
  if (!text) return { letter: "", notes: [] };
  const match = /^(\S+?)\s*\((.*)\)\s*$/.exec(text);
  if (!match) return { letter: text, notes: [] };
  return { letter: match[1] ?? text, notes: (match[2] ?? "").split(";").map((n) => n.trim()).filter(Boolean) };
}

export function gradeTone(grade: string | null | undefined): Tone {
  if (!grade) return "neutral";
  const letter = grade.trim().charAt(0).toUpperCase();
  if (letter === "A" || letter === "B") return "good";
  if (letter === "C") return "warn";
  return "bad";
}

export function scoreTone(score: number | null | undefined): Tone {
  if (score === null || score === undefined) return "neutral";
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}
