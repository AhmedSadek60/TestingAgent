---
name: reliability-testing
description: Repeated runs of key behaviours to measure pass rate, flakiness and consistency across paraphrases and after bad input.
---

# Reliability testing

Skill `reliability-testing` v1.0.0 · kind `tests` · taxonomy O · default risk class `safe` · test-id prefix `REL`

## Purpose

A test that passes once and fails repeatedly must not be reported as a plain PASS. Reliability tests are repeated on purpose.

## Applicability

Applies to every target that can receive messages (the generator itself checks the interfaces).
The plan always states why a skill was selected or skipped.

## Prerequisites

- An interface of one of these kinds: `mock`, `llm`, `api`, `command`, `mcp`, `web`.

## Methodology

Each test is repeated N times (3 at quick intensity, 5 standard, 10 thorough; the global repetition count and per-risk overrides also apply). The result records pass rate, flakiness (some passes and some failures), variance and, for `pass^k`, the probability that all k attempts pass.

## Test generation

Stable factual answer, recovery after malformed input, consistency across paraphrases and stable tool use for the first read-only tool.

Generator: `agentlab.skills.builtin.functional:reliability_tests`.

## Execution

Attempts of one test are run sequentially in fresh sessions; the aggregate status is PASSED only when every attempt passes, FLAKY (a pass-rate below the threshold) otherwise.

## Evaluation rules

- Mixed outcomes are reported as flaky with the pass rate.
- Timeouts and transient errors are counted separately from deterministic failures.
- A single success never overrides repeated failures.

## Severity guidance

Default severity on failure: **medium**.

- High: a core behaviour fails more than 20% of the time.
- Medium: occasional failures (flaky).
- Low: variance in wording only.

## Evidence requirements

- per-attempt outcome
- pass rate and variance
- error kinds per attempt

## Metrics

- pass_rate
- consistency
- recovery
- pass_at_k

## Limitations

- Statistics from 3-10 repetitions are indicative, not conclusive; the report states the sample size.

## References

- https://arxiv.org/abs/2406.12045
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

These sources informed the methodology; no third-party content or code was imported into this skill.
