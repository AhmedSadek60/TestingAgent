/** Minimal stand-ins for the API's objects. A test names only the fields it is about; the rest are plausible defaults. */
import type { Finding, PlannedTest, RunDetail, RunEvent, RunSummary, TestPlan, TestResult } from "../api/types";

type Overrides<T> = Partial<{ [K in keyof T]: unknown }>;

export function result(overrides: Overrides<TestResult> = {}): TestResult {
  return {
    id: "r1",
    test_id: "T-1",
    test_name: "A test",
    category: "functional",
    status: "passed",
    severity: null,
    score: 1,
    attempts: [],
    blocked_reason: null,
    ...overrides,
  } as unknown as TestResult;
}

export function finding(overrides: Overrides<Finding> = {}): Finding {
  return {
    id: "f1",
    test_id: "T-1",
    title: "A finding",
    category: "quality",
    severity: "medium",
    status: "open",
    is_security: false,
    observed: "what was seen",
    ...overrides,
  } as unknown as Finding;
}

export function planned(id: string, overrides: Overrides<PlannedTest> & { test?: Record<string, unknown> } = {}): PlannedTest {
  const { test, ...rest } = overrides;
  return {
    test: { id, name: `Test ${id}`, objective: `Check ${id}`, category: "functional", severity_on_failure: "medium", ...test },
    skill: "conversational-agent-testing",
    risk: "safe",
    selected: true,
    predicted: "runnable",
    est_attempts: 1,
    est_calls: 2,
    est_judge_calls: 0,
    est_tokens: 100,
    est_seconds: 3,
    deselected_reason: null,
    blocked_reason: null,
    ...rest,
  } as unknown as PlannedTest;
}

export function plan(tests: PlannedTest[], overrides: Overrides<TestPlan> = {}): TestPlan {
  return { id: "plan-1", tests, warnings: [], skills: [], ...overrides } as unknown as TestPlan;
}

export function event(type: string, payload: Record<string, unknown> = {}, overrides: Overrides<RunEvent> = {}): RunEvent {
  return {
    event_id: `e-${Math.random().toString(36).slice(2)}`,
    type,
    timestamp: "2026-10-06T10:00:00Z",
    payload,
    test_id: null,
    ...overrides,
  } as unknown as RunEvent;
}

export function run(overrides: Overrides<RunDetail> = {}): RunDetail {
  return {
    id: "11111111-2222-3333-4444-555555555555",
    project_id: "p1",
    kind: "run",
    suite: "full",
    status: "completed",
    created_at: "2026-10-06T10:00:00Z",
    started_at: "2026-10-06T10:00:01Z",
    finished_at: "2026-10-06T10:00:11Z",
    target: "Demo",
    overall: 84.2,
    grade: "B",
    manifest: {},
    limits: {},
    ...overrides,
  } as unknown as RunDetail;
}

export function summary(overrides: Overrides<RunSummary> = {}): RunSummary {
  return run(overrides as Overrides<RunDetail>) as unknown as RunSummary;
}
