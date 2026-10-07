# Changelog

All notable changes to this project are written here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

AgentLab 0.1.0 is the version `pyproject.toml` declares. Nothing has been tagged or published: this is what the first
pull request adds to the governance template the repository started as.

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
- Files for one Railway service with a PostgreSQL service and one volume (`railway.json`, `docker/railway-start.sh`,
  `docker/agentlab.railway.yaml`). **It has not been deployed on Railway.**

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
- The Railway files and the `CI` workflow have not been run on Railway and GitHub. The image cannot run Docker
  containers or a browser, so those tests are blocked there.
- Linux is the only platform it was run on, and nobody outside the project has audited it for security.

What was verified, how, and what was not is in [docs/development.md](docs/development.md#verification-status).
