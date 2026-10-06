import { describe, expect, it } from "vitest";
import {
  buildOptions,
  buildOverrides,
  buildTarget,
  evaluationApproach,
  firstProblemStep,
  initialWizard,
  isHttpUrl,
  isRemote,
  isRepoUrl,
  lines,
  planRequest,
  planSignature,
  runRequest,
  splitCommand,
  STEPS,
  stepIsValid,
  summarise,
  validateStep,
  type WizardState,
} from "./wizard";

const wizard = (changes: Partial<WizardState> = {}): WizardState => ({ ...initialWizard(), ...changes });

describe("reading what a person typed", () => {
  it("splits a command the way a shell would for plain words and quotes", () => {
    expect(splitCommand("python app.py --port 8000")).toEqual(["python", "app.py", "--port", "8000"]);
    expect(splitCommand(`node "my agent.js" 'a b'`)).toEqual(["node", "my agent.js", "a b"]);
    expect(splitCommand(`echo "say \\"hi\\""`)).toEqual(["echo", 'say "hi"']);
    expect(splitCommand(`run ""`)).toEqual(["run", ""]);
    expect(splitCommand("   ")).toEqual([]);
  });

  it("splits lines and drops blanks", () => {
    expect(lines("one\r\n\n  two  \nthree")).toEqual(["one", "two", "three"]);
  });

  it("accepts only http(s) addresses, and https or ssh addresses for a repository", () => {
    expect(isHttpUrl("https://example.com/agent")).toBe(true);
    expect(isHttpUrl("ftp://example.com")).toBe(false);
    expect(isHttpUrl("example.com")).toBe(false);
    expect(isRepoUrl("https://github.com/org/agent")).toBe(true);
    expect(isRepoUrl("git@github.com:org/agent.git")).toBe(true);
    expect(isRepoUrl("http://github.com/org/agent")).toBe(false);
    expect(isRepoUrl("file:///etc/passwd")).toBe(false);
  });

  it("tells a target on this machine from one that is not", () => {
    expect(isRemote(wizard({ webUrl: "http://localhost:3000" }))).toBe(false);
    expect(isRemote(wizard({ apiUrl: "http://127.0.0.1:8000/chat" }))).toBe(false);
    expect(isRemote(wizard({ apiUrl: "https://agent.example.com/chat" }))).toBe(true);
    expect(isRemote(wizard({ webUrl: "http://localhost:3000", mcpUrl: "https://mcp.example.com" }))).toBe(true);
    // with no endpoint address the tests go to the host of the OpenAPI document
    expect(isRemote(wizard({ openapiUrl: "https://agent.example.com/openapi.json" }))).toBe(true);
    expect(isRemote(wizard({ openapiUrl: "http://127.0.0.1:8000/openapi.json" }))).toBe(false);
    expect(isRemote(wizard({ apiUrl: "http://127.0.0.1:8000/chat", openapiUrl: "https://docs.example.com/openapi.json" }))).toBe(false);
    expect(isRemote(wizard())).toBe(false);
  });

  it("says how the target will be examined", () => {
    expect(evaluationApproach(wizard({ source: "repository" }))).toBe("white_box");
    expect(evaluationApproach(wizard({ source: "api" }))).toBe("black_box");
    expect(evaluationApproach(wizard({ source: "repository", runCommand: "python app.py" }))).toBe("hybrid");
    expect(evaluationApproach(wizard({ source: "documents" }))).toBe("black_box");
  });
});

