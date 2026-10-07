/** What the live view knows about a run, built one event at a time from the server's event stream. Pure: the same events in
 * the same order always give the same state, so it can be tested without a browser or a server. */
import type { RunEvent } from "../api/types";
import { pretty, truncate } from "./format";

export const MAX_EVENTS = 600;
const MAX_RECENT = 40;

export type PhaseStatus = "running" | "completed" | "skipped" | "failed";

export interface PhaseState {
  status: PhaseStatus;
  durationS?: number;
  note?: string;
}

export interface LiveTest {
  id: string;
  name: string;
  category: string;
  startedAt: string;
  status: string; // "running" until it completes
  score?: number | null;
  severity?: string | null;
}

export interface LiveAlert {
  at: string;
  testId: string | null;
  text: string;
}

export interface LiveState {
  /** The most recent events, oldest first. */
  events: RunEvent[];
  /** How many events have been seen, including the ones no longer kept. */
  seen: number;
  phases: Record<string, PhaseState>;
  tests: Record<string, LiveTest>;
  running: string[];
  tools: { at: string; testId: string | null; tool: string; args: unknown; status?: string }[];
  browser: { at: string; testId: string | null; text: string }[];
  alerts: LiveAlert[];
  findings: { at: string; testId: string | null; title: string; severity: string }[];
  limits: string[];
  lastRequest: { testId: string | null; text: string } | null;
  lastResponse: { testId: string | null; text: string; latencyMs: number | null; error: string | null } | null;
  terminal: string | null;
}

export function initialLive(): LiveState {
  return {
    events: [],
    seen: 0,
    phases: {},
    tests: {},
    running: [],
    tools: [],
    browser: [],
    alerts: [],
    findings: [],
    limits: [],
    lastRequest: null,
    lastResponse: null,
    terminal: null,
  };
}

const str = (value: unknown): string => (typeof value === "string" ? value : value === undefined || value === null ? "" : pretty(value));
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

function recent<T>(list: T[], item: T, limit = MAX_RECENT): T[] {
  return [...list, item].slice(-limit);
}

export function applyEvent(state: LiveState, event: RunEvent): LiveState {
  const p = event.payload as Record<string, unknown>;
  const next: LiveState = { ...state, events: [...state.events, event].slice(-MAX_EVENTS), seen: state.seen + 1 };
  const testId = event.test_id;
  switch (event.type) {
    case "PhaseStarted":
      next.phases = { ...state.phases, [str(p.phase)]: { status: "running" } };
      break;
    case "PhaseCompleted": {
      const status = str(p.status);
      next.phases = {
        ...state.phases,
        [str(p.phase)]: {
          status: status === "skipped" ? "skipped" : status === "failed" ? "failed" : "completed",
          durationS: num(p.duration_s) ?? undefined,
          note: str(p.note) || undefined,
        },
      };
      break;
    }
    case "TestStarted":
      if (testId) {
        next.tests = {
          ...state.tests,
          [testId]: { id: testId, name: str(p.name) || testId, category: str(p.category), startedAt: event.timestamp, status: "running" },
        };
        next.running = state.running.includes(testId) ? state.running : [...state.running, testId];
      }
      break;
    case "TestCompleted":
      if (testId) {
        const before = state.tests[testId];
        next.tests = {
          ...state.tests,
          [testId]: {
            id: testId,
            name: before?.name ?? testId,
            category: before?.category ?? "",
            startedAt: before?.startedAt ?? event.timestamp,
            status: str(p.status) || "completed",
            score: num(p.score),
            severity: typeof p.severity === "string" ? p.severity : null,
          },
        };
        next.running = state.running.filter((id) => id !== testId);
      }
      break;
    case "AgentRequest":
      next.lastRequest = { testId, text: str(p.input) };
      break;
    case "AgentResponse":
      next.lastResponse = { testId, text: str(p.output), latencyMs: num(p.latency_ms), error: str(p.error) || null };
      break;
    case "ToolCalled":
      next.tools = recent(state.tools, { at: event.timestamp, testId, tool: str(p.tool), args: p.arguments_redacted });
      break;
    case "ToolReturned":
      next.tools = state.tools.map((t, i) => (i === state.tools.length - 1 && t.tool === str(p.tool) ? { ...t, status: str(p.status) } : t));
      break;
    case "BrowserAction":
      next.browser = recent(state.browser, { at: event.timestamp, testId, text: describeBrowser(p) });
      break;
    case "SecurityAlert":
      next.alerts = recent(state.alerts, { at: event.timestamp, testId, text: alertText(p) }, 100);
      break;
    case "FindingCreated":
      next.findings = recent(state.findings, { at: event.timestamp, testId, title: str(p.title), severity: str(p.severity) }, 100);
      break;
    case "LimitReached":
      next.limits = recent(state.limits, str(p.reason) || str(p.message) || str(p.status), 10);
      break;
    case "RunCompleted":
    case "RunFailed":
    case "RunCancelled":
      next.terminal = event.type;
      next.running = [];
      break;
    default:
      break;
  }
  return next;
}

/** An alert comes from one of two places: a failed check on a trace (assertion, message) or a security finding (severity, title). */
export function alertText(p: Record<string, unknown>): string {
  const assertion = str(p.assertion);
  const message = str(p.message);
  if (assertion || message) return assertion && message ? `${assertion}: ${message}` : assertion || message;
  const title = str(p.title);
  const severity = str(p.severity);
  if (title) return severity ? `${severity} finding: ${title}` : title;
  return "A security alert was raised";
}

function describeBrowser(p: Record<string, unknown>): string {
  const action = str(p.agent_event) || str(p.action) || str(p.observer) || "action";
  const target = str(p.url) || str(p.selector) || str(p.target) || "";
  return target ? `${action} ${truncate(target, 140)}` : action;
}

/** One line for the event feed. */
export function describeEvent(event: RunEvent): string {
  const p = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "RunStarted":
      return `Run started${p.target ? ` for ${str(p.target)}` : ""}`;
    case "PhaseStarted":
      return `Phase ${str(p.index)}/${str(p.total)} started: ${str(p.phase).replace(/_/g, " ")}`;
    case "PhaseCompleted":
      return `Phase ${str(p.phase).replace(/_/g, " ")} ${str(p.status)}${p.note ? ` (${truncate(str(p.note), 100)})` : ""}`;
    case "TestStarted":
      return `${str(p.name)} · ${str(p.category)}`;
    case "TestCompleted":
      return `${str(p.status)}${num(p.score) !== null ? ` · score ${num(p.score)}` : ""}${p.reason ? ` · ${truncate(str(p.reason), 100)}` : ""}`;
    case "AgentRequest":
      return `→ ${truncate(str(p.input), 200)}`;
    case "AgentResponse":
      return `← ${truncate(str(p.output) || str(p.error), 200)}`;
    case "ToolCalled":
      return `${str(p.tool)}(${truncate(pretty(p.arguments_redacted ?? {}), 120)})`;
    case "ToolReturned":
      return `${str(p.tool)} returned ${str(p.status)}`;
    case "SecurityAlert":
      return truncate(alertText(p), 200);
    case "FindingCreated":
      return `${str(p.severity)}: ${truncate(str(p.title), 160)}`;
    case "LimitReached":
      return truncate(str(p.reason) || str(p.message) || str(p.status), 160);
    case "BrowserAction":
      return describeBrowser(p);
    default:
      return truncate(pretty(p), 200);
  }
}

export const ALERT_TYPES = new Set(["SecurityAlert", "LimitReached", "Error", "RunFailed"]);
