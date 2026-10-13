# Implementation Plan: Feedback & Support (feature requests, issue reports, diagnostics, engineering handoff)

- Issue: none yet (unknown)
- Author / agent: Claude Code
- Risk class: HIGH RISK (authentication and authorization, new database schema and migrations, a public API contract, file
  uploads from untrusted users, secret handling, a new store of data about people's activity, an optional external integration)
- Date: 2026-10-12 (approved); updated 2026-10-13
- Status: **decisions answered by the owner on 2026-10-12; implementation in progress.** Slices 0 to 3 are built and tested
  locally, slice 4 is being built, slices 5 and 6 are not started. **Nothing has been pushed and no GitHub check has run on any of
  it** (the server has no GitHub credential). Four questions and one assumption are open for the owner: see "Open questions".

## Objective

Add two features to AgentLab, inside the existing application: **Feature Requests** and **Issue Reports**, with secure
attachments, user-approved and redacted diagnostics, a submission lifecycle, a maintainer triage console, and an
**engineering handoff package** (a sanitized ZIP and a `CLAUDE_CODE_TASK.md`) that an authorized engineer can give to Claude
Code. Nothing runs Claude Code or changes the repository automatically.

## Progress

| Slice | What | Branch | Migration | ADR | State |
|---|---|---|---|---|---|
| 0 | Accounts: registration, password sign-in, sessions, roles, `agentlab users`, the account pages | `feat/accounts-and-roles` | `0002` | 0011 | **Built and tested locally.** Also here: a request-size limit, the visitor's address behind Caddy, `PATCH` in CORS, honest sign-in copy |
| 1 | Core: submissions, workflow, comments and internal notes, history, labels, links, unread marker, duplicate suggestions, audit, allowances | `feat/feedback-core` | `0003` | 0012 | **Built and tested locally** |
| 2 | Attachments, `DiagnosticRedactor`, sanitized copies, scanner hook | `feat/feedback-attachments` | `0004` | 0013 | **Built and tested locally** |
| 3 | Diagnostics: the small log, the collector, preview, consent, bundle, request ids | `feat/feedback-diagnostics` | `0005` | 0014 | **Built and tested locally** |
| 4 | Engineering handoff: package, `CLAUDE_CODE_TASK.md`, export, audit | `feat/feedback-handoff` | `0006` | 0015 (to write) | **In progress**: service, routes and table are written; tests, docs and the ADR are not |
| 5 | Web interface: the forms, My Submissions, details, the maintainer queue and triage, handoff preview, an end-to-end test | not started | none | | **Not started** |
| 6 | GitHub provider interface (off by default), retention and purge, final documentation | not started | `0007` (planned) | | **Not started** |
| – | Existing defect: the agent adapter reused HTTP connections a server may have closed | `fix/agent-adapter-fresh-connections` | none | | **Fixed and tested locally**; its own pull request |
| – | The staged release process and the removal of the single-hosted-service path | `chore/staging-pipeline-and-remove-railway` | none | 0010 | **Built; the first pull request, to be merged by the owner** (it amends the merge rule) |

The branches are stacked in this order, and so are the pull requests: pipeline, adapter fix, accounts, core, attachments,
diagnostics, handoff, then the web interface and the integrations, and **this plan last**. Each branch has the previous one
merged into it. The plan is last because it names files that the slices add (the documentation tests check that every path a
document names exists in the tree it ships in), and because it is then a record of what was built rather than a promise.

## Context and constraints (checked in the repository, not assumed)

| Fact | Evidence | Consequence for the design |
|---|---|---|
| **There were no accounts, roles or tenants.** One shared API token; whoever holds it could do everything. The only reviewer identity is a typed name, "not verified". | `docs/security.md`, `docs/assumptions.md`, `src/agentlab/api/security.py` | "My submissions", "a user cannot see another user's submission", "maintainers only" and "security reports restricted" could not be built on it: decision 1, slice 0. |
| No notification, e-mail or webhook infrastructure. | `git grep` for smtp, email, notification, webhook finds only test fixtures and detection signals. | Notifications are in-app (an unread marker); decision 3. |
| **No application log was kept.** `logging` writes to the process's standard error. | `src/agentlab/core/config.py` had no log setting; no handlers. | A new, small, masked, run- and request-correlated log: decision 4 and ADR 0014. Browser errors can only come from the web interface. |
| No audit subsystem. | `git grep -i audit` found a field description only. | `submission_audit_events` and `account_events`, scoped to this feature. |
| A secret redactor exists and is applied to every persisted JSON and text column. | `security/redactor.py`, `storage/orm.py` | `DiagnosticRedactor` builds on it. |
| An artifact store with sensitivity levels and content addressing exists. | `storage/artifacts.py` | Attachments, diagnostics and handoff packages use it (`sensitivity=restricted`). It gained an optional `delete`. |
| Uploads exist for documents, with a suffix allow-list. | `api/routes/projects.py` | Attachments generalize that (signature checks, archives, limits). |
| One migration, SQLAlchemy `Entity` base, Alembic. | `src/agentlab/storage/` | Each slice adds its own migration; SQLite and PostgreSQL. |
| Every API field must be documented; `web/openapi.json` and the TypeScript schema must match the code. | `tests/api/test_openapi.py`, `scripts/export_openapi.py --check`, CI | Done for every slice; both files are regenerated with the tooling. |
| **FastAPI reads and validates a request body before it runs the authentication dependency, and nothing bounded the body.** | confirmed by sending invalid JSON with no credential to `POST /projects`: the answer is `422`, not `401` | A request-size limit in the request guard (slice 0). It also fixed the same weakness for every existing endpoint. |
| **Behind Caddy every visitor appeared as Caddy's address.** uvicorn trusts `X-Forwarded-For` only from `127.0.0.1`. | `docker/compose.vps.yaml`, `cmd_serve.py` | `FORWARDED_ALLOW_IPS=*` for the API, which is safe only because no API port is published; a test pins both. |
| Routing hands the application a *copy* of the ASGI scope (Starlette 1.7). | found while adding request ids | A mutable record placed in the scope before the call carries the route and the caller back to the middleware. |

## Proposed approach

### Data model (as built)

| Table | Slice | Purpose |
|---|---|---|
| `users`, `user_sessions`, `account_events` | 0 (`0002`) | Accounts, server-side sessions (hashed at rest), the account audit trail |
| `submissions`, `feature_request_details`, `issue_report_details`, `submission_comments`, `submission_status_history`, `submission_labels`, `submission_relations`, `submission_audit_events` | 1 (`0003`) | The submission and everything about it. The audit table has no foreign key to the submission, so a record outlives it |
| `submission_attachments` | 2 (`0004`) | What is known about an attachment; the bytes are restricted artifacts |
| `log_events`, `diagnostic_collections`, `diagnostic_items` | 3 (`0005`) | The small log; what a person collected; one row per item with its masking report (kinds and places, never values) |
| `engineering_handoffs` | 4 (`0006`) | A built package: manifest, hashes, who and when |
| `external_issue_links` | 6 (`0007`, planned) | The external issue a submission was published to |

Differences from the first draft: `diagnostic_artifacts` and `diagnostic_redactions` are one table (`diagnostic_items` with its
`redaction_report`); `SubmissionAssignment` is `assignee_user_id` on the submission plus `assigned` events in the audit trail.
Statuses, categories and limits are configuration, not enums in code, and are validated when the configuration loads.

### Backend (`src/agentlab/feedback/`, routes in `api/routes/`)

* **Identity and roles** (slice 0, built): enforced in the API and storage layers, never only in the web app. A test enumerates
  every operation in the OpenAPI document and asserts that a reporter is refused by each one that is not on a named list.
* **Visibility is a clause of the query** (slice 1, built; ADR 0012): `policy.visible` is the one definition of "may see", used
  by every read. A submission the caller may not see is answered exactly like one that does not exist.
* **Attachment service** (slice 2, built; ADR 0013): the type is decided by content from a fixed list; archives are read and
  never extracted; the bytes are restricted artifacts under their hash; text files keep their original and get a sanitized copy;
  a scanner runs only if configured and the record says `not_scanned` otherwise; removal deletes bytes nothing else uses.
* **DiagnosticRedactor** (slice 2, built): structured fields, headers and cookies (names kept), credentials in URLs,
  assignments, every pattern and registered value of the process, e-mail, phone and card numbers, the owner's rules, and a
  **second inspection** that marks `needs_review` and blocks external sharing. It is documented as risk reduction.
  Every pattern is held to linear time by a test that runs it against adversarial input.
* **Diagnostic collector** (slice 3, built; ADR 0014): only what AgentLab really keeps is offered, each category saying why it is
  not available; selection is by *ownership* (the caller's own requests, system-level lines, and for a maintainer a named run),
  never by time alone; only masked text is kept; drafts expire; consent is the JSON value `true`; a source that fails or is
  empty is listed with the reason and has no file.
* **EngineeringHandoffService** (slice 4, in progress): the package of the specification, with a SHA-256 manifest, generated
  files marked as generated, what is left out recorded with the reason, no empty placeholders; user claims, system
  observations and maintainers' decisions kept apart; every quoted text in a block that cannot be closed from inside;
  `CLAUDE_CODE_TASK.md` with the sections the specification lists; export audited and for maintainers only.
* **Issue tracker provider interface** (slice 6, not started): GitHub implementation, **off by default**, preview before
  publishing, never for private security reports, never with raw logs or private attachments, idempotent on retry.
* **Retention** (slice 6, not started): configurable periods per object class and a purge job that records audit metadata
  without secrets and refuses to remove evidence of an open security report. Until it is built, nothing is deleted on a
  schedule except draft collections and the captured log (which have their own bounds).
* **Rate limits and allowances** (built): per person per day or hour, in configuration; a request-size limit for everything.

### Frontend (slice 5, not started; `web/src`, existing design system)

Only the account pages exist (sign-in, registration, your account, accounts administration, a reduced shell for a reporter), and
they say honestly that the Feedback pages are not in the interface yet. To build: `Feedback & Support` in the navigation;
`Request a Feature` and `Report an Issue` forms with progressive disclosure; contextual "Report this issue" entry points on a
failed result, a run and a trace, pre-filled with ids but **never pre-attaching diagnostics**; `AttachmentUploader` (progress,
retry, removal, preview); the diagnostics panel and preview with the masking summary and explicit consent; duplicate
suggestions that never block; confirmation only after the backend persisted the record; `My Submissions`; details with a
timeline and comments (public and internal visually distinct); the maintainer queue and triage panel; handoff preview and
download; a small buffer of the browser's own console and network errors for the diagnostics option; unsaved-draft
confirmation; accessible status indicators.

### Delivery in slices (each a pull request through `scripts/ship.py`)

0. **Accounts.** 1. **Core.** 2. **Attachments and redaction.** 3. **Diagnostics.** 4. **Handoff.** 5. **Frontend.**
6. **Integrations and operations.** See "Progress" for where each stands.

## Accounts design (slice 0, built as designed)

* **Principals.** One `Principal` per request: the shared API token (a *service credential*, unchanged for the CLI, CI and
  today's token sign-in; acts as a maintainer), or a signed-in user (`reporter`, `maintainer` or `admin`).
* **Roles.** `reporter`: Feedback & Support only. `maintainer`: everything the shared token reaches, plus triage, private
  reports and handoff export. `admin`: maintainer plus account management. The check for the existing API is **one rule in the
  authentication layer**, not an edit of every route.
* **Safeguard, flagged for confirmation.** Open self-registration *and* "signed-in users keep today's access" would give every
  self-registered person the full power of the shared token. So self-registered accounts are `reporter`; promotion is an admin
  action; `accounts.registration` (`open` by default, `approval`, `closed`) and `accounts.default_role` are configuration.
* **Passwords.** Standard-library `scrypt` (no new dependency), per-user salt, parameters stored with the hash, minimum 12
  characters, a common-password check, constant-time verification, one answer for an unknown user and a wrong password;
  limits per address and per account, with a lock (5 wrong in a row, 15 minutes).
* **Sessions.** Server-side, 256-bit random value in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS), stored
  hashed; idle (60 minutes) and absolute (12 hours) lifetimes; revoked on sign-out, password change, role change and disable.
  A cookie-authenticated change must carry a CSRF token; an `Authorization` header is not subject to CSRF.
* **First administrator.** `agentlab users create NAME --role admin` on the server; the password at a hidden prompt or on
  standard input; never in a file, the environment or an argument.
* **No e-mail**, so no verification or reset link: an admin resets a password (a temporary one, changed at the next sign-in).
* **Audit.** Registration, sign-in and failure, lock, sign-out, password and role changes, disable and enable.
* **Not in scope:** multi-factor authentication, single sign-on, an e-mail channel, role-based access for the rest of AgentLab.

## Decisions (answered by the owner on 2026-10-12)

1. **Identity: full accounts with sign-in.** 2. **Delivery: six slices**, plus slice 0 for accounts. 3. **Notifications:
in-app only.** 4. **Logs: add bounded capture.** 5. **Registration: open self-registration.** 6. **The shared token stays** as
a service credential. 7. **Signed-in users keep today's access to the rest of AgentLab**, which for self-registered `reporter`
accounts is the safeguard's exception. 8. **The first administrator is created by a command on the server.**
Also decided: the Railway deployment path and its history are deleted; the staged process may merge its own pull request when
every check on the exact head is green; a merged slice is published automatically.

### Choices made inside that scope (for the owner to confirm or change)

| Choice | Default | Where it changes |
|---|---|---|
| The shared API token does **not** see other people's private security reports | `feedback.service_token_sees_security: false` | configuration |
| "Authorised maintainers" for a private report means every maintainer and admin | role is the only gate | would need a per-account grant (not built) |
| A reporter is not told the maintainers' priority, severity, assignee, labels, links, internal notes or maintainers' names | one function builds the view | `SubmissionService._summaries` / `_detail` |
| Allowances for a reporter: 20 submissions and 100 comments a day, 50 attachments a day, 30 diagnostic collections an hour | as listed | `feedback.*` |
| Attachments: 10 files, 25 MB each, 100 MB in all; archives at most 200 entries, 200 MB expanded, ratio 100 | the specification's defaults, as configuration | `feedback.attachments` |
| The log keeps WARNING and above, failed (not 401) or slow requests, for 48 hours or 50,000 lines | as listed | `feedback.diagnostics` |
| A request body above 4 MB (an upload: its own limit) is refused with 413 | `server.max_body_mb: 4` | configuration |
| Internal notes and non-text attachments are **not** in a handoff unless a maintainer selects them | as listed | the request that builds it |
| What makes a submission unread for its reporter is configurable | all three kinds | `feedback.notify_reporter_on` |
| No semantic (embedding) duplicate search: the repository has no vector infrastructure in use | title-word similarity over what the caller may see | the specification allows it |

## Changes to the design since the plan was approved

* **Visibility moved from "checked in the route" to "a clause of the query"**, and a hidden submission is answered with the same
  status and body as a missing one, by id and by number (ADR 0012).
* **The log is selected by ownership, not by time window** (ADR 0014). The first draft said "by time window"; a window alone would
  hand a person other people's requests.
* **A request id is made by the server and returned as `X-Request-Id`; one a client sends is ignored**, because a client could
  name another person's request.
* **Consent is the JSON value `true`**, not a truthy value.
* **Patterns are held to linear time.** The existing `private_key` and `connection_string` patterns were quadratic on hostile text
  and were changed; a test runs every pattern against adversarial input.
* **The artifact store gained an optional `delete`** so a removed attachment or an expired draft stops being kept.
* **The image carries `AGENTLAB_BUILD`**, set by `publish`, so an environment report can name the build.
* **The web interface says what it cannot do yet**: the sign-in and account pages no longer promise Feedback pages that do not
  exist.

## Checked against the specification (2026-10-13)

The whole specification was read again against slices 0 to 3. Gaps found and closed: an issue report now requires a description
of the problem; the test result id is a context field of its own; a maintainer can ask for another reproduction attempt; the
queue filters on requested priority, project, version and run; search finds what an issue says happened and the ids it names;
the audit trail has one record for each thing that was done; notifications are configurable; the discussion and the history can be
read on their own.

| Criterion | State |
|---|---|
| AC-01, AC-02 independent submissions | Built; tested through the API |
| AC-03 required fields | Built; tested (an issue needs a description and an impact; a feature a title and the problem) |
| AC-04 multiple files | Built; tested |
| AC-05 server-side validation | Built; tested (spoofed types and names, hostile archives, limits) |
| AC-06, AC-07 diagnostics selected, reviewed and approved | Built; tested through the API (preview, include, consent). The review screen is slice 5 |
| AC-08 redactor masks known secret types in diagnostics and packages | Diagnostics: tested. Packages: slice 4, tests not written |
| AC-09 unavailable diagnostics do not prevent submission | Tested |
| AC-10 no access to another user's submissions or attachments | Tested extensively (isolation, identifiers in every spelling, enumeration, other people's collections and files) |
| AC-11 maintainers manage status and triage | Built; tested through the API. The console is slice 5 |
| AC-12 users see details and updates | Built (details, history, unread marker); tested through the API. The pages are slice 5 |
| AC-13 to AC-16 handoff package, task file, provenance, unknowns and criteria | Slice 4: code written, not yet tested |
| AC-17 never executes Claude Code | By construction; a test that generation touches no subprocess and no network is planned in slice 4 |
| AC-18 no false claim of collection, scanning, redaction or publication | Tested for scanning (`not_scanned`), collection (unavailable, failed) and redaction (`needs_review`); handoff and publication pending |
| AC-19 GitHub is not a hard dependency | Slice 6, not started; it will be off by default |
| AC-20 the existing application keeps working | Existing suites passed in the focused runs below; the full local check is pending on the final heads |
| AC-21 migrations, unit, integration and end-to-end tests pass | Pending: see "Verification" |
| AC-22 documented for maintainers | Slices 0 to 3 documented (`docs/accounts.md`, `docs/feedback-support.md`, configuration, security, ADRs 0011 to 0014); slices 4 to 6 pending |

## Defects found in existing code while doing this

| Defect | How it was found | Where fixed |
|---|---|---|
| A request body was read in full before authentication, with no bound: one oversized request could use up the server's memory, signed in or not | opening `/auth/register` and `/auth/login` to anonymous callers | slice 0 (request guard, Caddy's `request_body`) |
| Behind Caddy every visitor had the same address, so the wrong-token, sign-in and registration limits were shared by everyone | reading how the address is derived | slice 0 (`FORWARDED_ALLOW_IPS`, pinned by a test) |
| The guard's own refusals lacked the security headers | a test for the 413 | slice 0 |
| `PATCH` was missing from the cross-origin methods | the accounts page uses it | slice 0 |
| **The agent adapter reused pooled HTTP connections, so a server closing an idle one made a correct agent fail a test with `connection error: ReadError`** | running the acceptance scenarios four at a time on one CPU: correct fixture agents failed one or two tests per run, always with an empty reply on one turn | `fix/agent-adapter-fresh-connections`; 0 failures in 16 runs under the same load afterwards |
| `private_key` and `connection_string` patterns were quadratic on hostile text | adversarial inputs against every pattern | slice 2 |
| A very long file name lost its extension when shortened, so a legitimate file was refused | a hostile-name test | slice 2 |

