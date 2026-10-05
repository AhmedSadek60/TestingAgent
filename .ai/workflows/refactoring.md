# Refactoring

1. **Define invariants** — what behavior must not change (public API, outputs, performance bounds, side effects).
2. **Safety net** — ensure tests cover the invariants; add characterization tests first where missing (esp. legacy code).
3. **Change structure** — in small steps, keeping the code working between steps. No behavior change mixed in.
4. **Verify** — run the same tests after every step; behavior must be identical.
5. **Inspect diff** — confirm no accidental behavior change, no unrelated files, no formatting sweeps.

Keep refactors in their own PR/commits, separate from features. Do not modernize
or update dependencies unless that is the stated task.
