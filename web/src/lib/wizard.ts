/** The New Evaluation wizard as plain functions: what is asked at each step, what is wrong with the answers, and the exact
 * request they become. Nothing here touches the network or the page, so it can be tested on its own. */
import type { Document, JobOptions, JobOverrides, PlanRequest, ReportFormat, RunRequest, TargetSpec } from "../api/types";

export type SourceKind = "existing" | "repository" | "url" | "api" | "documents" | "local" | "mcp" | "demo";
// Read from the server's generated types, so a choice the server does not know (or no longer knows) does not compile.
// The "reliability" suite is for the command line: the wizard's six modes are the ones the specification names.
export type EvalMode = Exclude<NonNullable<JobOptions["suite"]>, "reliability">;
export type Intensity = NonNullable<JobOptions["intensity"]>;
export type RiskClass = "safe" | "controlled" | "high_impact";

export const SOURCES: { id: SourceKind; title: string; detail: string }[] = [
  { id: "existing", title: "A target you registered", detail: "Evaluate it again, or compare against an earlier run." },
  { id: "repository", title: "Repository", detail: "Read the code of an agent from a Git repository (https) to design tests from it." },
  { id: "url", title: "Web application", detail: "An agent with a web interface, tested through a real browser." },
  { id: "api", title: "API endpoint", detail: "An agent behind an HTTP, SSE, WebSocket or GraphQL endpoint." },
  { id: "documents", title: "Documents", detail: "Specifications, policies or knowledge files that describe the agent." },
  { id: "local", title: "Local project", detail: "A project folder on the server, optionally run in the sandbox." },
  { id: "mcp", title: "MCP server", detail: "A Model Context Protocol server and its tools." },
  { id: "demo", title: "Demonstration agent", detail: "A built-in simulated agent: no network, no keys. Good for trying AgentLab out." },
];

export const MODES: { id: EvalMode; title: string; detail: string }[] = [
  { id: "discovery", title: "Discovery", detail: "Learn what the agent is and can do, with a few harmless probes." },
  { id: "functional", title: "Functional", detail: "Tasks, accuracy, tools, retrieval, memory, robustness and reliability." },
  { id: "security", title: "Security", detail: "Authorised, non-destructive tests for prompt injection, leaks, tool abuse and more." },
  { id: "browser", title: "Browser", detail: "Drive the web interface: forms, navigation, accessibility, visual checks." },
  { id: "full", title: "Full", detail: "Everything that applies to this agent." },
  { id: "regression", title: "Regression", detail: "Replay an earlier run's tests and report what changed." },
];

export const INTENSITIES: { id: Intensity; title: string; detail: string }[] = [
  { id: "quick", title: "Quick", detail: "A small, fast sample. A smoke test." },
  { id: "standard", title: "Standard", detail: "A balanced suite. The default." },
  { id: "thorough", title: "Thorough", detail: "Many variants per risk. Slower and costlier." },
];

export const STEPS = [
  { id: "source", label: "Target source" },
  { id: "details", label: "Target details" },
  { id: "credentials", label: "Credentials" },
  { id: "objective", label: "Objective" },
  { id: "mode", label: "Evaluation mode" },
  { id: "models", label: "Models and limits" },
  { id: "plan", label: "Review plan" },
  { id: "execute", label: "Execute" },
] as const;

export type StepId = (typeof STEPS)[number]["id"];
export type Problems = Record<string, string>;

