# ADR-0012: Feedback & Support: who may see a submission is a clause of the query

- Status: Proposed
- Date: 2026-10-12
- Deciders: Ahmed (to accept by review)

## Context

With accounts ([ADR 0011](0011-accounts-beside-the-shared-token.md)) and open registration, AgentLab holds free text from
people it does not know: feature requests and issue reports, discussed with maintainers. The requirements are that a person sees
only their own, that maintainers triage, that security reports are restricted, and that the audit trail says who did what.

## Problem

1. Every route that reads a submission must apply the same rule, and the rule must not be forgettable when a route is added.
2. A refusal tells the caller something: "forbidden" on a number proves that the number exists.
3. What maintainers decide (priority, severity, who is assigned, internal notes) must not leak to the reporter, including through
   filters and sorting, which are an oracle for hidden fields.
4. Two maintainers triaging at once must not overwrite each other, across processes.
5. The shared token is a credential many people may hold: it is a poor judge of who may read a security report.

## Decision

* **One definition of "may see", as a SQL clause** (`policy.visible`), used by every read: list, detail, counts, suggestions
  and links. A submission outside it is not fetched; the answer is `404` with the same body as a missing one, by id and by number.
  The denial is recorded in the audit trail (at most once per caller and submission per ten minutes).
* **Views are built per role** in one place (`SubmissionService._summaries` and `_detail`): fields a reporter is not told are
  `null` or empty in the same schema, and a maintainer's name reads "AgentLab maintainer". Filters and sorts on those fields are
  refused to a reporter (`403`).
* **Private submissions** (`visibility: security`) are visible to the reporter and to maintainers, admins and the local machine.
  The shared token sees them only with `feedback.service_token_sees_security`.
* **Optimistic concurrency by one statement**: `UPDATE ... SET version = version + 1 WHERE id = ? AND version = ?`; a stale
  version is `409` and nothing else is written. This is correct across processes and across SQLite and PostgreSQL.
* **Numbers** are `FR-` / `IR-` + year + a per-type, per-year sequence, allocated in the transaction, protected by a unique
  constraint and retried when another process took the number.
* **Statuses, categories and limits are configuration**, validated when the configuration loads (a closed status that no workflow
  has is an error). The history table keeps every change.
* **Free text** is cleaned of control characters at the API boundary and masked by the existing redactor column types before it
  is stored; a response is built from the stored values.
* **Audit rows hold fields and statuses, never text**, and have no foreign key to the submission so they outlive it.
* **Allowances** (`max_submissions_per_day`, `max_comments_per_day`) bound what a self-registered account can store; maintainers
  and the token have none.

## Alternatives considered

- **Check access in each route.** Rejected: the next route forgets. A test enumerates the operations, but a clause in the query
  does not depend on remembering.
- **Row-level security in PostgreSQL.** Rejected for now: SQLite is a supported database and the application already has the
  caller's identity; the policy lives in one module and is tested on both.
- **`403` for a hidden submission.** Rejected: it confirms that the number exists.
- **Show assigned priority and severity to the reporter.** Possible later by changing one function; the safe default is to say
  nothing the maintainers have not chosen to publish.
- **Soft-delete and an edit history for the reporter's own text.** Not needed for triage; retention and erasure are a later slice.

## Consequences

A reporter cannot learn that other submissions exist, who the maintainers are or how their own was rated, and a maintainer's
changes cannot be lost to a concurrent edit. Costs: a maintainer sees every non-private submission and, with the role, every
private one (there is no finer grant than the role); similarity suggestions for a reporter are of their own submissions only; the
list endpoint counts with a subquery, which is fine at the expected volumes and should be revisited at tens of thousands of rows.
Migration `0003` adds eight tables and its downgrade drops them with their data.
