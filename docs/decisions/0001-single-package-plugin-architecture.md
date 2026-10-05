# ADR-0001: One Python package with plug-in registries

- Status: Proposed
- Date: 2026-10-05
- Deciders: Ahmed (to accept by review)

## Context

AgentLab needs providers, agent adapters, assertion evaluators, execution engines, sandbox providers,
document parsers, artifact stores, vector stores and report renderers to be swappable. The requirements
suggest a monorepo of apps and packages.

## Problem

A multi-package monorepo adds release and import friction before the seams are proven, while a single
module without seams would make third-party extension impossible.

## Decision

Ship one installable package (`agentlab`, src layout). Every extension point is a typed `Registry`
(`agentlab.core.plugins`) that also loads Python entry points in the group `agentlab.<kind>`, so
third-party packages can contribute plug-ins without touching orchestration code. The web UI is a
separate build artifact in `web/`.

## Alternatives considered

- Monorepo with one package per layer: stronger enforced boundaries, much higher overhead; can be adopted
  later because the registries already mark the boundaries.
- Plain subclassing without registries: no discovery for third parties.

## Consequences

Simple install and tests; boundaries are by convention and lint rules rather than by package metadata.

## Risks

A broken third-party plug-in must not break AgentLab: entry-point loading catches and logs failures.

## Migration / rollout notes

New code only.
