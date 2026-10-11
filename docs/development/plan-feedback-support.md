# Implementation Plan: Feedback & Support (feature requests, issue reports, diagnostics, engineering handoff)

- Issue: none yet (unknown)
- Author / agent: Claude Code
- Risk class: HIGH RISK (authentication and authorization, a new database schema and migration, a public API contract, file
  uploads from untrusted users, secret handling, an optional external integration)
- Date: 2026-10-12
- Status: **waiting for human approval of the open decisions below. Nothing in this plan has been implemented.**

## Objective

Add two features to AgentLab, inside the existing application: **Feature Requests** and **Issue Reports**, with secure
attachments, user-approved and redacted diagnostics, a submission lifecycle, a maintainer triage console, and an
**engineering handoff package** (a sanitized ZIP and a `CLAUDE_CODE_TASK.md`) that an authorized engineer can give to Claude
Code. Nothing runs Claude Code or changes the repository automatically.

## Context and constraints (checked in the repository, not assumed)

| Fact | Evidence | Consequence for the design |
|---|---|---|
| **There are no accounts, roles or tenants.** One shared API token; whoever holds it can do everything. The only reviewer identity is a typed name, "not verified". | `docs/security.md` ("one shared token and no accounts"), `docs/assumptions.md`, `src/agentlab/api/security.py` | "My submissions", "a user cannot see another user's submission", "maintainers only", and "security reports restricted" **cannot be built on the current authentication**. This needs a new identity and role mechanism: decision 1. |
| No notification, e-mail or webhook infrastructure. | `git grep` for smtp, email, notification, webhook finds only test fixtures and detection signals. | Notifications are in-app (a per-submission timeline and an unread marker) unless an outbound channel is chosen: decision 3. |
| **No application log is kept.** `logging` writes to the process's output; there is no log file, store or query. | `src/agentlab/core/config.py` has no log setting; `grep` for handlers finds none. | "Relevant application and backend logs, by time window" needs a new, bounded, redacted, run-correlated log capture: decision 4. Browser console and network errors can only come from the web app itself. |
| No audit log subsystem. | `git grep -i audit` finds a field description only. | A new `submission_audit_events` table, scoped to this feature. |
| A secret redactor exists and is applied to every persisted JSON and text column. | `src/agentlab/security/redactor.py`, `storage/orm.py` (`RedactedJSON`, `RedactedText`) | `DiagnosticRedactor` extends it (PII, cookies, connection strings, custom rules, a second inspection pass) rather than competing with it. |
| An artifact store with sensitivity levels, content addressing and run correlation exists. | `src/agentlab/storage/artifacts.py` | Attachments and handoff packages use it (`kind=support_*`, `sensitivity=restricted`), plus a per-attachment access check. |
| Uploads exist for documents, with a suffix allow-list and `safe_filename`. | `src/agentlab/api/routes/projects.py` | The attachment service generalizes that (signature checks, limits, archives). |
| One migration (`0001`), SQLAlchemy `Entity` base (id, timestamps, version, status), Alembic. | `src/agentlab/storage/` | New tables follow `Entity`; migration `0002`; SQLite and PostgreSQL both tested. |
| Every API field must be documented, and `web/openapi.json` plus the generated TypeScript schema must match the code. | `tests/api/test_openapi.py`, `scripts/export_openapi.py --check`, CI | Each new schema field gets a description; the schema files are regenerated with the tooling. |
| React web app with pages, a nav, an API client generated from the OpenAPI document; no browser-error capture exists. | `web/src` | New pages in the existing style; a small client-side error buffer for the browser-diagnostics option. |
| `scripts/ship.py` and CI: lint, format, types, API description, governance, tests, web job, image build. | this repository | Each slice is its own pull request through that process. |

## Proposed approach

### Data model (migration `0002`, SQLAlchemy `Entity` subclasses, indexed for filter and search)

