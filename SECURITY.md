# Security Policy

## Reporting a vulnerability
Do not open a public issue for security problems.

Preferred: use GitHub's **private vulnerability reporting** ("Security" tab ->
"Report a vulnerability") if enabled on this repository.
Otherwise contact the maintainers privately: **TBD — add a security contact email
or channel when adopting this template.**

Include: affected component/version, reproduction steps, impact, and any suggested fix.
Do not include real secrets or customer data in the report.

Expected handling: acknowledgement, triage, fix, coordinated disclosure.
Response-time targets: TBD by the project.

## Supported versions
TBD by the project.

## What is in scope
AgentLab sends requests to other systems, starts containers and a browser, and stores credentials, so a flaw in its own
controls matters: the authorization gate, the sandbox, redaction and canaries, the egress checks, the credential store,
and the token and origin checks of the API. [docs/security.md](docs/security.md) says what each control does, which test
exercises it, and what AgentLab does **not** protect against. Nobody outside the project has audited it.

## If you find an exposed secret
Tell the maintainers privately and immediately; the secret must be revoked/rotated
by its owner. Removing it from Git history alone is not sufficient.

## AI-assisted development
Rules for agents and developers are in `AGENTS.md` and `.ai/policies/security.md`.
Do not paste secrets or sensitive business data into AI tools.
