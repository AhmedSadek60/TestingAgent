# ADR-0003: Skills are data; Python generators are trusted-only

- Status: Proposed
- Date: 2026-10-05
- Deciders: Ahmed (to accept by review)

## Context

Skills encode testing knowledge and must be reusable, versioned and extensible, including by third
parties. Third-party skill files are untrusted input.

## Problem

Allowing arbitrary code in skills would turn "import a skill" into "run untrusted code on the evaluator".

## Decision

A skill is a directory with `skill.yaml` and `SKILL.md`. Tests come from declarative templates evaluated in
a sandboxed Jinja environment (custom delimiters, no imports, no dunder access) or from a Python generator.
Generators are honoured only for built-in skills (module under `agentlab.skills.builtin`) and installed
plug-ins. Imported or generated skills are marked `draft`/untrusted, never selected automatically, never
carry code, and need an explicit human promotion. Importing records a safety review of the source files.

## Alternatives considered

- Python-only skills: simplest, unsafe for sharing.
- Fully declarative only: not expressive enough for data-driven generation from discovery output.

## Consequences

Built-in skills can be rich; shared skills stay safe. A skill cannot grant itself trust.

## Risks

Template sandbox bugs: mitigated by Jinja's immutable sandbox, size limits and tests.

## Migration / rollout notes

None.
