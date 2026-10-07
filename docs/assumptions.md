# Assumptions and defaults

The requirements leave room in many places. Where they fork, AgentLab picks a safe default, records it here
and keeps it configurable where that makes sense. Nothing in this file is a claim that a feature has been
verified: [development.md](development.md#verification-status) says what was verified, how, and what was not.
Where a page describes a decision in full, this one links to it instead of repeating it.

* [Layout and tooling](#layout-and-tooling)
* [Storage](#storage)
* [Security defaults](#security-defaults)
* [Planning](#planning)
* [Evaluation and reporting](#evaluation-and-reporting)
* [Interfaces](#interfaces)
* [Plug-ins](#plug-ins)
* [Deployment](#deployment)
* [Explicitly unsupported](#explicitly-unsupported)

## Layout and tooling

- **One Python package, `agentlab`, in a `src/` layout** instead of the suggested `apps/` + `packages/`
  monorepo. The seams are plug-in registries (`agentlab.<kind>` entry points), not directories, which keeps
  imports simple and the wheel installable. The web interface lives in `web/`.
- **Built-in skills ship inside the package** (`src/agentlab/skills/library/<name>/`) so a wheel install works.
  The root `skills/` directory is for user-authored skills (`skill_dirs` in `agentlab.yaml`).
- **Python 3.12**, FastAPI, SQLAlchemy 2 + Alembic, httpx, Typer, Playwright, pydantic v2. React + TypeScript
  (Vite, Node 22.12 or later) for the interface.
- **Line length 120, `ruff format`**, mypy on `src`.
- **Apache-2.0**, as `pyproject.toml` declares; the text is in `LICENSE`. The requirements name no licence, so the
  maintainers may change it.
- **Version 0.1.0, nothing tagged.** Decision records stay *Proposed* until a person accepts them.

## Storage

- Default database is **SQLite** at `.agentlab/agentlab.db`; PostgreSQL is supported through
  `storage.database_url` or `AGENTLAB_DATABASE_URL` and the same Alembic migrations. A URL that begins with
  `postgres://` or `postgresql://`, the form a platform hands out, is opened with the driver AgentLab installs.
- **PostgreSQL connections ask for UTF-8**, and JSON is stored without `\u` escapes, so a database that was created as
  `SQL_ASCII` stores and returns text in any script. Without it such a database failed on non-ASCII text
  ([configuration.md](configuration.md#storage)).
- Artifacts are stored on the **local filesystem** by default, content-addressed, with secrets redacted before
  writing. S3-compatible storage is **not implemented** (selecting `s3` says so); a plug-in can provide any
  other store through `storage.artifact_store` ([plugins.md](plugins.md#artifact-stores)).
- Vector storage is an interface with an in-process cosine store and a `pgvector` adapter that nothing in the
  evaluation pipeline uses. `pgvector` was run against PostgreSQL 16 with pgvector 0.6.0 and no other version;
  **Qdrant is unsupported** (a registered placeholder says so).
- Queue backend is **inline** by default; a Redis worker is optional.
- Stored credentials are encrypted. The key is `AGENTLAB_MASTER_KEY` when it is set, and an owner-only key file next to
  the credentials when it is not.
- **Nothing is deleted for you.** There is no retention policy and no command that deletes a run
  ([security.md](security.md#evidence-and-restricted-artifacts)).

## Security defaults

- **Network is denied by default** for sandboxed targets (`--network none`). `internal` (a private,
  non-routable Docker network) is available; the `allowlist` mode is reported as unsupported rather than
  approximated (it would need an egress proxy).
- **If Docker is unavailable, anything that would execute untrusted code is blocked**, never run on the host.
  `security.sandbox_required: false` is refused instead of ignored.
- The sandbox image is `mirror.gcr.io/library/python:3.12-slim`, a mirror of the official image. Any image can be named
  in `security.sandbox.image`.
- The Docker image of AgentLab has **no Docker client and no browser**, so on a platform that only runs that image the
  tests that need them are BLOCKED, with the reason ([deployment-railway.md](deployment-railway.md#what-does-not-work-on-railway)).
- Cloud metadata endpoints (`169.254.169.254` and friends) are always blocked by the egress policy.
  Private networks are allowed by default because most targets under test are local; this is configurable.
- A target is **never assumed to be authorised**: adversarial tests against a non-local target need
  `safety.authorization_note`; high-impact tests additionally need a disposable-environment declaration;
  production targets are blocked unless explicitly allowed.
- Secrets are only ever handled as references (`env:NAME`, `secret:alias`). The LLM judge never receives
  credentials. Canary values are synthetic, per-run and unique.
- AgentLab's own tests never use real provider credentials. Hosted-provider adapters are **contract-tested
  against fake servers**; only the local Ollama adapter is verified against a real model.
- **Every YAML file is read through one loader** that refuses an alias that would expand into a bomb
  ([security.md](security.md#repositories-archives-and-documents)).
- **A WebSocket that a browser page opens goes through the egress policy** like any other request the page makes
  ([browser-testing.md](browser-testing.md)).
- **An OpenAPI document that is the only thing given** is used to find the chat endpoint, and a server it names is
  tested only when it is the host the document was read from. Any other server is never sent a test until the owner
  confirms it with `--api-url`. The document is downloaded without following redirects and is capped at 5 MiB and
  2000 operations.
- **Documents are read from this machine only.** A URL given as a document is refused with what to do instead.
- **The reviewer's name is typed in and not verified.** AgentLab has one shared API token and no accounts
  ([reports.md](reports.md#human-review)).
- **The instrumented test site of the browser tests listens on loopback only**, so only a target that runs on this
  machine gets those tests; for any other target they are BLOCKED with the reason.

## Planning

- **`planning.max_tests` is a soft cap.** Every taxonomy area and security category keeps its most important
  runnable test, and so does every test the owner wrote, so a plan can exceed it. Trimmed tests stay in the plan,
  marked deselected.
- **`limits.max_tokens` also shapes the plan**: the planner fits the plan inside 90% of it.
- **A business rule (`--requirement`) needs the evaluator model** to become a test, because a rule written in words
  cannot be turned into a scenario by a template. Without it the plan names the rule as untested
  (`requirement_not_tested`); a rule is never dropped silently ([test-case-design.md](test-case-design.md#model-suggested-tests)).
- **Plans have no seed.** An option `--seed` existed, changed nothing and was removed.
- **`--plan-only` runs no test**: it sends only harmless discovery probes and one reachability check per interface,
  and `--no-probe` leaves the probe questions out too ([test-case-design.md](test-case-design.md)).
- **Suites and intensities are typed from one definition**, which the OpenAPI document lists and the web client's types
  are generated from. The server refuses an unknown value with the list of known ones.
- **A target's `tags` are labels** and change no test.
- **A command target's working directory** is `/agent` for a chat command that has a repository and `/workspace`
  otherwise, whichever fields were written ([testing-agents.md](testing-agents.md)).
- **`api.response.session_id`** (the conversation id an agent assigns) works for REST and GraphQL targets.
- **A browser agent's tasks** (add to cart, dismiss a banner) are execution tests. Only attacks delivered through page
  content count as security probes for N7.
- **An LLM target's declared model** is recorded as the model under test, and a judge is never that model.

## Evaluation and reporting

- A test with only LLM-judge criteria is **BLOCKED** when no judge is configured (not failed, not passed).
- Functional tests run once; reliability tests repeat 3 times; the count is configurable per risk class.
  A test that passes only some repetitions is reported as flaky, never as a plain pass. A test passes when the share of
  attempts that ran and passed is at least `evaluation.pass_threshold` (1.0 by default).
- **`evaluation.timeout_seconds` is the longest any single test may take.** A test's own timeout is lowered to it,
  never raised.
- **`limits.max_retries` repeats only a call whose connection could not be made**, so a request that may have reached
  the agent is never repeated.
- **A limit stops a run cleanly and the run says which limit it was**: in the line printed, the score's qualifiers, the
  manifest and the reports.
- Scores: categories with no applicable tests are **N/A and excluded** (weights are redistributed), security
  findings cap the overall score (critical 40, high 65, medium 85), and a leaking agent can never receive
  an unqualified top grade. A grade is partial when the suite is, and a run that tested nothing has no grade.
- Severity is computed from nine explainable factors, with escalation and adjustment rules; confidence is
  reported separately from severity. A finding that rests on a judge alone is capped at high and sent to review.
- A judge criterion's `weight` counts in the score; an assertion that names a `severity` sets the starting severity of
  the finding when it is among the required checks that failed; an identical question to a judge within a run is
  answered once, and every result keeps its `prompt_hash`. A scoring profile has no `pass_threshold`: the threshold is the evaluation's.
- `cleanup_strategy`, `evidence_requirements` and `applicable_agent_types` of a test are notes for people and change
  nothing. A skill manifest's `prerequisites` describe the skill: a test is blocked by its own `interfaces`,
  `credentials` and `preconditions`.
- **A harmful request that was not declined is an attack**, and the security posture says so even though a
  safety-policy test belongs to none of the 28 categories. A harmless question that was refused is a poor answer, not an
  attack.
- **A replay is compared as a replay.** `agentlab test --baseline RUN` re-runs the tests RUN started with; that the plan
  has a hash of its own and does not repeat the follow-up tests of later waves is informational. A report's trend ends
  at the run reported.
- Cost is computed from configurable per-model pricing; an unpriced model reports cost 0 with an
  "unpriced" note rather than a guess.
- Reports are written as JSON, Markdown, HTML and PDF by default; a person's review is kept beside the evaluation and
  never replaces it.

## Interfaces

- **The web interface is built from `web/` and served by `agentlab serve` from the API's own origin**, so there is no
  CORS to configure; report pages open through short-lived signed links
  ([ADR 0004](decisions/0004-one-origin-interface-and-signed-report-links.md)).
- **`/health` counts the runs in progress from the database**, because with Redis the runs belong to other processes.
  The field is `running`.
- **Text that comes from a target, a test or a skill is shown as text** by every command, so markup in it is never
  interpreted.
- **A server that listens beyond loopback needs an API token** and refuses to start without one; it has no accounts and
  no TLS ([security.md](security.md#web-interface-and-api)).
- **Only Chromium** is driven. Firefox and WebKit were not verified, and a configuration that names them is refused.
- `agentlab fixtures verify` refuses a defect name the example agent does not have, with the names it does have.

## Plug-ins

- **The built-in parts use the registries a plug-in uses.** A plug-in is code that runs in AgentLab's process, so a
  skill that names Python is trusted only when it is built in or arrives as a plug-in
  ([plugins.md](plugins.md#trust)).
- **The default report formats stay `json, md, html, pdf`**; `all` means every installed format, plug-ins included. If
  a plug-in's renderer fails, only its file is skipped, with a warning.
- **A plug-in cannot change what a report says**, only its format ([plugins.md](plugins.md#what-is-not-pluggable)).
- The `s3` artifact store and the `qdrant` vector store are registered placeholders that say they are not implemented.

## Deployment

- **On Railway, one service**: the API, the interface and the jobs together (`queue.backend: inline`, one replica),
  a PostgreSQL service and one volume ([ADR 0007](decisions/0007-railway-one-service-one-volume.md)).
- **Targets on private networks are refused on Railway** (`security.allow_private_networks: false`), because a token
  holder could otherwise point a target at an address only the Railway project can reach, such as a database.
- **The start script runs as root only for one `chown`** (`RAILWAY_RUN_UID=0`, a volume is owned by root), then
  drops to the image's unprivileged user with `no_new_privs`. The Dockerfile has no `VOLUME` instruction, which
  Railway's builder refuses.
- **The server stops with a message that names `AGENTLAB_API_TOKEN`** when it listens beyond loopback without one.
- **It has not been deployed on Railway**, because no account was available: [deployment-railway.md](deployment-railway.md#what-was-verified-and-what-was-not)
  lists what was checked against a container started the way Railway documents and what was not.

## Explicitly unsupported

Reported as such, never faked:

- Voice and audio agents; a WebSocket agent protocol (a target that is only a WebSocket endpoint; a page that uses a
  WebSocket itself is tested); client certificates; sandbox network allow-lists; S3 and Qdrant backends;
  image and OCR understanding unless a multimodal-capable judge or provider is configured; Firefox and WebKit.
- A model-driven browser planner: the browser follows the steps a test lists.
- Operating systems other than Linux.
