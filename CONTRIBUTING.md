# Contributing

Goal: quality and traceability, not process for its own sake. Every step below exists
to catch real problems.

**AI-generated code is not automatically trusted.** The developer submitting a PR is
responsible for understanding and reviewing the final change, whichever tool wrote it.

## Normal workflow
1. **Issue** — open or pick an issue (use a template; include acceptance criteria).
2. **Branch** — `feature|fix|refactor|docs|chore/<issue-id>-<short-name>` from up-to-date `main`.
3. **Implement** — small, focused commits (Conventional Commits).
4. **Test** — run the project's real checks; add tests for behavior changes.
5. **PR** — fill the template honestly; link the issue.
6. **Review** — at least one human reviewer approves; address feedback.
7. **Merge** — a human merges via the PR once checks pass.

## AI-assisted workflow
1. **Issue** — preferably the *AI-assisted task* template, so context is not trapped in a private chat.
2. **Inspect** — let the agent explore the code and discover the real toolchain.
3. **Read `AGENTS.md`** — your agent must follow it regardless of vendor.
4. **Plan** — classify TRIVIAL / STANDARD / HIGH RISK; HIGH RISK needs a written plan and approval before irreversible steps.
5. **Implement** — on your own branch/worktree; one agent per worktree.
6. **Verify** — run checks yourself or confirm the agent really ran them; never accept "should work".
7. **Review the diff** — read every changed line; remove unrelated edits and stray files; check for secrets.
8. **Commit** — only task-related changes.
9. **PR** — disclose AI assistance in the template (optional but encouraged); state verification status accurately.
10. **Human review** — an independent reviewer approves. An agent never approves or merges its own work.

Parallel work: use `git worktree` (see [README](README.md#worktree-workflow)).
Durable knowledge (decisions, architecture, repeated corrections) goes into the repo via PR, not into chat.

## Governance changes
These files are governance: `AGENTS.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.ai/policies/*`,
`.github/workflows/ai-governance.yml`, and CODEOWNERS configuration. Changes need review by
another team member (enforce with CODEOWNERS). AI agents may propose such changes; humans approve them.

## Validation
`python scripts/ai/validate_governance.py` must pass. It checks governance files only; your project's
own build/test CI is separate and project-specific.

## Security
Never commit secrets. Report vulnerabilities privately per [SECURITY.md](SECURITY.md).
