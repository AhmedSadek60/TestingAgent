# ADR-0006: Human reviews sit beside the evaluation and never rewrite it

- Status: Proposed
- Date: 2026-10-06
- Deciders: Ahmed (to accept by review)

## Context

Automated evaluation produces false positives and false negatives, and a person has the last word on whether a
finding is real. If a reviewer's decision replaced the machine's result, the original evidence of what the
evaluation said would be lost, and a later reader could not tell a measurement from an opinion.

## Problem

How can people correct the evaluation while keeping the original evidence and a clear record of who decided
what and why?

## Decision

* A review is a **new row** with the original result, the reviewed result, the reason, a timestamp and the
  reviewer's name. Results and findings are not updated by a review, and the API has no way to edit or delete a
  review (it can create and list them).
* Decisions are `approve`, `false_positive`, `false_negative`, `override_score`, `change_severity` and `comment`.
  The ones that change a verdict, a score or a severity require a reason. The service refuses a decision that
  does not fit the subject (for example a false positive for a result that passed).
* When reviews exist, the report shows the machine scorecard unchanged and a second scorecard, clearly labelled
  as computed from reviewed values. The reviewed scorecard and each review (beside what the evaluation said)
  appear in the HTML and Markdown reports and in the report model that the JSON report carries, and the
  interface shows each review beside the original.
* Comparisons between runs are computed from the stored evaluation, which a review never changes.

## Alternatives considered

- **Update the result in place:** rejected; it destroys the evidence.
- **One merged scorecard:** rejected; a reader could mistake an opinion for a measurement.
- **Reviewer accounts and roles:** deferred; reviews carry a free-text reviewer name, so the API's token is the
  only authentication. Recorded as a limitation in [assumptions](../assumptions.md).

## Consequences

Reviewing does not change the run's stored score, so the numbers in an earlier report stay true. The reviewed
scorecard is computed whenever a report is built.

## Risks

A reviewer name is not verified. In a shared deployment whoever holds the token can write a review in any name.

## Migration / rollout notes

None.
