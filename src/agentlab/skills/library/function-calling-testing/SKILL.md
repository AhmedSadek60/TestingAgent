---
name: function-calling-testing
description: Schema conformance of function-call arguments: types, required fields, enums, and asking for missing values instead of guessing.
---

# Function-calling testing

Skill `function-calling-testing` v1.0.0 · kind `tests` · taxonomy E · default risk class `safe` · test-id prefix `FUNC`

## Purpose

Check the contract between the model and its functions: the arguments are valid according to the declared schema and the agent does not invent missing required values.

## Applicability

Selected when the target matches any of these agent types: `function_calling`, `tool_calling`.
Requires at least one discovered tool.
The plan always states why a skill was selected or skipped.

## Prerequisites

- Needs tools with declared JSON-schema parameters.

## Methodology

AST-style checking in the spirit of the Berkeley Function Calling Leaderboard: validate each call's arguments against the declared JSON schema, test enum selection and test that omitting a required value leads to a question rather than a call.

## Test generation

For tools with typed parameters: a schema-conformance test, an enum-choice test when an enum exists and a missing-argument test for the first required parameter.

Generator: `agentlab.skills.builtin.tools:function_calling_tests`.

## Execution

As tool-calling-testing; the missing-argument test asserts that the function was **not** called.

## Evaluation rules

- Arguments must validate against the declared schema.
- A call with a guessed value for a missing required argument fails.

## Severity guidance

Default severity on failure: **medium**.

- High: invalid or guessed arguments on side-effecting functions.
- Medium: schema violations on read-only functions.
- Low: optional-argument handling.

## Evidence requirements

- call arguments and the schema
- the validation error

## Metrics

- argument_accuracy
- schema_validity

## Limitations

- Only JSON-schema-declared parameters can be validated; undeclared parameters are not checked.

## References

- https://gorilla.cs.berkeley.edu/leaderboard.html
- https://developers.openai.com/api/docs/guides/function-calling

These sources informed the methodology; no third-party content or code was imported into this skill.
