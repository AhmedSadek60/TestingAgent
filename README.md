# AgentLab

**AI agent testing, evaluation and security.** Point AgentLab at an agent (a repository, an HTTP API, an OpenAPI
description, a web page, an MCP server, documents, a model, or only a description) and it works out what the agent is,
plans tests that fit it, runs them in isolation, evaluates what happened in three layers, and reports what is wrong,
how serious it is, how sure it is, and what it could not test.

```console
$ agentlab test --mock success --intensity quick        # the built-in demo agent: no model, key or service needed
```

* **It plans before it runs.** A test plan is explained: which skills were chosen and why, what each test is for, what
  is predicted to be blocked and what the budget trimmed. `--plan-only` stops there.
* **It judges in three layers.** Deterministic checks decide first; an LLM judge, independent of the agent under test,
  covers what a rule cannot say; trajectory metrics look at the steps taken. A judge-only finding is capped and sent to
  review.
* **Security testing is canary-based, authorised and non-destructive.** Twenty-eight categories of agent risk are
  planned and reported, each as covered, partly covered, not covered (with the reason) or not applicable.
* **Blocked is not failed.** A test that could not run (no sandbox, no credential, no permission) is reported as
  blocked, with the reason, and never counted as a pass or a failure.
* **The agent under test never controls the evaluator.** Everything it says is untrusted data.
* **It says what it did not verify.** Reports separate what was observed from what was inferred and judged, and a
  passing run is not a certificate: it says what ran and what did not.

## Quick start

You need Python 3.12 or newer. From a source checkout:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
agentlab doctor
```

`agentlab doctor` says what works on this machine; what it lacks (Docker, a browser, a model provider) blocks only the
tests that need it. Then, in an empty folder:

```bash
agentlab init
agentlab fixtures serve chatbot --variant flawed &
agentlab fixtures target chatbot --variant flawed > chatbot.yaml
agentlab test --target chatbot.yaml --intensity quick
```

That tests an example agent with defects planted on purpose. It is a stand-in that shows what a finding looks like; it
says nothing about your agent. To test yours, describe it in `target.yaml` or pass flags such as
`--api-url http://localhost:8000/chat`, and start with `--plan-only`: [testing-agents.md](docs/testing-agents.md).

The web interface, served by the same process as the API:

```bash
cd web && npm ci && npm run build && cd ..
agentlab serve                       # http://127.0.0.1:8080
```

## What it can test

| Target | Supported | Notes |
|---|---|---|
| An HTTP API (REST, server-sent events, GraphQL), with or without an OpenAPI description | Yes | A WebSocket agent protocol is **not** supported. |
| A web page of the agent (browser testing) | Yes, Chromium | Firefox and WebKit are not supported. The browser follows the steps a test lists; there is no model-driven browser planner. |
| A command-line agent or a coding agent | Yes, in a Docker sandbox | Never run on the machine itself: without Docker those tests are blocked. |
| An MCP server | Yes | By URL, or started by a command in the sandbox. |
| A model, as the agent | Yes | Through the model providers below. |
| A repository, documents, a description | Yes | Analysed, never executed on the host. |
| Anything else | Through a plug-in | Agent adapters, execution engines, assertions, document parsers, sandbox providers, artifact stores, vector stores, report formats and model providers are registries: [plugins.md](docs/plugins.md). |

Model providers: Gemini, OpenRouter, OpenAI, Anthropic, Ollama, LM Studio, vLLM, llama.cpp, any OpenAI-compatible
endpoint, and a built-in offline mock. **The hosted providers were verified only against local stand-ins of their APIs**
(Ollama was also run against a real server): [providers.md](docs/providers.md).

Reports are written as JSON, Markdown, HTML and PDF, versioned and checksummed. Two runs can be compared, and a person
can review a result without the original evaluation ever being rewritten: [reports.md](docs/reports.md).

Everything above says what was implemented. What was *verified*, how, and what was not (Railway itself, the hosted
providers, other operating systems) is in [docs/development.md](docs/development.md#verification-status).

## Run it as a service

* **Docker:** one image runs the API, the web interface and the worker, with Compose files for Redis and PostgreSQL
  ([installation.md](docs/installation.md#docker)).
* **Railway:** one service from the same Dockerfile, with a PostgreSQL service and one volume. The files are in the
  repository, but **it has not been deployed on Railway**: [deployment-railway.md](docs/deployment-railway.md) says what
  was checked and what was not.

A server that listens beyond loopback needs an API token and refuses to start without one. It has no accounts and no TLS
of its own: read [security.md](docs/security.md#web-interface-and-api) before exposing it.

## Documentation

| Page | What it covers |
|---|---|
| [Installation](docs/installation.md) | Requirements, extras, Docker, the web interface, troubleshooting. |
| [Testing an agent](docs/testing-agents.md) | Describing a target, running, exit codes, reading the result. |
| [Configuration](docs/configuration.md) | Every key of `agentlab.yaml` and every environment variable. |
| [Model providers](docs/providers.md) | Providers, keys, capabilities and how a judge is chosen. |
| [Test case design](docs/test-case-design.md) | The taxonomy, the security categories, plans, budgets and your own tests. |
| [Skills](docs/skills.md) | The built-in skills, how one is chosen, writing and importing your own. |
| [Evaluation](docs/evaluation.md) | Assertions, the judge, trajectory, reliability, scoring, severity, root cause. |
| [Browser testing](docs/browser-testing.md) | The Chromium engine and what it checks. |
| [Security](docs/security.md) | What AgentLab trusts and refuses, canaries, credentials, the sandbox, the API's own controls. |
| [Reports](docs/reports.md) | Formats, comparison of runs, human review, the API for reports. |
| [Plug-ins](docs/plugins.md) | What can be added, how, and what is not pluggable. |
| [Architecture](docs/architecture.md) | The parts, the seventeen phases of a run, the principles. |
| [Deploying on Railway](docs/deployment-railway.md) | The deployment, its variables and what was verified. |
| [Development](docs/development.md) | Setting up, the checks, the tests, and the verification status. |
| [Assumptions](docs/assumptions.md) | What was decided where the requirements left room. |
| [Decisions](docs/decisions/README.md) | Architecture decision records. |
| [References](docs/references.md) | The sources behind the taxonomy and the checks. |

## Working on this repository

This repository follows one contract for people and AI coding agents alike: [AGENTS.md](AGENTS.md). It points to the
policies and workflows in [`.ai/`](.ai/), and everything the team needs to know lives in Git, not in a chat. Changes go
through a pull request with a human review; [CONTRIBUTING.md](CONTRIBUTING.md) has the flow and
[SECURITY.md](SECURITY.md) says how to report a vulnerability.

```bash
python3 scripts/ai/validate_governance.py        # the governance files (Python 3 only)
```

The checks for the code and the pages are in [docs/development.md](docs/development.md#the-checks).

### Worktree workflow

One issue, one branch, one agent per worktree. Parallel work uses `git worktree`, never two agents in one directory:

```bash
git worktree add ../myrepo-123 -b feature/123-auth origin/main   # one developer, any agent
git worktree add ../myrepo-124 -b feature/124-api  origin/main   # another, another agent
git worktree remove ../myrepo-123                                # after the pull request is merged
```

Branches are `feature|fix|refactor|docs|chore/<issue-id>-<short-name>` and commits follow Conventional Commits; the
details are in [`.ai/policies/git.md`](.ai/policies/git.md).

## License

Apache License 2.0: see [LICENSE](LICENSE). Notable changes are listed in [CHANGELOG.md](CHANGELOG.md).
