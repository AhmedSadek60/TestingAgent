---
name: test-case-generation
description: Guidance the TestDesignerAgent follows to turn a profile into measurable, explainable, de-duplicated test cases within budget.
---

# Test case generation

Skill `test-case-generation` v1.0.0 · kind `meta` · taxonomy n/a · default risk class `safe` · test-id prefix `GEN`

## Purpose

Every generated test must have a measurable purpose, a stated reason and the evidence that triggered it. This skill records the rules that make that true.

## Applicability

Applies to every target that can receive messages (the generator itself checks the interfaces).
The plan always states why a skill was selected or skipped.

## Prerequisites

- Needs an AgentProfile; works best with repository analysis and documents.

## Methodology

Select skills that apply to the profile, ask each to propose tests, give every test a stable id (`<PREFIX>-<TOPIC>-<NNN>`), merge duplicates, classify risk through the safety gate, predict which tests would be BLOCKED (missing credentials, interface, Docker, browser, judge) and keep the total within the step, time and cost budget. Prefer deterministic oracles; add LLM-judge criteria only where no deterministic oracle exists, and say so.

## Test generation

The designer calls each applicable skill's generator or templates with a read-only SkillContext. Output is a TestPlan: tests, coverage over taxonomy A-Q, skipped skills with reasons, a budget estimate and warnings. A second wave is generated adaptively after the first results (for example more tests around a discovered weakness).

Generator: none (this skill produces no tests).

## Execution

Planning is free of side effects: nothing is sent to the target while planning. The plan is shown to the user and can be edited before execution.

## Evaluation rules

- No random questions: each test has an objective, rationale, oracle and metrics.
- Test ids are unique and stable between runs when the profile does not change.
- The plan states what is not covered and why.

## Severity guidance

Default severity on failure: **info**.

- Not applicable.

## Evidence requirements

- rationale per test ('Observed X, therefore Y')
- skill name and version per test
- coverage table

## Metrics

- coverage
- estimated_cost

## Limitations

- Coverage is bounded by the skills installed; uncovered capabilities are reported so a skill can be added.

## References

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://developers.openai.com/api/docs/guides/evaluation-best-practices

These sources informed the methodology; no third-party content or code was imported into this skill.
