<div align="center">

# agent-starter

### One engineering contract for every AI coding agent on your team.

**Claude Code, Codex, Cursor, Kilo Code, Kiro, Windsurf, Copilot. Same rules, same workflow, no shared chat.**

[![CI](https://img.shields.io/github/actions/workflow/status/AhmedSadek60/agent-starter/ai-governance.yml?branch=main&style=flat-square&label=CI)](https://github.com/AhmedSadek60/agent-starter/actions/workflows/ai-governance.yml)
[![Contract: AGENTS.md](https://img.shields.io/badge/contract-AGENTS.md-5c4ee5?style=flat-square)](AGENTS.md)
[![Validator: Python 3 stdlib](https://img.shields.io/badge/validator-Python_3_stdlib-3776ab?style=flat-square&logo=python&logoColor=white)](scripts/ai/validate_governance.py)
[![Vendor neutral](https://img.shields.io/badge/AI_vendor-neutral-2ea44f?style=flat-square)](#why-agent-starter)

[Quick start](#quick-start) · [Collaboration model](#the-collaboration-model) · [What's inside](#whats-inside) · [Current status](#current-status) · [How it works](#how-it-works) · [FAQ](#faq)

<code>python scripts/ai/validate_governance.py</code>

<br />

</div>

---

Your team uses more than one AI coding agent. Without a shared contract, each
tool gets its own copy of the rules, the copies drift apart, and the real context
lives in private chats nobody else can see.

agent-starter is a template for fixing that. It gives every agent one file to
follow, `AGENTS.md`, and puts everything else the team needs into Git: policies,
workflows, templates, decisions and task context. It has no application code and
assumes no language, framework, cloud or AI vendor.

## Just ask your agent

Copy this repo into your project (see [Quick start](#quick-start)), then give any
agent a task like this:

```text
Read AGENTS.md, then work on issue 123. Plan first, keep the diff focused, run the
project's real checks, and report exactly what you verified and what you did not.
```

It works the same in Claude Code, Codex, Cursor, Kilo Code, Kiro, Windsurf or
Copilot, because they all read the same file.

## The collaboration model

<table>
<tr>
<td width="33%" valign="top">
<strong>One contract</strong><br /><br />
<code>AGENTS.md</code> is the only instruction file, kept to 200 lines. Detail lives in <code>.ai/</code> and <code>docs/</code> and is linked, not copied.
</td>
<td width="33%" valign="top">
<strong>One task, one lane</strong><br /><br />
One issue, one branch, optionally one worktree, one primary agent per worktree. Agents never share a working directory.
</td>
<td width="33%" valign="top">
<strong>Repository is memory</strong><br /><br />
Chat history stays local. Decisions, architecture and task context are written to Git and GitHub where the whole team can see them.
</td>
</tr>
</table>

The agent assists, the developer owns the change, and a reviewer is the
independent gate. agent-starter does not treat AI output as trusted.

## Why agent-starter

|                            |                                                                                                   |
| -------------------------- | ------------------------------------------------------------------------------------------------- |
| **Vendor neutral**         | Switch tools, or use all of them at once. The repository policy is the contract, not the tool.    |
| **No duplicated rules**    | One `AGENTS.md`. Tool-specific files are allowed only as short pointers, and CI enforces it.      |
| **Safe by default**        | No force pushes, no direct pushes to `main`, no secrets, explicit human approval for risky work.  |
| **Scales with the repo**   | Add nested `AGENTS.md` files for `backend/`, `frontend/` or `infrastructure/` without bloat.      |
| **Honest reporting**       | Agents must say what they ran, what they did not run, and mark results verified or not verified. |
| **Works with legacy code** | Adds alongside what you have. It never re-tools or modernizes code the task does not touch.       |

## Quick start

### Use the template (recommended)

Click **Use this template** on GitHub, or copy the files into an existing
repository without overwriting anything. Then:

1. Edit `.ai/project.json`: fill in the project and technology fields, and only
   commands you have actually run. Leave unknown commands as `null`.
2. Set `"templateMode": false`.
3. Copy `.github/CODEOWNERS.example` to `.github/CODEOWNERS` with real owners.
4. Add a security contact to `SECURITY.md`.
5. Describe the architecture in `docs/architecture/README.md`.

### Check the setup

```bash
python scripts/ai/validate_governance.py
```

It needs Python 3 and nothing else. It prints readable errors and exits with
code 1 on a real problem. The same check runs in CI as `validate-governance`,
with a read-only token. It never runs commands from `project.json`; your
project's own build and test pipeline stays separate.

### Work in parallel with worktrees

```bash
git worktree add ../myrepo-123 -b feature/123-auth origin/main   # Dev A, any agent
git worktree add ../myrepo-124 -b feature/124-api  origin/main   # Dev B, another agent
git worktree remove ../myrepo-123                                # after the PR is merged
```

Branches are `feature|fix|refactor|docs|chore/<issue-id>-<short-name>` and
commits follow Conventional Commits. Details are in
[`.ai/policies/git.md`](.ai/policies/git.md).

## What's inside

| Path | Purpose |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | The canonical agent contract. |
| [`.ai/project.json`](.ai/project.json) | Stack and commands for this project. Metadata only, not policy. |
| [`.ai/policies/`](.ai/policies/) | Security, git, testing, change control, documentation, collaboration. |
| [`.ai/workflows/`](.ai/workflows/) | Task execution, features, bug fixing, refactoring, code review, incidents. |
| [`.ai/templates/`](.ai/templates/) | Implementation plan, ADR, handoff, investigation. |
| [`docs/`](docs/) | `architecture/`, `decisions/` (ADRs), `development/`. |
| [`scripts/ai/`](scripts/ai/) | The governance validator. |
| [`.github/`](.github/) | PR template, issue templates (including AI-assisted task), CODEOWNERS example, governance workflow. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md) | Human and AI contribution flow, vulnerability reporting. |

## Current status

| Area | State |
| --- | --- |
| `AGENTS.md`, policies, workflows, templates | Included. |
| PR and issue templates, CODEOWNERS example | Included. |
| Governance validator and CI workflow | Included and passing. |
| Branch protection, secret scanning, push protection | **Not applied by this repo.** Set them in GitHub, see the checklist below. |
| Tool-specific adapters (`.cursor/`, `.kiro/`, `.kilo/`) | None by design. Add only when a tool cannot read `AGENTS.md`. |
| Project commands, architecture, security contact | `TBD` until your team fills them in. |

### GitHub settings checklist

These are repository settings that files cannot apply. Set them under
**Settings** and do not assume they are on.

- [ ] Default branch is `main`.
- [ ] Branch protection on `main`: PR required, at least one approval, required
      check `validate-governance`, no force pushes, no deletion.
- [ ] Code owner review required, after creating `.github/CODEOWNERS`.
- [ ] Secret scanning and push protection on.
- [ ] Dependabot alerts and security updates, if they fit your stack.
- [ ] Private vulnerability reporting on.
- [ ] Default workflow token permissions set to read-only.

## How it works

Every agent follows the same six phases from `AGENTS.md`:

1. **Understand**: read the issue, the code and the applicable `AGENTS.md` files.
2. **Plan**: classify the task as trivial, standard or high risk. High risk work
   needs a written plan and a named human approval before anything irreversible.
3. **Implement**: the smallest safe change, using existing patterns.
4. **Verify**: run the project's real checks, never assumed ones.
5. **Review**: read `git diff` and `git status` for scope and secrets.
6. **Report**: what changed, what was run, what was not, risks and follow-ups.

Agents stop and ask a human before destructive data operations, production
changes, real credentials, auth or security-control changes, irreversible
migrations, force pushes, repository security settings, wider CI permissions,
bypassing failing checks, or anything outside the task. The full list is in
`AGENTS.md`.

### Customizing

| To do this | Do this |
| --- | --- |
| Add a project rule | If it applies to every task, add it to `AGENTS.md`. Otherwise add a file under `.ai/policies/` or `.ai/workflows/` and link it. |
| Add rules for one folder | Add a nested `AGENTS.md`, for example `backend/AGENTS.md`, with only the extra rules for that folder. Never copy the root file. It cannot weaken security or git rules. |
| Record a decision | Copy `.ai/templates/adr.md` to `docs/decisions/NNNN-short-title.md`, add it to the index, get it reviewed. |
| Support a tool that ignores `AGENTS.md` | Add a thin pointer (for example `.cursor/rules/`) that is labeled vendor-specific and reviewed like code. The validator rejects adapters over 15 lines. |

Changes to `AGENTS.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.ai/policies/` and the
governance workflow count as governance changes and need review by another
team member.

### Never commit

Secrets, API keys, tokens, passwords, certificates, `.env` files, cloud or SSH
credentials, AI chat transcripts or session dumps, local MCP, IDE or agent
configuration, private local paths, personal preferences, and customer or
business data. `.gitignore` covers the common cases.

## FAQ

<details>
<summary><strong>Why not keep a rules file per tool?</strong></summary>

Copies drift and contradict each other, and every change has to be made once per
tool. One `AGENTS.md` is read natively by Codex, Cursor, Copilot, Kilo Code,
Windsurf and others, and the rest can be pointed at it.

</details>

<details>
<summary><strong>My tool does not read AGENTS.md. What do I do?</strong></summary>

Add a short adapter that points to `AGENTS.md`, for example a one-line
`CLAUDE.md`. Label it vendor-specific, keep it under 15 lines, and review it
like code. The validator fails on adapters that copy policy.

</details>

<details>
<summary><strong>Can agents merge their own PRs?</strong></summary>

Not by default. A human reviews and merges, unless a human explicitly tells an
agent to. AI-generated code is not automatically trusted, and the developer who
submits a PR is responsible for understanding it.

</details>

<details>
<summary><strong>Does it work on a legacy codebase?</strong></summary>

Yes. Add the files alongside the existing ones. The rules tell agents not to
modernize unrelated code, upgrade dependencies because they are old, or replace
your toolchain.

</details>

<details>
<summary><strong>The validator failed. What now?</strong></summary>

Read the message, it names the file. Common causes: `AGENTS.md` over 200 lines
(move detail into `.ai/`), a vendor file that does not point to `AGENTS.md`,
`templateMode` false with the project name still `TBD`, placeholder owners left
in `CODEOWNERS`, a possible secret (remove it and revoke the credential), or a
broken relative link.

</details>

## Development

Governance changes go through a PR and need another person's review. Run
`python scripts/ai/validate_governance.py` before you push. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Project links

- [AGENTS.md](AGENTS.md): the agent contract
- [CONTRIBUTING.md](CONTRIBUTING.md): human and AI contribution workflow
- [SECURITY.md](SECURITY.md): reporting a vulnerability
- [Architecture decisions](docs/decisions/README.md)
