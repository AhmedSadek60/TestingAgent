---
name: regression-testing
description: Compare two runs of the same plan: new failures, fixed tests, score movement and incompatibility flags when plans, versions or settings differ.
---

# Regression testing

Skill `regression-testing` v1.0.0 · kind `meta` · taxonomy O · default risk class `safe` · test-id prefix `REG`

## Purpose

Answer 'did we get better or worse?' honestly, without comparing apples with oranges.

## Applicability

Not selected automatically: it is used by an explicit suite or request (for example `agentlab test --suite regression`).
The plan always states why a skill was selected or skipped.

## Prerequisites

- Needs a baseline run of the same target. Comparison is only meaningful when test ids, skill versions and scoring profile match; mismatches are reported.

## Methodology

Tests are matched by id. Each is classified as unchanged, regressed (pass to fail), fixed (fail to pass), newly failing (new test), removed or incomparable (different skill version, repetitions, judge or scoring profile). Category and total score deltas are computed on the intersection of comparable tests.

## Test generation

The regression suite replays the baseline's tests (all, or only failed/flaky ones) and adds the critical canary set. No new tests are invented.

Generator: none (this skill produces no tests).

## Execution

Runs the selected plan and then calls the comparison engine. Reports show incompatibility warnings next to every delta.

## Evaluation rules

- Only comparable tests contribute to deltas.
- Changed judge model, scoring profile or skill version is flagged, not hidden.

## Severity guidance

Default severity on failure: **info**.

- A new critical failure is reported as a regression regardless of the score movement.

## Evidence requirements

- both run ids and plan hashes
- per-test status transition

## Metrics

- regressions
- fixed
- score_delta

## Limitations

- Non-deterministic agents produce noisy diffs; the comparison uses pass rates over repetitions where they exist.

## References

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://arxiv.org/abs/2406.12045

These sources informed the methodology; no third-party content or code was imported into this skill.
