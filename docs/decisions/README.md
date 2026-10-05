# Architecture Decision Records

Record significant decisions as `NNNN-short-title.md` using `/.ai/templates/adr.md`.

Write an ADR for: architecture changes, major framework choices, dependency strategy,
data storage, communication patterns, API contracts, security architecture, deployment architecture.

Rules: ADRs are append-only history (supersede, don't rewrite). Never invent past
decisions; if the rationale cannot be verified, write "unknown". Decisions are
accepted by human review, not by an agent.

## Index
<!-- Add one line per ADR: [0001](0001-title.md) — Title — Status -->
[0001](0001-single-package-plugin-architecture.md) — One Python package with plug-in registries — Proposed
[0002](0002-fail-closed-isolation.md) — Fail closed when isolation is unavailable — Proposed
[0003](0003-skills-trust-model.md) — Skills are data; Python generators are trusted-only — Proposed
