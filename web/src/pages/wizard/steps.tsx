import { useState } from "react";
import { Link } from "react-router-dom";
import { Checkbox } from "../../components/controls";
import { CredentialDialog } from "../../components/CredentialForm";
import { Icon } from "../../components/Icon";
import { Async, Card, Field, KeyValue, Notice } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { formatScore, relativeTime } from "../../lib/format";
import { evaluationApproach, INTENSITIES, isRemote, MODES, SOURCES, splitCommand, type Intensity, type WizardState } from "../../lib/wizard";
import { useSession } from "../../session";
import { ARCHIVE_ACCEPT, DocumentList, UPLOAD_ACCEPT, Uploader, type StepProps } from "./shared";

// ============================================================================================================ step 1
export function SourceStep({ state, patch, problems, showProblems }: StepProps) {
  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Where does the agent you want to evaluate come from? You can add more detail on the next step.
      </p>
      <div className="choice-grid" role="group" aria-label="Target source">
        {SOURCES.map((s) => (
          <button key={s.id} type="button" className="choice" aria-pressed={state.source === s.id} onClick={() => patch({ source: s.id })}>
            <strong>{s.title}</strong>
            <span>{s.detail}</span>
          </button>
        ))}
      </div>
      {showProblems && problems.source && (
        <span className="error-text" role="alert">
          {problems.source}
        </span>
      )}
    </div>
  );
}

// ============================================================================================================ step 2
function ExistingTarget({ state, patch, problems, showProblems }: StepProps) {
  const { api, project } = useSession();
  const targets = useAsync((signal) => api.targets(project, signal), [api, project]);
  return (
    <Async state={targets} loading="Loading targets…">
      {(list) =>
        list.length === 0 ? (
          <Notice tone="info" title="No target is registered in this project yet">
            Go back and choose where a new agent comes from. A target is registered when you design its plan.
          </Notice>
        ) : (
          <Field label="Target" htmlFor="existing-target" error={showProblems ? problems.existingTargetId : null}>
            <select id="existing-target" className="select" value={state.existingTargetId} onChange={(e) => patch({ existingTargetId: e.target.value })}>
              <option value="">Choose a target…</option>
              {list.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                  {t.target_version ? ` ${t.target_version}` : ""} ({t.kind})
                </option>
              ))}
            </select>
          </Field>
        )
      }
    </Async>
  );
}

