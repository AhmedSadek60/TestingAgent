---
name: conversational-agent-testing
description: Basic functional and multi-turn behaviour: valid, empty, malformed, long and ambiguous input, instruction following, context retention, corrections and topic switching.
---

# Conversational agent testing

Skill `conversational-agent-testing` v1.0.0 · kind `tests` · taxonomy A, B · default risk class `safe` · test-id prefix `CONV`

## Purpose

Establish that the agent does the ordinary things right before more specialised behaviour is examined, and that it stays well-behaved on degenerate input.

## Applicability

Applies to every target that can receive messages (the generator itself checks the interfaces).
The plan always states why a skill was selected or skipped.

## Prerequisites

- An interface of one of these kinds: `mock`, `llm`, `api`, `command`, `mcp`, `web`.
- LLM-judge criteria on a few tests need a judge; their deterministic assertions run without one.

## Methodology

Short, independently checkable tasks (an arithmetic fact, a formatting instruction, a stated-then-recalled detail) are paired with deterministic oracles; degenerate inputs (empty, one character, 6,000 characters, odd Unicode, template-like text) are checked for graceful handling with no stack traces and no 5xx; multi-turn tests check retention across turns, corrections, topic switches, pronoun resolution and contradictions.

## Test generation

A fixed core set adapted to the interfaces in use, plus workflow tests derived from LLM-suggested workflows when enrichment is on (labelled advisory). Intensity scales the number of repeated and long-context tests.

Generator: `agentlab.skills.builtin.functional:conversational_tests`.

## Execution

Each test uses fresh sessions; multi-turn tests keep one session for all turns. Assertions attach to a specific turn where needed.

## Evaluation rules

- Numeric and keyword oracles are preferred to judged ones.
- Internal error text (stack traces, framework exceptions) in a reply fails the test.
- 5xx on malformed input fails; a clean 4xx or a clarification passes.

## Severity guidance

Default severity on failure: **medium**.

- High: the agent cannot answer or crashes on ordinary input.
- Medium: wrong answer to a trivial factual question; context lost between turns.
- Low: formatting, tone or minor ambiguity handling.

## Evidence requirements

- full transcript per test
- the assertion that failed with the expected and observed text
- HTTP status where applicable

## Metrics

- task_completion
- correctness
- instruction_adherence
- context_retention
- robustness

## Limitations

- Keyword oracles can miss a correct answer phrased in an unexpected way; such cases are flagged for human review rather than failed silently when a judge is unavailable.

## References

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://arxiv.org/abs/2406.12045

These sources informed the methodology; no third-party content or code was imported into this skill.