Not root-caused: the intermittent end-to-end failure of scenario 11 (a clean run reported as insecure) was not reproduced in 78
further runs; its assertion now prints the findings if it recurs. It is probably the same family as the adapter defect, which is
now fixed, but that is not established.

## Alternatives considered

- **One generic "ticket" table with a JSON column.** Rejected: the request needs filtering, access control and search per field.
- **Per-person tokens or two shared tokens instead of accounts.** Smaller, but the owner chose accounts.
- **Send submissions to an LLM to improve them.** Not built: it needs separate consent and policy.
- **Store attachments in the database.** Rejected: the artifact store provides content addressing and sensitivity.
- **Select log lines by time window and let the person remove lines.** Rejected: the default would leak.
- **Store only the sanitized text of an attachment.** Rejected: the original is evidence and a masking mistake would be permanent.
- **Row-level security in PostgreSQL.** Rejected for now: SQLite is supported, and the policy is one module tested on both.

## Affected components and files

New: `src/agentlab/accounts/`, `src/agentlab/feedback/`, `src/agentlab/security/diagnostic_redactor.py`,
`src/agentlab/core/context.py`, `src/agentlab/api/requestlog.py`, routes `accounts.py`, `feedback.py`, `diagnostics.py`,
`handoffs.py`, migrations `0002` to `0006`, `web/src/pages/` (account pages; Feedback pages in slice 5),
`docs/accounts.md`, `docs/feedback-support.md`, ADRs 0010 to 0014. Changed: `api/app.py`, `api/security.py` (request guard),
`api/routes/common.py`, `core/config.py`, `storage/orm.py`, `storage/artifacts.py` (`delete`), `security/redactor.py` (two patterns),
`jobs/runner.py` (the run id context), `adapters/http.py` (connections), `docker/Dockerfile`, `docker/compose*.yaml`,
`docker/Caddyfile`, `scripts/ship.py`, `web/openapi.json`, `web/src/api/schema.d.ts`. Not touched: the orchestrator, skills,
evaluation, browser engine and report generator.

