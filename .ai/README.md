# .ai/ — Detailed AI Collaboration Guidance

`/AGENTS.md` is the canonical, concise contract. This directory holds the
detail that should not be loaded for every task.

| Path | Purpose |
|---|---|
| `project.json` | Technology metadata and commands (metadata only, not policy) |
| `policies/` | Focused rules: security, git, testing, change control, docs, collaboration |
| `workflows/` | Step-by-step procedures per task type |
| `templates/` | Implementation plan, ADR, handoff, investigation |

## Rules for this directory
- Nothing here may contradict `AGENTS.md`. If it does, `AGENTS.md` wins and the conflict is a bug.
- Do not duplicate content across files; link instead.
- Never store transcripts, secrets, local paths, personal preferences, or local tool config here.
- Changes to `policies/` are governance changes and need human review (see `CONTRIBUTING.md`).

## Vendor-specific adapters
Do not create them by default. If a tool cannot read `AGENTS.md`, add a thin adapter that:
1. is clearly labeled vendor-specific (e.g. `.cursor/rules/`, `.kiro/steering/`, `.kilo/rules/`),
2. only points to `AGENTS.md` and does not copy or alter its policy,
3. is reviewed like code, and
4. is documented in the PR that adds it.

Codex and most other agents read `AGENTS.md` natively. Claude Code users who need a
`CLAUDE.md` should make it a one-line pointer to `AGENTS.md`.