`submissions` (common: human id `FR-2026-00042` / `IR-2026-00042`, type, title, description, category, status, requested
priority, assigned priority, severity, reporter id, assignee id, visibility `normal|security`, project id, timestamps),
`feature_request_details`, `issue_report_details` (expected, actual, steps, frequency, impact, first observed, workaround,
related run / test / trace / report ids, version and build), `submission_comments` (`visibility: public|internal`),
`submission_status_history`, `submission_labels`, `submission_relations`, `submission_attachments`, `diagnostic_collections`,
`diagnostic_artifacts`, `diagnostic_redactions` (type and location, never the value), `engineering_handoffs`,
`external_issue_links`, `submission_audit_events`. Statuses and categories are configuration (YAML), not enums in code.

### Backend (`src/agentlab/feedback/`, routes in `api/routes/feedback.py`)

* **Identity and roles** (decision 1): enforced in the API and storage layers, never only in the web app.
* **Attachment service**: allow-list of types from configuration; extension, declared type, **file signature** and size all
  checked server-side; generated storage names; limits (10 files, 25 MB each, 100 MB total, configurable); private store only;
  an access check on every download; ZIP validation without extraction (entry count, expanded size, ratio, path traversal,
  absolute paths, symbolic links, nested archives); **"not scanned" is recorded and shown** unless a scanner is configured.
* **DiagnosticRedactor**: structured keys, headers, cookies, tokens, JWTs, private keys, connection strings, e-mail and phone
  patterns, registered secret values, custom rules; a **second inspection pass** that blocks external sharing when a likely secret
  survives; a redaction report with kind and location only. It is documented as risk reduction, not a guarantee.
* **Diagnostic collector**: only categories the system can really supply are offered, each with source, time range, size,
  redaction count and success or failure. Sources: environment and version; run, test and trace summaries from the existing
  stores (only for runs the requester may see); the new bounded log capture (decision 4); browser console and network errors
  sent by the web app; a screenshot the user attaches. A failed source never blocks submission.
* **EngineeringHandoffService**: builds the ZIP described in the request (README, `CLAUDE_CODE_TASK.md`, submission,
  reproduction, diagnostics, evidence, context, security), with a SHA-256 manifest, generated files marked as generated,
  unavailable artifacts recorded as unavailable, and no empty placeholders. `CLAUDE_CODE_TASK.md` carries the role, objective,
  context, evidence index, reproduction, engineering instructions, the untrusted-input rules, acceptance criteria (stated, or
  proposed and labelled so), and the expected final response. Export is audited and requires the maintainer role.
* **Issue tracker provider interface** with a GitHub implementation, **off by default**: preview before publishing, never for
  security-sensitive reports, never with raw logs or private attachments, idempotent on retry, explicit authorization.
* **Retention**: configurable periods per object class (submission, attachment, diagnostics, handoff, internal note) and a purge
  job; deletion records audit metadata without secrets and refuses to remove evidence of an open security report.
* **Rate limiting** on submission and upload endpoints, request-size limits, concurrent-update protection through the existing
  `version` column.

### Frontend (`web/src`, existing design system)

`Feedback & Support` in the navigation; `Request a Feature` and `Report an Issue` forms with progressive disclosure; contextual
"Report this issue" entry points on a failed result, a run and a trace, pre-filled with ids but **never pre-attaching
diagnostics**; `AttachmentUploader` (progress, retry, removal, preview); `DiagnosticCollectionPanel` and preview with the
redaction summary and explicit consent; duplicate suggestions that never block; confirmation only after the backend persisted
the record; `My Submissions`; submission details with timeline and comments (public and internal visually distinct); the
maintainer queue and triage panel; handoff preview and download. Unsaved-draft confirmation and accessible status indicators.

### Delivery in slices (each a pull request through `scripts/ship.py`)

1. **Core**: schema and migration `0002`, identity and roles (decision 1), submissions API, comments, status history, audit.
2. **Attachments and redaction**: the attachment service, `DiagnosticRedactor`, their security tests.
3. **Diagnostics**: the collector, the log capture (decision 4), the browser-error endpoint.
4. **Handoff**: `EngineeringHandoffService`, `CLAUDE_CODE_TASK.md`, export, audit.
5. **Frontend**: all pages and components, web tests, an end-to-end test of both workflows.
6. **Integrations and operations**: provider interface and GitHub implementation, retention and purge, documentation.