## Verification plan

Per slice: `scripts/ship.py check` locally, then CI. The tests of sections 18.1 to 18.6 map to the slices (flows: 1 and 2;
diagnostics and redaction: 2 and 3; security: all; handoff: 4; end to end: 5).

**Run so far** (and what each does and does not show):

* Focused suites with CI's marker selection (`api`, `security`, and the feedback and redaction unit tests): **689 passed,
  17 deselected** at the diagnostics head (`69fefce`). Lint, format, type, documentation, OpenAPI and governance checks passed
  when each of slices 1 to 3 was committed, and at each of slice 0's later commits.
* **Deliberate breakage:** ten protections of slice 1, ten of slice 2 and nine of slice 3 were broken one at a time in a scratch
  copy; each was caught by a test. That is evidence the tests can fail, not that nothing is missing.
* **The full local pre-flight (`ship.py check`, eight stages) has been green only for slice 0 at an earlier head**, and has been
  red once on slice 1 (two of my own tests were order-dependent; one acceptance test met the adapter defect). It has **not** been
  re-run on any current head. The adapter fix was verified by 16 acceptance runs under four-way load with no failure.
* Migrations `0002`, `0003` and `0004` were tested up and down on SQLite; `0005` was checked against the models for drift but has
  **no downgrade test yet**, and `0006` has no test. **Not run: PostgreSQL**, the Docker-marked and browser-marked suites, and
  anything on GitHub.