describe("what each step needs", () => {
  it("wants a source first", () => {
    expect(validateStep("source", wizard())).toHaveProperty("source");
    expect(stepIsValid("source", wizard({ source: "demo" }))).toBe(true);
  });

  it("checks the details of each kind of target", () => {
    expect(validateStep("details", wizard({ source: "api" }))).toMatchObject({ name: expect.any(String), apiUrl: expect.any(String) });
    expect(validateStep("details", wizard({ source: "api", name: "x", apiUrl: "ftp://x" }))).toHaveProperty("apiUrl");
    expect(validateStep("details", wizard({ source: "api", name: "x", apiUrl: "https://x.test/chat", requestTemplate: "[1]" }))).toHaveProperty("requestTemplate");
    expect(validateStep("details", wizard({ source: "api", name: "x", apiUrl: "https://x.test/chat", requestTemplate: '{"q":"{{input}}"}' }))).toEqual({});
    // an OpenAPI document can stand in for the endpoint address: the endpoint is found in it
    expect(validateStep("details", wizard({ source: "api", name: "x", openapiUrl: "https://x.test/openapi.json" }))).toEqual({});
    expect(validateStep("details", wizard({ source: "api", name: "x", openapiUrl: "x.test/openapi.json" }))).toHaveProperty("openapiUrl");
    expect(validateStep("details", wizard({ source: "api", name: "x", apiUrl: "https://x.test/chat", apiProtocol: "websocket" }))).toHaveProperty("apiProtocol");
    expect(validateStep("details", wizard({ source: "repository", name: "x" }))).toHaveProperty("repoUrl");
    expect(validateStep("details", wizard({ source: "repository", name: "x", repoUrl: "http://insecure.test/r" }))).toHaveProperty("repoUrl");
    expect(validateStep("details", wizard({ source: "url", name: "x", webUrl: "https://app.test" }))).toEqual({});
    expect(validateStep("details", wizard({ source: "local", name: "x" }))).toHaveProperty("localPath");
    expect(validateStep("details", wizard({ source: "documents", name: "x" }))).toHaveProperty("documents");
    expect(validateStep("details", wizard({ source: "mcp", name: "x", mcpTransport: "stdio" }))).toHaveProperty("mcpCommand");
    expect(validateStep("details", wizard({ source: "mcp", name: "x", mcpTransport: "sse" }))).toHaveProperty("mcpUrl");
    expect(validateStep("details", wizard({ source: "existing" }))).toHaveProperty("existingTargetId");
    expect(validateStep("details", wizard({ source: "existing", existingTargetId: "t1" }))).toEqual({});
  });

  it("only offers a credential the target was given", () => {
    expect(validateStep("credentials", wizard({ authCredential: "staging", credentials: [] }))).toHaveProperty("authCredential");
    expect(validateStep("credentials", wizard({ authCredential: "staging", credentials: ["staging"] }))).toEqual({});
  });

  it("holds high-impact tests to a written authorisation, and never on production", () => {
    expect(validateStep("objective", wizard({ allowHighImpact: true }))).toHaveProperty("authorizationNote");
    expect(validateStep("objective", wizard({ allowHighImpact: true, authorizationNote: "Approved by the owner", production: true }))).toHaveProperty("allowHighImpact");
    expect(validateStep("objective", wizard({ allowHighImpact: true, authorizationNote: "Approved by the owner" }))).toEqual({});
  });

  it("asks a regression for its baseline, and for a registered target", () => {
    const problems = validateStep("mode", wizard({ mode: "regression", source: "api" }));
    expect(problems).toHaveProperty("baselineRunId");
    expect(problems).toHaveProperty("mode");
    expect(validateStep("mode", wizard({ mode: "regression", source: "existing", baselineRunId: "r1" }))).toEqual({});
  });

  it("keeps limits to what the server accepts", () => {
    expect(validateStep("models", wizard())).toEqual({});
    expect(validateStep("models", wizard({ maxCostUsd: "0" }))).toHaveProperty("maxCostUsd");
    expect(validateStep("models", wizard({ maxCostUsd: "abc" }))).toHaveProperty("maxCostUsd");
    expect(validateStep("models", wizard({ maxParallel: "65" }))).toHaveProperty("maxParallel");
    expect(validateStep("models", wizard({ maxParallel: "2.5" }))).toHaveProperty("maxParallel");
    expect(validateStep("models", wizard({ repetitions: "21" }))).toHaveProperty("repetitions");
    expect(validateStep("models", wizard({ maxTests: "-1" }))).toHaveProperty("maxTests");
    expect(validateStep("models", wizard({ maxCostUsd: "0.5", maxMinutes: "10", maxParallel: "64", repetitions: "20", maxTests: "6" }))).toEqual({});
  });

  it("finds the first step that needs attention, and none when the plan can be designed", () => {
    expect(firstProblemStep(wizard())).toBe(0);
    expect(firstProblemStep(wizard({ source: "api" }))).toBe(1);
    expect(firstProblemStep(wizard({ source: "demo", name: "Demo" }))).toBeNull();
    expect(STEPS.map((s) => s.id)).toEqual(["source", "details", "credentials", "objective", "mode", "models", "plan", "execute"]);
  });
});

