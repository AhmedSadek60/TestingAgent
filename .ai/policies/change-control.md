# Change Control Policy

## Task classification
- **TRIVIAL** — one small localized change; no architecture, schema, security, or public-API impact. Proceed directly.
- **STANDARD** — multiple files or meaningful behavior change. Lightweight plan when useful.
- **HIGH RISK** — architecture; database/schema migrations; authn/authz; security controls;
  production infrastructure; public API contracts; dependency strategy; CI/CD permissions;
  destructive operations; data deletion; major refactors.
  Requires a formal plan (`../templates/implementation-plan.md`) and a named human
  approval point before any irreversible step.

When unsure between two classes, choose the higher one.

## Scope discipline
- Smallest safe change; reuse existing abstractions.
- No unrelated refactors, formatting sweeps, or dependency upgrades.
- Out-of-scope discoveries become follow-up issues, not extra edits.
- Separate modernization from feature work.

## Approval boundaries
See `AGENTS.md` section 13. Approval must be explicit, current, and from a human.
Approval for one action does not extend to another.

## Governance changes
`AGENTS.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.ai/policies/*`,
`.github/workflows/ai-governance.yml`, and CODEOWNERS configuration need review by
another team member. Agents may propose, humans approve.

## Dependencies
Before adding: already present? stdlib/existing code sufficient? maintained?
compatible? license and security acceptable? operational cost justified? truly required?
Do not touch lockfiles or upgrade unrelated packages unless the change requires it.
