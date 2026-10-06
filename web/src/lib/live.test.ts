import { describe, expect, it } from "vitest";
import { event } from "../test/factories";
import { alertText, ALERT_TYPES, applyEvent, describeEvent, initialLive, MAX_EVENTS } from "./live";

const feed = (...events: ReturnType<typeof event>[]) => events.reduce(applyEvent, initialLive());

describe("the live state of a run", () => {
  it("follows the phases as they start and finish", () => {
    const state = feed(
      event("PhaseStarted", { phase: "input_validation", index: 1, total: 17 }),
      event("PhaseCompleted", { phase: "input_validation", status: "completed", duration_s: 0.12 }),
      event("PhaseStarted", { phase: "skill_selection" }),
      event("PhaseCompleted", { phase: "skill_selection", status: "skipped", note: "no skills apply" }),
      event("PhaseStarted", { phase: "test_execution" }),
    );
    expect(state.phases.input_validation).toEqual({ status: "completed", durationS: 0.12, note: undefined });
    expect(state.phases.skill_selection).toMatchObject({ status: "skipped", note: "no skills apply" });
    expect(state.phases.test_execution).toEqual({ status: "running" });
    expect(state.seen).toBe(5);
  });

  it("knows which tests are running and how each ended", () => {
    const state = feed(
      event("TestStarted", { name: "Greets the user", category: "functional" }, { test_id: "CONV-1" }),
      event("TestStarted", { name: "Resists injection", category: "security" }, { test_id: "INJ-1" }),
      event("TestCompleted", { status: "passed", score: 1 }, { test_id: "CONV-1" }),
    );
    expect(state.running).toEqual(["INJ-1"]);
    expect(state.tests["CONV-1"]).toMatchObject({ name: "Greets the user", category: "functional", status: "passed", score: 1 });
    expect(state.tests["INJ-1"].status).toBe("running");
  });

  it("does not list a test twice when it is restarted", () => {
    const state = feed(event("TestStarted", { name: "x" }, { test_id: "A" }), event("TestStarted", { name: "x" }, { test_id: "A" }));
    expect(state.running).toEqual(["A"]);
  });

  it("keeps what the agent was last asked and what it answered", () => {
    const state = feed(
      event("AgentRequest", { input: "What is 17 + 25?" }, { test_id: "T" }),
      event("AgentResponse", { output: "42", latency_ms: 51 }, { test_id: "T" }),
    );
    expect(state.lastRequest).toEqual({ testId: "T", text: "What is 17 + 25?" });
    expect(state.lastResponse).toEqual({ testId: "T", text: "42", latencyMs: 51, error: null });
    const failed = applyEvent(state, event("AgentResponse", { output: "", error: "timed out" }));
    expect(failed.lastResponse?.error).toBe("timed out");
  });

  it("pairs a tool result with the call it answers", () => {
    const state = feed(
      event("ToolCalled", { tool: "calculator", arguments_redacted: { expression: "17 + 25" } }),
      event("ToolCalled", { tool: "send_email", arguments_redacted: { to: "[REDACTED:email]" } }),
      event("ToolReturned", { tool: "send_email", status: "success" }),
    );
    expect(state.tools.map((t) => [t.tool, t.status])).toEqual([
      ["calculator", undefined],
      ["send_email", "success"],
    ]);
  });

  it("collects alerts, findings and limits, newest last, within a bound", () => {
    let state = initialLive();
    for (let i = 0; i < 130; i += 1) state = applyEvent(state, event("SecurityAlert", { assertion: "no_canary_leak", message: `leak ${i}` }));
    expect(state.alerts).toHaveLength(100);
    expect(state.alerts.at(-1)?.text).toBe("no_canary_leak: leak 129");
    state = applyEvent(state, event("FindingCreated", { title: "Canary leaked", severity: "critical" }, { test_id: "EXFIL-1" }));
    expect(state.findings).toEqual([{ at: "2026-10-06T10:00:00Z", testId: "EXFIL-1", title: "Canary leaked", severity: "critical" }]);
    state = applyEvent(state, event("LimitReached", { reason: "cost limit of $0.05 reached" }));
    expect(state.limits).toEqual(["cost limit of $0.05 reached"]);
  });

  it("ends when the run does, and nothing is running any more", () => {
    const state = feed(event("TestStarted", { name: "x" }, { test_id: "A" }), event("RunCancelled", {}));
    expect(state.terminal).toBe("RunCancelled");
    expect(state.running).toEqual([]);
  });

  it("keeps only the most recent events but counts them all", () => {
    let state = initialLive();
    for (let i = 0; i < MAX_EVENTS + 50; i += 1) state = applyEvent(state, event("PhaseStarted", { phase: `p${i}` }));
    expect(state.events).toHaveLength(MAX_EVENTS);
    expect(state.seen).toBe(MAX_EVENTS + 50);
  });

  it("never changes the state it was given", () => {
    const before = initialLive();
    const frozen = JSON.stringify(before);
    applyEvent(before, event("ToolCalled", { tool: "x" }));
    expect(JSON.stringify(before)).toBe(frozen);
  });

  it("shows browser steps and ignores events it does not know", () => {
    const state = feed(event("BrowserAction", { agent_event: "click", selector: "#send" }), event("SomethingNew", { a: 1 }));
    expect(state.browser[0].text).toBe("click #send");
    expect(state.seen).toBe(2);
  });
});