**Still to do:** downgrade tests for `0005` and `0006`; the handoff tests (generation of both package kinds, selected and excluded attachments, hashes, masking, the task
file naming what is missing and keeping instructions as evidence, authorization of the download, no subprocess and no network
during generation); the web tests and an end-to-end test of both workflows; the full check on every head; GitHub CI.

## Risks, rollback, migrations

- **Authentication and authorization.** The most tested part: every operation is enumerated against the roles; hidden and missing
  submissions, collections and files are answered alike. A mistake here exposes submissions, so a change to `policy.py` or the
  role rules needs the security suites to pass and a second review.
- **Open registration on a public server.** The safeguard limits a self-registered person to Feedback & Support, and allowances
  bound what they can store, but anyone can create accounts. `accounts.registration: approval` or `closed` is one setting. See
  "Open questions".
- **Migrations `0002` to `0006`** add tables only. `downgrade` drops them with their data; a backup is taken before each publish,
  and a migration is applied to the live database only by `publish`.
- **Uploads** are the main attack surface: content checks, private storage, no extraction, no execution, bounded work.
- **The captured log** is a new store of data about people's activity: bounded by age (48 hours) and count (50,000), masked,
  holding routes, statuses and account ids and never bodies; `diagnostics.capture_logs: false` turns it off.