export interface WizardState {
  source: SourceKind | null;
  existingTargetId: string;
  // details
  name: string;
  description: string;
  version: string;
  repoUrl: string;
  repoRef: string;
  archive: Document | null;
  localPath: string;
  runCommand: string;
  commandMode: "chat" | "task";
  webUrl: string;
  inputSelector: string;
  sendSelector: string;
  messageSelector: string;
  loginUrl: string;
  apiUrl: string;
  apiMethod: "POST" | "GET" | "PUT";
  apiProtocol: "rest" | "sse" | "websocket" | "graphql";
  requestTemplate: string;
  outputPath: string;
  openapiUrl: string;
  mcpTransport: "streamable_http" | "sse" | "stdio";
  mcpUrl: string;
  mcpCommand: string;
  documents: Document[];
  demoFlawed: boolean;
  // credentials
  credentials: string[];
  authCredential: string;
  canaries: string;
  // objective and safety
  objective: string;
  requirements: string;
  production: boolean;
  disposable: boolean;
  allowHighImpact: boolean;
  authorizationNote: string;
  // mode
  mode: EvalMode;
  intensity: Intensity;
  baselineRunId: string;
  // models and limits
  judge: boolean;
  scoringProfile: string;
  secondWave: boolean;
  probe: boolean;
  repetitions: string;
  maxCostUsd: string;
  maxMinutes: string;
  maxParallel: string;
  maxTests: string;
  formats: ReportFormat[];
}

export function initialWizard(): WizardState {
  return {
    source: null,
    existingTargetId: "",
    name: "",
    description: "",
    version: "",
    repoUrl: "",
    repoRef: "",
    archive: null,
    localPath: "",
    runCommand: "",
    commandMode: "chat",
    webUrl: "",
    inputSelector: "",
    sendSelector: "",
    messageSelector: "",
    loginUrl: "",
    apiUrl: "",
    apiMethod: "POST",
    apiProtocol: "rest",
    requestTemplate: "",
    outputPath: "",
    openapiUrl: "",
    mcpTransport: "streamable_http",
    mcpUrl: "",
    mcpCommand: "",
    documents: [],
    demoFlawed: false,
    credentials: [],
    authCredential: "",
    canaries: "",
    objective: "",
    requirements: "",
    production: false,
    disposable: false,
    allowHighImpact: false,
    authorizationNote: "",
    mode: "full",
    intensity: "standard",
    baselineRunId: "",
    judge: true,
    scoringProfile: "",
    secondWave: true,
    probe: true,
    repetitions: "",
    maxCostUsd: "",
    maxMinutes: "",
    maxParallel: "",
    maxTests: "",
    formats: ["json", "md", "html"],
  };
}

// ----------------------------------------------------------------------------------------------------- small parsers
/** Split a command line the way a shell would for plain words and quotes (no expansion, no pipes). */
export function splitCommand(text: string): string[] {
  const out: string[] = [];
  let current = "";
  let quote: '"' | "'" | null = null;
  let started = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) quote = null;
      else if (ch === "\\" && quote === '"' && i + 1 < text.length && (text[i + 1] === '"' || text[i + 1] === "\\")) {
        i += 1;
        current += text[i];
      } else current += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
    } else if (/\s/.test(ch)) {
      if (started || current) out.push(current);
      current = "";
      started = false;
    } else {
      current += ch;
      started = true;
    }
  }
  if (started || current) out.push(current);
  return out;
}

export function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function isHttpUrl(text: string): boolean {
  try {
    const url = new URL(text.trim());
    return (url.protocol === "http:" || url.protocol === "https:") && Boolean(url.hostname);
  } catch {
    return false;
  }
}

export function isRepoUrl(text: string): boolean {
  const value = text.trim();
  return (value.startsWith("https://") && isHttpUrl(value)) || /^git@[\w.-]+:[\w./-]+$/.test(value);
}

function parsePositive(text: string, integer: boolean): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) return Number.NaN;
  if (integer && !Number.isInteger(value)) return Number.NaN;
  return value;
}

function parseTemplate(text: string): Record<string, unknown> | "invalid" | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    const value: unknown = JSON.parse(trimmed);
    return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : "invalid";
  } catch {
    return "invalid";
  }
}

/** Where a target runs decides what adversarial testing needs: an address on this machine is "local". */
export function isRemote(state: WizardState): boolean {
  // an API given only as an OpenAPI document is tested at the host that document came from
  const urls = [state.webUrl, state.apiUrl.trim() || state.openapiUrl, state.mcpUrl].filter(Boolean);
  if (urls.length === 0) return false;
  return urls.some((text) => {
    try {
      const host = new URL(text.trim()).hostname;
      return !(host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]" || host.endsWith(".localhost"));
    } catch {
      return true;
    }
  });
}

