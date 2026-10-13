# Changelog

All notable changes to this project are written here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

AgentLab 0.1.0 is the version `pyproject.toml` declares. Nothing has been tagged or published: this is what the first
pull request adds to the governance template the repository started as.

### Added

* **Diagnostics for issue reports** ([feedback-support.md](docs/feedback-support.md#diagnostics)): what AgentLab can really
  supply (environment, application and backend logs, errors, a run's metadata, a test's result and trace, what the browser
  saw), collected only when asked for, from the person's own requests and, for a maintainer, a named run; masked, previewed,
  attached with consent as the JSON value `true`, downloadable as a ZIP with a manifest. A bounded, masked log of warnings,
  errors and failed or slow requests is kept for it, tied to runs and requests; every response carries an `X-Request-Id`.
  Migration `0005`.
* **Attachments and the DiagnosticRedactor** ([feedback-support.md](docs/feedback-support.md#attachments)): files on a submission,
  checked by content, archives refused when hostile, kept privately under a hash, text files given a sanitized copy with the
  secrets masked and a record of what was masked and where, a malware scan when a scanner is configured and an honest
  `not_scanned` when not. Migration `0004`. The redaction patterns are held to linear time by a test, and the existing
  `private_key` and `connection_string` patterns were made linear.
* **Feedback & Support, core** ([feedback-support.md](docs/feedback-support.md)): feature requests and issue reports through the
  API, with numbers (`FR-2026-00042`, `IR-2026-00042`), a configurable workflow and status history, comments and internal notes,
  assigned priority and severity kept apart from what the reporter asked for, labels, assignment, links between submissions,
  an unread marker, duplicate suggestions, private security reports, daily allowances, optimistic concurrency on triage and an
  audit trail. A reporter sees only their own and nothing of the triage. Migration `0003`. The web pages come in a later slice.

* **Accounts** beside the shared API token ([accounts.md](docs/accounts.md), [ADR 0011](docs/decisions/0011-accounts-beside-the-shared-token.md)):
  registration, sign-in with a password, server-side sessions with CSRF protection, the roles `reporter`, `maintainer` and `admin`,
  account administration in the web interface and API, `agentlab users` on the server, and an audit trail. A reporter reaches
  Feedback & Support only; the shared token and maintainers keep what they had. Migration `0002`.

* `scripts/ship.py`, the staged release process: `check` (what CI runs, locally), `pr`, `wait`, `merge` (only when every check on the
  exact head is green) and `publish` (backup, build, smoke test, rollback) ([ADR 0010](docs/decisions/0010-staged-release-process.md)).

### Removed

* The deployment files for a single hosted-container service with one volume (its configuration, start script, runtime
  configuration, guide and decision record). A server is deployed with the Compose files in `docker/` and
  `docs/deployment-vps.md`; the image no longer contains a start script for a platform.

### Fixed

* Web targets: a page that cannot be opened, a control that cannot be clicked, or no answer within the wait now end the
  test in ERROR (not scored) instead of failing its checks on an empty reply, and a screenshot of the page is kept.
  `web.reply_timeout_seconds` (default 60, at most 600) sets how long to wait for an answer.
* Any browser test whose step could not be done (a timeout, a missing element, no reply, a page that did not open) now ends in
  ERROR, not in a failed check on whatever was read; this includes tests with their own `browser_steps`.
* Cookie and consent dialogs are closed before each message (`web.consent: reject|accept|off`, `web.dismiss_selectors`), a page
  whose load event never fires is used once its document is ready, and `web.navigation_timeout_seconds` sets the wait to open it.
* Set-up errors are no longer counted against an agent's reliability; a grade built on fewer than half of the planned tests says
  how many ran; blocked tests are described as a policy block or a missing prerequisite; a web target gets a latency budget of at
  least 30 seconds.
* Web targets: a status line ("Processing", "Thinking…") or a visible *Stop* button is no longer read as the answer; the
  labels of the page's own buttons are removed from it; `web.busy_selector` names an element that is visible while the page
  is still writing. Discovery probes wait longer than the target's own reply wait.
* A form login on a single-page application is no longer reported as failed because the form was still showing a moment
  after the click.

### Added

**Testing an agent**

- Targets: an HTTP API (REST, server-sent events or GraphQL) with or without an OpenAPI description, a web page, a
  command-line or coding agent run in a Docker sandbox, an MCP server, a model, a repository, documents, and a plain
  description. A target is fingerprinted before anything is planned.
- Thirty built-in skills, each a `skill.yaml` and a `SKILL.md` with a version; your own skills, imported skills, and
  drafts for a kind of agent no skill covers (`agentlab skills new`, `import`, `forge` and `promote`).
- An explainable test plan over a taxonomy of seventeen areas (A to Q), which includes twenty-eight canary-based,
  authorised and non-destructive security categories (N1 to N28), with limits on tests, steps, tokens, cost and time.
  `--plan-only` stops at the plan.
- A run in seventeen phases that executes the plan safely, with a Docker sandbox that denies the network by default and
  refuses to run untrusted code on the host. A test that cannot run is **blocked**, with the reason, and is never
  counted as a pass or a failure.
- Tests of your own, written in YAML.
- A Chromium engine that follows the steps a test lists and checks what the page shows.

**Evaluating**

- Three layers: deterministic assertions (a registry, so a plug-in can add one), an LLM judge that is independent of the
  agent under test, and trajectory metrics. A finding that only the judge supports is capped and sent to review.
- Scoring profiles, severity computed from explained factors, root-cause classification, confidence reported apart
  from severity, repeated runs for reliability, and a comparison of two runs.
- Human review that records a person's opinion beside the evaluation and never rewrites it.
- Model providers: Gemini, OpenRouter, OpenAI, Anthropic, Ollama, LM Studio, vLLM, llama.cpp, any OpenAI-compatible
  endpoint, and an offline mock.

**Reports**

- JSON, Markdown, HTML and PDF reports of twenty-seven sections, versioned and checksummed, with secrets redacted and
  restricted evidence left out unless asked for.

**Interfaces**

- A command line (`agentlab`) with documented exit codes, a REST API described by OpenAPI, and a web interface (React,
  TypeScript, Vite) that `agentlab serve` serves from the API's own origin.
- A job queue that runs in the process by default, and a Redis queue with `agentlab worker`.

**Storage**

- SQLite by default and PostgreSQL 16, with one Alembic migration; artifacts on the local filesystem, content-addressed;
  an in-process vector store and a pgvector one (no run uses a vector store yet).
- A credential store that encrypts what it keeps and scopes it to hosts.

**Plug-ins**

- Registries for model providers, agent adapters, execution engines, sandbox providers, assertions, artifact stores,
  vector stores, document parsers and report formats, filled by entry points, by the `plugins:` list of the
  configuration, or by code; `agentlab plugins list` shows what is installed.

**Deployment**

- One Docker image for the API, the interface and the jobs, with Compose files for Redis and PostgreSQL.

**Example agents**

- Twelve disposable example agents, each correct and flawed, with the defects planted on purpose, and
  `agentlab fixtures verify`, which checks that every planted defect is found and nothing else is.

**Repository**

- Documentation for every part (`docs/`), architecture decision records 0001 to 0007 (all *Proposed*), and a test that
  compares the pages with the code.
- A `CI` workflow (`.github/workflows/ci.yml`) that only reads the repository, an Apache-2.0 `LICENSE`, and this file.

### Changed

- `README.md` describes AgentLab instead of the template. `CONTRIBUTING.md` and `SECURITY.md` have a section added and
  nothing removed, and `.ai/project.json` is out of template mode. The security contact in `SECURITY.md` is still to be
  decided by the maintainers.

### Known limitations

- A WebSocket agent protocol, Firefox and WebKit, a model-driven browser planner, an allow-list network for the sandbox,
  client certificates, and the S3 and Qdrant backends are **not supported**, and AgentLab says so where one is asked for.
- The hosted model providers (Gemini, OpenRouter, OpenAI, Anthropic) were run only against local stand-ins of their
  APIs; Ollama was also run against a real server.
- The image cannot run Docker containers or a browser, so those tests
  are blocked there.
- Linux is the only platform it was run on, and nobody outside the project has audited it for security.

What was verified, how, and what was not is in [docs/development.md](docs/development.md#verification-status).
