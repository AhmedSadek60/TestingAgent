# Documentation Policy

## Hierarchy
| Document | Holds |
|---|---|
| `AGENTS.md` | Universal rules for agents |
| `.ai/` | AI workflows, policies, templates, project metadata |
| `docs/architecture/` | Current architecture |
| `docs/decisions/` | ADRs: decisions and rationale |
| `docs/development/` | Local setup and developer how-tos |
| `README.md` | Onboarding and overview |
| `CONTRIBUTING.md` | Human + AI contribution workflow |

## Rules
- One home per fact; link instead of copying.
- Explain WHY, not just WHAT.
- Update docs in the same PR as the behavior change.
- ADRs for architecture, framework, dependency strategy, data storage, communication
  patterns, API contracts, security architecture, deployment architecture.
- ADRs are append-only history: supersede, don't rewrite. Mark unverifiable history "unknown".
- Keep examples small; avoid giant generated docs.
- Never document secrets, internal-only URLs you cannot verify, or local machine paths.

## Knowledge model
Chat history, personal preferences, local agent/MCP/IDE configuration stay local.
Anything the team needs later must be written into the repository.
