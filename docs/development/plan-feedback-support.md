# Implementation Plan: Feedback & Support (feature requests, issue reports, diagnostics, engineering handoff)

- Issue: none yet (unknown)
- Author / agent: Claude Code
- Risk class: HIGH RISK (authentication and authorization, new database schema and migrations, a public API contract, file
  uploads from untrusted users, secret handling, a new store of data about people's activity, deletion of data, an optional
  external integration)
- Date: 2026-10-12 (approved); updated 2026-10-13
- Status: **decisions answered by the owner on 2026-10-12; all six slices and slice 0 are built and tested locally.** The
  branches are stacked in this repository and **nothing has been pushed**: the server has no GitHub credential, so no pull
  request has been opened, no GitHub check has run, nothing is merged and nothing is published. Five questions are open for
  the owner: see "Open questions".

## Objective

Add two features to AgentLab, inside the existing application: **Feature Requests** and **Issue Reports**, with secure
attachments, user-approved and redacted diagnostics, a submission lifecycle, a maintainer triage console, and an
**engineering handoff package** (a sanitized ZIP and a `CLAUDE_CODE_TASK.md`) that an authorized engineer can give to Claude
Code. Nothing runs Claude Code or changes the repository automatically.

## Progress

| Slice | What | Branch | Migration | ADR | State |
|---|---|---|---|---|---|
| 0 | Accounts: registration, password sign-in, sessions, roles, `agentlab users`, the account pages | `feat/accounts-and-roles` | `0002` | 0011 | **Built and tested locally.** Also here: a request-size limit, the visitor's address behind Caddy, `PATCH` in CORS, and names that PostgreSQL can store |
| 1 | Core: submissions, workflow, comments and internal notes, history, labels, links, unread marker, duplicate suggestions, audit, allowances | `feat/feedback-core` | `0003` | 0012 | **Built and tested locally** |
| 2 | Attachments, `DiagnosticRedactor`, sanitized copies, scanner hook | `feat/feedback-attachments` | `0004` | 0013 | **Built and tested locally** |
| 3 | Diagnostics: the small log, the collector, preview, consent, bundle, request ids | `feat/feedback-diagnostics` | `0005` | 0014 | **Built and tested locally** |
| 4 | Engineering handoff: package, `CLAUDE_CODE_TASK.md`, export, audit | `feat/feedback-handoff` | `0006` | 0015 | **Built and tested locally** |
| 5 | Web interface: the forms, My Submissions, a submission's page, triage, handoff, entry points, drafts, browser-error buffer | `feat/feedback-ui` | none | 0016 | **Built and tested locally**, including both workflows in a real browser |
| 6 | Retention and deletion; the issue-tracker interface and GitHub, off by default | `feat/feedback-ui` (same branch, its own commit) | `0007`, `0008` | 0017, 0018 | **Built and tested locally** |
| – | Existing defect: the agent adapter reused HTTP connections a server may have closed | `fix/agent-adapter-fresh-connections` | none | | **Fixed and tested locally**; its own pull request |
| – | The staged release process and the removal of the single-hosted-service path | `chore/staging-pipeline-and-remove-railway` | none | 0010 | **Built; the first pull request, to be merged by the owner** (it amends the merge rule) |

The branches are stacked in this order, and so are the pull requests: pipeline, adapter fix, accounts, core, attachments,
diagnostics, handoff, then the web interface with retention and publication, and **this plan last**. Each branch has the
previous one merged into it. The plan is last because it names files that the slices add (the documentation tests check
that every path a document names exists in the tree it ships in), and because it is then a record of what was built.
Slices 5 and 6 share a branch to save a pull request; they are separate commits and can be split.

## Context and constraints (checked in the repository, not assumed)

