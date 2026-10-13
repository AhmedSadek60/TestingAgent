# Feedback & Support

People who use AgentLab can ask for something new (a **feature request**) or report something that is wrong (an **issue
report**), follow what happens to it, and answer questions. Maintainers triage them. This page describes what exists
today and who can see what. It is delivered in slices; [Status](#status) says which are in.

* [Status](#status)
* [Who sees what](#who-sees-what)
* [Making a submission](#making-a-submission)
* [The workflow](#the-workflow)
* [Triage](#triage)
* [The discussion](#the-discussion)
* [Finding submissions](#finding-submissions)
* [Attachments](#attachments)
* [The web interface](#the-web-interface)
* [Diagnostics](#diagnostics)
* [Engineering handoff](#engineering-handoff)
* [Private submissions](#private-submissions)
* [Limits and protections](#limits-and-protections)
* [The API](#the-api)
* [Configuration](#configuration)

## Status

| Part | State |
|---|---|
| Feature requests and issue reports, numbers, workflow, status history, comments and internal notes, labels, priority, severity, assignment, links between submissions, unread marker, duplicate suggestions, audit trail, allowances, private security reports | **Built** (this page) |
| The web pages: the request and report forms, My Submissions, a submission's page with its discussion, files and history, the maintainer's queue and triage, and the handoff | **Built** ([The web interface](#the-web-interface)) |
| File attachments, with the type checked from the content, hostile archives refused, private storage, a sanitized copy of text files, and a malware scan when the owner has a scanner | **Built** ([Attachments](#attachments)) |
| Diagnostics collected from AgentLab: environment, application and backend logs, errors, the run's metadata, a test's result and trace, and what the browser saw; preview, masking, consent, a downloadable bundle | **Built** ([Diagnostics](#diagnostics)) |
| An engineering handoff package (a sanitized ZIP and `CLAUDE_CODE_TASK.md`) for an authorised engineer | **Built** ([Engineering handoff](#engineering-handoff)) |
| Publishing to GitHub, retention and purging | Not yet built |

Nothing here runs Claude Code, changes the repository or sends a submission anywhere outside this server.

## Who sees what

The rules are applied to the database query, so a caller is never given a row they may not see, and a submission they may not
see is answered exactly as one that does not exist (`404`, the same body).

| | Reporter (own submissions) | Maintainer or admin | Shared API token | Local machine |
|---|---|---|---|---|
| Make a submission | yes, with a daily allowance | yes, no allowance | yes, no allowance | yes |
| See submissions | **their own only** | all | all but other people's private ones | all |
| Public comments | yes | yes | yes | yes |
| Internal notes | **never** | yes | yes | yes |
| Who the maintainers are | **no**: they read "AgentLab maintainer" | yes | yes | yes |
| Assigned priority, severity, assignee, labels, links | **no** | yes | yes | yes |
| Change status, priority and the rest | no | yes | yes | yes |
| Audit trail | no | yes | yes | yes |
| Private (security) submissions of other people | no | yes | **no**, unless `feedback.service_token_sees_security` | yes |

A reporter is the role a person gets when they register ([accounts.md](accounts.md)). Filtering or sorting by something a
reporter is not told (priority, severity, assignee, reporter, label, visibility) is refused to them, because the answer would
tell them.

## Making a submission

`POST /submissions` with a `type`, a `title`, a `category` and the details of that type.

* A **feature request** (`type: feature`) needs a title and the problem to solve (`problem_statement`), and may say the expected behaviour, the main use case, who
  is affected, the impact, the current workaround, a proposed solution, acceptance criteria, the area of AgentLab it concerns,
  dependencies, and the priority the person asks for (`low`, `normal`, `high`, `critical_business_need`).
* An **issue report** (`type: issue`) needs a title, a description of the problem, a category and a summary of how it affects
  the work. It may also say what was expected and what happened (kept apart), the steps to reproduce (or that they are not
  known: nobody is made to invent them), how often it happens, whether it is consistent and blocks work, when it was first
  seen, a workaround, and how the person says it affects them (`cosmetic`, `slows_work`, `prevents_workflow`,
  `blocks_project`, `incorrect_results`, `data_loss_or_exposure`, `unavailable`, `other`).
* Either may name the project, run, test case, test result, trace, report or target it concerns, and the AgentLab version and
  build. These are references: AgentLab does not attach anything because an id was named.

The server answers `201` **after** the submission has been stored, with its number (`FR-2026-00042` for a feature request,
`IR-2026-00042` for an issue report, counted per type and year) and its first status. The answer is what a later read
returns. The person asks for a priority or describes an impact; **the maintainers decide the assigned priority and the
severity** when they triage, and the two are kept apart.

Text is cleaned (control characters are removed) and anything that looks like a secret is masked before it is stored: the
answer, every later read, the audit trail and the logs show the masked text. This lowers the chance that a pasted key is
kept; it cannot be a guarantee, so do not paste credentials.

`GET /submissions/similar` suggests submissions that share words with a title, so the same thing is not filed twice. It is a
suggestion and never blocks anything, and it only considers what the caller may see: for a reporter, their own.

## The workflow

Each type has a list of statuses in the configuration (`feedback.feature_statuses`, `feedback.issue_statuses`); the first is
where a submission starts. A maintainer may move it to any status of its own type's list. Every change is kept in the status
history with who made it, when, and the comment that went with it; nothing is overwritten.

Entering a *resolved* status (`feedback.resolved_statuses`) sets the resolution time, entering a *closed* status
(`feedback.closed_statuses`) sets the closing time, and moving back to an open status clears both. A closed submission is not
counted as open.

## Triage

`PATCH /submissions/{id}` changes the status (with a comment the reporter sees), the assigned priority, the severity, the
assignee (an active maintainer or admin), the category, the labels (the complete set) and the visibility. What is sent is
applied together or not at all.

Send back the `version` that was read. If someone else changed the submission since, the answer is `409` and nothing is
changed, so two maintainers cannot overwrite each other. Sending `null` for the assigned priority, the severity or the assignee
clears it.

`POST /submissions/{id}/relations` links two submissions as `duplicate_of`, `related`, `caused_by` or `implements`. A
`duplicate_of` link needs the same type on both sides and cannot make a loop.

## The discussion

`POST /submissions/{id}/comments` adds a line. The reporter may comment on their own submission, publicly. A maintainer may
also write an **internal note** (`visibility: internal`, never shown to the reporter), **ask for information**
(`kind: info_request`) or **ask for another attempt** (`kind: reproduction_request`); both requests are public.
`GET /submissions/{id}/comments` and `GET /submissions/{id}/history` read the discussion and the status history on their own
(they are also in the submission itself), with the same rules.

**In-app notification.** There is no e-mail. A submission is `unread` for its reporter when a maintainer wrote a public comment,
asked for information or another attempt, or changed the status after the reporter last looked. Which of those count is
`feedback.notify_reporter_on`. `GET /submissions/summary` counts them, `unread=true` lists them, and
`POST /submissions/{id}/seen` marks them seen (so does writing in the thread). Changes the reporter cannot see (priority, labels,
internal notes) never make a submission unread.

## Finding submissions

`GET /submissions` lists what the caller may see, newest activity first, with filters (type, status, category, the
priority the reporter asked for, assigned priority, severity, assignee, reporter, label, visibility, project, AgentLab version,
run, creation time), a text search, sorting, and paging (`limit` up to 200, `offset`). `mine=true` lists the caller's own.
`GET /submissions/summary` gives the counts by status and type.

The text search looks at the title, the description, what an issue says happened and its impact and steps, and what a feature
request says the problem is; a submission number, or an exact run, test, trace, result or report id, finds what names it. `%` and
`_` in a search are ordinary characters. Comments are not searched, so a search cannot be used to read what a reporter may not.

## Attachments

`POST /submissions/{id}/attachments` attaches one file (`multipart/form-data`, field `file`, optional `visibility`). The person
who made the submission may attach to it; a maintainer to any they may see. What a file is, is decided by looking at it:

| Type | How it is checked |
|---|---|
| `png`, `jpg`/`jpeg`, `webp` | The file signature must match. |
| `pdf` | A PDF header. |
| `docx`, `zip` | A ZIP directory that is read, never extracted: the number of entries, the size it declares to expand to, the ratio, absolute and climbing paths, links, encrypted entries, programs and scripts, and archives inside the archive. A Word document must have its document part. |
| `txt`, `md`, `csv`, `json`, `jsonl`, `yaml`/`yml`, `log` | Valid UTF-8 and no binary bytes. |

The browser's claim of a type is recorded and not believed; `.html`, `.svg`, `.exe` and the like cannot be attached at all. A
file name is a label: it is reduced to its last part, without control characters or direction overrides, and the bytes are
kept in the *restricted* artifact store under a name made from their hash, readable only by the server's user.

**Text files get a sanitized copy.** The DiagnosticRedactor masks what it recognises (headers and cookies with their names
kept, credentials in URLs, assignments such as `API_TOKEN=...`, every pattern the server knows, registered secret values,
e-mail, phone and card numbers, and the owner's `redaction.rules`) and then looks at the result again. The original is kept as
uploaded and is what its owner and maintainers can download; the sanitized copy (`version=sanitized`) is what a preview shows
and what the engineering handoff will use. The record says, without any value, what was masked and where. The status is:

| `secrets.status` | Meaning |
|---|---|
| `none` | The patterns found nothing. |
| `redacted` | Patterns matched and were masked. |
| `needs_review` | The second inspection still found something that looks like a key, and masked it too. The file is not to leave AgentLab until a person has looked at it. |
| `not_inspected` | The file is larger than `attachments.sanitize_max_mb`. |
| `not_applicable` | Not a text file (an image, a PDF, an archive): nothing can be masked in it. |

This is risk reduction, not a guarantee: a secret in a shape nothing recognises passes, and an image can show one.

**Malware.** AgentLab ships no scanner. With `attachments.scanner_command` set, every file is scanned before it is kept and
one that is flagged is refused and not stored. Without it the record says `not_scanned`; AgentLab never says a file was scanned
when no scanner ran.

**Limits** (`feedback.attachments`): 10 files, 25 MB each and 100 MB in all per submission; 50 uploads a day for a reporter;
an identical file is not attached twice. The server is the authority on all of them.

**Who sees a file.** The same rule as the submission it is on, applied first: someone who may not see a submission cannot tell
that it has attachments. A reporter does not see files a maintainer attached with `visibility: internal` and cannot attach one.
Every download is recorded in the submission's audit trail. A download is always sent as `application/octet-stream` with
`Content-Disposition: attachment`, `nosniff` and a sandboxing CSP, never as something a browser would display.

**Removal.** `DELETE` makes a file unavailable at once and deletes its stored bytes unless another attachment uses them. A
reporter removes what they attached; a maintainer anything. The record stays, for the audit trail.

## The web interface

Everything above is reachable through the API; the web interface puts it in front of people. **Feedback & Support** is in the
side menu of a maintainer, and is the whole interface of a person with the role *reporter*.

| Page | What it is for |
|---|---|
| `#/feedback` | The ways in: request a feature, report an issue, My submissions (with how many have an answer waiting), and for a maintainer, what needs triage |
| `#/feedback/feature` | The feature request form. A title, a category and the problem are required; everything else is under "More detail" |
| `#/feedback/issue` | The issue report form. A title, the kind of problem, what happened and how it affects the work are required |
| `#/feedback/submissions` | A reporter's own submissions, or a maintainer's queue, with search, filters, sorting and pages |
| `#/feedback/submissions/<number>` | One submission: what was sent, the discussion, files, diagnostic information, the history; and, for a maintainer, triage, links, the engineering handoff and the audit trail |

**Forms that do not get in the way.** Required fields say what is missing next to the field, in words; nothing else is
required, and a person who cannot reproduce a problem says so instead of inventing steps. Similar earlier submissions are
suggested while a title is typed and are only suggestions: sending is never blocked and nothing is merged.
Choosing the security category shows a warning that the report is private. A confirmation with the number appears **only after the
server has kept the submission**; a file that fails shows why and can be tried again, and the submission itself stays saved.

**Context, shown and removable.** A "Report an issue" link on a run, a result or a trace opens the form with identifiers only
(the run, the test, the result, the trace and the screen). The form lists exactly what will be sent and each can be taken out.
Nothing else about the project, and no diagnostic information, is attached on the person's behalf.

**Diagnostic information, chosen, reviewed, agreed to.** Nothing is ticked. A source AgentLab cannot supply is shown with the
reason and cannot be ticked; a screenshot says it must be attached as a file. "Select all available diagnostics" is a convenience
that still requires pressing *Collect and review*, looking at each item (source, period, size, what was masked and where, and the
masked text) and agreeing. A report cannot be sent with diagnostics chosen and not reviewed, and a failure to collect never
stops the report: it is sent without them and says so.

**What this tab saw.** The web interface keeps the last 50 console errors and warnings and the last 50 failed requests of the
tab, in memory only: the method, the path without its query, the status and the server's request id, never a body. They are sent
only if the person ticks "browser console and network errors" and agrees to the preview; closing the tab or signing out forgets them.

**Drafts and leaving.** What a person has typed in a form is kept in the tab (session storage: gone when the tab is closed or
they sign out) and offered back after a reload, with a button to start again; files and diagnostics are not kept. Following a link
inside the interface or pressing *Cancel* with unsent text asks first, and closing the tab asks through the browser.
The browser's own back button cannot be asked in a hash-routed page: the draft is why nothing is lost by it.

**Text is text.** Everything a person or a system wrote (titles, descriptions, comments, logs, file names) is shown as text,
set apart from the page's own words; nothing is rendered as HTML or Markdown. Internal notes are set apart in colour and in
words ("Internal note: maintainers only") and a maintainer is told, before sending, who will see what they write.

**Accessibility.** Every control has a label; a status is always a word and never a colour alone; the list is a table with a
caption; the progress of an upload is announced; everything is reachable by keyboard.

Tested by `web/src/feedback.test.tsx`, `web/src/lib/feedback.test.ts`, `web/src/lib/browserErrors.test.ts`,
`web/src/api/upload.test.ts` and, in a real browser against a real server, `tests/e2e/test_feedback_ui.py`.

## Diagnostics

An issue report is much easier to act on with the evidence next to it, and much riskier to share with it. Diagnostics are
therefore collected only when asked for, from what AgentLab really keeps, shown **masked** for review, and attached only with
the person's **consent**. Nothing leaves the server.

`GET /diagnostics/available` lists every category and says, for each, whether it can be collected here and now, and why not:

| Category | What it holds | Available to |
|---|---|---|
| `environment` | AgentLab's version and build, the kind of deployment, what is switched on. Never host names, addresses, paths or environment variables | everyone |
| `application_logs` | What the web server logged at `WARNING` or above (`INFO` if you set it), including each request that failed or took 2 seconds or more, with its request id | everyone, for their own requests (see below); a maintainer also gets a named run's lines |
| `backend_logs` | The same for the background worker | the same, and only once a worker has logged something |
| `errors` | The errors recorded for a named run or test, and exceptions logged during the person's own requests | a maintainer for a run; everyone for their own requests |
| `run_metadata` | A run's status, times and totals | maintainers |
| `test_summary` | One test's status, attempts, failed checks and (cut) outputs | maintainers |
| `trace` | The recorded events of one test attempt | maintainers |
| `browser` | Console errors and failed requests the web interface saw in the person's tab, sent by the web interface | everyone, from the web interface |
| `screenshot` | **Not collected.** AgentLab cannot take one; it says so and the person attaches a file | manual |

A custom time window may be up to `diagnostics.max_window_minutes`; the fixed ones are 5, 15 and 30 minutes back from the
moment the problem happened (`reference_time`, default now).

**Whose lines.** The server tags what it logs with the run and the request it belongs to, and each request with who made it.
A collection takes: the lines of the **caller's own requests**, **system-level** lines (no person and no run attached: a
startup message, a lost database connection), and, for a maintainer who names a run, that run's. It never takes another
person's requests because they happened in the same minutes, and a request id a browser reports is only data: it does not
select anyone's lines. Every response carries an `X-Request-Id` made by the server (an id a client sends is ignored), which
is how a person can name what failed.

**Preview, then keep.** `POST /diagnostics/preview` collects and masks and keeps nothing; `POST /diagnostics/collect` keeps
the *masked* result as a draft for `diagnostics.draft_hours`. Each item shows its source, period, size, whether it was
truncated, **what was masked and where (kinds and locations, never values)**, and a masked preview. `POST
/diagnostics/redact-preview` does the same for a snippet before it is pasted anywhere. A category that cannot be collected is
listed with the reason and never stops the others; no empty file stands in for it.

**Consent, then attach.** `POST /submissions/{id}/diagnostics` attaches a draft to a submission the person may see. It needs
`consent: true` as the JSON value (the text is `consent_text` of `GET /diagnostics/available`: maintainers will see it, masking
is not a guarantee, nothing leaves the server). `include` lists the items to keep; **items left out are deleted, not kept**.
A draft that is never attached is deleted when it expires.

**The bundle.** `GET /diagnostics/{id}/download` is a ZIP with the files of the specification (`manifest.json`,
`environment.json`, `application/relevant.log`, `backend/relevant.log`, `errors/stack-trace.txt`,
`traces/selected-trace.json`, `browser/console-errors.json`, `browser/network-errors.json`,
`evaluation/run-summary.json`, `evaluation/test-summary.json`, `README.txt`) of the items that were included. The manifest
says what was collected, what was **not available**, what was **left out**, what was masked, a SHA-256 of each file, and that
every file is a *generated, masked copy*. An item the second inspection doubted is marked `needs_review`, and the manifest
says external sharing is blocked until a person has looked. Downloads of an attached collection are recorded.

A collection is its owner's until it is attached; then it is visible to the people who may see the submission, and to no one
else (the same rule as the submission: a private submission's diagnostics are private).

## Engineering handoff

A maintainer can turn a submission into a **package** that an authorised engineer (or Claude Code, run by that engineer) can
work from: a ZIP with the record, what the reporter said, the diagnostics that were attached, the attachments that may go
in, context from the run and test the report names, a masking report, a manifest of SHA-256 hashes and a briefing,
`CLAUDE_CODE_TASK.md`. **Nothing here runs Claude Code, changes a repository or sends the package anywhere**: AgentLab builds a
file and a person takes it. Building one is for maintainers (and the shared token and the local machine); a reporter is refused,
and a package of a private report is as hidden as the report.

```
agentlab-handoff-IR-2026-00042/
  README.md                       what is in it, what is not, privacy warnings, what is missing
  CLAUDE_CODE_TASK.md             the briefing, sections A to K
  manifest.json                   every file, its SHA-256 and what it is made from; when, which version, how masked
  submission/                     submission.json, description.md, status-history.json, related-links.json,
                                  discussion.json (public lines), internal-notes.json (only when asked for)
  reproduction/                   steps.md, expected-vs-actual.md, reproduction-metadata.json   (issue reports)
  feature/requirements.md                                                                       (feature requests)
  diagnostics/                    manifest.json, environment.json, sanitized-application.log, sanitized-backend.log,
                                  sanitized-errors.txt, relevant-trace.json, browser-console-errors.json,
                                  browser-network-errors.json, run-summary.json, test-summary.json
  evidence/                       attachment-manifest.json, selected-attachments/
  context/                        target-version.json, related-run-summary.json, related-test-summary.json
  security/                       redaction-report.json, privacy-summary.json
```

**Only what is available and authorised goes in, and nothing stands in for what is not.** A file is there because its
content exists. What was not put in is listed with the reason in `left_out`, in the README, in `privacy-summary.json`, and, for
attachments, as a line under *Missing information* in the task file.

**What goes in by default.**

| | Default | To change it |
|---|---|---|
| The submission's own record and what the reporter wrote | in, masked | |
| Public comments | in | |
| **Internal notes and internal files** | **out** | `include_internal_notes: true` (and the server must allow it: `feedback.handoff.allow_internal_notes`) |
| Diagnostics that were attached with consent | in, as the masked copies that were kept | `include_diagnostics: false` |
| A text attachment that had secrets | in, **as its sanitized copy** (the original stays private) | |
| A text attachment where none were recognised | in, as uploaded | `exclude_attachments` |
| A text attachment that was not inspected (too large) | **out** | `include_originals`, which marks it unsanitized |
| An image, a PDF, a Word document or an archive | **out**, with the reason | `include_originals`, which marks it **unsanitized** |
| Any attachment named in `exclude_attachments` | out (this wins over `include_originals`) | |

An id in `exclude_attachments` or `include_originals` that is not an attachment of this submission is refused.

**Three kinds of statement, kept apart.** What the **reporter claims** (the description, the steps, what they expected and
saw, how they rate the impact), what the **system observed** (logs, errors, run and test results, the environment) and what
is **generated or decided** (the maintainers' priority and severity, and everything AgentLab wrote for this package). The
evidence table of the task file says which each file is. Every file written by AgentLab is marked `generated` in the
manifest and is never presented as original evidence; a sanitized copy is marked as a copy.

**Everything a person or a system wrote is untrusted.** In the package it is quoted in blocks that cannot be closed from
inside (the fence is longer than any run of backticks in the text), a version, a build or a file name is shown as a quoted,
escaped one-line string or made safe, and the stated acceptance criteria are quoted one to a line. The task file says in its
own words that this is evidence and not instruction (section I). That reduces the risk of a model obeying a report; it does
not remove it, which is why the package is for an authorised engineer and not for a public place.

**The task file** has the sections the specification lists: A role, B objective (bug investigation, performance
investigation, test/evaluation issue, security investigation or feature implementation), C the submission as the reporter
wrote it, D what is known about the environment and **what is missing**, E the evidence and what each file may help
establish, F reproduction (with a note to investigate rather than assume when the steps are incomplete), G the sixteen
engineering instructions, H the ten extra ones for a feature request, I the trust boundaries, J the acceptance criteria
(**stated** ones as the reporter wrote them; when there are none, **proposed** ones, labelled as not approved by anyone) and K
the response expected. Nothing in D is inferred: a fact is given with where it comes from, or it is "unknown".

**Masking.** Everything a person wrote goes through the same masking as a diagnostic, once, and what was masked is recorded
by place and kind, never by value, in `security/redaction-report.json`. If the second inspection still doubts a text, the
package says `external_sharing_blocked` and `redaction_status: needs_review`, and a warning says a person must look before it
leaves AgentLab. Masking reduces risk and does not guarantee that nothing sensitive remains.

**Preview, build, take.** `POST /submissions/{id}/handoffs/preview` returns every file with its hash, what is left out and
the task file as it would read, and keeps nothing. `POST /submissions/{id}/handoffs` builds and keeps the package (restricted
artifact) with a record of who and when and which version of the submission it reflects; it does not change when the
submission does, and building twice at the same moment gives the same bytes (no clock is stored in the archive).
`GET /handoffs/{id}/download` is the ZIP and `GET /handoffs/{id}/task` the task file alone. **Building and every download is
recorded** in the submission's audit trail (`handoff_generated`, `handoff_downloaded`) with counts, never with text.

**Limits.** `feedback.handoff.max_package_mb` (50): a package that would be larger is refused before anything is kept, and
the attachments are read only until the limit is passed. A package is a file in the restricted artifact area under its
hash, mode `0600`; it is served with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and a sandboxing
`Content-Security-Policy`.

## Private submissions

An issue report in the **security category** (`feedback.security_category`), or one the reporter marks
`suspected_security`, is *private*: only its reporter, maintainers, admins and the local machine can read it. It is not listed,
counted, searched or suggested for anyone else, **including the shared API token** (a credential many people may hold) unless
the owner sets `feedback.service_token_sees_security`.

When a maintainer opens someone else's private submission it is recorded in its audit trail, and so is an attempt by anyone to
open one they may not see (at most once every ten minutes for the same person and submission, so it cannot be used to fill the
table). Moving a submission into the security category makes it private and never the other way round; a maintainer can set
the visibility back explicitly.

## Limits and protections

* **Allowances.** A person with the role reporter may make `feedback.max_submissions_per_day` submissions and write
  `feedback.max_comments_per_day` comments in 24 hours; past that the answer is `429` with `Retry-After`. Maintainers and the
  shared token have none.
* **Size.** Each text field is at most 20,000 characters (a title 200); labels are 60 characters, at most 30 per submission.
* **Identifiers** in a URL are an id or a number; anything else is refused before it is looked up.
* **Audit trail.** One record for each thing that was done, with who and when: `created`, `status_changed`, `closed`,
  `priority_changed`, `severity_changed`, `assigned`, `category_changed`, `visibility_changed`, `labels_changed`, `commented`,
  `related`, `unrelated`, `viewed` (a maintainer opened someone else's private submission), `access_denied`,
  `attachment_added`, `attachment_removed`, `attachment_downloaded`, `attachment_rejected`, `diagnostics_collected`,
  `diagnostics_attached`, `diagnostics_downloaded`, `handoff_generated` and `handoff_downloaded`. It records which fields and
  which statuses and how many files, **never the text**.
* **Changing someone's role or disabling their account** ends their sessions at once ([accounts.md](accounts.md)), so a
  demoted maintainer stops triaging immediately.

What this does **not** do: it does not translate or summarise text, it does not send anything to a language model, it does not
tell anyone by e-mail, and it does not scan an attachment for malware unless the owner has configured a scanner.

## The API

All of these need a credential (a session cookie or the API token); a cookie session must also send `X-CSRF-Token` on
requests that change something.

| Call | Who | What |
|---|---|---|
| `GET /submissions/options` | anyone signed in | Categories, statuses, priorities, the attachment limits and the other limits as configured, and whether the caller may triage |
| `GET /submissions/summary` | anyone signed in | Counts of what the caller can see, and how many are unread |
| `GET /submissions/similar` | anyone signed in | Submissions that look like a title |
| `GET /submissions/assignees` | maintainers | Accounts a submission can be assigned to |
| `POST /submissions` | anyone signed in | Make a submission |
| `GET /submissions` | anyone signed in | List and search |
| `GET /submissions/{id}` | anyone signed in | Read one, by id or number |
| `PATCH /submissions/{id}` | maintainers | Triage |
| `POST /submissions/{id}/comments` | anyone signed in | Comment (internal notes and information requests: maintainers) |
| `GET /submissions/{id}/comments` | anyone signed in | The discussion on its own (a reporter: public lines only) |
| `GET /submissions/{id}/history` | anyone signed in | The status history on its own |
| `POST /submissions/{id}/attachments` | anyone signed in | Attach a file |
| `GET /submissions/{id}/attachments` | anyone signed in | List the attachments |
| `GET /submissions/{id}/attachments/{attachment_id}` | anyone signed in | One attachment's record |
| `GET /submissions/{id}/attachments/{attachment_id}/preview` | anyone signed in | A sanitized preview of a text file |
| `GET /submissions/{id}/attachments/{attachment_id}/download` | anyone signed in | The file (`version=sanitized` for the masked copy) |
| `DELETE /submissions/{id}/attachments/{attachment_id}` | anyone signed in | Remove an attachment |
| `GET /diagnostics/available` | anyone signed in | What can be collected, and why not |
| `POST /diagnostics/preview` | anyone signed in | Collect and mask, keep nothing |
| `POST /diagnostics/collect` | anyone signed in | Collect and keep the masked result as a draft |
| `POST /diagnostics/redact-preview` | anyone signed in | See a snippet masked |
| `GET /diagnostics/{collection_id}` | owner, or those who may see its submission | A collection |
| `GET /diagnostics/{collection_id}/download` | the same | The bundle as a ZIP |
| `POST /submissions/{id}/diagnostics` | anyone signed in | Attach a draft, with consent |
| `GET /submissions/{id}/diagnostics` | anyone who may see the submission | What is attached |
| `POST /submissions/{id}/handoffs/preview` | maintainers | What a package would hold, with the task file; keeps nothing |
| `POST /submissions/{id}/handoffs` | maintainers | Build a package |
| `GET /submissions/{id}/handoffs` | maintainers | The packages built from a submission, newest first |
| `GET /handoffs/{handoff_id}` | maintainers | What a built package holds and its task file |
| `GET /handoffs/{handoff_id}/download` | maintainers | The ZIP (recorded) |
| `GET /handoffs/{handoff_id}/task` | maintainers | `CLAUDE_CODE_TASK.md` on its own (recorded) |
| `POST /submissions/{id}/seen` | anyone signed in | Mark the answers seen |
| `POST /submissions/{id}/relations` | maintainers | Link two submissions |
| `DELETE /submissions/{id}/relations/{relation_id}` | maintainers | Remove a link |
| `GET /submissions/{id}/audit` | maintainers | The audit trail |

The full contract is in the generated API description (`web/openapi.json`, or `/docs` on a running server).

## Configuration

The `feedback` section: [configuration.md](configuration.md#feedback). The categories and statuses are lists you can change
without a migration.
