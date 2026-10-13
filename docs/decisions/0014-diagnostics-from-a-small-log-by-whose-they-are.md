# ADR-0014: Diagnostics come from a small, masked log, selected by whose lines they are

- Status: Proposed
- Date: 2026-10-12
- Deciders: Ahmed (to accept by review)

## Context

An issue report is much easier to act on with the evidence beside it: what the server logged, what failed, which run and test,
what the browser saw. The requirement is to collect that selectively, show it masked, share it only with consent, and never
hand a person another person's activity. AgentLab kept no log: its messages went to standard error.

## Problem

1. "Relevant logs by time window" cannot be answered without keeping some log, and a log of what people did is data about
   people.
2. A time window alone selects *everyone's* lines in those minutes; the requirement says that must not happen.
3. Correlation has to survive two processes (the web server and the worker) and cannot depend on every call site passing an id.
4. A client must not be able to widen its own selection by naming ids that are not its own.
5. What is collected must not be kept unmasked, even briefly, and what the person leaves out must not be kept at all.

## Decision

* **Keep a small log on purpose.** A handler on the `agentlab` logger writes `WARNING` and above (configurable to `INFO`),
  masked and cut, to a bounded table (by age and by count) through a queue and a background thread that drops lines rather than
  block or fail the server. The web server also logs each request that failed (not `401`) or was slow. No bodies, ever.
* **Tag lines with what they belong to**: the run (a context variable the worker sets for the length of the run), the request
  (an id the server makes and returns as `X-Request-Id`; one a client sends is ignored) and, on the request's own line, who
  made it. A mutable record placed in the request scope carries the route and the caller back to the middleware, because
  routing hands the application a copy of the scope.
* **Select by ownership, not by time.** A collection takes the caller's own requests (and everything logged during them),
  system-level lines (no person and no run), and, for a maintainer who names a run, that run's. A request id a browser reports
  is data and selects nothing.
* **Collect, mask, then keep.** The raw text is never stored; each item is masked by the DiagnosticRedactor and cut to a size
  limit, and the masked bytes go to the restricted store. A collection is a draft until it is attached with consent, and a
  draft expires and is deleted with its bytes; items left out at attach time are deleted.
* **Be honest about what is missing.** A source that is off, empty or failed is listed with a reason and has no file; the
  screenshot is *manual* because AgentLab cannot take one. A source failing never stops the others.
* **Evaluation data is for maintainers**, and a reporter who names a run gets the same answer for one that exists and one that
  does not.

## Alternatives considered

- **Read the process's log files.** There are none (standard error), they are unstructured and unmasked, and the API and the
  worker are different processes.
- **A ring buffer in memory.** Not shared between the API and the worker, and lost on restart, which is when it is wanted.
- **Select by time window and let the person remove lines.** The default would leak; the person cannot judge other people's
  lines.
- **Accept the browser's `traceparent`/request id to correlate.** Rejected: it lets a client name another person's request.
- **Ship logs to an external service.** Out of scope, and the requirement is that nothing leaves the server by default.

## Consequences

Anyone who reads the database can read the captured lines (masked, bounded, 48 hours by default); they hold routes, statuses
and account ids, not bodies. Lines of other libraries and INFO-level detail are not in it unless the owner raises the level.
Failed requests of an unauthenticated caller are not recorded (a flood of them must not push out what is useful). Migration
`0005` adds three tables; its downgrade drops them.
