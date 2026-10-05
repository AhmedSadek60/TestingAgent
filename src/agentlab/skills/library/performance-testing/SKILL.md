---
name: performance-testing
description: Latency against a budget, token usage, step counts and bounded concurrent sessions (a load check, never a stress test).
---

# Performance testing

Skill `performance-testing` v1.0.0 · kind `tests` · taxonomy P · default risk class `safe` · test-id prefix `PERF`

## Purpose

Report whether the agent answers fast enough, with a sensible amount of work, and holds up for a handful of parallel users.

## Applicability

Applies to every target that can receive messages (the generator itself checks the interfaces).
The plan always states why a skill was selected or skipped.

## Prerequisites

- An interface of one of these kinds: `mock`, `llm`, `api`, `command`, `mcp`, `web`.

## Methodology

Latency of simple requests is compared with `evaluation.latency_budget_ms`; token and step counts of simple tasks are bounded; a concurrency test runs a small, fixed number of parallel sessions and asserts success rate and p95 latency.

## Test generation

Latency, token-use, concurrency (needs parallel sessions) and steps-for-single-tool tests.

Generator: `agentlab.skills.builtin.functional:performance_tests`.

## Execution

The load engine runs a bounded number of sessions and rounds and records per-request latency; it enforces cost and time limits and never exceeds the configured session cap.

## Evaluation rules

- Latency over budget is a failure only for the tested request class.
- Concurrency tests assert the success rate and p95, not raw throughput.
- No stress or denial-of-service testing is performed.

## Severity guidance

Default severity on failure: **low**.

- Medium: concurrent sessions fail or exceed 2x budget.
- Low: latency or token use over budget.

## Evidence requirements

- latency samples
- token and step counts
- load statistics

## Metrics

- latency
- token_usage
- steps
- concurrency

## Limitations

- Results depend on the network and on provider load; AgentLab records the conditions but cannot control them.

## References

- https://opentelemetry.io/docs/specs/semconv/gen-ai/

These sources informed the methodology; no third-party content or code was imported into this skill.
