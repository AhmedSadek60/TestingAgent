---
name: agent-fingerprinting
description: Classify the target into agent types with evidence and confidence, build its capability matrix, architecture graph and attack-surface list.
---

# Agent fingerprinting

Skill `agent-fingerprinting` v1.0.0 · kind `analysis` · taxonomy n/a · default risk class `safe` · test-id prefix `FP`

## Purpose

Decide what the target is, so tests are chosen for what it actually does. Every classification is a weighted, explainable score rather than a guess.

## Applicability

Applies to every target that can receive messages (the generator itself checks the interfaces).
The plan always states why a skill was selected or skipped.

## Prerequisites

- Works with any subset of inputs (description only, repository only, black-box probing only).

## Methodology

Combine weak signals (repository imports and tool definitions, declared tools, observed tool calls and retrieved contexts, document types, free-text description, interface kinds) into per-type confidence with the supporting evidence listed. Black-box targets get SAFE-class probes (greeting, capability question, two-turn memory check, a tool-triggering request) and the observations are added as evidence. A capability matrix then states for each capability whether it was detected and whether AgentLab can test it with the available interfaces.

## Test generation

Produces the AgentProfile that all test-generating skills read. It generates no tests itself; its strategy list and attack surfaces explain the plan to the user.

Generator: none (this skill produces no tests).

## Execution

Runs once per target before planning. Probing is read-only and SAFE-class. Optional LLM enrichment is advisory: model-suggested workflows are labelled as unverified and can never raise a confidence score on their own.

## Evaluation rules

- Each type score lists its evidence and can be recomputed.
- Unsupported capabilities are reported as unsupported, never silently dropped.
- LLM enrichment is advisory and labelled as such.

## Severity guidance

Default severity on failure: **info**.

- Not applicable: fingerprinting reports facts. A mismatch between declared and observed behaviour is surfaced as a plan warning.

## Evidence requirements

- per-type evidence list with source and confidence
- probe transcripts (redacted)
- capability matrix with testability

## Metrics

- classification_confidence

## Limitations

- Black-box probing sees only what the agent shows in a few harmless exchanges.
- Types overlap; a target is usually several types at once, and the plan treats it that way.

## References

- https://www.anthropic.com/engineering/building-effective-agents
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

These sources informed the methodology; no third-party content or code was imported into this skill.
