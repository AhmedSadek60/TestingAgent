/** Reading a test plan: what is selected, what it would cost, how the tests group. Pure, so the plan screen stays thin. */
import type { PlannedTest, TestPlan } from "../api/types";

export interface PlanTotals {
  selected: number;
  runnable: number;
  blocked: number;
  attempts: number;
  targetCalls: number;
  judgeCalls: number;
  tokens: number;
  serialSeconds: number;
}

/** The ids the person has switched off, on top of what the server deselected itself. */
export type Deselected = ReadonlySet<string>;

export function isSelected(item: PlannedTest, deselected: Deselected): boolean {
  return item.selected && !deselected.has(item.test.id);
}

/** What will happen to a test: the planner's choice first, then the person's. */
export type TestState = "run" | "blocked" | "left-out" | "switched-off";

export function testState(item: PlannedTest, deselected: Deselected): TestState {
  if (!item.selected) return "left-out";
  if (deselected.has(item.test.id)) return "switched-off";
  return item.predicted === "blocked" ? "blocked" : "run";
}

export function planTotals(plan: TestPlan, deselected: Deselected): PlanTotals {
  const totals: PlanTotals = { selected: 0, runnable: 0, blocked: 0, attempts: 0, targetCalls: 0, judgeCalls: 0, tokens: 0, serialSeconds: 0 };
  for (const item of plan.tests) {
    if (!isSelected(item, deselected)) continue;
    totals.selected += 1;
    if (item.predicted === "blocked") totals.blocked += 1;
    else {
      totals.runnable += 1;
      totals.attempts += item.est_attempts;
      totals.targetCalls += item.est_calls;
      totals.judgeCalls += item.est_judge_calls;
      totals.tokens += item.est_tokens;
      totals.serialSeconds += item.est_seconds;
    }
  }
  return totals;
}

export interface PlanFilters {
  q: string;
  category: string;
  skill: string;
  risk: string;
  /** One of `TestState`, or empty for every test. */
  state: string;
}

export const NO_PLAN_FILTERS: PlanFilters = { q: "", category: "", skill: "", risk: "", state: "" };

export function filterPlan(plan: TestPlan, filters: PlanFilters, deselected: Deselected = new Set()): PlannedTest[] {
  const needle = filters.q.trim().toLowerCase();
  return plan.tests.filter(
    (item) =>
      (!filters.category || item.test.category === filters.category) &&
      (!filters.skill || item.skill === filters.skill) &&
      (!filters.risk || item.risk === filters.risk) &&
      (!filters.state || testState(item, deselected) === filters.state) &&
      (!needle || item.test.id.toLowerCase().includes(needle) || item.test.name.toLowerCase().includes(needle) || item.test.objective.toLowerCase().includes(needle)),
  );
}

export function distinct<T>(items: T[], pick: (item: T) => string): string[] {
  return [...new Set(items.map(pick))].filter(Boolean).sort((a, b) => a.localeCompare(b));
}

/** Tests in the plan by category, for the summary bar. */
export function byCategory(plan: TestPlan): { category: string; total: number; blocked: number }[] {
  const map = new Map<string, { category: string; total: number; blocked: number }>();
  for (const item of plan.tests) {
    const row = map.get(item.test.category) ?? { category: item.test.category, total: 0, blocked: 0 };
    row.total += 1;
    if (item.predicted === "blocked") row.blocked += 1;
    map.set(item.test.category, row);
  }
  return [...map.values()].sort((a, b) => b.total - a.total || a.category.localeCompare(b.category));
}

export function blockers(plan: TestPlan): string[] {
  return plan.warnings.filter((w) => w.level === "blocker").map((w) => w.message);
}
