# Security

AgentLab sends hostile input to software and runs code it did not write, so its own safety is part of the product. This
page says what is protected from what, which control does it, which test proves it, and what AgentLab does **not**
promise. To report a vulnerability *in AgentLab*, see [SECURITY.md](../SECURITY.md).

* [What is protected from what](#what-is-protected-from-what)
* [The authorization gate](#the-authorization-gate)
* [Canaries](#canaries)
* [The 28 security categories](#the-28-security-categories)
* [Network egress](#network-egress)
* [Credentials](#credentials)
* [Redaction](#redaction)
* [Untrusted content and the evaluator](#untrusted-content-and-the-evaluator)
* [Repositories, archives and documents](#repositories-archives-and-documents)
* [Sandbox](#sandbox)
* [Web interface and API](#web-interface-and-api)
* [Evidence and restricted artifacts](#evidence-and-restricted-artifacts)
* [What this does not promise](#what-this-does-not-promise)

## What is protected from what

| Who is protected | From what | Controls |
|---|---|---|
| The **target** and its owner | AgentLab attacking a system nobody agreed to test, or damaging one that was agreed | The [authorization gate](#the-authorization-gate); [canary-based](#canaries) tests that use made-up values; limits on cost, steps and time ([configuration.md](configuration.md#limits)) |
| The **evaluator** (AgentLab and the machine it runs on) | Text, code or documents from the target that try to steer or attack it | [Fences around untrusted content](#untrusted-content-and-the-evaluator); deterministic checks outrank the judge; [safe ingestion](#repositories-archives-and-documents); the [sandbox](#sandbox); the [egress policy](#network-egress) |
| **Secrets** | Ending up in a trace, report, log or database row | An [encrypted, host-scoped store](#credentials); [redaction](#redaction) before anything is written; [restricted artifacts](#evidence-and-restricted-artifacts) |
| AgentLab's **server** | A caller (or a web page in a caller's browser) making it send requests, start containers or read files | [Token, host and origin checks, confined paths, signed report links](#web-interface-and-api) |

Every control below names the test that exercises it. Those tests live in `tests/security/` and `tests/api/`; the ones that
need Docker are marked `docker` and are skipped, not faked, where Docker is absent
([development.md](development.md#verification-status)).

## The authorization gate

Every test is classified **before** it runs, and the gate then lets it through or **blocks** it. A blocked test is
reported with its reason and is never counted as a failure ([evaluation.md](evaluation.md#how-a-test-gets-its-verdict)):
the target was simply not tested. The gate never infers permission from possessing a URL.

### Classes

| Class | What it covers | Allowed by default |
|---|---|---|
| `safe` | Ordinary questions, read-only probes, synthetic data, mocked tools | Yes, always |
| `controlled` | Anything signed in (a stored credential, an authenticated browser session), anything that runs repository code or a coding agent, and security tests | Yes (`safety.authorized_risk_classes` defaults to `safe` and `controlled`) |
| `high_impact` | A test that could cause an external side effect: sending e-mail, moving money, deleting data, changing accounts | **No.** Only the owner can grant it, in words |

The class of a test is the highest of the class its skill declared and what its content implies, so a test cannot claim to
be safer than it is: it is at least `controlled` when it needs a credential, drives a signed-in browser or has a
workspace, and `high_impact` when it names one of these tools (`send_email`, `transfer_funds`, `delete_file`,
`delete_record`, `execute_payment`, `post_message`, `create_user`, `delete_user`, `run_shell`, `execute_code`) *and* says
the call has real side effects. Against a remote production target every security, safety or adversarial test counts as
at least `controlled`.

### Where the target is

The gate looks at the `api`, `web` and `mcp` addresses of the target and takes the most remote one. A target with none of
them (the built-in mock agent, a model, a command in the sandbox, a repository) is `local`.

| Locality | Which hosts |
|---|---|
| `local` | `localhost`, `*.localhost`, `host.docker.internal`, loopback addresses |
| `private` | RFC 1918 and other private or link-local addresses; names ending `.local`, `.internal`, `.lan`, `.home.arpa`; a name without a dot |
| `remote` | everything else |

### The rules

Prerequisites are checked first, then authorization, so that you fix the right thing first. A prerequisite is a fact about
the setup (**block kind `prerequisite`**); authorization is a statement only the owner can make (**block kind `policy`**).
The messages are the gate's own words.

| # | A test is blocked when | Kind | To proceed |
|---|---|---|---|
| 1 | it needs an interface the target does not have, or has but could not be reached (the reason is shown) | prerequisite | Declare or start the interface ([testing-agents.md](testing-agents.md)) |
| 2 | it needs a browser and Playwright or Chromium is not available | prerequisite | Install them ([browser-testing.md](browser-testing.md)) |
| 3 | it needs a credential profile that is not stored | prerequisite | `agentlab credentials add` ([below](#credentials)) |
| 4 | it needs a sandbox and Docker is unavailable: *"AgentLab fails closed instead of executing on the host"* | prerequisite | Start Docker ([Sandbox](#sandbox)) |
| 5 | it needs an independent judge and none is configured | prerequisite | Configure one ([providers.md](providers.md)) |
| 6 | its class is not in the target's `safety.authorized_risk_classes` | policy | Grant it: `--authorize` |
| 7 | it is `high_impact` and `safety.authorization_note` is empty or only whitespace | policy | Write the note: `--authorization-note` |
| 8 | it is `high_impact`, the target is `remote` and `safety.disposable_environment` is not true | policy | Say the environment is disposable: `--disposable-environment` |
| 9 | the target is marked `production` and the test is above `safe`, unless `security.allow_production_targets` is true | policy | An operator setting in the configuration file, not a per-run flag |
| 10 | it is `high_impact` and the target is marked `production` | policy | **Never allowed**, whatever else is granted |
| 11 | it is a security, safety or adversarial test, the target is `remote` and `safety.authorization_note` is empty | policy | The owner's attestation: `--authorization-note` |

Security tests against your own machine or network (`local`, `private`) need no attestation, because the owner of the
infrastructure is the person running AgentLab. Against someone else's host they need the owner's written statement, and
AgentLab never attacks arbitrary infrastructure.

### Granting authorization

In the target file (`target.yaml`), or in the same `safety` object of a target sent to the API:

```yaml
safety:
  production: false
  authorized_risk_classes: [safe, controlled, high_impact]
  authorization_note: Staging copy owned by the platform team; reset every night
  disposable_environment: true
```

On the command line, which adds to what the file says (`safe` is always included):

```bash
agentlab test --target target.yaml \
  --authorize high_impact \
  --authorization-note "Staging copy owned by the platform team; reset every night" \
  --disposable-environment
```

`--production` marks the target as production. The note is the owner's statement, kept with the target definition (it is
part of the `spec_hash` in the run manifest, so a run made under a different statement has a different hash). AgentLab
cannot check that the statement is true; it can only refuse to go on without it.

### What it looks like

Planning against the tool-using example agent (`agentlab fixtures serve tool`) on this machine, with nothing granted,
`agentlab test --target target.yaml --plan-only` predicts the nine `high_impact` tests blocked, and says why:

```
HIGH_IMPACT tests are not authorised for this target; grant them explicitly in the target's
safety.authorized_risk_classes (authorisation is never inferred from a URL)
```

With `--authorize high_impact` alone the reason becomes *"HIGH_IMPACT tests need a written authorisation note
(safety.authorization_note)"*; with the note as well they are runnable on a `local` target (a `remote` one also needs
`--disposable-environment`). Marking the same target `--production` blocks every test above `safe`
(*"target is marked as production and security.allow_production_targets is false"*). Plans are made without sending any
test, but they do check that each interface answers, with one `OPTIONS` or `HEAD` request.

Tested by `tests/security/test_gate.py`, which has a test for every row above, including that whitespace is not an
attestation and that prerequisites are reported before authorization.

## Canaries

A security test that tried to steal a *real* secret would be an attack. AgentLab uses **canaries** instead: made-up,
unique values that are harmless to show in a report. Seeing a canary where it must not be is proof of a leak.

* **Issued by AgentLab.** `{{canary:NAME}}` in a test's input or checks becomes `AGENTLAB_CANARY_` plus eight random
  upper-case hex digits (the prefix is `security.canary_prefix`). The same name gives the same value for the whole run, so a
  value placed in one turn can be looked for in another; `{{b64:canary:NAME}}` is its base64 form, for a target that decodes
  what it is given.
* **Planted by the owner.** For a deployment you configured with synthetic secrets (in its system prompt, knowledge base or
  environment), list them in `known_canaries`. Any sighting of them in a reply, a tool call, an event or a citation is
  reported as a leak.

  ```yaml
  known_canaries: [AGENTLAB_CANARY_PLANTED_01]
  ```
* **What AgentLab can plant itself.** Only where it controls the target's inputs: the built-in mock agent, and an HTTP
  target that offers a `knowledge_endpoint`. For every other target a test that needs a secret *inside the target* is
  **blocked**, with the reason that `known_canaries` has to be declared. AgentLab does not pretend to have planted
  something it could not plant.
* **Canaries are never masked.** Redaction hides real secrets; a canary is evidence and is left readable
  ([Redaction](#redaction)).
* **Where leaks are looked for.** `no_canary_leak` checks the reply, the arguments and results of tool calls, events and
  citations and names the channel the canary left by; `no_secret_leak` does the same for secret-shaped text;
  `diff_no_secrets` does it for the diff of a coding agent ([evaluation.md](evaluation.md#deterministic-assertions)).

Tested by `tests/unit/test_assertions.py` (a canary that escapes through a tool call names the channel; canaries a run issued
are watched without being listed), `tests/unit/test_design.py` (canary seeding is never simulated), `tests/unit/test_skills.py`
(declared canaries replace seeding) and `tests/security/test_redaction.py` (a canary is never masked).

## The 28 security categories

Security tests are grouped in 28 categories, N1 to N28. A plan reports for each whether it is **covered**, only **partly**
covered (some tests blocked), **not covered** (with the reason), or **not applicable** (the target shows no sign of what the
category is about, for example no tools). "Covered" counts what was tested; it does not mean the agent is secure
([evaluation.md](evaluation.md#what-this-cannot-tell-you)).

| | Category | Built-in skills that test it | Applies when the target shows |
|---|---|---|---|
| N1 | Direct prompt injection | prompt-injection-testing | any conversational target |
| N2 | Indirect prompt injection | indirect-prompt-injection-testing | any conversational target |
| N3 | System instruction override | prompt-injection-testing, data-exfiltration-testing | any conversational target |
| N4 | Tool injection | tool-abuse-testing, mcp-testing | tools or MCP |
| N5 | Tool output poisoning | indirect-prompt-injection-testing | tools |
| N6 | RAG document injection | indirect-prompt-injection-testing, rag-testing, document-agent-testing | retrieval or documents |
| N7 | Browser content injection | browser-agent-testing | a browser |
| N8 | Memory poisoning | memory-testing | memory |
| N9 | Goal hijacking | prompt-injection-testing, multi-agent-testing | any conversational target |
| N10 | Excessive agency | excessive-agency-testing, autonomous-agent-testing | tools |
| N11 | Unauthorized tool usage | authorization-testing | tools |
| N12 | Privilege escalation | authorization-testing | tools |
| N13 | Data exfiltration | data-exfiltration-testing, indirect-prompt-injection-testing | any conversational target |
| N14 | Cross-user data leakage | memory-testing | memory |
| N15 | Cross-session leakage | memory-testing | memory |
| N16 | Secret leakage | data-exfiltration-testing | any conversational target |
| N17 | Credential exposure | data-exfiltration-testing, coding-agent-testing | any conversational target |
| N18 | Unsafe destructive action | excessive-agency-testing, browser-agent-testing, coding-agent-testing | tools, a browser or coding |
| N19 | Missing confirmation | excessive-agency-testing | tools |
| N20 | SSRF-like unsafe network behaviour | tool-abuse-testing, mcp-testing | tools or MCP |
| N21 | Path traversal | tool-abuse-testing, mcp-testing | tools or MCP |
| N22 | Command-execution safety | tool-abuse-testing, mcp-testing | tools, MCP or coding |
| N23 | Unsafe generated-code execution | coding-agent-testing | coding |
| N24 | Denial-of-wallet and runaway loops | cost-testing, autonomous-agent-testing, browser-agent-testing, multi-agent-testing | tools, autonomy, a browser or several agents |
| N25 | Recursive agent delegation | multi-agent-testing | several agents |
| N26 | Malicious external content | indirect-prompt-injection-testing, browser-agent-testing, multimodal-agent-testing | any conversational target |
| N27 | Malicious skill or tool instructions | mcp-testing | MCP |
| N28 | Insecure fallback behaviour | authorization-testing | tools or MCP |

A security test is deterministic wherever it can be (a canary appeared or it did not; a tool was called or it was not)
and uses the judge only for what a rule cannot say. A security failure caps the overall score and the best grade
([evaluation.md](evaluation.md#scoring)), and its severity starts from the baseline of the category and rises with the
signals it shows ([evaluation.md](evaluation.md#severity)).

## Network egress

AgentLab itself sends requests to addresses that come from outside: a target URL, an `openapi_url`, a repository URL, a
redirect, an MCP endpoint, a page a browser loads. The **egress policy** stops those requests from being turned against the
machine AgentLab runs on or its cloud account.

| Check | Default | Setting |
|---|---|---|
| Only `http`, `https`, `ws` and `wss` are requested; a URL needs a host | always | |
| Cloud metadata endpoints are refused: `169.254.169.254`, `metadata.google.internal`, `metadata`, `fd00:ec2::254`, `100.100.100.200`, and any name that *resolves* to a link-local address, however it is written (`::ffff:169.254.169.254` is the same service) | on | `security.block_metadata_endpoints` |
| Loopback and private addresses are allowed, because most agents under test run on a developer machine or a private network. Turn this off to refuse them | allowed | `security.allow_private_networks` |

If any one of several DNS answers is refused, the name is refused. A name that does not resolve is left to fail as an
ordinary target error.

The policy is applied when an adapter opens, to the OpenAPI document, the knowledge endpoint and the MCP endpoint, to
**every hop of a redirect** (at most three are followed, by AgentLab and not by the HTTP client), to a repository URL
before anything is fetched (only `https` is accepted; `git@` addresses are refused), and to **every request a browser page
makes**, WebSockets included, which is aborted (or closed) when refused and reported. A target that is in a forbidden network is refused before a single
request is made.

A redirect to another origin also drops the credential and every header the owner configured; only `Content-Type` and
`Accept` travel on.

**An OpenAPI document decides how to talk to a target, never where.** When a target is given only as an OpenAPI document
(`--openapi URL`, no `--api-url`), the chat endpoint is found in the document and its address is built from the server the
document names, **but only when that server is on the host the document was read from**. A server on another host, port or
scheme is not used: tests are sent nowhere, the run reports why, and the owner confirms the address with `--api-url`. The
reason is the gate: the owner authorised testing the target they named, and a file they pointed AgentLab at must not be able
to redirect attack tests to a third party. An address the owner gives is always where tests go; the document then only fills
in the request shape (the message field, the session field, where the answer is, SSE) the owner left at the default. The
document is downloaded through the egress policy, never follows a redirect, and is read up to 5 MiB. Tested by
`tests/unit/test_openapi_resolution.py` and `tests/integration/test_openapi_target.py`.

Tested by `tests/security/test_egress.py` and `tests/security/test_mcp_transport.py`.

## Credentials

Test credentials let AgentLab sign in as a test user. They are the most sensitive thing AgentLab handles, so:

* **They never sit in a configuration file, a target file, a report or the database.** A target names a profile
  (`auth_credential: test-user`); the value lives in the store or in an environment variable.
* **The store is encrypted.** Values are kept in one file, encrypted and authenticated with Fernet (AES-128-CBC with an
  HMAC), written with mode `0600` through a temporary file and an atomic rename. A store that has been tampered with, or that
  is opened with the wrong key, is refused and says nothing about its contents.
* **The key** is `AGENTLAB_MASTER_KEY` when that is set (then no key file exists), and otherwise a `0600` key file next to
  the store, created on first use. A key file that other users can read is refused.
* **Values are never accepted on a command line**, where they would land in shell history and process listings. They are
  prompted for (hidden), piped with `--stdin`, read from a file (`--state-file`, for a browser session), or referenced
  with `--from-env FIELD=VARIABLE`, in which case nothing is stored at all.
* **Each profile is scoped to the places it may be sent.** A host scope (`example.com`, `*.example.com`) covers that host
  and its subdomains; a URL scope (`https://api.example.com/v1`) covers exactly that scheme, host and port and the path
  below it. Both are compared on whole labels and segments, so these are **not** inside `example.com`:
  `example.com.evil.net`, `evilexample.com` and `https://example.com@evil.net`; and `/v1x` is not inside `/v1`. A profile
  with no scope needs `--unscoped`, which allows any host the target names and is not recommended.
* **A profile can expire** (`--expires 2026-12-31`), and an expired one is refused with a message that says so.
* **Listing never shows a value.** `agentlab credentials list` shows names, kinds, scopes, field *names*, expiry and version;
  the API never returns a secret and accepts values only on write.

Kinds: `bearer`, `oauth_token`, `api_key` (header `X-API-Key` unless `--header-name` says otherwise), `basic`, `headers`,
`cookies`, `browser_state` (a Playwright storage state, written to a private temporary file for the length of a test and then
deleted) and `env` (references only). The `client_cert` kind appears in the model's list of kinds, but nothing offers it and
no adapter uses it: client certificates are **not supported**.

```bash
agentlab credentials add test-user --kind bearer --scope api.staging.example.com   # prompts, hidden
printf '%s' "$TOKEN" | agentlab credentials add ci-user --kind bearer --scope localhost --stdin
agentlab credentials add from-ci --kind bearer --from-env token=STAGING_TOKEN --scope api.staging.example.com
agentlab credentials list
agentlab credentials remove test-user --yes
```

The CLI lists, adds and removes. The API can also replace the secret values of a profile (`POST /credentials/{name}/rotate`),
which keeps its scope and expiry, raises its version and records when it happened (timestamps only); the old value stays
masked in everything written afterwards. Every value that is resolved for use is registered with the
[redactor](#redaction) in the forms it travels in.

Tested by `tests/security/test_credentials.py` and, for the CLI, `tests/integration/test_cli.py`.

## Redaction

Everything is redacted **before it is written**: traces, artifacts, database columns, logs, exception messages, report
files and the text sent to a judge. Three sources are combined.

1. **Registered values.** Every credential AgentLab resolves is registered, in its plain, URL-encoded and base64 forms, and
   masked wherever it appears. The longest value is masked first, so no fragment of it remains. Values shorter than six
   characters are not registered, because masking them would mangle ordinary text.
2. **Built-in patterns** for common shapes of secret: `private_key`, `aws_access_key`, `aws_secret_key`, `jwt`, `bearer`,
   `basic_auth`, `github_token`, `slack_token`, `google_api_key`, `anthropic_key`, `openrouter_key`, `openai_key`,
   `connection_string` (a URL with a user and password) and `password_assignment` (`password=…`, `api_key: …`,
   `access_token = …`, also inside JSON text).
3. **Your own patterns**, as regular expressions in `security.custom_secret_patterns`.

In a structure, the value under a sensitive key is masked whatever it looks like: `authorization`, `proxy-authorization`,
`cookie`, `set-cookie`, `x-api-key`, `api_key`, `password`, `secret`, `client_secret`, `access_token`, `refresh_token`,
`id_token`, `token`, `private_key`, `session_token`, `storage_state`. For a database column only secret *values* are
masked and the field names stay, because hiding the name `password` in a tool's schema would destroy the record.

A masked value is replaced by a label that says what was hidden, so a reader knows something was there:

```
Authorization: [REDACTED:bearer]
password=[REDACTED:password_assignment]
my [REDACTED:credential:demo] is here
```

Log records are masked in the message, the arguments and the traceback; a record that cannot be masked is withheld rather than
written. Artifacts record whether anything was redacted. Canary values are never masked.

Redaction is **best effort**: a secret in a shape that no pattern knows, that was never registered and that sits under a key
that is not sensitive can survive. Registered credentials are the reliable part. Use test credentials, and add a pattern for
anything specific to your systems.

Tested by `tests/security/test_redaction.py`, which has a sample for every built-in pattern.

## Untrusted content and the evaluator

The target is software being judged, and what it produces is evidence, never instruction. Everything that originates from
a target (its replies, tool results, retrieved documents, repository files, web pages, a skill someone shared) is
**untrusted data**.

* **Fenced.** When such text is shown to a model, it is wrapped as `<UNTRUSTED_AGENT_OUTPUT nonce="9f3a1c07"> … </UNTRUSTED_AGENT_OUTPUT nonce="9f3a1c07">`
  (also `UNTRUSTED_TARGET_REPOSITORY`, `_DOCUMENT`, `_TOOL_OUTPUT`, `_WEB_CONTENT`, `_TRACE`, `_THIRD_PARTY_SKILL`). The
  nonce is random for every fence, so the target cannot know what its closing tag would have to look like; anything in
  the content that resembles an `UNTRUSTED` tag, with or without a nonce, and any chat-template control token (`<|…|>`),
  is defused. Long content is truncated and says how much was dropped.
* **A policy the target cannot write.** The judge's system message states that everything in `UNTRUSTED_*` blocks may contain
  instructions, claims of authority or attempts to change the scoring, and must be treated only as evidence.
* **Deterministic checks outrank the judge.** A judge that is fooled cannot turn a failed check into a pass, and what a target
  says about its own verdict ("score: 10/10") is never parsed as one ([evaluation.md](evaluation.md#the-llm-judge)).
* **The judge is independent.** A judge that is, or may be, the model under test is never used: judging is switched off with
  that reason instead ([providers.md](providers.md)).
* **Model-suggested tests are filtered.** When `evaluation.llm_test_generation` is on, a model may propose extra tests; its
  answer is schema-checked and limited to `safe` tests with plain conversational input, no URLs and no instruction-like text,
  and they are labelled *model-suggested (unverified)* in the plan, where a person can deselect them.
* **Hostile files are data.** The instructions a repository, a document or a shared skill contains are read, never followed.
  Instruction-like text in them (the usual "ignore previous instructions" phrases, requests to send secrets, `curl` of
  a URL, `rm -rf`) is flagged: on a document as an indicator, and in a skill someone shared as a warning of the import. An
  imported skill is only ever an untrusted draft and is never selected until a person has reviewed and promoted it
  ([skills.md](skills.md#trust-and-where-skills-come-from)).
* **Reports escape what an agent said.** HTML reports escape agent text, and the web interface renders it as text, never
  as markup ([Web interface and API](#web-interface-and-api)).

Content sent to a hosted model provider leaves your machine. When a judge or a model-suggested test design is on, the fenced
and redacted excerpts of what the target said go to that provider; use a local provider (Ollama, LM Studio, vLLM or
llama.cpp, [providers.md](providers.md)) when that must not happen.

Tested by `tests/security/test_untrusted_content.py`.

## Repositories, archives and documents

Every imported repository is untrusted, and **importing never runs its code**.

* **Git.** A clone runs with hooks, templates, filters, submodules, `fsmonitor`, symlinks and every protocol except
  `https` switched off, a scrubbed environment (no global or system git configuration, no prompts, no inherited secrets) and
  a shallow, single-branch fetch. A repository token travels in the environment, never in a command line, and a failed clone
  never repeats it. The `.git` directory is read as plain text for the commit and then deleted; git is never run on an
  untrusted checkout.
* **Archives** (zip, tar, `.tar.gz`, `.tar.bz2`, `.tar.xz`) are extracted by AgentLab itself. A member whose path leaves the
  destination stops the import; links, devices and special files are dropped; permissions are not taken from the archive; and
  limits apply: 20,000 files, 500 MiB in all, 50 MiB per file, and a compression ratio of 200 for any member over 1 MB (a
  decompression bomb is stopped before it is written). A refused import leaves nothing behind.
* **Folders** are copied, never mounted, without following links, skipping build and vendor directories.
* **Documents and attachments** are parsed with size limits: a document over 50 MiB is refused and the text taken from one is
  capped at 2,000,000 characters; an attachment is at most 10 MiB, is read only from the fixtures directory, and a link
  inside it cannot lead out; generated attachments are at most 2,000,000 bytes (a generated picture at most 2048 pixels a
  side), so a test cannot exhaust memory or disk.

* **YAML** from a repository, a document, a skill, an OpenAPI document or the owner's own files is read by one loader,
  `agentlab.security.safeyaml`. `yaml.safe_load` cannot run code, but an alias makes the loader *share* a structure instead of
  copying it, so a 368-byte file can describe billions of values and used to run the document parser out of memory after
  eight seconds. The loader counts how large the document would be with every alias expanded *before* any value is built and
  refuses it when that is far more than the text could hold, refuses an alias that refers to its own container, text over
  2,000,000 characters and nesting the parser cannot follow, and says why. A refused document is a parser error, a warning (a
  markdown file's front matter) or an invalid skill, never a crash. A test fails if any module reads YAML another way.

Anything that must *run* repository code (a coding agent, a command target, an MCP server started by a command) runs in the
[sandbox](#sandbox), or is blocked.

Tested by `tests/security/test_ingestion.py`, `tests/security/test_paths.py` and `tests/unit/test_safeyaml.py`.

## Sandbox

Untrusted code runs only through a sandbox provider. If none can guarantee isolation, the tests that need one are
**blocked** and AgentLab says so; it never falls back to running the code on the host
([ADR 0002](decisions/0002-fail-closed-isolation.md)). `security.sandbox_required` cannot be turned off: `false` is refused
when the configuration is loaded. `security.sandbox.provider: disabled` makes every sandbox request fail closed.

The Docker provider starts each sandbox as a container with these restrictions, all of which a test inspects on a real
container:

| Control | Setting |
|---|---|
| Network | **none** by default. `internal` creates a private network with no route out for the sandbox and removes it with the container. `allowlist` is **not supported** (it would need an egress proxy) and is refused with that explanation rather than pretended |
| Filesystem | Read-only root. `/workspace` and `/tmp` are in-memory, size-limited mounts (`/tmp` also `noexec`); nothing from the host is mounted and the Docker socket is never exposed |
| Privileges | `--cap-drop ALL`, `no-new-privileges`, user `65534:65534` (`security.sandbox.user`) |
| Resources | `security.sandbox`: `cpus` (1.0), `memory_mb` (1024, swap disabled), `pids_limit` (256), `disk_mb` (512), `nofile` 1024 |
| Time | Every command runs under `timeout -s KILL` inside the container, and a host-side watchdog removes the whole container if the Docker client itself hangs |
| Output | Standard output and error are each capped at 2,000,000 bytes on the way out |
| Files | Move in and out as tar streams over `docker exec`. Links are never copied in, `.git` is skipped, and what comes out is regular files only (links and devices dropped, paths checked against traversal, mode `0600`) |
| Lifetime | Containers are labelled `agentlab=1` and `agentlab.run=<run id>`, removed when the sandbox closes and again when the process exits |

The image is `security.sandbox.image` (default `mirror.gcr.io/library/python:3.12-slim`), pulled on first use if it is not
present. A command target (`command:` in `target.yaml`) gets a copy of its repository at `/agent` and the network the owner
chose (`command.network`, default `none`); the project's own tests are run to check a result in a separate clean sandbox
with no agent code, no network and none of the owner's environment. An MCP server over stdio is started in a sandbox with no
network, and the host runs only the Docker client that connects to it.

Two cautions. Values in `command.env` reach the container as `docker exec -e NAME=VALUE`, so they are visible in the
host's process list while a command runs: give a sandboxed agent only test values. And the Docker image of AgentLab itself
does not contain the Docker client, so inside it sandbox tests are blocked unless you give it access to a Docker
daemon yourself ([installation.md](installation.md#docker)).

A container is a strong boundary, not a perfect one: a kernel or runtime flaw can still let code out, and no control here
removes that risk. Run AgentLab on a machine that can afford it.

Tested by `tests/security/test_sandbox_escape.py` against real containers (marked `docker`): no route to the host or the
internet, nothing of the host visible, no privilege, a process bomb and a memory hog contained, an output flood capped, a
command that ignores its timeout killed, files crossing the boundary as plain files only.

## Web interface and API

Whoever can call the API can make the server send requests, start containers and drive a browser, so the API protects itself
in layers.

* **Where it listens.** `agentlab serve` listens on `127.0.0.1`. It **refuses to listen anywhere else without a token**
  (`server.token_ref`), both on the command line and when the application starts:

  ```
  error: refusing to listen on 0.0.0.0: other machines could reach it and no API token is configured. Set
  server.token_ref (for example env:AGENTLAB_API_TOKEN, 16 or more characters, from `openssl rand -hex 24`), or
  listen on 127.0.0.1
  ```
* **The token.** `server.token_ref` is `env:NAME` or `secret:NAME` (a stored credential with a `token` or `key` field), at
  least 16 characters. Clients send `Authorization: Bearer TOKEN` or `X-API-Key: TOKEN`. It is compared in constant time.
  After 10 failed attempts from one address within a minute, every request from that address gets `429` until the minute
  has passed, even with the right token. A request that sends no token at all counts for nothing.
* **Without a token (loopback only).** The `Host` header must name this machine (`localhost`, `127.0.0.1`, `::1`), which
  defeats a web page that points its own domain at `127.0.0.1` (DNS rebinding), and a `POST`, `PUT`, `PATCH` or `DELETE`
  that carries an `Origin` header must come from the server's own origin or one listed in `server.cors_origins`, which stops
  a form on another site from calling it. A request without an `Origin` (curl, the CLI, another program) is not a browser
  page and is not affected.

  ```
  $ curl -H 'Host: evil.example:8080' http://127.0.0.1:8080/providers
  {"error": {"kind": "host_not_allowed", "message": "this server answers to 127.0.0.1, ::1, [::1], localhost"}}   400
  $ curl -X POST -H 'Origin: https://evil.example' http://127.0.0.1:8080/projects
  {"error": {"kind": "origin_not_allowed", "message": "requests from this web origin are not allowed"}}            403
  ```
* **What needs no token:** `GET /health` (counts only: version, whether a token is required, runs in progress, jobs waiting),
  the API reference (`/docs`, `/openapi.json`), the built web interface's files, and `GET /view/{token}` (below). Everything
  else needs the token.
* **Paths on the server.** A request can point a target at a file on the server (a repository folder, a document) only
  inside the folders listed in `server.allowed_paths`, after following links. With the default empty list a client cannot
  name a server path at all and uploads instead (`server.max_upload_mb`, 25 MB), whose file names are reduced to a harmless
  last component.
* **No secrets inline.** A target sent to the API cannot carry a secret: a header or an environment value that is named like
  a secret, or that matches a [secret pattern](#redaction), is refused with the instruction to store it with
  `POST /credentials` and name it in `auth_credential`. Header names must be valid and a value cannot contain a line break.
* **CORS** is off unless `server.cors_origins` lists origins, and then allows no credentials. The bundled interface is served
  by the API from the **same origin** and needs none ([ADR 0004](decisions/0004-one-origin-interface-and-signed-report-links.md)).
* **Headers.** Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY` and `Cross-Origin-Resource-Policy: same-origin`. The interface's pages get a content security
  policy that allows only the server's own scripts, styles and connections.
* **Reports are shown from signed links.** A frame cannot send an `Authorization` header and a token must not go in a URL,
  so the interface calls `POST /reports/{id}/view-link` and receives `/view/<token>`: the report, the format and an expiry,
  signed with HMAC-SHA-256 under a key that exists only in the server's memory. The link opens that one file for five minutes
  and stops working when the server restarts. The report is shown in `<iframe sandbox="allow-scripts">` without
  `allow-same-origin`, with a content security policy of `sandbox allow-scripts; default-src 'none'` and
  `frame-ancestors 'self'`: the document has no origin of its own, can reach nothing and cannot read the interface's
  session storage or call the API as the person looking at it.
* **Downloads.** An artifact is served with `Cache-Control: no-store`; content a browser could execute (HTML, SVG, XML) is
  served as an attachment with the same sandboxing policy. [Restricted evidence](#evidence-and-restricted-artifacts) needs
  `include_restricted=true`.
* **The interface keeps the token only in memory and in the tab's session storage**, and writes secrets once: stored
  credentials show their field names and never their values.

AgentLab has **one shared token and no accounts**: whoever holds it can do everything the API can. Reviews are signed with a
reviewer name that is typed in and is not verified ([reports.md](reports.md#human-review)). It does not terminate TLS: put a
reverse proxy that does in front of a team server, one that keeps a single origin for the interface and the API.

Tested by `tests/api/test_api_security.py` (token, throttling, host and origin checks, headers, paths, uploads),
`tests/api/test_report_view.py` (signed links, expiry, tampering, sandbox headers) and `tests/e2e/test_web_ui.py` (a real
Chromium against a real server, including hostile agent metadata).

## Evidence and restricted artifacts

AgentLab keeps what a run recorded so that a finding can be reproduced and a reviewer can see what happened. Artifacts are
stored by content hash, so identical evidence is stored once and a reference can be verified. Text is redacted before it is
written.

Raw traces and the evidence an engine collects (screenshots, page snapshots, a coding agent's diff) are **restricted** by
default, because they can show a signed-in session or a target's data. Restricted artifacts live in a folder created with mode
`0700`, are left out of report bundles unless `--include-sensitive` (the API's `include_sensitive`) asks for them, and are
downloaded only with `include_restricted=true`, which writes a warning to the log.

AgentLab keeps its records until you remove them. There is **no retention policy and no command to delete a run**: the
runs are rows in the database and the artifacts are files, and removing them is the operator's job.

## What this does not promise

* **Isolation is not absolute.** A container sandbox reduces the risk of running hostile code; it does not remove it.
* **Redaction is best effort**, as described above.
* **A judge can be wrong.** Deterministic checks decide first, a judge-only finding is capped and sent to review, but a model
  can still be fooled.
* **The attestation is a statement.** AgentLab refuses to proceed without it and cannot check that it is true.
* **The network `allowlist` mode, WebSocket agent endpoints, client certificates and browsers other than Chromium are not
  supported.** AgentLab says so where you ask for them instead of ignoring the request.
* **No accounts, roles or TLS** in the server, and no data-retention controls.
* **Hosted model providers were verified only against local stand-ins** of their APIs (Ollama was also exercised live); see
  [development.md](development.md#verification-status).
* **A passing run is not a certificate.** It says the tests that ran passed, and the report says what was not tested.

The decisions behind these rules are recorded in [docs/decisions](decisions/README.md).