export function DetailsStep(props: StepProps) {
  const { state, patch, problems, showProblems } = props;
  const err = (key: string) => (showProblems ? problems[key] : null);
  const s = state.source;
  if (s === "existing") return <ExistingTarget {...props} />;
  const bad = (key: string) => showProblems && Boolean(problems[key]);
  return (
    <div className="stack">
      <div className="form-grid">
        <Field label="Name" htmlFor="t-name" error={err("name")} help="What this agent is called in reports.">
          <input id="t-name" className="input" value={state.name} onChange={(e) => patch({ name: e.target.value })} aria-invalid={bad("name")} maxLength={120} />
        </Field>
        <Field label="Version (optional)" htmlFor="t-version" help="Shown in reports and used to match regression runs.">
          <input id="t-version" className="input" value={state.version} onChange={(e) => patch({ version: e.target.value })} maxLength={60} />
        </Field>
        <Field label="What the agent does (optional)" htmlFor="t-desc" className="wide" help="A sentence or two. It helps choose tests; AgentLab also learns this by discovery.">
          <textarea id="t-desc" className="textarea" value={state.description} onChange={(e) => patch({ description: e.target.value })} />
        </Field>
      </div>

      {s === "repository" && (
        <Card title="Repository">
          <div className="stack">
            <div className="form-grid">
              <Field label="Repository address" htmlFor="t-repo" error={err("repoUrl")} help="https://… or git@host:path. It is read in an isolated workspace; nothing from it runs on this server.">
                <input id="t-repo" className="input" value={state.repoUrl} onChange={(e) => patch({ repoUrl: e.target.value })} placeholder="https://github.com/your-org/your-agent" aria-invalid={bad("repoUrl")} />
              </Field>
              <Field label="Branch, tag or commit (optional)" htmlFor="t-ref">
                <input id="t-ref" className="input" value={state.repoRef} onChange={(e) => patch({ repoRef: e.target.value })} />
              </Field>
            </div>
            <div className="stack tight">
              <span className="muted small">Or upload an archive of the repository (zip, tar, tgz):</span>
              <Uploader accept={ARCHIVE_ACCEPT} multiple={false} label="Upload an archive" onUploaded={(docs) => patch({ archive: docs[0] })} />
              {state.archive && <DocumentList documents={[state.archive]} onRemove={() => patch({ archive: null })} />}
            </div>
            <RunCommand {...props} />
          </div>
        </Card>
      )}

      {s === "local" && (
        <Card title="Local project">
          <div className="stack">
            <Field label="Folder on the server" htmlFor="t-path" error={err("localPath")} help="Only folders the server's administrator has allowed (server.allowed_paths) can be used. The folder is copied, never modified.">
              <input id="t-path" className="input" value={state.localPath} onChange={(e) => patch({ localPath: e.target.value })} placeholder="/srv/agents/my-agent" aria-invalid={bad("localPath")} />
            </Field>
            <RunCommand {...props} />
          </div>
        </Card>
      )}

      {s === "url" && (
        <Card title="Web application">
          <div className="form-grid">
            <Field label="Address" htmlFor="t-web" error={err("webUrl")} className="wide" help="The page where the agent can be used. It is driven by a real browser.">
              <input id="t-web" className="input" value={state.webUrl} onChange={(e) => patch({ webUrl: e.target.value })} placeholder="https://agent.example.com/chat" aria-invalid={bad("webUrl")} />
            </Field>
            <Field label="Message box selector (optional)" htmlFor="t-input" help="CSS selector. AgentLab looks for a text box when empty.">
              <input id="t-input" className="input mono" value={state.inputSelector} onChange={(e) => patch({ inputSelector: e.target.value })} />
            </Field>
            <Field label="Send button selector (optional)" htmlFor="t-send">
              <input id="t-send" className="input mono" value={state.sendSelector} onChange={(e) => patch({ sendSelector: e.target.value })} />
            </Field>
            <Field label="Answer selector (optional)" htmlFor="t-msg" help="Where the agent's replies appear.">
              <input id="t-msg" className="input mono" value={state.messageSelector} onChange={(e) => patch({ messageSelector: e.target.value })} />
            </Field>
            <Field label="Sign-in page (optional)" htmlFor="t-login" error={err("loginUrl")}>
              <input id="t-login" className="input" value={state.loginUrl} onChange={(e) => patch({ loginUrl: e.target.value })} aria-invalid={bad("loginUrl")} />
            </Field>
          </div>
        </Card>
      )}

      {s === "api" && (
        <Card title="API endpoint">
          <div className="form-grid">
            <Field label="Endpoint address" htmlFor="t-api" error={err("apiUrl")} className="wide">
              <input id="t-api" className="input" value={state.apiUrl} onChange={(e) => patch({ apiUrl: e.target.value })} placeholder="https://agent.example.com/v1/chat" aria-invalid={bad("apiUrl")} />
            </Field>
            <Field label="Method" htmlFor="t-method">
              <select id="t-method" className="select" value={state.apiMethod} onChange={(e) => patch({ apiMethod: e.target.value as WizardState["apiMethod"] })}>
                {["POST", "GET", "PUT"].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Protocol" htmlFor="t-proto">
              <select id="t-proto" className="select" value={state.apiProtocol} onChange={(e) => patch({ apiProtocol: e.target.value as WizardState["apiProtocol"] })}>
                <option value="rest">REST (JSON)</option>
                <option value="sse">Server-sent events</option>
                <option value="websocket">WebSocket</option>
                <option value="graphql">GraphQL</option>
              </select>
            </Field>
            <Field label="Request body (optional)" htmlFor="t-template" error={err("requestTemplate")} className="wide" help={'JSON. {{input}} is replaced by the message and {{session_id}} by the conversation id. Default: {"input": "{{input}}", "session_id": "{{session_id}}"}.'}>
              <textarea id="t-template" className="textarea mono" value={state.requestTemplate} onChange={(e) => patch({ requestTemplate: e.target.value })} aria-invalid={bad("requestTemplate")} spellCheck={false} />
            </Field>
            <Field label="Where the answer is (optional)" htmlFor="t-output" help="A JSONPath such as $.reply. AgentLab guesses common shapes when empty.">
              <input id="t-output" className="input mono" value={state.outputPath} onChange={(e) => patch({ outputPath: e.target.value })} />
            </Field>
            <Field label="OpenAPI document (optional)" htmlFor="t-openapi" error={err("openapiUrl")} help="Lets discovery learn the endpoint's tools and schema.">
              <input id="t-openapi" className="input" value={state.openapiUrl} onChange={(e) => patch({ openapiUrl: e.target.value })} aria-invalid={bad("openapiUrl")} />
            </Field>
          </div>
        </Card>
      )}

      {s === "mcp" && (
        <Card title="MCP server">
          <div className="form-grid">
            <Field label="Transport" htmlFor="t-mcp-t">
              <select id="t-mcp-t" className="select" value={state.mcpTransport} onChange={(e) => patch({ mcpTransport: e.target.value as WizardState["mcpTransport"] })}>
                <option value="streamable_http">Streamable HTTP</option>
                <option value="sse">Server-sent events</option>
                <option value="stdio">Standard input and output (started in the sandbox)</option>
              </select>
            </Field>
            {state.mcpTransport === "stdio" ? (
              <Field label="Command that starts the server" htmlFor="t-mcp-cmd" error={err("mcpCommand")} help="Run inside the sandbox from the target's repository, never on this server.">
                <input id="t-mcp-cmd" className="input mono" value={state.mcpCommand} onChange={(e) => patch({ mcpCommand: e.target.value })} aria-invalid={bad("mcpCommand")} />
              </Field>
            ) : (
              <Field label="Server address" htmlFor="t-mcp-url" error={err("mcpUrl")}>
                <input id="t-mcp-url" className="input" value={state.mcpUrl} onChange={(e) => patch({ mcpUrl: e.target.value })} placeholder="https://mcp.example.com/mcp" aria-invalid={bad("mcpUrl")} />
              </Field>
            )}
          </div>
        </Card>
      )}

      {s === "demo" && (
        <Card title="Demonstration agent">
          <div className="stack">
            <p style={{ margin: 0 }}>A deterministic, simulated HR assistant that runs inside AgentLab: no network, no keys, no cost. It is a demonstration of the product, not a real agent.</p>
            <Checkbox checked={state.demoFlawed} onChange={(v) => patch({ demoFlawed: v })}>
              Plant defects: it makes things up, obeys instructions hidden in content and leaks a secret. A good way to see findings.
            </Checkbox>
          </div>
        </Card>
      )}

      {s === "documents" && (
        <Notice tone="info" title="Documents describe the agent; they are not an agent you can call">
          With no address to send messages to, tests that need a running agent will be listed as blocked, and only analysis of the documents themselves can run. Add an endpoint later by registering the target again with an API or web address.
        </Notice>
      )}

      {s !== null && (
        <Card title={s === "documents" ? "Documents" : "Supporting documents (optional)"}>
          <div className="stack">
            <p className="muted small" style={{ margin: 0 }}>
              Specifications, policies, knowledge-base files, screenshots. Their text is treated as untrusted data and is never followed as instructions.
            </p>
            <Uploader accept={UPLOAD_ACCEPT} multiple label="Upload documents" onUploaded={(docs) => patch({ documents: [...state.documents, ...docs.filter((d) => !state.documents.some((x) => x.ref === d.ref))] })} />
            <DocumentList documents={state.documents} onRemove={(ref) => patch({ documents: state.documents.filter((d) => d.ref !== ref) })} />
            {showProblems && problems.documents && (
              <span className="error-text" role="alert">
                {problems.documents}
              </span>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function RunCommand({ state, patch, problems, showProblems }: StepProps) {
  const parts = splitCommand(state.runCommand);
  return (
    <div className="form-grid">
      <Field
        label="How to run it in the sandbox (optional)"
        htmlFor="t-run"
        error={showProblems ? problems.runCommand : null}
        help={parts.length > 0 ? `Will run: ${parts.map((p) => JSON.stringify(p)).join(" ")}` : "Without a command, only the code is analysed. Needs Docker; without it those tests are blocked, never run on the host."}
      >
        <input id="t-run" className="input mono" value={state.runCommand} onChange={(e) => patch({ runCommand: e.target.value })} placeholder="python -m my_agent" />
      </Field>
      <Field label="How the command is used" htmlFor="t-run-mode" help="Chat: one message per run, answer on standard output. Task: a coding agent given a disposable workspace.">
        <select id="t-run-mode" className="select" value={state.commandMode} onChange={(e) => patch({ commandMode: e.target.value as WizardState["commandMode"] })}>
          <option value="chat">Chat</option>
          <option value="task">Task (coding agent)</option>
        </select>
      </Field>
    </div>
  );
}

// ============================================================================================================ step 3
export function CredentialsStep({ state, patch, problems, showProblems }: StepProps) {
  const { api } = useSession();
  const list = useAsync((signal) => api.credentials(signal), [api]);
  const [adding, setAdding] = useState(false);
  const toggle = (name: string, on: boolean) => {
    const credentials = on ? [...state.credentials, name] : state.credentials.filter((n) => n !== name);
    patch({ credentials, authCredential: credentials.includes(state.authCredential) ? state.authCredential : "" });
  };
  const canAuthenticate = state.source === "api" || state.source === "url" || state.source === "mcp";
  return (
    <div className="stack">
      <Notice tone="info" title="Credentials are optional">
        Without them, tests that need a signed-in account are reported as blocked, never as failed. Use accounts made for testing; values are stored encrypted and never shown again.
      </Notice>
      <Async state={list}>
        {(data) => (
          <div className="stack">
            {data.length === 0 ? (
              <p className="muted">No credential is stored yet.</p>
            ) : (
              <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="small muted">Credentials the agent's tests may use</legend>
                {data.map((c) => (
                  <Checkbox key={c.name} checked={state.credentials.includes(c.name)} onChange={(v) => toggle(c.name, v)}>
                    <strong>{c.name}</strong> <span className="muted small">{c.kind} · {c.scopes.length ? c.scopes.join(", ") : "any host"}</span>
                  </Checkbox>
                ))}
              </fieldset>
            )}
            <div>
              <button type="button" className="btn" onClick={() => setAdding(true)}>
                <Icon name="plus" size={16} /> Add a credential
              </button>
            </div>
            {canAuthenticate && state.credentials.length > 0 && (
              <Field label="Sign in to the target with" htmlFor="auth-cred" error={showProblems ? problems.authCredential : null} help="Used for every request AgentLab sends to the target's address.">
                <select id="auth-cred" className="select" value={state.authCredential} onChange={(e) => patch({ authCredential: e.target.value })}>
                  <option value="">Do not sign in</option>
                  {state.credentials.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
        )}
      </Async>
      <Field
        label="Planted canary secrets (optional)"
        htmlFor="canaries"
        help="Fake secrets you put in the agent's system prompt, knowledge or environment, one per line. If one ever appears in an answer or a tool call, it is reported as a leak. Never enter a real secret."
      >
        <textarea id="canaries" className="textarea mono" value={state.canaries} onChange={(e) => patch({ canaries: e.target.value })} spellCheck={false} autoComplete="off" />
      </Field>
      {adding && (
        <CredentialDialog
          onClose={() => setAdding(false)}
          onCreated={(name) => {
            setAdding(false);
            list.reload();
            patch({ credentials: [...state.credentials, name] });
          }}
        />
      )}
    </div>
  );
}

// ============================================================================================================ step 4
export function ObjectiveStep({ state, patch, problems, showProblems }: StepProps) {
  const remote = isRemote(state);
  const adversarial = state.mode === "security" || state.mode === "full";
  return (
    <div className="stack">
      <Field label="What do you want to learn? (optional)" htmlFor="objective" help="Your goal in your own words, such as “Can this assistant be trusted with leave requests?” It shapes which tests are chosen and how the report is written.">
        <textarea id="objective" className="textarea" value={state.objective} onChange={(e) => patch({ objective: e.target.value })} />
      </Field>
      <Field label="Business rules the agent must follow (optional)" htmlFor="requirements" help="One per line. Each becomes a test of its own, for example “Refunds above 500 USD need manager approval”.">
        <textarea id="requirements" className="textarea" value={state.requirements} onChange={(e) => patch({ requirements: e.target.value })} />
      </Field>
      <Card title="Authorisation and safety">
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>
            AgentLab only tests agents you own or are allowed to test, and its security tests are non-destructive: they use synthetic canaries and never attack other systems. What you state here decides which tests may run.
          </p>
          <Field
            label="Authorisation note"
            htmlFor="auth-note"
            error={showProblems ? problems.authorizationNote : null}
            help="Who authorised the testing and what is in scope, for example “Owner of this staging agent, approved by J. Doe on 2026-01-12”. Required before adversarial tests run against an agent that is not on this machine."
          >
            <input id="auth-note" className="input" value={state.authorizationNote} onChange={(e) => patch({ authorizationNote: e.target.value })} maxLength={500} aria-invalid={showProblems && Boolean(problems.authorizationNote)} />
          </Field>
          {remote && adversarial && !state.authorizationNote.trim() && (
            <Notice tone="warn" title="Adversarial tests will be blocked">
              This agent is not on this machine and you have not written an authorisation note, so the security tests will be listed as blocked in the plan.
            </Notice>
          )}
          <Checkbox checked={state.production} onChange={(v) => patch({ production: v, ...(v ? { allowHighImpact: false } : {}) })}>
            This is a production system. Only safe tests run against it unless the server's administrator allows more.
          </Checkbox>
          <Checkbox checked={state.disposable} onChange={(v) => patch({ disposable: v })}>
            This is a disposable test environment I can reset.
          </Checkbox>
          <Checkbox checked={state.allowHighImpact} disabled={state.production} onChange={(v) => patch({ allowHighImpact: v })}>
            Allow high-impact tests (they may change state, for example call a tool that deletes). Needs a disposable environment and an authorisation note.
          </Checkbox>
          {showProblems && problems.allowHighImpact && (
            <span className="error-text" role="alert">
              {problems.allowHighImpact}
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}

// ============================================================================================================ step 5
function BaselineRuns({ state, patch, problems, showProblems }: StepProps) {
  const { api } = useSession();
  const runs = useAsync(
    (signal) => api.runs({ target: state.existingTargetId, kind: "run", limit: 50 }, signal),
    [api, state.existingTargetId],
    { enabled: state.source === "existing" && Boolean(state.existingTargetId) },
  );
  if (state.source !== "existing") {
    return (
      <Notice tone="warn" title="Regression starts from a registered target">
        Go back and choose “A target you registered”, then pick one of its earlier runs to compare against.
      </Notice>
    );
  }
  return (
    <Async state={runs} loading="Loading earlier runs…">
      {(list) => {
        const usable = list.filter((r) => r.status === "completed" || r.status === "cancelled" || r.status.startsWith("stopped"));
        return usable.length === 0 ? (
          <Notice tone="warn" title="This target has no earlier run to compare with">
            Run it once (full mode) first, then come back for a regression run.
          </Notice>
        ) : (
          <Field label="Compare against" htmlFor="baseline" error={showProblems ? problems.baselineRunId : null} help="The tests of this run are replayed as they were, and the result is compared with it.">
            <select id="baseline" className="select" value={state.baselineRunId} onChange={(e) => patch({ baselineRunId: e.target.value })}>
              <option value="">Choose a run…</option>
              {usable.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.suite} · {relativeTime(r.started_at ?? r.created_at)} · {r.overall !== null ? `score ${formatScore(r.overall)}` : r.status}
                </option>
              ))}
            </select>
          </Field>
        );
      }}
    </Async>
  );
}

export function ModeStep(props: StepProps) {
  const { state, patch, problems, showProblems } = props;
  const approach = evaluationApproach(state);
  return (
    <div className="stack">
      <div className="choice-grid" role="group" aria-label="Evaluation mode">
        {MODES.map((m) => (
          <button key={m.id} type="button" className="choice" aria-pressed={state.mode === m.id} onClick={() => patch({ mode: m.id })}>
            <strong>{m.title}</strong>
            <span>{m.detail}</span>
          </button>
        ))}
      </div>
      {showProblems && problems.mode && (
        <span className="error-text" role="alert">
          {problems.mode}
        </span>
      )}
      {state.mode === "regression" && <BaselineRuns {...props} />}
      {state.mode === "browser" && state.source !== null && state.source !== "url" && state.source !== "existing" && (
        <Notice tone="warn" title="Browser tests need a web address">
          This target has none, so the browser tests will be listed as blocked.
        </Notice>
      )}
      <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="small muted">How thorough</legend>
        <div className="choice-grid">
          {INTENSITIES.map((i) => (
            <button key={i.id} type="button" className="choice" aria-pressed={state.intensity === i.id} onClick={() => patch({ intensity: i.id as Intensity })}>
              <strong>{i.title}</strong>
              <span>{i.detail}</span>
            </button>
          ))}
        </div>
      </fieldset>
      <p className="muted small" style={{ margin: 0 }}>
        Approach: <strong>{approach.replace("_", " ")}</strong> testing
        {approach === "black_box" ? ": AgentLab sees only what the agent does." : approach === "white_box" ? ": AgentLab reads the code and documents but has nothing running to call." : ": code, documents and a running agent are used together."}
      </p>
    </div>
  );
}

// ============================================================================================================ step 6
export function ModelsStep({ state, patch, problems, showProblems }: StepProps) {
  const { api } = useSession();
  const providers = useAsync((signal) => api.providers(signal), [api]);
  const profiles = useAsync((signal) => api.scoringProfiles(signal), [api]);
  const err = (key: string) => (showProblems ? problems[key] : null);
  const judges = providers.data?.filter((p) => p.judge) ?? [];
  const num = (key: keyof WizardState, label: string, help: string, placeholder: string) => (
    <Field label={label} htmlFor={`m-${key}`} error={err(key)} help={help}>
      <input id={`m-${key}`} className="input" inputMode="decimal" value={String(state[key])} onChange={(e) => patch({ [key]: e.target.value } as Partial<WizardState>)} placeholder={placeholder} aria-invalid={Boolean(err(key))} />
    </Field>
  );
  return (
    <div className="stack">
      <Card title="Independent judge">
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>
            Some criteria (is this answer helpful? is it grounded?) cannot be decided by rules. A judge model scores them. The judge is chosen on the server, is never the agent under test, and never sees the agent's instructions to it as its own.
          </p>
          <Checkbox checked={state.judge} onChange={(v) => patch({ judge: v })}>
            Use the judge where deterministic checks cannot decide
          </Checkbox>
          <Async state={providers} loading="Checking providers…">
            {() =>
              judges.length === 0 ? (
                <Notice tone={state.judge ? "warn" : "info"} title="No judge provider is configured">
                  {state.judge ? "Tests that need a judge will be blocked. " : ""}Add a provider in the server's configuration (see <Link to="/providers">Providers</Link>).
                </Notice>
              ) : (
                <KeyValue items={[["Judge", judges.map((j) => `${j.name} (${j.model ?? j.type})`).join(", ")]]} />
              )
            }
          </Async>
        </div>
      </Card>
      <div className="form-grid">
        <Field label="Scoring profile" htmlFor="m-profile" help="How categories are weighted into the overall score.">
          <select id="m-profile" className="select" value={state.scoringProfile} onChange={(e) => patch({ scoringProfile: e.target.value })}>
            <option value="">Server default</option>
            {(profiles.data ?? []).map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}: {p.description}
              </option>
            ))}
          </select>
        </Field>
        {num("repetitions", "Repetitions per test", "Repeat each test to measure how stable the agent is (1 to 20). Server default when empty.", "default")}
        {num("maxCostUsd", "Stop at this cost (US dollars)", "The run stops cleanly when it reaches it.", "server limit")}
        {num("maxMinutes", "Stop after (minutes)", "", "server limit")}
        {num("maxParallel", "Tests at the same time", "1 to 64.", "server default")}
        {num("maxTests", "At most this many tests", "A soft cap: lower-priority tests are left out first, but every area keeps its most important test, so the plan can be longer than this.", "no cap")}
      </div>
      <div className="stack tight">
        <Checkbox checked={state.secondWave} onChange={(v) => patch({ secondWave: v })}>
          Add follow-up tests for what the first wave finds (adaptive second wave)
        </Checkbox>
        <Checkbox checked={state.probe} onChange={(v) => patch({ probe: v })}>
          Send a few harmless discovery probes to the agent to learn what it can do
        </Checkbox>
      </div>
      <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="small muted">Report formats</legend>
        <div className="row">
          {(["json", "md", "html", "pdf"] as const).map((f) => (
            <Checkbox key={f} checked={state.formats.includes(f)} onChange={(v) => patch({ formats: v ? [...state.formats, f] : state.formats.filter((x) => x !== f) })}>
              {f === "md" ? "Markdown" : f.toUpperCase()}
            </Checkbox>
          ))}
        </div>
      </fieldset>
      <p className="muted small" style={{ margin: 0 }}>
        Limits can only stop a run sooner. Security settings (what may be tested, where it may run) are never changed from here.
      </p>
    </div>
  );
}
