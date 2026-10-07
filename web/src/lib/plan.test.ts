import { describe, expect, it } from "vitest";
import { plan, planned } from "../test/factories";
import { blockers, byCategory, distinct, filterPlan, isSelected, NO_PLAN_FILTERS, planTotals, testState } from "./plan";

const tests = [
  planned("A", { skill: "authorization-testing", risk: "controlled", test: { category: "security" } }),
  planned("B", { test: { category: "functional", name: "Greets the user" } }),
  planned("C", { predicted: "blocked", blocked_reason: "needs a judge", test: { category: "functional" } }),
  planned("D", { selected: false, deselected_reason: "trimmed to fit the plan budget", test: { category: "security" } }),
];

describe("what will happen to each test", () => {
  it("puts the planner's choice before the person's", () => {
    expect(testState(tests[0], new Set())).toBe("run");
    expect(testState(tests[2], new Set())).toBe("blocked");
    expect(testState(tests[3], new Set())).toBe("left-out");
    expect(testState(tests[3], new Set(["D"]))).toBe("left-out");
    expect(testState(tests[0], new Set(["A"]))).toBe("switched-off");
  });

  it("counts a test as selected only when neither the planner nor the person left it out", () => {
    expect(isSelected(tests[0], new Set())).toBe(true);
    expect(isSelected(tests[0], new Set(["A"]))).toBe(false);
    expect(isSelected(tests[3], new Set())).toBe(false);
  });
});

describe("the plan's totals", () => {
  it("counts runnable and blocked tests separately and adds up only what will run", () => {
    expect(planTotals(plan(tests), new Set())).toEqual({
      selected: 3,
      runnable: 2,
      blocked: 1,
      attempts: 2,
      targetCalls: 4,
      judgeCalls: 0,
      tokens: 200,
      serialSeconds: 6,
    });
  });

  it("drops a test the person switched off", () => {
    const totals = planTotals(plan(tests), new Set(["A", "C"]));
    expect(totals).toMatchObject({ selected: 1, runnable: 1, blocked: 0, tokens: 100 });
  });
});

describe("finding tests in the plan", () => {
  const p = plan(tests);
  const ids = (found: ReturnType<typeof filterPlan>) => found.map((t) => t.test.id);

  it("shows everything when nothing is filtered", () => {
    expect(ids(filterPlan(p, NO_PLAN_FILTERS))).toEqual(["A", "B", "C", "D"]);
  });

  it("filters by category, skill, risk and state", () => {
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, category: "security" }))).toEqual(["A", "D"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, skill: "authorization-testing" }))).toEqual(["A"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, risk: "controlled" }))).toEqual(["A"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, state: "run" }))).toEqual(["A", "B"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, state: "blocked" }))).toEqual(["C"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, state: "left-out" }))).toEqual(["D"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, state: "switched-off" }, new Set(["B"])))).toEqual(["B"]);
  });

  it("searches names, ids and objectives without regard to case", () => {
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, q: "GREETS" }))).toEqual(["B"]);
    expect(ids(filterPlan(p, { ...NO_PLAN_FILTERS, q: "check c" }))).toEqual(["C"]);
  });

  it("lists the values a filter can take, once and in order", () => {
    expect(distinct(tests, (t) => t.test.category)).toEqual(["functional", "security"]);
  });

  it("groups tests by category, largest first, with how many will be blocked", () => {
    expect(byCategory(p)).toEqual([
      { category: "functional", total: 2, blocked: 1 },
      { category: "security", total: 2, blocked: 0 },
    ]);
  });
});

describe("what stops a plan from being run", () => {
  it("lists only the warnings that are blockers", () => {
    const p = plan(tests, { warnings: [{ level: "warning", message: "trimmed" }, { level: "blocker", message: "no credential" }] });
    expect(blockers(p)).toEqual(["no credential"]);
  });
});
