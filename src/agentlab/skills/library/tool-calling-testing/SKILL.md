---
name: tool-calling-testing
description: Tool selection, argument accuracy, result use, unnecessary calls, invalid arguments and tool failures for every discovered tool.
---

# Tool-calling testing

Skill `tool-calling-testing` v1.0.0 · kind `tests` · taxonomy E · default risk class `safe` · test-id prefix `TOOL`

## Purpose

Verify that the agent picks the right tool, calls it with the right arguments, uses the result faithfully, and does not call tools it does not need.

## Applicability

Selected when the target matches any of these agent types: `tool_calling`, `function_calling`, `react`, `autonomous`, `planning`, `workflow`, `supervisor`, `mcp`.
Requires at least one discovered tool.
The plan always states why a skill was selected or skipped.

## Prerequisites

- Tool calls must be observable on the interface (reports_tool_calls); otherwise the skill states that and generates nothing.

## Methodology

Per tool, a natural-language request is derived from its name, description and schema (with a confirmation sentence for tools with side effects so that a careful agent is not penalised). The oracle is the trajectory: the expected tool was called, its arguments match (subset match with `contains` matchers), no duplicate calls. Deterministic tools (calculators) verify that the result is used; failing tools verify honest error reporting.

## Test generation

Tools are ordered read-only first, then side-effecting, bounded by intensity. The generator adds a no-tool-needed test, result-use and invalid-argument tests when a calculator-like tool exists, and tool-failure tests when the interface can replace a tool result.

Generator: `agentlab.skills.builtin.tools:tool_calling_tests`.

## Execution

Read-only tools run as SAFE; side-effecting tools are classified CONTROLLED or HIGH_IMPACT by the safety gate and only run when the target's policy allows it.

## Evaluation rules

- `tool_called` matches name and argument subset.
- Duplicate identical calls are flagged.
- Tool-failure tests require the interface to be able to replace a tool result; otherwise they are BLOCKED, never simulated.

## Severity guidance

Default severity on failure: **medium**.

- High: wrong tool or wrong arguments for a side-effecting tool.
- Medium: wrong argument for a read-only tool; result not used.
- Low: redundant call; extra clarification.

## Evidence requirements

- tool-call trajectory with arguments and results
- expected vs observed call
- tool schema excerpt

## Metrics

- tool_selection_accuracy
- argument_accuracy
- tool_success
- unnecessary_calls
- result_utilization

## Limitations

- Natural-language prompts for unusual tools are inferred from their descriptions and may need a hand-written override in target.yaml.

## References

- https://www.anthropic.com/engineering/writing-tools-for-agents
- https://gorilla.cs.berkeley.edu/leaderboard.html
- https://docs.langchain.com/langsmith/trajectory-evals

These sources informed the methodology; no third-party content or code was imported into this skill.
