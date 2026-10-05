# ADR-0002: Fail closed when isolation is unavailable

- Status: Proposed
- Date: 2026-10-05
- Deciders: Ahmed (to accept by review)

## Context

AgentLab analyses and sometimes runs code it does not trust (target repositories, coding agents, MCP
servers, tools). Running that code on the evaluator host would let a target control the evaluator.

## Problem

Docker may be missing or unusable. Falling back to host execution would silently remove the isolation
guarantee.

## Decision

Untrusted code only ever runs through a `SandboxProvider`. The Docker provider starts containers with no
network, a read-only root filesystem, dropped capabilities, `no-new-privileges`, a non-root user and CPU,
memory, PID and disk limits. When the provider is unavailable, tests that need it are **BLOCKED** with an
explicit reason; repository analysis stays static (no execution). Repository ingestion never runs git
hooks, never trusts a checked-out `.git` directory and caps archive size, file count and path depth.

## Alternatives considered

- Fall back to a subprocess on the host: rejected, unsafe.
- Require Docker for everything: rejected, black-box API testing does not need it.

## Consequences

Coding-agent and command-target tests need Docker. Static analysis, API, MCP-over-HTTP and mock tests do
not. The network `allowlist` mode is unsupported until an egress proxy exists.

## Risks

Container escape remains a residual risk of any container sandbox; the controls above reduce, not
eliminate, it.

## Migration / rollout notes

None.
