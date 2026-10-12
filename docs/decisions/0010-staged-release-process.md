# ADR-0010: A staged release process, merged and published by a script

- Status: Proposed
- Date: 2026-10-11
- Deciders: Ahmed (to accept by review)

## Context

Changes reached the running system by hand: tests run in a shell, a pull request opened in a browser, a merge by a person, a
rebuild by whoever had the server open. Each step could be skipped or done on a different commit than the one tested, and two
checks that CI runs (`ruff`, and a documented-API-field test) were first seen red on GitHub because they were not run locally.
The owner asked for one path with nine steps: test locally, verify, open a pull request, wait for the checks, verify they are
green, resolve what is red, loop, merge to `main`, publish to the system link.

## Problem

1. The path must be the same for a person and an agent, and each step must refuse to run when the one before it did not hold.
2. Merging to `main` and publishing change shared state: they must not happen around a red check, and a push after the checks
   must not be merged by mistake.
3. `AGENTS.md` said agents never merge their own work, which the requested step 8 contradicts.
4. The credential that opens and merges a pull request must not end up in a repository, a git configuration or a log.

## Decision

* One script, `scripts/ship.py`, with a sub-command per stage (`check`, `pr`, `wait`, `merge`, `publish`, `all`), described in
  `docs/development.md`. Its decisions (what is green, what stops a merge, what a published system must answer) are pure functions
  with unit tests.
* `check` runs exactly what CI runs and writes a marker for the exact commit; `pr` refuses without it. `merge` re-verifies on the
  current head and pins the merge to that sha. `publish` takes a backup, builds, replaces the API and worker, runs a smoke test and
  puts the previous image back if it fails; it refuses while runs are in progress unless forced.
* The owner amends `AGENTS.md` section 8 and the git policy: the staged process may merge its own pull request, under that rule,
  and nothing else may. The process does not touch branch protection, CI permissions or secrets.
* The GitHub token is a file outside the repository (owner-only, fine-grained, one repository, short expiry). Git receives it
  through a temporary credential helper that is deleted afterwards; it is never printed or written into a configuration.

## Alternatives considered

- **Merge and publish from GitHub Actions.** Rejected for now: it needs deployment secrets in GitHub and wider CI permissions, which
  `AGENTS.md` reserves for the owner, and only the server can build and restart its own stack.
- **Keep merging by hand.** Rejected by the owner: the point is that no step can be skipped.
- **Branch protection only.** Recommended in addition (required checks on `main`), but it is a repository setting the script does
  not change, and it does not build, merge on green or publish.

## Consequences

Everything that reaches `main` has passed the same local checks and the same CI checks, on the same commit. The server holds a
token that can merge: it is scoped to one repository and short-lived, and the owner must rotate it. Publishing interrupts runs
in progress unless it waits for them. A database migration is not undone by putting the previous image back: the backup taken
first is the way back.
