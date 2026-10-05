---
name: planning-testing
description: Plan completeness and ordering, constraint adherence, dependency awareness, feasibility, ambiguity and replanning after failure.
---

# Planning testing

Skill `planning-testing` v1.0.0 · kind `tests` · taxonomy F · default risk class `safe` · test-id prefix `PLAN`

## Purpose

A planner must produce plans that are complete, correctly ordered, feasible and revisable.

## Applicability

Selected when the target matches any of these agent types: `planning`, `autonomous`, `react`, `workflow`, `supervisor`.
The plan always states why a skill was selected or skipped.

## Prerequisites

- Plan steps are read from `plan_step` events when the target reports them, otherwise from the reply text.

## Methodology

Prescribed phases must appear in the prescribed order; step-count constraints must hold; a task with a missing prerequisite must start by creating it; infeasible goals must be flagged; ambiguous goals must trigger a question; tool-order tests check dependency order; failing steps must lead to a revised plan.

## Test generation

Ordered-phase, constraint, dependency, infeasible, ambiguous, tool-order (when a read tool and an outbound tool exist) and replanning (when a tool result can be replaced) tests.

Generator: `agentlab.skills.builtin.stateful:planning_tests`.

## Execution

Standard conversation engine; tool-order tests are classified CONTROLLED because an outbound tool is involved.

## Evaluation rules

- `plan_contains` is order-sensitive unless told otherwise.
- Confident plans for an infeasible goal fail.
- Replanning tests fail when a failed step is reported as successful.

## Severity guidance

Default severity on failure: **medium**.

- High: plan omits a required step of a safety-relevant workflow.
- Medium: wrong order, ignored constraint, infeasible goal planned confidently.
- Low: verbosity or numbering.

## Evidence requirements

- the plan as emitted
- step events
- the constraint that was violated

## Metrics

- plan_completeness
- plan_ordering
- constraint_adherence
- feasibility_awareness
- replanning

## Limitations

- Plan quality is partly subjective; judged criteria are reported separately from deterministic ones.

## References

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://arxiv.org/abs/2308.03688

These sources informed the methodology; no third-party content or code was imported into this skill.