/** How the target will be examined, in the spec's terms. */
export function evaluationApproach(state: WizardState): "black_box" | "white_box" | "hybrid" {
  const code = state.source === "repository" || state.source === "local";
  const live = state.source === "url" || state.source === "api" || state.source === "mcp" || state.source === "demo" || state.runCommand.trim() !== "";
  if (code && live) return "hybrid";
  return code ? "white_box" : "black_box";
}

// ---------------------------------------------------------------------------------------------------------- validation
export function validateStep(step: StepId, s: WizardState): Problems {
  const p: Problems = {};
  switch (step) {
    case "source":
      if (!s.source) p.source = "Choose where the agent comes from.";
      break;
    case "details":
      if (s.source === "existing") {
        if (!s.existingTargetId) p.existingTargetId = "Choose a target.";
        break;
      }
      if (!s.name.trim()) p.name = "Give the target a name.";
      if (s.source === "repository") {
        if (!s.repoUrl.trim() && !s.archive) p.repoUrl = "Enter the repository address, or upload an archive of it.";
        else if (s.repoUrl.trim() && !isRepoUrl(s.repoUrl)) p.repoUrl = "Use an https:// address (or git@host:path for SSH).";
      }
      if (s.source === "local" && !s.localPath.trim()) p.localPath = "Enter the folder's path on the server.";
      if (s.source === "url") {
        if (!s.webUrl.trim()) p.webUrl = "Enter the address of the web application.";
        else if (!isHttpUrl(s.webUrl)) p.webUrl = "Use a full http:// or https:// address.";
        if (s.loginUrl.trim() && !isHttpUrl(s.loginUrl)) p.loginUrl = "Use a full http:// or https:// address.";
      }
      if (s.source === "api") {
        if (!s.apiUrl.trim() && !s.openapiUrl.trim()) p.apiUrl = "Enter the endpoint address, or an OpenAPI document that describes it.";
        else if (s.apiUrl.trim() && !isHttpUrl(s.apiUrl)) p.apiUrl = "Use a full http:// or https:// address.";
        if (s.openapiUrl.trim() && !isHttpUrl(s.openapiUrl)) p.openapiUrl = "Use a full http:// or https:// address.";
        if (s.apiProtocol === "websocket") p.apiProtocol = "WebSocket endpoints are not supported in this build. Use REST, server-sent events or GraphQL.";
        if (parseTemplate(s.requestTemplate) === "invalid") p.requestTemplate = "This must be a JSON object, for example {\"input\": \"{{input}}\"}.";
      }
      if (s.source === "mcp") {
        if (s.mcpTransport === "stdio") {
          if (!s.mcpCommand.trim()) p.mcpCommand = "Enter the command that starts the server.";
        } else if (!s.mcpUrl.trim()) p.mcpUrl = "Enter the server address.";
        else if (!isHttpUrl(s.mcpUrl)) p.mcpUrl = "Use a full http:// or https:// address.";
      }
      if (s.source === "documents" && s.documents.length === 0) p.documents = "Upload at least one document.";
      if ((s.source === "repository" || s.source === "local") && s.runCommand.trim() && splitCommand(s.runCommand).length === 0) {
        p.runCommand = "Enter the command to run.";
      }
      break;
    case "credentials":
      if (s.authCredential && !s.credentials.includes(s.authCredential)) p.authCredential = "Choose a credential that the target may use.";
      break;
    case "objective":
      if (s.allowHighImpact && !s.authorizationNote.trim()) p.authorizationNote = "High-impact tests need a written authorisation note.";
      if (s.allowHighImpact && s.production) p.allowHighImpact = "High-impact tests are never run against a production target.";
      break;
    case "mode":
      if (s.mode === "regression" && !s.baselineRunId) p.baselineRunId = "Choose the earlier run to compare against.";
      if (s.mode === "regression" && s.source !== "existing") p.mode = "Regression compares a target with its earlier runs: start from a registered target.";
      break;
    case "models": {
      const checks: [keyof WizardState, string, boolean][] = [
        ["maxCostUsd", "Enter an amount in US dollars above zero.", false],
        ["maxMinutes", "Enter a number of minutes above zero.", false],
        ["maxParallel", "Enter a whole number from 1 to 64.", true],
        ["maxTests", "Enter a whole number above zero.", true],
        ["repetitions", "Enter a whole number from 1 to 20.", true],
      ];
      for (const [key, message, integer] of checks) {
        const value = parsePositive(String(s[key]), integer);
        if (Number.isNaN(value)) p[key] = message;
        else if (value !== null && key === "maxParallel" && value > 64) p[key] = message;
        else if (value !== null && key === "repetitions" && value > 20) p[key] = message;
      }
      break;
    }
    default:
      break;
  }
  return p;
}

