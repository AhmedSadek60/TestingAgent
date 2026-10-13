# ADR-0017: Retention counts from closing and removes the content, not the record that it was there

- Status: Proposed
- Date: 2026-10-13
- Deciders: Ahmed (to accept by review)

## Context

Feedback & Support keeps text that strangers wrote, files they attached, diagnostic information, and packages built for
engineers. The requirement is a configurable retention policy with different periods for submissions, attachments, diagnostics,
handoff packages and internal notes; authorised deletion; removal of private artifacts; audit metadata kept without secrets;
no broken or unauthorised access to what was removed; and no deletion of evidence of an active security investigation without
authorisation.

## Problem

1. Removal is irreversible, so a default period would delete things nobody agreed to delete.
2. "Old" is ambiguous: a submission can be old and still open; one can be reopened.
3. Stored files are content addressed, so one file can be the evidence of several records.
4. The audit trail must outlive what it is about, without holding what was removed.
5. Submission numbers are `max(seq) + 1`: deleting the newest would let a number be given twice.
6. A package that was downloaded is out of AgentLab's hands.

## Decision

* **Every period is empty by default.** The mechanism ships; the owner chooses the periods.
* **Periods count from the day the submission closed**, and only a closed submission is ever eligible. An open one, a private
  security report above all, is never touched by the policy. Reopening clears the clock.
* **Remove the content, keep the record of it.** An attachment is marked removed (as when a person removes one), a diagnostic
  item is marked removed by the policy, a package is marked purged with its manifest, internal notes are deleted, and a whole
  submission is deleted by cascade, leaving one audit record of counts, the number and type, who and why (masked).
* **Delete a file only when nothing that is left refers to it**, decided after the records have changed.
* **One administrator action for one submission**, with a reason, and an explicit acknowledgement to delete an open private
  security report; a maintainer and the shared token cannot.
* **A counter keeps numbers unique for good** (`submission_counters`, migration `0007`, seeded from what exists).
* **Say what cannot be done**: a downloaded copy, a backup, and another system are outside; the answer counts the downloads.
* **Apply it from the server and from a command**: the API process runs it every `interval_hours` while any period is set; `agentlab
  feedback purge` (with `--dry-run`) does it on demand.

## Alternatives considered

- **Sensible default periods.** Plausible numbers, and an unrequested, unrecoverable deletion on upgrade.
- **Count from creation or last activity.** Deletes open work, or never deletes something that is quiet.
- **Soft delete (hide, keep the bytes).** Not removal; the privacy request is not met.
- **Delete the audit record too.** Loses the proof that the removal happened and by whom.
- **A background worker for retention.** One more process to run; the API already has a maintenance loop for abandoned runs.

## Consequences

An owner who never sets a period keeps everything. A submission deleted by policy is gone: only counts are left. A reporter who
wants theirs removed asks an administrator. A restore needs a backup taken before. Numbers have gaps.

## Risks

- A misconfigured short period removes data sooner than intended: `--dry-run` shows it first, and the policy only acts on
  closed submissions.
- Another system may hold copies (a downloaded package, a ticket): out of reach, and documented.

## Migration / rollout notes

Migration `0007` adds `submission_counters` and seeds it from `submissions`; its downgrade drops the table (numbering then
falls back to the highest number that exists). No period is set by upgrading.