| Fact | Evidence | Consequence for the design |
|---|---|---|
| **There were no accounts, roles or tenants.** One shared API token; whoever holds it could do everything. The only reviewer identity is a typed name, "not verified". | `docs/security.md`, `docs/assumptions.md`, `src/agentlab/api/security.py` | "My submissions", "a user cannot see another user's submission", "maintainers only" and "security reports restricted" could not be built on it: decision 1, slice 0. |
| No notification, e-mail or webhook infrastructure. | `git grep` for smtp, email, notification, webhook finds only test fixtures and detection signals. | Notifications are in-app (an unread marker); decision 3. |
| **No application log was kept.** `logging` writes to the process's standard error. | `src/agentlab/core/config.py` had no log setting; no handlers. | A new, small, masked, run- and request-correlated log: decision 4 and ADR 0014. Browser errors can only come from the web interface. |
| No audit subsystem. | `git grep -i audit` found a field description only. | `submission_audit_events` and `account_events`, scoped to this feature. |
| A secret redactor exists and is applied to every persisted JSON and text column. | `security/redactor.py`, `storage/orm.py` | `DiagnosticRedactor` builds on it; it also means the storage layer masks text a second time. |
| An artifact store with sensitivity levels and content addressing exists. | `storage/artifacts.py` | Attachments, diagnostics and handoff packages use it (`sensitivity=restricted`). It gained an optional `delete`. Content addressing means one stored file can be several records' evidence, which retention has to respect. |
| Uploads exist for documents, with a suffix allow-list. | `api/routes/projects.py` | Attachments generalize that (signature checks, archives, limits). |
| One migration, SQLAlchemy `Entity` base, Alembic. | `src/agentlab/storage/` | Each slice adds its own migration; SQLite and PostgreSQL. |
| Every API field must be documented; `web/openapi.json` and the TypeScript schema must match the code. | `tests/api/test_openapi.py`, `scripts/export_openapi.py --check`, CI | Done for every slice; both files are regenerated with the tooling. |
| **FastAPI reads and validates a request body before it runs the authentication dependency, and nothing bounded the body.** | confirmed by sending invalid JSON with no credential to `POST /projects`: the answer is `422`, not `401` | A request-size limit in the request guard (slice 0). It also fixed the same weakness for every existing endpoint. |
| **Behind Caddy every visitor appeared as Caddy's address.** uvicorn trusts `X-Forwarded-For` only from `127.0.0.1`. | `docker/compose.vps.yaml`, `cmd_serve.py` | `FORWARDED_ALLOW_IPS=*` for the API, which is safe only because no API port is published; a test pins both. |
| Routing hands the application a *copy* of the ASGI scope (Starlette 1.7). | found while adding request ids | A mutable record placed in the scope before the call carries the route and the caller back to the middleware. |
| **The browser tests need a built interface and Chromium, which the host and CI do not have.** | `tests/support/browser.py`, `.github/workflows/ci.yml` (`-m "not browser"`) | They run inside the worker image here; CI does not run them. A change that only the browser tests would notice can reach a pull request unseen (see "Defects"). |

## Proposed approach

### Data model (as built)

| Table | Slice | Purpose |
|---|---|---|
| `users`, `user_sessions`, `account_events` | 0 (`0002`) | Accounts, server-side sessions (hashed at rest), the account audit trail |
| `submissions`, `feature_request_details`, `issue_report_details`, `submission_comments`, `submission_status_history`, `submission_labels`, `submission_relations`, `submission_audit_events` | 1 (`0003`) | The submission and everything about it. The audit table has no foreign key to the submission, so a record outlives it |
| `submission_attachments` | 2 (`0004`) | What is known about an attachment; the bytes are restricted artifacts |
| `log_events`, `diagnostic_collections`, `diagnostic_items` | 3 (`0005`) | The small log; what a person collected; one row per item with its masking report (kinds and places, never values) |
| `engineering_handoffs` | 4 (`0006`) | A built package: manifest, hashes, who and when; `purged` once retention removed the files |
| `submission_counters` | 6 (`0007`) | The highest number given for a type and a year, so a deleted submission's number is never given again |
| `external_issue_links` | 6 (`0008`) | The issue a submission was published to: one row per submission and provider, with the key that makes a retry safe |

Differences from the first draft: `diagnostic_artifacts` and `diagnostic_redactions` are one table (`diagnostic_items` with its
`redaction_report`); `SubmissionAssignment` is `assignee_user_id` on the submission plus `assigned` events in the audit trail.
Statuses, categories and limits are configuration, not enums in code, and are validated when the configuration loads.

### Backend (`src/agentlab/feedback/`, routes in `api/routes/`)

