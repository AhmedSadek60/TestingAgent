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
* [Private submissions](#private-submissions)
* [Limits and protections](#limits-and-protections)
* [The API](#the-api)
* [Configuration](#configuration)

## Status

| Part | State |
|---|---|
| Feature requests and issue reports, numbers, workflow, status history, comments and internal notes, labels, priority, severity, assignment, links between submissions, unread marker, duplicate suggestions, audit trail, allowances, private security reports | **Built** (this page) |
| The web pages (request and report forms, My Submissions, the maintainer queue) | **Not yet built.** Everything is reachable through the API; the web interface comes in a later slice |
| File attachments and a redaction pass over what is attached | Not yet built |
| Diagnostics collected from AgentLab (environment, run and trace summaries, logs) | Not yet built |
| An engineering handoff package (`CLAUDE_CODE_TASK.md` and a ZIP) | Not yet built |
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

* A **feature request** (`type: feature`) says the problem to solve and may say the expected behaviour, the main use case, who
  is affected, the impact, the current workaround, a proposed solution, acceptance criteria, the area of AgentLab it concerns,
  dependencies, and the priority the person asks for (`low`, `normal`, `high`, `critical_business_need`).
* An **issue report** (`type: issue`) says how it affects the work, what was expected and what happened, the steps to
  reproduce (or that they are not known), how often it happens, when it was first seen, a workaround, and how the person says
  it affects them (`cosmetic`, `slows_work`, `prevents_workflow`, `blocks_project`, `incorrect_results`,
  `data_loss_or_exposure`, `unavailable`, `other`).
* Either may name the project, run, test, trace, report or target it concerns, and the AgentLab version.

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
also write an **internal note** (`visibility: internal`, never shown to the reporter) or **ask for information**
(`kind: info_request`, shown to the reporter as a request).

**In-app notification.** There is no e-mail. A submission is `unread` for its reporter when a maintainer wrote a public comment
or changed the status after the reporter last looked. `GET /submissions/summary` counts them, `unread=true` lists them, and
`POST /submissions/{id}/seen` marks them seen (so does writing in the thread). Changes the reporter cannot see (priority, labels,
internal notes) never make a submission unread.

## Finding submissions

`GET /submissions` lists what the caller may see, newest activity first, with filters (type, status, category, assigned
priority, severity, assignee, reporter, label, visibility, creation time), a text search over the title and description (a
submission number finds that submission; `%` and `_` in a search are ordinary characters), sorting, and paging
(`limit` up to 200, `offset`). `mine=true` lists the caller's own. `GET /submissions/summary` gives the counts by status and
type.

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
* **Audit trail.** Made, status changes, edits, comments, links, who opened a private submission, and denied attempts, each with
  who and when. It records which fields and which statuses, **never the text**.
* **Changing someone's role or disabling their account** ends their sessions at once ([accounts.md](accounts.md)), so a
  demoted maintainer stops triaging immediately.

What this does **not** do: it does not scan, translate or summarise text, it does not send anything to a language model, and it
does not tell anyone by e-mail.

## The API

All of these need a credential (a session cookie or the API token); a cookie session must also send `X-CSRF-Token` on
requests that change something.

| Call | Who | What |
|---|---|---|
| `GET /submissions/options` | anyone signed in | Categories, statuses, priorities and limits as configured, and whether the caller may triage |
| `GET /submissions/summary` | anyone signed in | Counts of what the caller can see, and how many are unread |
| `GET /submissions/similar` | anyone signed in | Submissions that look like a title |
| `GET /submissions/assignees` | maintainers | Accounts a submission can be assigned to |
| `POST /submissions` | anyone signed in | Make a submission |
| `GET /submissions` | anyone signed in | List and search |
| `GET /submissions/{id}` | anyone signed in | Read one, by id or number |
| `PATCH /submissions/{id}` | maintainers | Triage |
| `POST /submissions/{id}/comments` | anyone signed in | Comment (internal notes and information requests: maintainers) |
| `POST /submissions/{id}/seen` | anyone signed in | Mark the answers seen |
| `POST /submissions/{id}/relations` | maintainers | Link two submissions |
| `DELETE /submissions/{id}/relations/{relation_id}` | maintainers | Remove a link |
| `GET /submissions/{id}/audit` | maintainers | The audit trail |

The full contract is in the generated API description (`web/openapi.json`, or `/docs` on a running server).

## Configuration

The `feedback` section: [configuration.md](configuration.md#feedback). The categories and statuses are lists you can change
without a migration.
