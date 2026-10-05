# Task Execution (all tasks)

1. **Understand** — read the issue; read relevant code and applicable `AGENTS.md` files;
   note constraints and existing patterns. Discover the toolchain from repo evidence.
2. **Plan** — classify TRIVIAL / STANDARD / HIGH RISK (`../policies/change-control.md`).
   HIGH RISK: write `../templates/implementation-plan.md`, name the human approval point, wait.
3. **Implement** — `git status` first; work on a task branch; smallest safe change.
4. **Verify** — run the strongest relevant project-native checks; record commands and results.
5. **Review** — `git diff` and `git status`: scope, unintended files, secrets, regressions, docs, tests.
6. **Report** — what/why, files, tests run and not run, verification status, risks,
   migrations, follow-ups, assumptions. No "everything works" without evidence.