## Alternatives considered

- **One generic "ticket" table with a JSON column.** Rejected: the request needs filtering, access control and search per field.
- **Build accounts and sign-in.** Larger than the request: see decision 1 for the smaller option.
- **Send submissions to an LLM to improve them.** Not built: it needs separate consent and policy, and the request makes it opt-in.
- **Store attachments in the database.** Rejected: the artifact store already provides content addressing and sensitivity.

## Affected components and files

New: `src/agentlab/feedback/`, `api/routes/feedback.py`, migration `0002`, `web/src/pages/Feedback*`, `web/src/components/feedback/`,
tests under `tests/unit`, `tests/api`, `tests/security`, `tests/integration`, `tests/e2e`, `docs/feedback-support.md`.
Changed: `api/app.py` (router, role middleware), `api/security.py` (roles), `core/config.py` (a `feedback` section),
`storage/orm.py` and `storage/db.py`, `web/openapi.json`, `web/src/api/schema.d.ts`, the navigation. Not touched: the
orchestrator, skills, evaluation, browser engine and report generator (they are read, not changed).

## Verification plan

Per slice: `scripts/ship.py check` locally, then CI. Tests listed in the request (sections 18.1 to 18.6) map to the slices:
feature and issue flows (1, 2), diagnostics and redaction (2, 3), security (1, 2, 4: authorization, cross-user access,
traversal, spoofed types, archive bombs, XSS, injection, rate limits, secret leakage, prompt injection kept as evidence),
handoff (4), end to end (5). The migration is tested on SQLite and PostgreSQL, upgrade and downgrade, with existing data.

## Risks, rollback, migrations

- **Authentication change** (decision 1): additive, and the existing token keeps working unchanged; rollback is removing the
  roles. A mistake here exposes submissions, so it has the most tests.
- **Migration `0002`** adds tables only. A rollback is `downgrade`, which drops them (data in them is lost); a backup is taken
  before the publish step.
- **Uploads** are the main new attack surface: strict validation, private storage, no extraction to disk, no execution.
- **Log capture** adds a small memory cost and must never contain unredacted secrets: it stores only redacted lines, bounded.
- **Secrets in submissions** are a residual risk of any free-text field; the redactor lowers it and the user reviews the package.
- Rollback of a slice: revert its merge commit and the migration; nothing outside the new tables and routes changes.

## Irreversible steps and human approval point

Approval of decisions 1 to 4 is required before slice 1. Applying migration `0002` to the live database happens only at the
publish step of the staged process, after a backup, and only after the slice's pull request is green.

## Open questions / assumptions

1. **Identity and roles** (the blocker). AgentLab has no users. Options:
   - **A. Two shared tokens**: the existing token stays and gains the *reporter* role; a new `server.maintainer_token_ref` is the
     *maintainer* role. A reporter's name is typed and remembered in the browser; "My submissions" filters by that name, which is
     **not** a security boundary between people who share the reporter token. Security-report access and maintainer actions are
     enforced in the backend. Smallest change; AC-10 is met only between roles, and the limitation is documented.
   - **B. Per-person tokens issued by a maintainer** (recommended): a maintainer creates and revokes named tokens (stored as
     hashes, shown once); each token is a *reporter* or a *maintainer*; the existing shared token keeps working as a maintainer
     (backwards compatible). Isolation between reporters is real and AC-10 is met. Cost: token management endpoints and one page.
   - **C. Full accounts with sign-in.** Not proposed: it is a different product change.
2. **Slices**: deliver in the six pull requests above (recommended), or one large pull request.
3. **Notifications**: in-app only (recommended; nothing new to operate), or add an outbound webhook or SMTP setting.
4. **Application logs**: add a bounded, redacted, run-correlated log capture (recommended; without it log diagnostics cannot be
   offered), or offer only traces, run summaries, environment and browser errors.
5. Assumed: file limits and retention periods start at the defaults in the request and are configuration; GitHub publishing is
   off until an owner configures it; no malware scanner exists here, so attachments are labelled "not scanned".