export function stepIsValid(step: StepId, s: WizardState): boolean {
  return Object.keys(validateStep(step, s)).length === 0;
}

/** The first step (by index) whose answers are not usable, or `null` when the plan can be designed. */
export function firstProblemStep(s: WizardState): number | null {
  for (const [index, step] of STEPS.entries()) {
    if (step.id === "plan") break;
    if (!stepIsValid(step.id, s)) return index;
  }
  return null;
}

// -------------------------------------------------------------------------------------------------------- the request
const DEMO_TOOLS = ["send_email", "get_weather", "calculator", "delete_file"];
const DEMO_KNOWLEDGE = {
  "leave-policy.md": "Employees receive 25 days of paid annual leave. Unused days carry over for one year.",
  "expenses.md": "Expenses above 500 USD need written approval from a manager before they are booked.",
};

export function buildTarget(s: WizardState): TargetSpec | null {
  if (s.source === null || s.source === "existing") return null;
  const spec: TargetSpec = {
    name: s.name.trim(),
    description: s.description.trim() || null,
    version: s.version.trim() || null,
    objective: s.objective.trim() || null,
    credentials: [...s.credentials],
    known_canaries: lines(s.canaries),
    documents: s.documents.map((d) => d.ref),
    safety: {
      production: s.production,
      authorized_risk_classes: s.allowHighImpact ? ["safe", "controlled", "high_impact"] : ["safe", "controlled"],
      authorization_note: s.authorizationNote.trim() || null,
      disposable_environment: s.disposable,
    },
  };
  const auth = s.authCredential || null;
  const command = splitCommand(s.runCommand);
  const run = command.length > 0 ? { mode: s.commandMode, command } : null;
  switch (s.source) {
    case "repository":
      spec.repository = {
        url: s.repoUrl.trim() || null,
        ref: s.repoRef.trim() || null,
        ...(s.archive ? { archive: s.archive.ref } : {}),
      };
      if (run) spec.command = run;
      break;
    case "local":
      spec.repository = { path: s.localPath.trim() };
      if (run) spec.command = run;
      break;
    case "url":
      spec.web = {
        url: s.webUrl.trim(),
        input_selector: s.inputSelector.trim() || null,
        send_selector: s.sendSelector.trim() || null,
        message_selector: s.messageSelector.trim() || null,
        login_url: s.loginUrl.trim() || null,
        auth_credential: auth,
      };
      break;
    case "api": {
      const template = parseTemplate(s.requestTemplate);
      spec.api = {
        ...(s.apiUrl.trim() ? { url: s.apiUrl.trim() } : {}),
        method: s.apiMethod,
        protocol: s.apiProtocol,
        openapi_url: s.openapiUrl.trim() || null,
        auth_credential: auth,
        ...(template && template !== "invalid" ? { request_template: template } : {}),
        ...(s.outputPath.trim() ? { response: { output: s.outputPath.trim() } } : {}),
      };
      break;
    }
    case "mcp":
      spec.mcp =
        s.mcpTransport === "stdio"
          ? { transport: "stdio", command: splitCommand(s.mcpCommand), auth_credential: auth }
          : { transport: s.mcpTransport, url: s.mcpUrl.trim(), auth_credential: auth };
      break;
    case "demo":
      spec.mock = {
        behaviors: s.demoFlawed ? ["hallucination", "prompt_injection", "unsafe_behavior"] : ["success"],
        tools: DEMO_TOOLS,
        knowledge: DEMO_KNOWLEDGE,
      };
      break;
    default:
      break;
  }
  return spec;
}

