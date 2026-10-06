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
  OpenRouter, OpenAI and Anthropic adapters are contract-tested against fake servers only.

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
| Execution | `adapters`, `execution`, `sandbox`, `browser` | adapters (mock, llm, http, openapi, command, mcp, web), engines (conversation, workspace, browser), Docker sandbox, limits, scheduler |
| Evaluation | `evaluation` | 44 deterministic assertions, trajectory metrics, judge engine, reliability, severity, root cause, scoring profiles, findings |
| Orchestration | `orchestrator` | the 17 phases, events, persistence, cancellation |
| Interfaces | `cli`, `api`, `web/` | Typer CLI, FastAPI, React + TypeScript UI |
| Reports | `reports` | JSON, Markdown, HTML, PDF; versioned; checksummed artifact bundle |

Delivery follows the development phases of the requirements: (1) core, providers, models, basic evaluator,
CLI; (2) analyzers, skills, generation; (3) tool/RAG/memory/agent evaluations; (4) browser engine;
(5) adversarial engine; (6) reports/dashboard; (7) regression/comparison; (8) multi-agent and MCP.

## Alternatives considered

See ADR-0001 (monorepo layout), ADR-0002 (fail-closed isolation), ADR-0003 (skills trust model).

## Affected components and files

All new. The only existing files touched are `README.md`, `.ai/project.json` (template mode off, real
commands) and `.gitignore` (local state directories).

## Verification plan (commands, tests, manual checks)

```
python -m pytest                       # unit, integration, e2e, security
ruff check . && ruff format --check .
mypy src
python scripts/ai/validate_governance.py
```

Docker-, browser-, PostgreSQL-, Redis- and Ollama-dependent tests carry pytest markers and are skipped
(never silently passed) when the dependency is missing. Acceptance scenarios 1–12 and the final audit
checklist are re-run at the end and recorded in the pull request with their real outcome.

## Risks, rollback, migrations

- New code only; rollback is reverting the pull request.
- Database: Alembic migrations create all tables; verified on SQLite and PostgreSQL 16. No data exists yet.
- Highest-risk surfaces: sandbox escape, secret leakage, prompt injection reaching the evaluator, and
  accidental attack of third-party systems. Mitigations are listed in `docs/security.md` and covered by
  the security test suite.

## Irreversible steps and human approval point

None are performed by this change. Merging the pull request is the human approval point. Nothing in the
change deletes data, alters infrastructure or touches CI permissions beyond adding a read-only workflow
(`contents: read`), which is called out in the pull request.

## Open questions / assumptions

Recorded in [`../assumptions.md`](../assumptions.md).