* **Identity and roles** (slice 0): enforced in the API and storage layers, never only in the web app. A test enumerates every
  operation in the OpenAPI document and asserts that a reporter is refused by each one that is not on a named list.
* **Visibility is a clause of the query** (slice 1; ADR 0012): `policy.visible` is the one definition of "may see", used by every
  read. A submission the caller may not see is answered exactly like one that does not exist.
* **Attachment service** (slice 2; ADR 0013): the type is decided by content from a fixed list; archives are read and never
  extracted; the bytes are restricted artifacts under their hash; text files keep their original and get a sanitized copy; a
  scanner runs only if configured and the record says `not_scanned` otherwise.
* **DiagnosticRedactor** (slice 2): structured fields, headers and cookies, credentials in URLs, assignments, every pattern and
  registered value of the process, e-mail, phone and card numbers, the owner's rules, and a second inspection that marks
  `needs_review` and blocks external sharing. Every pattern is held to linear time by a test that runs it against adversarial input.
* **Diagnostic collector** (slice 3; ADR 0014): only what AgentLab really keeps is offered; selection is by *ownership*, never by
  time alone; only masked text is kept; drafts expire; consent is the JSON value `true`.
* **EngineeringHandoffService** (slice 4; ADR 0015): the package of the specification with a SHA-256 manifest; everything a
  person wrote is quoted in a block that cannot be closed from inside (or is a JSON string, or is made safe); generated files
  are marked generated; what is left out is listed with the reason and nothing stands in for it; claims, observations and
  decisions are kept apart; internal notes and unsanitizable files are out unless chosen; maintainers only; every build and
  download is audited. Nothing runs Claude Code or sends the package anywhere (a test makes a subprocess or a connection fail).
* **Retention and deletion** (slice 6; ADR 0017): periods per kind, counted from the day a submission closed and **empty by
  default**; an open submission is never touched; content goes and a record of counts stays; a stored file is deleted only when
  nothing that is left refers to it; `agentlab feedback purge [--dry-run]` and a server loop; an administrator can delete one
  submission with a reason, and an open private security report only when they acknowledge it.
* **Issue-tracker interface and GitHub** (slice 6; ADR 0018): off until the owner switches it on; a maintainer previews a
  sanitized, masked, quoted summary and publishes exactly that (the preview's hash must come back); never a private security
  report, never files or diagnostics; never twice; GitHub's errors said in words; status pushed on request.
* **Rate limits and allowances**: per person per day or hour, in configuration; a request-size limit for everything.

### Frontend (slice 5; `web/src`, existing design system)

`Feedback & Support` in the navigation of a maintainer and as the whole interface of a reporter: the hub, the feature request and
issue report forms (progressive disclosure, inline validation, duplicate suggestions that never block, a private-report
warning, confirmation only after the server kept it), contextual "Report an issue" links on a run, a result and a trace that
carry identifiers only and list what will be sent, an attachment uploader with progress, retry and preview, the diagnostics
panel with preview and explicit consent (nothing pre-ticked), a bounded in-memory buffer of the tab's own errors, drafts in the
tab and a guard against abandoning unsent text, My submissions and the maintainer queue, a submission's page (discussion with
internal notes set apart, files, diagnostics, history), the triage panel, links, the handoff, the publication panel and the
deletion panel. What a person wrote is only ever a text node.

### Delivery in slices (each a pull request through `scripts/ship.py`)

0. **Accounts.** 1. **Core.** 2. **Attachments and redaction.** 3. **Diagnostics.** 4. **Handoff.** 5. **Frontend.**
6. **Integrations and operations.** See "Progress" for where each stands.

## Accounts design (slice 0, built as designed)

