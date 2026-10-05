# Bug Fixing

1. **Reproduce** — get a failing case (test, script, or exact steps). If it cannot be reproduced, say so; do not "fix" blindly.
2. **Isolate** — narrow to the smallest failing input/component.
3. **Diagnose** — find the root cause, not only the symptom. Record evidence.
4. **Minimal fix** — change only what the root cause requires; no drive-by refactors.
5. **Regression test** — a test that fails before the fix and passes after.
6. **Verify** — run the regression test and the surrounding suite; report actual results.
7. **Note** — mention related risks or other occurrences as follow-ups, not extra edits.
