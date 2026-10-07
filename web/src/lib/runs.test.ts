import { describe, expect, it } from "vitest";
import { run } from "../test/factories";
import { effectiveLimits, errorMessage, isActive, runPath, runSeconds, runTitle, shortId } from "./runs";

describe("runs", () => {
  it("knows which states are still going", () => {
    for (const s of ["pending", "running", "cancelling"]) expect(isActive(s)).toBe(true);
    for (const s of ["completed", "failed", "cancelled", "stopped_due_to_cost"]) expect(isActive(s)).toBe(false);
  });

  it("shortens an id for display", () => {
    expect(shortId("48176066-c68d-49db-b6a2-8c508f3b3b8b")).toBe("48176066");
    expect(shortId("abc")).toBe("abc");
  });

  it("measures a run from when it started, or was queued, to when it finished or now", () => {
    expect(runSeconds(run())).toBe(10);
    const now = new Date("2026-10-06T10:01:00Z");
    expect(runSeconds(run({ finished_at: null }), now)).toBe(59);
    expect(runSeconds(run({ started_at: null, finished_at: null }), now)).toBe(60);
  });

  it("reads the error a run ended with, whatever its shape", () => {
    expect(errorMessage({ message: "boom", kind: "target_error" })).toBe("boom (target_error)");
    expect(errorMessage({ message: "boom" })).toBe("boom");
    expect(errorMessage({ code: 5 })).toBe('{"code":5}');
    expect(errorMessage(null)).toBeNull();
  });

  it("uses the limits the run was started with, not the server's current defaults", () => {
    const limits = effectiveLimits(
      run({ manifest: { config: { limits: { max_cost_usd: 0.5, max_execution_time_seconds: 90, max_tokens: 1000 } } }, limits: { max_cost_usd: 10 } }),
    );
    expect(limits).toEqual({ costUsd: 0.5, seconds: 90, tokens: 1000 });
    expect(effectiveLimits(run({ manifest: {}, limits: { max_cost_usd: 10, max_execution_time_seconds: 3600, max_tokens: 500000 } }))).toEqual({
      costUsd: 10,
      seconds: 3600,
      tokens: 500000,
    });
    expect(effectiveLimits(run({ manifest: {}, limits: {} }))).toEqual({ costUsd: null, seconds: null, tokens: null });
  });

  it("titles and opens runs and plans", () => {
    expect(runTitle({ id: "1", target: "Demo", suite: "full", kind: "run" })).toBe("Demo · full");
    expect(runTitle({ id: "1", target: null, suite: "full", kind: "plan" })).toBe("Unnamed target · plan");
    expect(runPath({ id: "abc", kind: "run" })).toBe("/runs/abc");
    expect(runPath({ id: "abc", kind: "plan" })).toBe("/plans/abc");
  });
});