* **Principals.** One `Principal` per request: the shared API token (a *service credential*, unchanged for the CLI, CI and
  today's token sign-in; acts as a maintainer), or a signed-in user (`reporter`, `maintainer` or `admin`).
* **Roles.** `reporter`: Feedback & Support only. `maintainer`: everything the shared token reaches, plus triage, private
  reports and handoff export. `admin`: maintainer plus account management and deletion of a submission. The check for the
  existing API is **one rule in the authentication layer**, not an edit of every route.
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
| **Retention: no period is set** | every `feedback.retention.*_days` is empty | the owner chooses; `agentlab feedback purge --dry-run` shows the effect |
| **Only administrators delete a submission**; a maintainer and the shared token cannot | role `admin` | would need a per-account grant |
| **GitHub publication is off**, and administrators only when it is on | `feedback.integrations.github.enabled: false`, `publish_roles: [admin]` | configuration |
| A published summary never contains files, diagnostics, packages, names, internal notes or ratings, and a private report is never published | fixed in code | not configurable, on purpose |

## Changes to the design since the plan was approved

* **Visibility moved from "checked in the route" to "a clause of the query"**, and a hidden submission is answered with the same
  status and body as a missing one, by id and by number (ADR 0012).
* **The log is selected by ownership, not by time window** (ADR 0014): a window alone would hand a person other people's requests.
* **A request id is made by the server and returned as `X-Request-Id`; one a client sends is ignored.**
* **Consent is the JSON value `true`**, not a truthy value; so are the options that make a package or a publication wider.
* **Patterns are held to linear time.** The existing `private_key` and `connection_string` patterns were quadratic.
* **The artifact store gained an optional `delete`** so a removed attachment, an expired draft or a retained file stops being kept.
* **The image carries `AGENTLAB_BUILD`**, set by `publish`, so an environment report can name the build.
* **Nothing a person wrote is ever on a line of its own in a package or an issue**: not in a task file's criteria, not in a
  title, not in a version or a file name (found by tests of the package, not by review).
* **A number is never given again** (`submission_counters`): numbering was `max + 1`, which would have reused the number of a
  deleted newest submission.
* **`GET /submissions/options` gained the attachment limits**, so the form can say what a file may be before it is sent.
* **A reporter's whole interface is Feedback & Support**; a person with a temporary password still has the account page alone.

## Checked against the specification (2026-10-13)

| Criterion | State |
|---|---|
| AC-01, AC-02 independent submissions | Built; tested through the API and in a browser |
| AC-03 required fields | Built; tested (an issue needs a description and an impact; a feature a title and the problem) |
| AC-04 multiple files | Built; tested |
| AC-05 server-side validation | Built; tested (spoofed types and names, hostile archives, limits) |
| AC-06, AC-07 diagnostics selected, reviewed and approved | Built; tested through the API and the interface (nothing pre-ticked, review and consent before sending) |
| AC-08 redactor masks known secret types in diagnostics and packages | Tested for diagnostics, attachments and packages |
| AC-09 unavailable diagnostics do not prevent submission | Tested (API and interface) |
| AC-10 no access to another user's submissions or attachments | Tested extensively (isolation, identifiers in every spelling, enumeration, other people's collections, files and packages) |
| AC-11 maintainers manage status and triage | Built; tested through the API and the interface |
| AC-12 users see details and updates | Built (details, history, unread marker); tested through the API and a browser |
| AC-13 to AC-16 handoff package, task file, provenance, unknowns and criteria | Built; tested (unit, API, security, browser) |
| AC-17 never executes Claude Code | By construction; a test makes any process start or connection fail while a package is built |
| AC-18 no false claim of collection, scanning, redaction or publication | Tested for scanning (`not_scanned`), collection (unavailable, failed), redaction (`needs_review`), the handoff (what is left out) and publication (a failure says so and leaves the submission as it was) |
| AC-19 GitHub is not a hard dependency | Built; off by default, and nothing is called while off (tested) |
| AC-20 the existing application keeps working | The existing suites pass in the focused runs and in `scripts/ship.py check` below; the existing browser tests pass in the worker image |
| AC-21 migrations, unit, integration and end-to-end tests pass | See "Verification" |
| AC-22 documented for maintainers | `docs/accounts.md`, `docs/feedback-support.md`, configuration, security, ADRs 0011 to 0018, CHANGELOG |

## Defects found in existing code while doing this

