import { describe, expect, it } from "vitest";
import { finding, result } from "../test/factories";
import { distinctValues, filterFindings, filterResults, NO_FINDING_FILTERS, NO_RESULT_FILTERS, outcomeLine, severityCounts, severityRank, sortFindings, statusCounts } from "./results";

const results = [
  result({ test_id: "A-1", test_name: "Alpha", status: "failed", category: "security", severity: "high" }),
  result({ test_id: "B-1", test_name: "Beta", status: "passed", category: "functional" }),
  result({ test_id: "C-1", test_name: "Gamma", status: "blocked", category: "functional", blocked_reason: "No judge is configured" }),
];

describe("results", () => {
  it("filters by status, category, severity and text", () => {
    const ids = (f: Partial<typeof NO_RESULT_FILTERS>) => filterResults(results, { ...NO_RESULT_FILTERS, ...f }).map((r) => r.test_id);
    expect(ids({})).toEqual(["A-1", "B-1", "C-1"]);
    expect(ids({ status: "failed" })).toEqual(["A-1"]);
    expect(ids({ category: "functional" })).toEqual(["B-1", "C-1"]);
    expect(ids({ severity: "high" })).toEqual(["A-1"]);
    expect(ids({ q: "GAMMA" })).toEqual(["C-1"]);
    expect(ids({ q: "b-1" })).toEqual(["B-1"]);
  });

  it("counts by status and lists the categories there are", () => {
    expect(statusCounts(results)).toEqual({ failed: 1, passed: 1, blocked: 1 });
    expect(distinctValues(results, (r) => r.category)).toEqual(["functional", "security"]);
  });

  it("explains each outcome in one line", () => {
    expect(outcomeLine(results[2])).toBe("No judge is configured");
    expect(outcomeLine(result({ status: "blocked", blocked_reason: null }))).toMatch(/^Blocked/);
    expect(outcomeLine(result({ status: "skipped" }))).toMatch(/^Skipped/);
    expect(outcomeLine(result({ status: "passed" }))).toBe("All checks passed.");
    const failed = result({
      status: "failed",
      attempts: [{ assertions: [{ passed: true, message: "fine" }, { passed: false, evaluator_error: true, message: "judge broke" }, { passed: false, message: "output does not match" }] }],
    });
    expect(outcomeLine(failed)).toBe("output does not match");
    expect(outcomeLine(result({ status: "error", attempts: [{ assertions: [], error: "connection refused" }] }))).toBe("connection refused");
    expect(outcomeLine(result({ status: "failed", attempts: [] }))).toBe("The test did not meet its expectations.");
  });
});

describe("findings", () => {
  const findings = [
    finding({ id: "1", title: "Medium quality", severity: "medium", is_security: false }),
    finding({ id: "2", title: "Critical leak", severity: "critical", is_security: true, test_id: "EXFIL-1", category: "security" }),
    finding({ id: "3", title: "High quality", severity: "high", is_security: false }),
    finding({ id: "4", title: "High leak", severity: "high", is_security: true, category: "security" }),
    finding({ id: "5", title: "Rejected", severity: "critical", status: "false_positive", is_security: true }),
  ];
  const ids = (list: ReturnType<typeof sortFindings>) => list.map((f) => f.id);

  it("ranks severities, with unknown ones last", () => {
    expect(["critical", "high", "medium", "low", "info", "bogus", null].map(severityRank)).toEqual([4, 3, 2, 1, 0, -1, -1]);
  });

  it("puts the most serious first, security before quality, then by title", () => {
    expect(ids(sortFindings(findings))).toEqual(["2", "5", "4", "3", "1"]);
  });

  it("does not show what a reviewer rejected unless asked", () => {
    expect(ids(filterFindings(findings, NO_FINDING_FILTERS))).toEqual(["2", "4", "3", "1"]);
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, showRejected: true }))).toEqual(["2", "5", "4", "3", "1"]);
  });

  it("filters by severity, category, kind and text", () => {
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, severity: "high" }))).toEqual(["4", "3"]);
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, kind: "security" }))).toEqual(["2", "4"]);
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, kind: "quality" }))).toEqual(["3", "1"]);
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, category: "security" }))).toEqual(["2", "4"]);
    expect(ids(filterFindings(findings, { ...NO_FINDING_FILTERS, q: "exfil" }))).toEqual(["2"]);
  });

  it("does not change the list it is given", () => {
    const before = ids(findings);
    sortFindings(findings);
    expect(ids(findings)).toEqual(before);
  });

  it("counts the severities of what still stands", () => {
    expect(severityCounts(findings)).toEqual({ critical: 1, high: 2, medium: 1, low: 0, info: 0 });
  });
});
