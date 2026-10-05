# Testing and Verification Policy

Language- and framework-neutral. Use the project's own tools (discover them; see
`AGENTS.md` section 4).

## Rules
- Every behavior change gets appropriate verification, close to the changed behavior.
- Do not add tests only to hit a coverage number; do not remove tests because they are inconvenient.
- Do not change expected behavior to make a test pass unless the task changes that behavior.
- Do not claim tests passed unless the command was executed and returned success.
- Report failures, warnings, and skipped tests honestly, with the command and output summary.
- If tests cannot run, state exactly why (missing tool, no network, no credentials, ...).

## Bugs
reproduce -> diagnose -> minimal fix -> regression test -> verify. See `workflows/bug-fixing.md`.

## New functionality
acceptance criteria -> implement -> tests -> verify. See `workflows/feature-development.md`.

## Legacy code
Add characterization tests that pin current behavior before changing it.

## Status vocabulary
- **implemented**: code written, nothing run
- **partially verified**: some relevant checks run and passed; list the gaps
- **verified**: the relevant checks were run and passed; list them
- **not verified**: could not or did not run checks; say why

## Strongest relevant validation
Prefer, in order of availability: targeted tests, full test suite, build,
type check, lint, static analysis, security checks, formatting check.
