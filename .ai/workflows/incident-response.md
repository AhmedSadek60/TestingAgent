# Incident Response (agent-assisted)

Agents assist; humans decide and own production actions.

1. **Stabilize first** — humans choose mitigation (rollback, feature flag, scale). Agents propose; they do not touch production without explicit authorization.
2. **Gather evidence** — logs, metrics, recent changes (`git log`), deploy history. Read-only. Redact secrets and personal data before sharing anywhere.
3. **Hypothesize and test** — record hypotheses and evidence in `../templates/investigation.md`.
4. **Fix** — minimal, reviewed change on a branch; HIGH RISK classification applies.
5. **Verify** — reproduce, regression test, confirm in the appropriate environment (human-run for production).
6. **Follow up** — post-incident notes in the repo (and ADR if architecture changes). No blame, no fabricated timelines: mark unknowns as unknown.
