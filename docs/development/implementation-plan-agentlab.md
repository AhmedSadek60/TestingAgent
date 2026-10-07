# Implementation Plan: AgentLab — AI agent testing, evaluation and security platform

- Issue: requested in the project thread (no GitHub issue)
- Author / agent: Claude Code (session linked in the pull request)
- Risk class: HIGH RISK (new architecture, database schema, credential handling, security controls, public API)
- Date: 2026-10-05

## Objective

Build AgentLab: a production-grade, extensible platform that ingests an arbitrary AI agent (repository,
HTTP API, OpenAPI document, MCP server, web UI, documents, plain description, optional credentials),
fingerprints it, selects versioned skills, generates an explainable test plan, runs it safely, evaluates
the traces in three layers (deterministic, LLM judge, trajectory), scores and explains the results, and
produces reports, a REST API, a CLI and a web UI.

## Context and constraints

- The full requirements live in the project thread (66 sections). They are not repeated here; every
  assumption taken where they leave room is recorded in [`assumptions.md`](../assumptions.md).
- Governance: `AGENTS.md` applies. No real credentials are used by AgentLab's own tests (§13), no secrets
  are committed or logged (§9), CI permissions are not widened (§6).
- Principle: the target agent must never control the evaluator. Target output is untrusted data.
- Honesty rule: a capability is either implemented and tested, or explicitly reported as unsupported.
- Environment used to build and verify: Linux container, Python 3.12, Docker, Chromium (Playwright),
  PostgreSQL 16, Redis and a small local Ollama model. No hosted-provider API keys exist, so the Gemini,
  OpenRouter, OpenAI and Anthropic adapters are contract-tested against fake servers only. No Railway account
  was available, so the Railway files are checked against a container started the way Railway documents.
- Added to the request while it was built: a deployment on Railway (back end and web interface), and the plug-in
  architecture of the requirements, section 43, as registries for every kind of part.

## Current behavior / relevant code

The repository contained only the governance template (`AGENTS.md`, `.ai/`, validator, CI workflow).

## Proposed approach

Single Python package `agentlab` (src layout) with explicit seams (plug-in registries) so each concern can
be replaced without touching orchestration code:

| Layer | Package | Notes |
| --- | --- | --- |
| Core | `core` | typed pydantic models, enums, config, errors, plug-in `Registry` |
| Providers | `providers` | Mock, OpenAI-compatible (OpenAI, OpenRouter, LM Studio, vLLM, llama.cpp), Gemini, Anthropic, Ollama; capability negotiation |
| Security | `security` | credential manager (Fernet), secret redactor, canaries, egress policy, authorization gate, untrusted-content wrapping |
| Analysis | `repository`, `documents`, `discovery` | static analysis only; safe ingestion; provenance on every item; fingerprinting |
| Skills & design | `skills`, `design` | declarative skills (YAML + SKILL.md), trusted built-in generators, explainable test plan |
| Execution | `adapters`, `execution`, `sandbox`, `browser` | adapters and engines (both registries), the Docker sandbox, limits, scheduler |
| Evaluation | `evaluation` | deterministic assertions (a registry; [evaluation.md](../evaluation.md#deterministic-assertions) lists them), trajectory metrics, judge engine, reliability, severity, root cause, scoring profiles, findings |
| Orchestration | `orchestrator` | the 17 phases, events, persistence, cancellation |
| Interfaces | `cli`, `api`, `web/` | Typer CLI, FastAPI, React + TypeScript UI |
| Reports | `reporting` | JSON, Markdown, HTML and PDF renderers (a registry: a plug-in can add a format); versioned; checksummed artifact bundle |
| Storage | `storage` | SQLAlchemy models and Alembic migrations (SQLite, PostgreSQL), artifact stores and vector stores (registries) |
| Deployment | `docker/`, `railway.json` | one image for the API, the interface and the jobs; Compose with Redis and PostgreSQL; one Railway service ([ADR 0007](../decisions/0007-railway-one-service-one-volume.md)) |

Delivery follows the development phases of the requirements: (1) core, providers, models, basic evaluator,
CLI; (2) analyzers, skills, generation; (3) tool/RAG/memory/agent evaluations; (4) browser engine;
(5) adversarial engine; (6) reports/dashboard; (7) regression/comparison; (8) multi-agent and MCP.

## Alternatives considered

See [the decision records](../decisions/README.md): one package with plug-in registries (0001), fail-closed isolation (0002),
the skills trust model (0003), one origin for the interface and signed report links (0004), BLOCKED is not FAILED and an
independent judge (0005), reviews never rewrite the evaluation (0006), and one service with one volume on Railway (0007).

## Affected components and files

All new, apart from existing files that were changed: `README.md` (replaced), `CONTRIBUTING.md` and `SECURITY.md`
(additions only), `.ai/project.json` (template mode off, real commands), `.gitignore` (local state directories) and the
two template stubs `docs/architecture/README.md` and `docs/development/README.md`. `CONTRIBUTING.md` and `SECURITY.md`
are governance files and need a human's review. A `LICENSE` (Apache-2.0, as `pyproject.toml` declares) and a
`CHANGELOG.md` are added.

## Verification plan (commands, tests, manual checks)

```
pytest tests -q -m "not docker and not browser and not postgres and not redis and not ollama and not matrix"
ruff check . && ruff format --check .
mypy src
python scripts/export_openapi.py --check
python3 scripts/ai/validate_governance.py
cd web && npm test && npm run build
```

The commands, the markers and what each needs are in [development.md](../development.md).

Docker-, browser-, PostgreSQL-, Redis- and Ollama-dependent tests carry pytest markers and are skipped
(never silently passed) when the dependency is missing; they are run separately where the dependency exists.
Acceptance scenarios 1–12 and the final audit checklist are re-run at the end and recorded in the pull request
with their real outcome. What could not be verified (Railway itself, the hosted model providers) is listed in
[development.md](../development.md#verification-status).

## Risks, rollback, migrations

- New code only; rollback is reverting the pull request.
- Database: Alembic migrations create all tables; verified on SQLite and PostgreSQL 16. No data exists yet.
- Highest-risk surfaces: sandbox escape, secret leakage, prompt injection reaching the evaluator, and
  accidental attack of third-party systems. Mitigations are listed in `docs/security.md` and covered by
  the security test suite.
- The server can be put on the public internet (Railway). Its only authentication is a token; see
  [deployment-railway.md](../deployment-railway.md#security).

## Irreversible steps and human approval point

None are performed by this change. Merging the pull request is the human approval point. Nothing in the
change deletes data or alters infrastructure, and nothing is deployed. It adds a CI workflow, `.github/workflows/ci.yml`,
with `permissions: contents: read` and no secrets, and edits two governance files (`CONTRIBUTING.md`, `SECURITY.md`),
all called out in the pull request.

## Open questions / assumptions

Recorded in [`../assumptions.md`](../assumptions.md).