| Defect | How it was found | Where fixed |
|---|---|---|
| A request body was read in full before authentication, with no bound: one oversized request could use up the server's memory, signed in or not | opening `/auth/register` and `/auth/login` to anonymous callers | slice 0 (request guard, Caddy's `request_body`) |
| Behind Caddy every visitor had the same address, so the wrong-token, sign-in and registration limits were shared by everyone | reading how the address is derived | slice 0 (`FORWARDED_ALLOW_IPS`, pinned by a test) |
| The guard's own refusals lacked the security headers; `PATCH` was missing from the cross-origin methods | a test for the 413; the accounts page | slice 0 |
| **The agent adapter reused pooled HTTP connections, so a server closing an idle one made a correct agent fail a test with `connection error: ReadError`** | running the acceptance scenarios four at a time on one CPU | `fix/agent-adapter-fresh-connections`; 0 failures in 16 runs under the same load afterwards |
| `private_key` and `connection_string` patterns were quadratic on hostile text | adversarial inputs against every pattern | slice 2 |
| A very long file name lost its extension when shortened, so a legitimate file was refused | a hostile-name test | slice 2 |
| **Slice 0 broke a browser test without anyone seeing it**: the sign-in page puts the password form first, and `test_a_server_that_wants_a_token_...` looked for the token field | running the whole browser suite in the worker image for slice 5 (CI and the host cannot run it) | slice 5 (the test now asks for the token form) |
| A numbering scheme of `max + 1` reuses a deleted number | designing deletion | slice 6 (`submission_counters`) |
| **Three inputs answered 500 on PostgreSQL, which refuses a NUL byte in text**: a user name at sign-in, a display name or an e-mail address at registration, and the reason for a deletion. Nothing on SQLite showed it | running the feedback and account suites against a throwaway PostgreSQL 16 for the first time | slice 0 (`accounts/service.py`: a name with a NUL is an unknown name; names and addresses are one line without control characters) and slice 6 (the reason) |
| A zip entry was written without file-type bits, so unpacking tools show it as `?rw-r--r--` | a test that the archive is safe to unpack | slice 4 |

Not root-caused: the intermittent end-to-end failure of scenario 11 (a clean run reported as insecure) was not reproduced in 78
further runs; its assertion prints the findings if it recurs. It is probably the same family as the adapter defect, which is
now fixed, but that is not established.

## Alternatives considered

- **One generic "ticket" table with a JSON column.** Rejected: the request needs filtering, access control and search per field.
- **Per-person tokens or two shared tokens instead of accounts.** Smaller, but the owner chose accounts.
- **Send submissions to an LLM to improve them.** Not built: it needs separate consent and policy.
- **Store attachments in the database.** Rejected: the artifact store provides content addressing and sensitivity.
- **Select log lines by time window and let the person remove lines.** Rejected: the default would leak.
- **Row-level security in PostgreSQL.** Rejected for now: SQLite is supported, and the policy is one module tested on both.
- **Default retention periods.** Rejected: an unrequested, unrecoverable deletion on upgrade. The mechanism ships; the owner chooses.
- **Publishing on a status change.** Rejected: the unattended disclosure the specification forbids.

## Affected components and files

New: `src/agentlab/accounts/`, `src/agentlab/feedback/` (service, policy, attachments, diagnostics, logcapture, handoff,
retention, publication, integrations), `src/agentlab/security/diagnostic_redactor.py`, `src/agentlab/core/context.py`,
`src/agentlab/api/requestlog.py`, routes `accounts.py`, `feedback.py`, `diagnostics.py`, `handoffs.py`, `publications.py`,
`src/agentlab/cli/cmd_users.py` and `cmd_feedback.py`, migrations `0002` to `0008`, `web/src/pages/feedback/`,
`web/src/components/feedback/`, `docs/accounts.md`, `docs/feedback-support.md`, ADRs 0010 to 0018. Changed: `api/app.py`,
`api/security.py` (request guard), `api/routes/common.py`, `core/config.py`, `storage/orm.py`, `storage/artifacts.py`
(`delete`), `security/redactor.py` (two patterns), `jobs/runner.py` (the run id context), `adapters/http.py` (connections),
`docker/Dockerfile`, `docker/compose*.yaml`, `docker/Caddyfile`, `scripts/ship.py`, `web/openapi.json`,
`web/src/api/schema.d.ts`. Not touched: the orchestrator, skills, evaluation, browser engine and report generator.

## Verification plan

