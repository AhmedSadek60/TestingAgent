import { describe, expect, it } from "vitest";
import { gradeTone, PHASES, rootCauseLabel, scoreTone, severityTone, splitGrade, statusLabel, statusTone } from "./labels";

describe("grades", () => {
  it("separates the letter from what limits it", () => {
    expect(splitGrade("A")).toEqual({ letter: "A", notes: [] });
    expect(splitGrade("F (capped by security; 1 high-severity failure; partial coverage)")).toEqual({
      letter: "F",
      notes: ["capped by security", "1 high-severity failure", "partial coverage"],
    });
    expect(splitGrade("A (only 1 of 7 planned tests could run)")).toEqual({ letter: "A", notes: ["only 1 of 7 planned tests could run"] });
    expect(splitGrade("B+")).toEqual({ letter: "B+", notes: [] });
    expect(splitGrade(null)).toEqual({ letter: "", notes: [] });
    expect(splitGrade("  ")).toEqual({ letter: "", notes: [] });
  });

  it("colours by the letter, whatever follows it", () => {
    expect(gradeTone("A")).toBe("good");
    expect(gradeTone("B (partial coverage)")).toBe("good");
    expect(gradeTone("C")).toBe("warn");
    expect(gradeTone("F (capped by security)")).toBe("bad");
    expect(gradeTone(null)).toBe("neutral");
  });

  it("colours scores in three bands and says nothing for a missing one", () => {
    expect(scoreTone(95)).toBe("good");
    expect(scoreTone(80)).toBe("good");
    expect(scoreTone(79.9)).toBe("warn");
    expect(scoreTone(59)).toBe("bad");
    expect(scoreTone(null)).toBe("neutral");
  });
});

describe("vocabulary", () => {
  it("gives a blocked test a warning colour, never the failure colour", () => {
    expect(statusTone("blocked")).toBe("warn");
    expect(statusTone("failed")).toBe("bad");
    expect(statusTone("passed")).toBe("good");
    expect(statusTone("something new")).toBe("neutral");
  });

  it("knows the severities", () => {
    expect(severityTone("critical")).toBe("critical");
    expect(severityTone("high")).toBe("bad");
    expect(severityTone("nonsense")).toBe("neutral");
  });

  it("writes statuses for people", () => {
    expect(statusLabel("stopped_due_to_cost")).toBe("Stopped: cost limit");
    expect(statusLabel("not_comparable")).toBe("Not comparable");
    expect(statusLabel("new_failure")).toBe("New failure");
    expect(statusLabel("passed")).toBe("Passed");
  });

  it("names root causes, with a fallback for a new one", () => {
    expect(rootCauseLabel("security_vulnerability")).toBe("Security vulnerability");
    expect(rootCauseLabel("brand_new_cause")).toBe("Brand new cause");
    expect(rootCauseLabel(null)).toBe("n/a");
  });

  it("lists the seventeen phases of a run once each", () => {
    expect(PHASES).toHaveLength(17);
    expect(new Set(PHASES.map((p) => p.id)).size).toBe(17);
  });
});
