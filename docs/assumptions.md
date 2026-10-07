# Assumptions and defaults

The requirements leave room in many places. Where they fork, AgentLab picks a safe default, records it here
and keeps it configurable where that makes sense. Nothing in this file is a claim that a feature has been
verified; see the pull request and `docs/testing-agents.md` for verification status.

## Layout and tooling

- **One Python package, `agentlab`, in a `src/` layout** instead of the suggested `apps/` + `packages/`
  monorepo. The seams are plug-in registries (`agentlab.<kind>` entry points), not directories, which keeps
  imports simple and the wheel installable. The web UI lives in `web/`.
- **Built-in skills ship inside the package** (`src/agentlab/skills/library/<name>/`) so a wheel install works.
  The root `skills/` directory is for user-authored skills (`skill_dirs` in `agentlab.yaml`).
- **Python 3.12**, FastAPI, SQLAlchemy 2 + Alembic, httpx, Typer, Playwright, pydantic v2. React + TypeScript
  (Vite) for the UI.
- **Line length 120, `ruff format`**, mypy on `src`.

## Storage

- Default database is **SQLite** at `.agentlab/agentlab.db`; PostgreSQL is supported through
  `storage.database_url` and the same Alembic migrations.
- Artifacts are stored on the **local filesystem** by default, content-addressed, with secrets redacted before
  writing. S3-compatible storage is **not implemented** (selecting `s3` says so); a plug-in can provide any
  other store through `storage.artifact_store` ([plugins.md](plugins.md#artifact-stores)).
- Vector storage is an interface with an in-process cosine store and a `pgvector` adapter that nothing in the
  evaluation pipeline uses yet. `pgvector` is wired but **not verified**; **Qdrant is unsupported** (a registered
  placeholder says so).
- Queue backend is **inline** by default; a Redis worker is optional.

## Security defaults

- **Network is denied by default** for sandboxed targets (`--network none`). `internal` (a private,
  non-routable Docker network) is available; the `allowlist` mode is reported as unsupported rather than
  approximated (it would need an egress proxy).
- **If Docker is unavailable, anything that would execute untrusted code is blocked**, never run on the host.
- Cloud metadata endpoints (`169.254.169.254` and friends) are always blocked by the egress policy.
  Private networks are allowed by default because most targets under test are local; this is configurable.
- A target is **never assumed to be authorised**: adversarial tests against a non-local target need
  `safety.authorization_note`; high-impact tests additionally need a disposable-environment declaration;
  production targets are blocked unless explicitly allowed.
- Secrets are only ever handled as references (`env:NAME`, `secret:alias`). The LLM judge never receives
  credentials. Canary values are synthetic, per-run and unique.
- AgentLab's own tests never use real provider credentials. Hosted-provider adapters are **contract-tested
  against fake servers**; only the local Ollama adapter is verified against a real model.

## Evaluation defaults

- A test with only LLM-judge criteria is **BLOCKED** when no judge is configured (not failed, not passed).
- Functional tests run once; reliability tests repeat 3 times; the count is configurable per risk class.
  A test that passes only some repetitions is reported as flaky, never as a plain pass.
- Scores: categories with no applicable tests are **N/A and excluded** (weights are redistributed), security
  findings cap the overall score (critical 40, high 65, medium 85), and a leaking agent can never receive
  an unqualified top grade.
- Severity is computed from nine explainable factors, with escalation and adjustment rules; confidence is
  reported separately from severity.
- Cost is computed from configurable per-model pricing; an unpriced model reports cost 0 with an
  "unpriced" note rather than a guess.

## Explicitly unsupported (reported as such, never faked)

- Voice/audio agents; WebSocket and GraphQL transports for API targets; sandbox network allow-lists;
  S3 and Qdrant backends; image/OCR understanding unless a multimodal-capable judge/provider is configured;
  Firefox/WebKit (Chromium only in the build environment).