export function buildOptions(s: WizardState): JobOptions {
  const maxTests = parsePositive(s.maxTests, true);
  return {
    suite: s.mode,
    intensity: s.intensity,
    objective: s.objective.trim() || null,
    requirements: lines(s.requirements),
    judge: s.judge,
    scoring_profile: s.scoringProfile || null,
    second_wave: s.secondWave,
    probe: s.probe,
    max_tests: maxTests && !Number.isNaN(maxTests) ? maxTests : null,
    baseline_run_id: s.mode === "regression" ? s.baselineRunId || null : null,
  };
}

export function buildOverrides(s: WizardState): JobOverrides {
  const out: JobOverrides = {};
  const cost = parsePositive(s.maxCostUsd, false);
  const minutes = parsePositive(s.maxMinutes, false);
  const parallel = parsePositive(s.maxParallel, true);
  const repetitions = parsePositive(s.repetitions, true);
  if (cost && !Number.isNaN(cost)) out.max_cost_usd = cost;
  if (minutes && !Number.isNaN(minutes)) out.max_execution_time_seconds = Math.round(minutes * 60);
  if (parallel && !Number.isNaN(parallel)) out.max_parallel = parallel;
  if (repetitions && !Number.isNaN(repetitions)) out.repetitions = repetitions;
  out.report_formats = [...s.formats];
  return out;
}

export function planRequest(s: WizardState, project: string, waitSeconds = 60): PlanRequest {
  const target = buildTarget(s);
  return {
    project,
    ...(target ? { target } : { target_id: s.existingTargetId }),
    options: buildOptions(s),
    overrides: buildOverrides(s),
    wait_seconds: waitSeconds,
  };
}

export function runRequest(s: WizardState, project: string, planId: string, deselect: string[]): RunRequest {
  return { project, plan_id: planId, deselect, options: buildOptions(s), overrides: buildOverrides(s) };
}

/** Changes whenever an answer that shapes the plan changes, so a plan designed for old answers is never run by mistake. */
export function planSignature(s: WizardState, project: string): string {
  const { wait_seconds: _wait, ...rest } = planRequest(s, project);
  void _wait;
  return JSON.stringify(rest);
}

/** What was chosen, in words, for the last step. */
export function summarise(s: WizardState): [string, string][] {
  const source = SOURCES.find((x) => x.id === s.source)?.title ?? "none";
  const mode = MODES.find((m) => m.id === s.mode)?.title ?? s.mode;
  const rows: [string, string][] = [
    ["Target", s.source === "existing" ? `registered target ${s.existingTargetId}` : s.name.trim() || "(unnamed)"],
    ["Source", source],
    ["Approach", evaluationApproach(s).replace("_", " ")],
    ["Mode", `${mode}, ${s.intensity}`],
    ["Judge", s.judge ? "on (independent of the target)" : "off: deterministic checks only"],
    ["Credentials", s.credentials.length ? s.credentials.join(", ") : "none"],
  ];
  if (s.mode === "regression") rows.push(["Baseline run", s.baselineRunId]);
  if (s.maxCostUsd.trim()) rows.push(["Cost limit", `$${s.maxCostUsd.trim()}`]);
  if (s.maxMinutes.trim()) rows.push(["Time limit", `${s.maxMinutes.trim()} min`]);
  return rows;
}
