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
[0004](0004-one-origin-interface-and-signed-report-links.md) — One origin for the API and the interface; reports shown from signed, short-lived links — Proposed
[0005](0005-blocked-is-not-failed-independent-judge.md) — BLOCKED is not FAILED; the judge is independent of the target — Proposed
[0006](0006-reviews-never-rewrite-the-evaluation.md) — Human reviews sit beside the evaluation and never rewrite it — Proposed
[0008](0008-vps-compose-with-caddy.md) — On a single VPS, the Compose stack with Caddy in front — Proposed

[0009](0009-chromium-in-the-worker-with-a-userns-seccomp-profile.md) — Chromium in the worker, with a seccomp profile that allows user namespaces — Proposed
[0010](0010-staged-release-process.md) — A staged release process, merged and published by a script — Proposed
[0011](0011-accounts-beside-the-shared-token.md) — Accounts beside the shared token, with roles enforced in the authentication layer — Proposed
[0012](0012-feedback-visibility-in-the-query.md) — Feedback & Support: who may see a submission is a clause of the query — Proposed
[0013](0013-attachments-are-private-evidence-with-a-sanitized-copy.md) — Attachments are private evidence; a text file also has a sanitized copy — Proposed