Per slice: `scripts/ship.py check` locally, then CI. The tests of sections 18.1 to 18.6 map to the slices (flows: 1 and 2;
diagnostics and redaction: 2 and 3; security: all; handoff: 4; end to end: 5).

**Run so far** (and what each does and does not show):

* **`scripts/ship.py check` is green on every head of the stack at its final commit** (lint, format, types, the API description,
  governance, the tests CI selects, the web job with its schema, tests and build, and the image build). The heads, in the order
  of the pull requests:

  | Branch | Head | `scripts/ship.py check` |
  |---|---|---|
  | `chore/staging-pipeline-and-remove-railway` | `f0f1f47` | green |
  | `fix/agent-adapter-fresh-connections` | `3d74128` | green |
  | `feat/accounts-and-roles` | `3d053de` | green |
  | `feat/feedback-core` | `e5ba323` | green |
  | `feat/feedback-attachments` | `d3a87f1` | green |
  | `feat/feedback-diagnostics` | `8c795b5` | green |
  | `feat/feedback-handoff` | `51af99e` | green |
  | `feat/feedback-ui` (slices 5 and 6) | `6f2ae00` | green |

  The check prints a count only when something fails, so none is given here; the last head's test stage took 26 minutes. The
  plan's own head differs from the last one by this document and a merge only.
* Focused suites with CI's marker selection over the API, security and feedback tests, the documentation tests and the new
  command-line tests: **895 passed** at the same head.
* **In a real browser, against a real server** (Chromium of the worker image; neither the host nor CI has one):
  `tests/e2e/test_feedback_ui.py` **2 passed** (a person registers, asks for a feature with a file and follows it while a
  maintainer triages, adds an internal note, asks a question and the person sees the answer and none of the notes; then an issue
  report with diagnostics that are collected, reviewed and agreed to, a handoff that is built, previewed, downloaded and
  inspected, the issue resolved and the person seeing the new status) and `tests/e2e/test_web_ui.py` **13 passed**, including
  that no new page scrolls sideways on a phone and the browser test of token sign-in that slice 0 had broken.
* **Deliberate breakage:** each protection was broken in a scratch copy and a test had to fail. Handoff: 26 of 26 caught. The
  interface: 23 of 23 caught (one test was too weak and was strengthened); its publication and deletion panels: 7 of 8, the
  survivor being equivalent (a button that is disabled until the second yes already makes the value the same). Retention
  and publication: 23 of 24 caught, the survivor being equivalent (the storage layer masks the same text a second time). Earlier
  slices: 10, 10 and 9 caught for slices 1, 2 and 3. That is evidence the tests can fail, not that nothing is missing.
* **PostgreSQL 16** (a throwaway container; the production stack uses PostgreSQL): the migrations to `0008` apply and match the
  models (`test_migrations_and_store_on_postgres`); the feedback unit suites (service, attachments, diagnostics,
  handoff, retention, publication: 168 tests, 166 passing; the two that failed expect the database to be called `sqlite`) and
  the feedback API tests (31 passing), the account API and the security suites (99 tests: 97 passing, one reading `sqlite_master`,
  one finding the NUL-byte defect in the table above, which is fixed and passes) were run against it. **The standard suite in CI still runs on SQLite only**; the
  PostgreSQL run is a procedure, not yet a CI job.
* The migrations were also tested up and down on SQLite (`0002` to `0008`). **Not run:** Redis, Ollama, the Docker-marked
  tests, **anything on GitHub**, and the browser tests in CI (it does not run them).
* **Not verified at all:** that a real GitHub accepts what the GitHub client sends (the client is tested against a fake that
  keeps its own issues), and the behaviour of any of this on the live server, which does not have it.

## Risks, rollback, migrations

- **Authentication and authorization.** The most tested part: every operation is enumerated against the roles; hidden and missing
  submissions, collections, files and packages are answered alike. A mistake here exposes submissions, so a change to
  `policy.py` or the role rules needs the security suites to pass and a second review.
- **Open registration on a public server.** The safeguard limits a self-registered person to Feedback & Support, and allowances
  bound what they can store, but anyone can create accounts. `accounts.registration: approval` or `closed` is one setting.
