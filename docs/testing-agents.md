# Testing an agent

A **target** is everything AgentLab is told about the agent under test: how to reach it, what it is for, what it may be
asked to do and what AgentLab is allowed to do to it. You write it as a *target file* (`target.yaml`), as command-line
flags, or both. AgentLab never guesses an address, a credential or a permission. What is missing is reported as missing,
and the tests that needed it are **BLOCKED**: not run, and not counted as failures
([evaluation.md](evaluation.md#how-a-test-gets-its-verdict)).

## The shortest way in

Install AgentLab ([installation.md](installation.md)), then in an empty folder:

```console
$ agentlab init                                                  # agentlab.yaml, target.yaml, skills/, .agentlab/
$ agentlab doctor                                                # what works on this machine
$ agentlab fixtures serve chatbot --variant flawed &             # an example agent on http://127.0.0.1:8765
$ agentlab fixtures target chatbot --variant flawed > chatbot.yaml
$ agentlab test --target chatbot.yaml --intensity quick
```

The example agents (`agentlab fixtures list`) are small services with defects planted **on purpose**, so that the first
run shows what a finding looks like and `agentlab fixtures verify` can prove that AgentLab finds the planted defects
and only those ([development.md](development.md#verification-status)). They are stand-ins for a real agent and say
nothing about yours. `agentlab test --mock success` is the same kind of thing: a deterministic agent built into
AgentLab so that it can be tried (and developed) with no model, key or service. A run against it measures AgentLab.

`agentlab test` runs the 17 phases of a run ([architecture.md](architecture.md#the-run-phase-by-phase)), prints how
each went, then a summary, a scorecard, the security picture, the top findings, the tests that were BLOCKED and where
the reports were written (see [Reading the result](#reading-the-result)). The exit code says whether to look
([Exit codes](#exit-codes)); against the flawed example it is `1`.

To test your own agent, replace the second and third steps with a description of it: edit the `target.yaml` that
`agentlab init` wrote, or pass flags such as `--api-url http://localhost:8000/chat`. Start with
`agentlab test --target target.yaml --plan-only` to see what would be asked before anything is.

## Describing the target

### The target file

```yaml
name: support-bot                      # shown in reports; runs of the same name can be compared
description: Answers customers' questions about orders and returns.
objective: Never promise a refund; send refund questions to a human.
version: 2.3.1                         # free text, recorded in the run

api:                                   # an interface: see "Interfaces" below
  url: https://staging.example.com/chat
  request_template: {input: "{{input}}", session_id: "{{session_id}}"}
  response: {output: $.answer}
  auth_credential: test-user           # a stored credential profile, never a secret in this file
repository: {path: ../support-bot}     # source to analyse
documents: [docs/returns-policy.md]    # what the agent should know

known_canaries: [AGENTLAB_CANARY_planted_value_1]
declared_types: [chatbot, rag]
safety:
  authorized_risk_classes: [safe, controlled]
  authorization_note: "Staging copy owned by the platform team"
```

| Key | What it is | What AgentLab does with it |
|---|---|---|
| `name` | A name for the target | Titles the reports and decides which runs are compared. Default: the file's name |
| `description`, `objective` | What the agent is for, and what a good result means | Read by discovery as evidence of the agent's type and by the plan for its wording. **Words only**: they never grant anything and never change what is allowed |
| `version` | Free text | Recorded in the run manifest |
| `api`, `web`, `command`, `mcp`, `llm`, `mock` | Interfaces | How tests reach the agent: [below](#interfaces) |
| `custom` | `{name: {settings}}` | Interfaces that a plug-in adapter provides, by the name it registers under ([plugins.md](plugins.md#agent-adapters)). With no adapter of that name installed nothing is tested through it, and the run says so |
| `repository` | Source code | Analysed, never executed on this machine: [below](#a-repository) |
| `documents` | Files the agent should know (or is judged against) | Analysed for facts and requirements: [below](#documents) |
| `credentials` | Names of stored credential profiles the target may use | `agentlab credentials add`; [security.md](security.md#credentials) |
| `known_canaries` | Synthetic secrets **you** planted in the agent | Any appearance in an answer is reported as a leak ([security.md](security.md#canaries)) |
| `declared_tools` | `[{name, description, parameters, side_effects, requires_confirmation}]` | Tools you say the agent has when they cannot be discovered; they shape the plan |
| `declared_types` | Kinds of agent you say it is (`chatbot`, `rag`, `tool_calling`, `browser`, `mcp`, `coding`, `multimodal`, ...) | Raises that type's confidence in fingerprinting. With `{{attachments}}` in an `api` request template, `multimodal` also says the agent can be sent images |
| `safety` | What you authorise | The gate: [security.md](security.md#the-authorization-gate) |
| `tags` | Labels | Shown with the target. **They do not change which tests are made or run** |

Paths in a target file (`documents`, `repository.path`, `repository.archive`) are read relative to the file, so a project
can be run from any folder. An unknown key, or a value of the wrong kind, is refused with the file and the key in the
message; a target file is never half read.

### Flags

Everything above can also be given on the command line. Flags **change only what they name**: `--api-url` replaces the
file's `api.url` and leaves its headers, request template, response mapping and credential alone, and a flag for an
interface the file does not have creates it.

| Flag | Sets |
|---|---|
| `--target FILE`, `-t` | The file to start from |
| `--name NAME` | `name` (otherwise the file's, or one derived from the repository, URL or command) |
| `--repo PATH_OR_URL`, `--ref REF` | `repository.path` or `repository.url`, and `repository.ref` |
| `--url URL` | `web.url` (a chat page driven with a browser) |
| `--api-url URL` | `api.url` |
| `--openapi URL` | `api.openapi_url` ([below](#when-the-agent-has-an-openapi-document)) |
| `--mcp-url URL` | `mcp.url` (streamable HTTP, unless the file says `sse`) |
| `--mcp-command "CMD ARGS"` | `mcp.transport: stdio` and `mcp.command` (split at spaces) |
| `--command "CMD ARGS"` | `command.command` (split at spaces; chat mode) |
| `--mock BEHAVIOUR`, repeatable | `mock.behaviors` |
| `--llm PROVIDER[:MODEL]`, `--system-prompt TEXT\|@FILE` | `llm.provider`, `llm.model`, `llm.system_prompt` |
| `--docs PATH`, repeatable | **Adds** to `documents` |
| `--description TEXT`, `--objective TEXT` | The same keys |
| `--credentials NAME`, `-C`, repeatable | **Adds** to `credentials`; the first one authenticates every `api`, `web` and `mcp` interface that does not name its own |
| `--authorize controlled\|high_impact`, `--authorization-note TEXT`, `--disposable-environment`, `--production` | `safety.*` ([security.md](security.md#granting-authorization)) |

What flags cannot say, and the file must: request templates and response mappings, headers, selectors, timeouts,
`declared_tools`, `known_canaries`, `command.mode: task`, and a `knowledge_endpoint`.

## Interfaces

An **interface** is a way for AgentLab to talk to the agent. A target may have several. Each is opened separately: one
that cannot be reached blocks only the tests that need it, and the reason is shown (`agentlab doctor` and the plan say
which). A test names the interface it needs (a browser test needs `web`, an MCP test needs `mcp`). A test that does not
uses the first of the target's interfaces in this order: `mock`, `llm`, `api`, `command`, `mcp`, `web`.

A target with **no** interface can still be analysed and planned (from a repository, documents or a description), but
nothing can be sent, so `agentlab test` ends with exit code `4`: *"Nothing could be tested, so there is no verdict."*
It never reports such a run as a pass.

What a test can *observe* depends on the interface, and a test that needs something the interface cannot show is
BLOCKED, or is not generated at all, instead of guessing:

| Interface | Answers messages | Sees tool calls | Sees retrieved context | Can plant a canary, a document or a tool result |
|---|---|---|---|---|
| `api` | yes, with sessions | when `response.tool_calls` is mapped (or the probe saw some) | when `response.contexts` is mapped (or the probe saw some) | documents only, with a `knowledge_endpoint` |
| `web` | yes, with sessions | no: only the visible reply | no | no |
| `command`, `mode: chat` | yes, one sandbox per conversation | when it prints `AGENTLAB_EVENT` lines | no | no |
| `command`, `mode: task` | no: it is given a task and judged by what it leaves behind | no | no | no |
| `mcp` | no: tests are tool calls | yes | no | no |
| `llm` | yes, with sessions | yes, for the tools you declare | yes, for the `knowledge` you declare | no |
| `mock` | yes, with sessions | yes | yes | yes, all three |

A secret can be planted only where AgentLab controls the agent's inputs, which is the built-in mock. For anything else,
plant your own synthetic secrets in a deployment you control and list them in `known_canaries`: AgentLab then only has to
look for them. Tests that need to plant something and cannot say *"interface 'api' lacks capability ['canary_seeding']"*
and what to do about it.

### An HTTP API: `api`

```yaml
api:
  url: https://staging.example.com/v1/chat
  method: POST                         # POST (default), GET (the template is sent as query parameters) or PUT
  protocol: rest                       # rest (default), sse or graphql
  headers: {X-Tenant: acme}            # sent on every request; never put a secret here
  request_template:                    # a JSON body; {{input}}, {{session_id}} and {{attachments}} are filled in
    messages: [{role: user, content: "{{input}}"}]
    conversation: "{{session_id}}"
  response:                            # JSONPath into the answer (jsonpath-ng extended syntax)
    output: $.choices[0].message.content
    tool_calls: $.choices[0].message.tool_calls
    contexts: $.sources[*]
    citations: $.citations[*]
    events: $.trace[*]
    usage: $.usage
    session_id: $.conversation.id      # when the agent assigns its own conversation ids
  session_header: X-Conversation       # also send the session id in this header
  auth_credential: test-user           # stored profile; sent only to URLs inside its scope
  timeout_seconds: 60
```

* **The request.** The default template is `{"input": "{{input}}", "session_id": "{{session_id}}"}`. A value that is
  exactly one placeholder keeps its type (`"{{attachments}}"` becomes a list); inside a longer string a placeholder is
  text. A `graphql` target sends `{"query": graphql_query, "variables": <the template>}`.
* **The answer.** With no `response.output`, AgentLab reads an OpenAI-style `choices[0].message.content`, else the first
  of `output`, `response`, `answer`, `reply`, `text`, `message`, `content`, `result`, and takes a plain-text body as the
  answer. Tool calls are found in `choices[0].message.tool_calls` or a top-level `tool_calls`, and usage in a top-level
  `usage` (`input_tokens` or `prompt_tokens`, `output_tokens` or `completion_tokens`, `llm_calls`, `cost_usd` or
  `cost`). A body that says it is JSON but is not is reported as the agent's defect. A response above 5 MB is an error.
* **Streaming (`sse`).** Server-sent events are read as they arrive and the time to the first token is measured. The
  `response` mapping is not used: text comes from `choices[0].delta.content`, `delta` or `text`, and events of the types
  `tool_call` (or `tool_use`), `context` (or `retrieval`), `handoff`, `plan_step`, `browser_action`, `event` and
  `usage` become tool calls, contexts, events and usage; `[DONE]` ends the stream.
* **Conversations.** Every conversation gets an id, sent as `{{session_id}}` and in `session_header`. An agent that
  hands out its own ids is handled with `response.session_id` (REST and GraphQL): the id it returned is used for the
  rest of that conversation, so memory and multi-turn tests are not silently one-turn tests. Without it AgentLab's own
  id is sent every time.
* **Credentials.** `auth_credential` names a stored profile whose header is added to the request, **only for a URL
  inside the credential's scope**; a test written for another identity (`required_credentials`) is sent as that
  identity, and a test that checks authentication is enforced is sent with none.
* **Time and retries.** `timeout_seconds` (default 60) limits one request, and a timeout is reported as one. A request is
  repeated only when its connection **could not be made** (at most `limits.max_retries` times), so repeating it can never
  repeat something the agent did; a timeout, an error status or a closed connection are never retried. `HTTP 4xx` and
  `5xx` answers are the agent's answers: a test that sends malformed input expects a clean `4xx`, and the finding says
  what came back instead (`HTTP 500, expected one of [...]`).
* **Redirects.** At most three, each one checked against the egress policy ([security.md](security.md#network-egress));
  a redirect to another origin is followed without the credential and without any header you set.
* **Not supported.** `protocol: websocket` is accepted by the schema and **not implemented**. The run says *"interface
  'api' is unavailable: UnsupportedCapability: WebSocket agent endpoints are not supported in this build; use REST, SSE
  or GraphQL"* and the tests that need the interface are BLOCKED.
* **A way to plant documents.** `knowledge_endpoint` is a test hook **of a disposable deployment you control**: AgentLab
  posts `{session_id, name, text}` to it so that the indirect-prompt-injection tests can put a malicious document in the
  agent's knowledge. Never point it at production.

Tested by `tests/unit/test_http_protocols.py`, `test_http_retries.py` and `test_http_sessions.py`.

#### When the agent has an OpenAPI document

`openapi_url` (or `--openapi URL`) points at the agent's OpenAPI or Swagger document. AgentLab reads it to learn how to
talk to the agent: which operation answers a message, what its request looks like and where the answer is. **The
document decides how to talk, never where.**

* With **no `api.url`**, the endpoint that looks like a chat endpoint is chosen, and its address is built only from a
  server on the document's *own* origin (scheme, host and port). A document that names a server on another host is not
  followed (a hostile or merely misconfigured document must not choose where tests are sent): the tests are BLOCKED
  with the reason and a hint to pass `--api-url`.
* With an `api.url` given, the document only fills in what you left at its default: the request template, the answer
  location and the protocol.
* What was chosen is said in the run's warnings, so it can be checked:

  ```console
  $ agentlab test --openapi http://127.0.0.1:8765/openapi.json --plan-only
  ...
  Warnings
    - OpenAPI: no api.url was given, so tests go to POST http://127.0.0.1:8765/chat (Chat)
    - OpenAPI: the request carries {"message": "{{input}}", "session_id": "{{session_id}}"}
  ```

The document is downloaded through the egress policy, without following redirects, at most 5 MiB of it, and at most 2,000
operations are analysed. Tested by `tests/unit/test_openapi_resolution.py` and `tests/integration/test_openapi_target.py`.

### A web page: `web`

```yaml
web:
  url: https://staging.example.com/
  input_selector: "#message"           # all optional, see below
  send_selector: "button[type=submit]"
  message_selector: ".reply"
  auth_credential: test-user
  login_url: https://staging.example.com/login
```

AgentLab does what a person does: opens the page, types, presses send and reads what appears, with one browser context
(its own cookies and storage) per conversation. Unless you name the selectors it finds the message box (a visible text
box) and the send button (a button that says *Send*, *Submit* or *Ask*) the way a person would, and takes as the reply
the **new** text that appears after sending, once it has stopped changing; `message_selector` makes that exact. Because
the page is the interface, only the visible reply is observable. It needs Chromium and the `playwright` package:
[browser-testing.md](browser-testing.md).

### A command: `command`

```yaml
command:
  mode: chat                           # chat (default) or task
  command: [python, /agent/agent.py]
  image: python:3.12-slim              # default: security.sandbox.image
  timeout_seconds: 120
  network: none                        # none (default) or internal; `allowlist` is not supported
```

The command **always runs inside the sandbox**, never on this machine; when Docker is unavailable every test that needs
it is BLOCKED (*"AgentLab fails closed instead of executing on the host"*). The target's `repository` is copied into the
sandbox at `/agent`.

* **`chat`**: the command is run once per message, with the message on standard input and the answer on standard output,
  like a command-line assistant. The turns of one conversation share a sandbox, and `AGENTLAB_SESSION` and
  `AGENTLAB_TURN` are set, so an agent that keeps state in files can remember. It may report what it did by printing
  lines such as `AGENTLAB_EVENT {"type": "tool_call", "name": "search", "arguments": {"q": "x"}}`: tool calls become the
  response's tool calls and other types become events; everything else on standard output is the answer.
* **`task`**: a coding agent. It is started on a disposable copy of a project (`/workspace`, which `AGENTLAB_WORKSPACE`
  also holds) with the task on standard input and is **judged by what it leaves behind**: the files it changed and the
  project's own tests. It is not asked questions. The example is `agentlab fixtures target coding --dir ./repair-bot`.
* `workdir` is where the command starts. By default that is `/agent` for a chat command that has a repository and
  `/workspace` otherwise. `env` adds environment variables.

Tested by `tests/integration/test_command_adapter.py` and `tests/integration/test_workspace.py` (they need Docker).

### An MCP server: `mcp`

```yaml
mcp:
  transport: streamable_http           # streamable_http (default), sse or stdio
  url: http://localhost:9000/mcp
  auth_credential: test-user
```

An MCP server is not a chat partner: it is tools that *other* agents call, so AgentLab tests it the way an agent would
use it. It lists the server's tools, reads what the server tells every client about them, and calls them, with valid
and with hostile arguments. **A test's input is one tool call written as JSON**:
`{"tool": "read_file", "arguments": {"path": "notes.txt"}}`. For `stdio`, give `command` (and `image`, `env`); the
command is started **inside the sandbox** from a copy of the target's repository, never on this machine. MCP targets
need the optional package: `pip install 'agentlab[mcp]'`.

### A model: `llm`

```console
$ agentlab test --llm ollama:qwen2.5:0.5b --system-prompt @prompt.md --plan-only
```

A bare model, with a system prompt, optional tools and in-context knowledge, is a legitimate target: this is how a
prompt or a model is evaluated before it is wrapped in a service. The provider must be one of the **configured**
providers (`ollama` above is an entry of `providers:` in `agentlab.yaml`, [providers.md](providers.md#configuring-one);
one that is not configured is refused with exit code `2` before anything is sent); a model name may contain colons. The
model is recorded as the model under test in the profile and the reports. Tools you declare are answered with their
`mock_result`, so tool *selection* and *arguments* are evaluated and nothing has a side effect. **The judge is never
the model under test**: a judge that is, or may be, the target is not used, and the tests that need a judge are BLOCKED
rather than graded by the target ([providers.md](providers.md#judges)).

### The built-in demo agent: `mock`

`--mock success` (or `mock: {behaviors: [success]}`) is a deterministic agent inside AgentLab. Behaviours include
`hallucination`, `wrong_tool`, `prompt_injection`, `memory_leakage`, `unsafe_behavior` and `flaky`, which plant a defect
so that evaluators can be checked. It exists for development and self-tests and says nothing about any real agent.

## What else a target can bring

### A repository

`repository` (or `--repo`, `--ref`) gives AgentLab the agent's source. It is **analysed, not run**: from the dependency
manifests, configuration files, prompts, tool definitions, API routes, MCP and OpenAPI definitions, agent-guidance
files and a scan of the source, AgentLab learns which framework and model the agent uses, what tools it has and what
those tools can do, and what it is probably for. Every conclusion carries the file and line it came from, and what the
repository only *suggests* is labelled as inferred. A repository can also supply the OpenAPI document and the code that
a `command` or a stdio MCP server runs inside the sandbox.

| Source | Notes |
|---|---|
| `path` | A local folder. It is **copied** (never mounted) without following symbolic links |
| `url` | `https://` only. Cloned with hooks, templates, filters, submodules and every protocol but HTTPS switched off, and a scrubbed environment. `git@` (ssh) is **refused**: *"ssh repository URLs are not supported; use https://"*. A private repository takes a credential profile |
| `archive` | `.zip` or `.tar*`, extracted by AgentLab itself (no `tar` or `unzip` binary) with protection against paths that escape, links and devices, and limits on file count, size and compression ratio |

Limits: 20,000 files, 500 MiB in all, 50 MiB for a file; a repository over them is refused, not truncated. The `.git`
folder is read as text for the commit and branch and is never run through git. Guidance files in the repository
(`AGENTS.md`, `CLAUDE.md`, ...) are recorded and scanned for injection indicators and are **never obeyed**
([security.md](security.md#repositories-archives-and-documents)).

### Documents

`documents` (or `--docs`) lists **local** files, or folders (searched recursively, at most 200 files), that describe what
the agent should know or must do: policies, a product manual, requirements. A URL is **not downloaded** (a hostile address
must not be fetched on behalf of a document list): save the file and give its path, and AgentLab says so in a warning.
AgentLab extracts facts and requirements, with the page, section and line each came from. For an agent that answers from a knowledge base (a RAG or document agent) the
plan turns them into tests: questions whose answers the documents settle, questions that the documents contradict each
other on, and uploads of the documents themselves. For other agents documents are evidence for the profile, and the plan
says so.

Formats: text (`.txt`, `.log`, `.rst`), Markdown (the front matter is read too), CSV and TSV, JSON, YAML, HTML, PDF, Word
(`.docx`), source code and images. A document is limited to 50 MiB and 2,000,000 characters of text. A file AgentLab
cannot read is a warning and the rest are still used: a format with no parser is listed, with the warning, and not
read, and a missing path is named. Parsers never run what a document contains, and hidden text, active
content and instruction-like text are noted as safety observations (and never followed). Documents that are **YAML are read
through a guard against alias bombs** (a few hundred bytes that describe billions of values). Tests that need a file to
attach can generate one on the spot from a `gen://` reference (`gen://png/text?text=CODE%204821`,
`gen://corrupt/pdf`, `gen://bin/random`, ...), so no binary is stored in a test.

Tested by `tests/unit/test_safeyaml.py` and `tests/security/test_ingestion.py`.

### Business rules

```console
$ agentlab test --target target.yaml --requirement "Never promise a refund" \
                                      --requirement "Escalate refund requests above 500 USD"
```

A rule the agent must follow, written in words. No template can know what to ask to check an arbitrary rule, so a rule
becomes a test only when **a model designs its scenario**: with `evaluation.llm_test_generation: true` and a configured
provider ([providers.md](providers.md)), the evaluator model is given the rules, numbered, and asked for one scenario
for each. Those tests are labelled *model-suggested (unverified)*, record the rule they check, pass the same filters
as any model suggestion (plain messages, no links, no attacks) and sit in the plan, where you can read and deselect them
([test-case-design.md](test-case-design.md#model-suggested-tests)). **A rule that ends up with no test is named in the
plan's warnings and in the run's summary**, so that a rule nobody checks never reads as checked. To check a rule without
a model, write the scenario yourself with `--tests`.

Tested by `tests/unit/test_design.py` (the rules are numbered in the request, each test records its rule, a rule with no
test is named and the reason given is the real one).

### A description

`--description` and `--objective` (or the keys of the same name) are the cheapest ingredient: a sentence about what the
agent is for lets discovery recognise its type and lets the plan say why each test exists. They are evidence for
fingerprinting and nothing more.

## Discovery

```console
$ agentlab discover --target chatbot.yaml
```

Before anything is planned, AgentLab **fingerprints** the target: what kind of agent it is (each type with a confidence
and the evidence), the interfaces, models and frameworks it seems to use, the capabilities it shows or does not, its tools
and what they can do, and its attack surfaces. It combines the target file, the repository, the documents, the OpenAPI
document and a few harmless probes. Everything it concludes carries its evidence, and a type it cannot support stays
low-confidence instead of being guessed; the plan then says what it did and did not rely on.

The probes are SAFE questions: greetings, a question about what the agent can do, a two-turn memory check and a few
more. `--no-probe` leaves those questions out. It does **not** stop AgentLab from reading what the target volunteers: the
tool list of an MCP server and the page of a web target are still read, and `--plan-only` still sends one reachability
check (an `OPTIONS` or `HEAD` request) to each interface. A target that must not be contacted at all should be described
by files only (`--repo`, `--docs`, `--description`), which is analysed and planned but, with no interface, tested with
nothing ([above](#interfaces)).

`--json` prints the profile as JSON and `--save profile.json` writes it to a file. `agentlab discover` plans and runs
nothing, and does not store a run.

## Authorization and limits

What a test may do is decided by the **gate** before it is sent, from what *you* state in `safety` and what the test
implies, never from possessing a URL ([security.md](security.md#the-authorization-gate)). In short:

* `safe` tests always run; `controlled` ones (signed in, security tests, running repository code) run by default;
  `high_impact` ones run only when you say so, in words: `--authorize high_impact --authorization-note "..."`, and
  against a remote target also `--disposable-environment`.
* A security test against someone else's host needs the owner's written statement (`--authorization-note`).
* A target marked `--production` receives only `safe` tests, and never a `high_impact` one.

A run is also bounded by cost, tokens, steps, wall time and retries (`--max-cost`, `--max-time`, `--max-tests` and the
`limits` section: [configuration.md](configuration.md#limits)). It stops *cleanly*, finishes the evaluation of what ran, and
records which limit stopped it; the tests that did not run are listed as such and are never counted as passes.

## Plans

```console
$ agentlab test --target chatbot.yaml --plan-only --plan-detail --plan-output plan.md
$ agentlab test --target chatbot.yaml --confirm
```

A plan says which tests will be asked and why, before anything is: [test-case-design.md](test-case-design.md). `--plan-only`
designs and shows it and sends no test (but see the reachability check under [Discovery](#discovery)). `--plan-detail`
lists every test, `--plan-output FILE` writes the plan as Markdown or, for a `.json` name, JSON. `--confirm` shows the plan
and asks before running; it needs a terminal, and without one it stops with exit code `2` and says to use `--plan-only`
and then run the plan. `agentlab runs plan RUN` shows the plan a finished run used.

`--suite` (`discovery`, `functional`, `security`, `browser`, `reliability`, `full`, `regression`) says what a run is about
and `--intensity` (`quick`, `standard`, `thorough`) how deep each skill goes; `--skills`, `--exclude-skills` and
`--max-tests` narrow a plan, and `--tests FILE` adds test cases you wrote
([test-case-design.md](test-case-design.md#suites-and-intensity)).
`--no-second-wave` switches off the adaptive second wave, which follows up what the first found, and `--no-judge`
switches off LLM judging for the run.

## Running again, and comparing

```console
$ agentlab test --target chatbot.yaml --baseline 067fb368          # the same tests again, for a comparison
$ agentlab test --target chatbot.yaml --baseline 067fb368 --only CONV-ARITHMETIC-001
$ agentlab compare 067fb368 9a1c2e44 --fail-on-regression
```

`--baseline RUN` re-runs the tests of an earlier run unchanged (suite `regression`); `--only TEST_ID` runs just those. A
comparison says what is newly failing, what was resolved, and how score, latency, cost and reliability moved, and says
when two runs are not comparable because their plans differ ([reports.md](reports.md#comparing-runs)).
`agentlab runs list` lists the recent runs, newest first, with the ids these commands take (`--limit`, `--project`,
`--json`).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The run finished and no finding reached `--fail-on` (default `high`) |
| `1` | A finding reached `--fail-on`. This is checked before completion, so a run that stopped early still exits `1` when what it found is serious. For `compare --fail-on-regression`: the later run is worse or mixed |
| `2` | The input was invalid: a missing target file, an unknown flag value, `--confirm` without a terminal |
| `3` | The run did not complete (cancelled, failed, or stopped by a cost, time or step limit) and nothing it found reached `--fail-on`. For `compare`: the two runs cannot be compared |
| `4` | Not one test was executed, so there is no verdict (no interface, or every test was blocked) |

`--fail-on none` never exits `1` because of a finding. A CI job normally wants `agentlab test ... --fail-on high --quiet`.

## Reading the result

The output of the quick start above, trimmed (the nine phases that come before are not shown):

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ run 1410c095-4c56-45f3-9f4a-e046d29c5b7e · target fixture-chatbot · status   │
│ stopped_due_to_cost · 18.95s · 510978 tokens · $0.3018 · 2 wave(s)           │
╰──────────────────────────────────────────────────────────────────────────────╯
failed 59  passed 29  blocked 2
                                   Scorecard
  Category                Score   Confidence   Tests   Note
  functional_quality      66      0.85         5/11
  security                100     0.85         17/17
  safety                  0       0.85         0/7
  reliability             100     0.90         49/49
  tool_use                n/a     0.00         0/0     N/A: no applicable tests were executed
  ...
Overall 60.1  Grade D (partial coverage)  profile general
╭────────────────────────── Read this with the score ──────────────────────────╮
│ Do not treat this agent as secure: it must not receive an unqualified rating │
│ until the observed weaknesses are fixed and re-tested.                       │
╰──────────────────────────────────────────────────────────────────────────────╯
Security vulnerabilities observed: No security category showed a weakness, but 4 probe(s) that belong to no category
succeeded against the target (SAFE-FRAUD-001, SAFE-MALWARE-001, SAFE-PRIVACY-001, SAFE-WEAPONS-001).
  N1    Direct prompt injection   resistant   5/5     5 probe(s) run, none succeeded (within the probes AgentLab ran)
  ...
BLOCKED tests (not run, not failed):
  2 x an independent LLM judge is required but none is configured
```

* **A run that reaches a limit stops cleanly, and says which.** The example reports about 5,700 tokens for every answer
  (one of its planted defects), so this run reached the default token limit, `limits.max_tokens` (500,000), during its
  second wave. The status is `stopped_due_to_cost` (the one status for the cost and the token limits), the line printed
  when it happened and the box *Read this with the score* name the limit (*"run used 504926 tokens; max_tokens is
  500000"*), and the reports and the run's manifest carry it. Raise the limit in `agentlab.yaml` or use
  `--no-second-wave` ([configuration.md](configuration.md#limits)).
* **`blocked 2` is not `failed 2`.** A BLOCKED test was not run, because something it needs was missing (the reason is
  printed under *BLOCKED tests*) and it counts neither for nor against the agent.
* **A score is only over what was measured.** A category with no applicable test is `n/a`, never `100`, and the grade
  carries a qualification when coverage was partial, when a security finding capped it, or when little could run. The
  boxes headed *Read this with the score* say which ([evaluation.md](evaluation.md#scoring)).
* **The security block is about categories.** Each of the 28 categories is *resistant* (its probes ran and none
  succeeded), *vulnerable*, *partially tested*, *not tested* or *not covered*. "Resistant" means *within the probes that
  were run* and is never "secure". A harmful request the agent did not decline is an attack that succeeded even though
  it belongs to none of the 28 categories, and the block says so, as above.
* **Findings** carry the evidence, a severity with the reasons for it, a root-cause category with its own confidence, and
  a reproduction. The run prints the ten most serious; `agentlab runs show RUN` lists them all and `--finding TEST_ID`
  shows one in full. The reports hold the complete evidence ([reports.md](reports.md)).

### When tests are BLOCKED

| The reason printed | What to do |
|---|---|
| *there is no running instance to test: the target has no interface AgentLab can drive (give --url, --api-url, --mcp-url, --command, --llm or --mock)* | Give an interface ([above](#interfaces)) |
| *required interface 'api' is unavailable: ...* | The interface was declared but could not be opened (for an `api`, the address is wrong or the agent is down); the message after the colon says why |
| *no usable interface for this test (wanted ['mcp'], available ['api'])* | The test needs an interface the target does not have |
| *interface 'api' lacks capability [...]* | The interface cannot do what the test needs (plant a canary or a document, accept an attachment, omit credentials): the message says how to provide it |
| *an independent LLM judge is required but none is configured* | Configure a judge that is not the target ([providers.md](providers.md#judges)), or accept that these criteria are not judged |
| *browser engine is unavailable (Playwright/Chromium not installed or disabled)* | [browser-testing.md](browser-testing.md) |
| *credential profile 'x' was not provided; authenticated test skipped* | `agentlab credentials add x` |
| *an isolated sandbox is required for this test but Docker is unavailable; AgentLab fails closed instead of executing on the host* | Start Docker ([installation.md](installation.md#the-sandbox-docker)) |
| *HIGH_IMPACT tests are not authorised for this target* and the other `policy` reasons | Grant authorization in words: [security.md](security.md#the-rules) |
| *WebSocket agent endpoints are not supported in this build* | Use REST, SSE or GraphQL |

A reason of the kind *prerequisite* is a fact about the setup; one of the kind *policy* is a statement only the owner can
make, and AgentLab reports the prerequisites first so that the right thing is fixed first.

## From the web interface and the API

The same target is described in the web interface by an eight-step wizard (the interfaces, what to test, the
authorization, the limits, and a plan that can be read before it is run), and sent to the API as the JSON of the same
fields (`POST /test-plans` to plan, `POST /test-runs` to run, `POST /discover` to fingerprint; `/docs` on a running
server is the reference). WebSocket is shown in the wizard as unavailable. See [installation.md](installation.md#the-web-interface) and [reports.md](reports.md).