describe("the request the answers become", () => {
  it("builds a demonstration agent that is flawed only when asked", () => {
    const clean = buildTarget(wizard({ source: "demo", name: "Demo" }));
    expect(clean?.mock?.behaviors).toEqual(["success"]);
    expect(clean?.mock?.tools).toContain("send_email");
    const flawed = buildTarget(wizard({ source: "demo", name: "Demo", demoFlawed: true }));
    expect(flawed?.mock?.behaviors).toEqual(["hallucination", "prompt_injection", "unsafe_behavior"]);
  });

  it("builds an API target with its template and credential", () => {
    const target = buildTarget(
      wizard({
        source: "api",
        name: " Support bot ",
        apiUrl: "https://agent.example.com/chat ",
        apiMethod: "POST",
        requestTemplate: '{"message":"{{input}}"}',
        outputPath: "reply.text",
        credentials: ["staging"],
        authCredential: "staging",
        canaries: "CANARY-1\nCANARY-2",
      }),
    );
    expect(target).toMatchObject({
      name: "Support bot",
      credentials: ["staging"],
      known_canaries: ["CANARY-1", "CANARY-2"],
      api: { url: "https://agent.example.com/chat", method: "POST", auth_credential: "staging", request_template: { message: "{{input}}" }, response: { output: "reply.text" } },
    });
    expect(target?.safety).toMatchObject({ production: false, authorized_risk_classes: ["safe", "controlled"] });
  });

  it("builds an API target from an OpenAPI document alone, without an address", () => {
    const target = buildTarget(wizard({ source: "api", name: "Docs only", openapiUrl: " https://agent.example.com/openapi.json " }));
    expect(target?.api).toMatchObject({ openapi_url: "https://agent.example.com/openapi.json" });
    expect(target?.api).not.toHaveProperty("url");
  });

  it("builds repository, local, web and MCP targets", () => {
    expect(buildTarget(wizard({ source: "repository", name: "r", repoUrl: "https://github.com/o/r", repoRef: "main", runCommand: "python app.py" }))).toMatchObject({
      repository: { url: "https://github.com/o/r", ref: "main" },
      command: { mode: "chat", command: ["python", "app.py"] },
    });
    expect(buildTarget(wizard({ source: "local", name: "l", localPath: "/srv/agent" }))?.repository).toEqual({ path: "/srv/agent" });
    expect(buildTarget(wizard({ source: "url", name: "w", webUrl: "https://app.test", inputSelector: "#box" }))?.web).toMatchObject({ url: "https://app.test", input_selector: "#box", send_selector: null });
    expect(buildTarget(wizard({ source: "mcp", name: "m", mcpTransport: "stdio", mcpCommand: "node server.js --flag" }))?.mcp).toMatchObject({ transport: "stdio", command: ["node", "server.js", "--flag"] });
    expect(buildTarget(wizard({ source: "mcp", name: "m", mcpTransport: "sse", mcpUrl: "https://mcp.test/sse" }))?.mcp).toMatchObject({ transport: "sse", url: "https://mcp.test/sse" });
  });

  it("adds high-impact only when it was allowed", () => {
    const allowed = buildTarget(wizard({ source: "demo", name: "d", allowHighImpact: true, authorizationNote: "ok", disposable: true }));
    expect(allowed?.safety).toMatchObject({ authorized_risk_classes: ["safe", "controlled", "high_impact"], authorization_note: "ok", disposable_environment: true });
  });

  it("builds no target for a registered one, and asks for it by id", () => {
    const state = wizard({ source: "existing", existingTargetId: "t-9" });
    expect(buildTarget(state)).toBeNull();
    expect(planRequest(state, "default")).toMatchObject({ project: "default", target_id: "t-9" });
    expect(planRequest(state, "default")).not.toHaveProperty("target");
  });

  it("turns options and limits into the server's units", () => {
    const state = wizard({ mode: "security", intensity: "quick", maxTests: "6", maxMinutes: "2.5", maxCostUsd: "0.25", maxParallel: "4", repetitions: "3", secondWave: false, judge: false, requirements: "Never email customers\nAlways greet" });
    expect(buildOptions(state)).toMatchObject({ suite: "security", intensity: "quick", max_tests: 6, second_wave: false, judge: false, requirements: ["Never email customers", "Always greet"], baseline_run_id: null });
    expect(buildOverrides(state)).toEqual({ max_cost_usd: 0.25, max_execution_time_seconds: 150, max_parallel: 4, repetitions: 3, report_formats: ["json", "md", "html"] });
    expect(buildOptions(wizard({ maxTests: "abc" })).max_tests).toBeNull();
  });

  it("sends the baseline only for a regression", () => {
    expect(buildOptions(wizard({ mode: "regression", baselineRunId: "r1" })).baseline_run_id).toBe("r1");
    expect(buildOptions(wizard({ mode: "full", baselineRunId: "r1" })).baseline_run_id).toBeNull();
  });

  it("runs the plan that was approved, minus what was switched off", () => {
    const state = wizard({ source: "demo", name: "Demo" });
    expect(runRequest(state, "default", "plan-1", ["T-1", "T-2"])).toMatchObject({ project: "default", plan_id: "plan-1", deselect: ["T-1", "T-2"] });
  });

  it("changes the plan's signature when an answer that shapes it changes, and not when it waits longer", () => {
    const a = wizard({ source: "demo", name: "Demo" });
    expect(planSignature(a, "default")).toBe(planSignature({ ...a }, "default"));
    expect(planSignature(a, "default")).not.toBe(planSignature({ ...a, intensity: "thorough" }, "default"));
    expect(planSignature(a, "default")).not.toBe(planSignature({ ...a, name: "Other" }, "default"));
    expect(planSignature(a, "default")).not.toBe(planSignature(a, "other-project"));
  });

  it("summarises the choices in words", () => {
    const rows = Object.fromEntries(summarise(wizard({ source: "demo", name: "Demo", mode: "security", maxCostUsd: "1", credentials: ["a", "b"] })));
    expect(rows).toMatchObject({ Target: "Demo", Source: "Demonstration agent", Mode: "Security, standard", "Cost limit": "$1", Credentials: "a, b" });
  });
});