- **Migrations `0002` to `0008`** add tables only (`0007` also seeds one from existing rows). `downgrade` drops them with their
  data; a backup is taken before each publish, and a migration is applied to the live database only by `publish`.
- **Uploads** are the main attack surface: content checks, private storage, no extraction, no execution, bounded work.
- **The captured log** is a new store of data about people's activity: bounded by age (48 hours) and count (50,000), masked,
  holding routes, statuses and account ids and never bodies; `diagnostics.capture_logs: false` turns it off.
- **Regular expressions over hostile text.** A pattern that is not linear is a denial of service; the test that runs every
  pattern against adversarial input must pass before any pattern is added or changed.
- **A package given to a model** carries untrusted text. The task file quotes it in blocks that cannot be closed from inside and
  says not to obey it; that reduces the risk and does not remove it, so the package is for an authorised engineer only.
- **Deletion** is irreversible. Nothing is deleted by default; the policy only touches closed submissions; a dry run shows
  the effect; deleting an open private security report needs an acknowledgement. A downloaded copy, a backup and an
  issue published elsewhere are out of reach, and the documentation says so.
- **Publication** sends text to a system AgentLab does not control. It is off by default, previewed, confirmed, never for a
  private report, never twice, and the token is never stored or logged. Masking is not a guarantee, which is why a doubted text
  needs a second explicit yes.
- **The browser tests are not in CI.** The interface's two workflows are tested in a real browser here; a regression that only
  they would notice can reach a pull request unseen (it did once, in slice 0). Running them in CI needs Chromium there.
- **Rollback of a slice:** revert its merge commit and run `downgrade` to the previous revision; nothing outside the new tables,
  routes and configuration changes. The adapter fix and the request-size limit are independent of the feature and are kept.

## Irreversible steps and human approval point

Approval of decisions 1 to 4 was given on 2026-10-12. The remaining human gates are: **opening the pull requests** (needs a GitHub
credential the server does not have), **merging the first pull request** (the pipeline's own, which changes the merge rule and
so is merged by the owner), and **the first publish of accounts**, which makes registration reachable from the internet.
Applying a migration to the live database happens only in `publish`, after a backup, and only after the slice's pull request is
green. **Setting a retention period or switching on GitHub publication** are the owner's configuration, not a step of this work.

## Open questions

1. **A GitHub credential.** Nothing can be pushed, opened, merged or published until the owner creates the token file described
   in `docs/development.md#the-staged-release-process` (a fine-grained token for this repository only, with pull requests and
   contents read and write and actions read, expiring soon; never pasted into a conversation).
2. **Registration on the live server until the first publish.** With the default `open`, anyone can register as a reporter
   once this is published. Recommended for the first publish: `accounts.registration: approval`, then `open` when the owner
   has looked at it. This is a configuration of the server, not code.
3. **Confirm the safeguard**: self-registered accounts are feedback-only until an admin promotes them
   (`accounts.default_role: maintainer` would give them today's full access; read the risks first).
4. **Retention periods.** The mechanism is built and every period is empty. Proposal to approve, or change: closed
   submissions kept until an administrator deletes them; attachments, diagnostic information and handoff packages removed 365
   days after the submission closed; internal notes kept with the submission; the captured log 48 hours as now. Run
   `agentlab feedback purge --dry-run` after setting them.
5. **GitHub publication**: whether to switch it on, for which repository, with which roles. Until then it does nothing. Whether
   `service_token_sees_security` stays false (it does today).

## Next steps, in order

1. ~~Run `scripts/ship.py check` on each branch head in stack order~~ Done; see "Verification". A head that is changed again
   (a fix after a red check on GitHub, a merge of `main`) has to be checked again before its pull request is updated.
2. With the credential: `pr`, `wait`, `merge` and `publish` for each pull request in order; after the accounts publish, create
   the first administrator on the server with `agentlab users create NAME --role admin` and verify that a forged
   `X-Forwarded-For` through Caddy shows the real client address in `account_events.client`. **Back up before the first publish
   of this feature**: it adds seven migrations.
3. Decide whether PostgreSQL should be a CI job for the feedback suites (the procedure used here is a short plugin that points
   each test's database at a PostgreSQL schema), and whether the browser tests should run in CI (they need Chromium there).