- **Regular expressions over hostile text.** A pattern that is not linear is a denial of service; the test that runs every
  pattern against adversarial input must pass before any pattern is added or changed.
- **Secrets in free text** are a residual risk of any field; masking lowers it and a person reviews what they attach.
- **A package given to a model** carries untrusted text. The task file quotes it in blocks that cannot be closed from inside and
  says not to obey it; that reduces the risk and does not remove it, so the package is for an authorised engineer only.
- **Rollback of a slice:** revert its merge commit and run `downgrade` to the previous revision; nothing outside the new tables,
  routes and configuration changes. The adapter fix and the request-size limit are independent of the feature and are kept.

## Irreversible steps and human approval point

Approval of decisions 1 to 4 was given on 2026-10-12. The remaining human gates are: **opening the pull requests** (needs a GitHub
credential the server does not have), **merging the first pull request** (the pipeline's own, which changes the merge rule and
so is merged by the owner), and **the first publish of accounts**, which makes registration reachable from the internet.
Applying a migration to the live database happens only in `publish`, after a backup, and only after the slice's pull request is
green.

## Open questions

1. **A GitHub credential.** Nothing can be pushed, opened, merged or published until the owner creates the token file described
   in `docs/development.md#the-staged-release-process` (a fine-grained token for this repository only, with pull requests and
   contents read and write and actions read, expiring soon; never pasted into a conversation).
2. **Registration on the live server until the pages exist.** With the default `open`, anyone can register as a reporter and do
   nothing but change their password, because the Feedback pages are slice 5. Recommended: set `accounts.registration: approval`
   until then. This is a configuration of the server, not code.
3. **Confirm the safeguard**: self-registered accounts are feedback-only until an admin promotes them
   (`accounts.default_role: maintainer` would give them today's full access; read the risks first).
4. **Retention defaults** (before slice 6): the specification gives none. Proposal to approve: submissions and their comments are
   kept until deleted by an admin; attachments, diagnostics and handoff packages 1 year after the submission closes;
   internal notes with the submission; the captured log 48 hours as now.
5. Assumed until told otherwise: GitHub publishing is off until an owner configures it; there is no malware scanner here, so
   attachments are labelled "not scanned"; no language model is sent anything.

## Next steps, in order

1. Finish slice 4: tests, documentation, ADR 0015; then the full check on its head.
2. Slice 5, the web interface, with web tests and an end-to-end test of both workflows.
3. Slice 6: the provider interface, retention, and the final documentation pass.
4. The full local check on every head, in stack order, fixing what is red; then, with the credential, the staged process for
   each pull request in order.
