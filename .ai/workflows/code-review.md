# Code Review (human or agent reviewer)

An agent that wrote the code is not its independent reviewer. Review for:

- **Correctness** — does it meet the acceptance criteria? Edge cases, error handling, concurrency.
- **Security** — secrets, input validation, authn/authz, injection, unsafe defaults, new external calls.
- **Maintainability** — follows existing patterns; readable; no needless complexity.
- **Tests** — behavior covered; tests meaningful; none weakened or removed without reason.
- **Architecture** — respects boundaries; ADR present when a lasting decision was made.
- **Regression risk** — callers, migrations, config, backward compatibility, rollback path.
- **Scope** — only task-related changes; no unrelated edits, dependency churn, or CI permission changes.
- **Honesty** — PR claims match evidence; verification status is accurate.

Report findings by severity (blocking / should-fix / nit) with file and line references.
