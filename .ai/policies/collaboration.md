# Collaboration Policy

## Principle
AI conversation memory is NOT repository memory. Developers using different agents
collaborate through Git, issues, PRs, ADRs, docs, commits, and test results.

## Roles
- AI agent: implementation assistant.
- Developer: owner of the change; understands and reviews everything they submit.
- Reviewer: independent quality gate.
- Repository: durable knowledge. GitHub: collaboration and review state.
- AI conversation: temporary working context.

## Task context lives in the issue
Use the `ai-assisted-task` issue template: objective, context, acceptance criteria,
constraints, expected validation, risk, relevant files. A teammate with a different
agent must be able to pick the task up from the issue alone.

## Parallel work
- Separate branch and separate `git worktree` per task.
- Never two agents in one worktree; never edit another's worktree.
- Avoid overlapping files; if overlap is unavoidable, agree order in the issues first.

## Handoffs
Use `../templates/handoff.md`: state, done, remaining, decisions, how to verify, gotchas.
Put it in the PR or issue, not in private chat.

## Never store in the repo
Transcripts, session dumps, API keys, tokens, passwords, private local paths,
personal preferences, hidden agent memory, local machine/MCP/IDE config.

## Improving guidance
If an agent repeats the same mistake, fix the repo guidance via PR (nested
`AGENTS.md` for subsystem rules; `.ai/` for procedures) rather than re-correcting in chat.
