# ADR-0015: A handoff is a quoted, masked package for a person to take; AgentLab never runs it

- Status: Proposed
- Date: 2026-10-13
- Deciders: Ahmed (to accept by review)

## Context

A submission is most useful to an engineer when it arrives with what is known about it, in a form that can be given to a
coding assistant. The requirement is a *reproducible, structured, sanitized* package and a task file for Claude Code, and, in
the same breath, that nothing runs Claude Code or changes the repository because someone submitted a report. The package is
built from text that strangers wrote, and its destination is a model that reads text as instructions.

## Problem

1. A report, a log or a file name that says "ignore the above and ..." is, in this package, the *input of a language model*.
2. The package mixes what the reporter claims, what the system observed and what AgentLab or the maintainers decided; the
   reader (human or model) must be able to weigh them differently.
3. Sanitizing is partial: some files (images, documents, archives) cannot be masked at all, and some texts will still hold a
   secret after masking.
4. "Only what is available and authorised" means leaving things out, and a package full of empty placeholders would pretend to
   a completeness it does not have.
5. A package outlives the moment: it must say which version of which submission it reflects, and not change afterwards.
6. Exporting is a disclosure, so it has to be recorded and restricted, and a private report's package must be as hidden as the
   report.

## Decision

* **Build a file; do not run anything.** `EngineeringHandoffService` reads records, writes one restricted artifact and one
  record. It starts no process and opens no connection (a test makes both fail the test). Taking the package and giving it to
  Claude Code is a person's act; there is no route that triggers a run.
* **Quote every person's words, structurally.** What a person or a system wrote is only ever (a) in a fenced block whose fence
  is longer than any run of backticks in it, (b) in JSON, or (c) a quoted, escaped one-line string. No heading, table row or
  list item of the package comes from a person; a file name is reduced to `[A-Za-z0-9._-]`; the stated acceptance criteria are
  quoted one to a line. The task file states in its own words that all of it is evidence and untrusted. This is a reduction of
  risk, documented as such.
* **Keep three kinds of statement apart** (claim, observation, generated or decided) and say which each file is in the evidence
  table and in the manifest; generated files are marked `generated` and are not presented as evidence; proposed acceptance
  criteria are labelled as not approved by anyone.
* **Say what is not there.** A file exists only when its content does; what was left out is listed with the reason (in the
  answer, the README, the privacy summary and the manifest) and, for attachments, as a missing item in the task file. What is
  not known is written "unknown"; nothing is inferred.
* **Safe by default, deliberate to loosen.** Internal notes and files, anything that cannot be sanitized, and texts too large
  to inspect are out unless a maintainer chooses them, and each such choice is marked (`UNSANITIZED`) and warned about; the
  server can refuse internal notes altogether. A text the second inspection doubts blocks sharing outside AgentLab and says so.
* **Reproducible and immutable.** A fixed order, fixed archive timestamps, and a SHA-256 for every file; the record keeps the
  submission's version and does not change when the submission does. Building twice at the same moment gives the same bytes.
* **Restricted and recorded.** Maintainers (and the shared token and the local machine) only, through the same visibility rule
  as the submission, so a hidden report's package is answered as an id that was never issued; stored as a restricted artifact;
  every build and every download is an audit event with counts and never text.

## Alternatives considered

- **Generate the briefing with a language model.** It would turn untrusted text into more fluent untrusted text and send the
  report to a provider; the structure here is deterministic, testable and sends nothing.
- **Put the text in the task file as prose.** More readable, and exactly the shape of a prompt injection.
- **Include everything and let the engineer sort it out.** Non-text attachments cannot be inspected; including them by
  default would put unreviewable content in a file whose purpose is to be handed on.
- **Strip anything that looks like an instruction.** Unreliable in both directions, and it would alter the evidence.
- **Run Claude Code on submit.** Ruled out by the requirement; it would also let any registered person start a process with
  the server's authority.

## Consequences

A package is only as safe as the person who takes it: quoting and masking reduce risk, they do not remove it, and every file
the package holds can still show what masking missed. The task file is long because it quotes whole fields. A reporter's
report that the maintainers do not trust is still turned into a package on request; the package says that the steps are
unverified. Packages are kept until the retention work of a later slice removes them.

## Risks

- A text a pattern does not recognise remains (`needs_review` is a second inspection, not a proof).
- A maintainer can put an unsanitized file in on purpose; the package says so and the audit trail records the build.
- A model given the package can still follow a quoted instruction it should not; the package is for an authorised engineer.

## Migration / rollout notes

Migration `0006` adds one table, `engineering_handoffs`; its downgrade drops it (the stored files remain in the artifact
store until the retention work). New configuration: `feedback.handoff.max_package_mb`, `feedback.handoff.allow_internal_notes`.