describe("what an alert says", () => {
  it("reads a failed check", () => {
    expect(alertText({ assertion: "no_canary_leak", message: "canary leaked via output" })).toBe("no_canary_leak: canary leaked via output");
  });

  it("reads a security finding, which has a title and severity instead", () => {
    expect(alertText({ title: "Canary value leaked", severity: "critical", test: "EXFIL-1" })).toBe("critical finding: Canary value leaked");
    expect(alertText({ title: "Canary value leaked" })).toBe("Canary value leaked");
  });

  it("never shows an empty line", () => {
    expect(alertText({})).toBe("A security alert was raised");
  });
});

describe("the event feed", () => {
  it("describes each kind of event in one line", () => {
    expect(describeEvent(event("RunStarted", { target: "Demo" }))).toBe("Run started for Demo");
    expect(describeEvent(event("PhaseStarted", { phase: "test_execution", index: 8, total: 17 }))).toBe("Phase 8/17 started: test execution");
    expect(describeEvent(event("TestCompleted", { status: "failed", score: 0.5, reason: "output does not match" }))).toBe("failed · score 0.5 · output does not match");
    expect(describeEvent(event("AgentRequest", { input: "hi" }))).toBe("→ hi");
    expect(describeEvent(event("AgentResponse", { output: "hello" }))).toBe("← hello");
    expect(describeEvent(event("ToolCalled", { tool: "calculator", arguments_redacted: { expression: "1+1" } }))).toBe('calculator({\n  "expression": "1+1"\n})');
    expect(describeEvent(event("SecurityAlert", { title: "Leak", severity: "high" }))).toBe("high finding: Leak");
    expect(describeEvent(event("FindingCreated", { title: "Leak", severity: "high" }))).toBe("high: Leak");
  });

  it("cuts long text and falls back to the payload for unknown events", () => {
    expect(describeEvent(event("AgentRequest", { input: "x".repeat(500) })).length).toBeLessThanOrEqual(202);
    expect(describeEvent(event("Mystery", { a: 1 }))).toContain('"a": 1');
  });

  it("treats alerts, limits and failures as the ones to look at", () => {
    for (const type of ["SecurityAlert", "LimitReached", "Error", "RunFailed"]) expect(ALERT_TYPES.has(type)).toBe(true);
    expect(ALERT_TYPES.has("ToolCalled")).toBe(false);
  });
});
