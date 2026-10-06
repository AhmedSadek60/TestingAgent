---
name: api-agent-testing
description: Agents behind HTTP APIs: contract (2xx, non-empty, no secrets), oversize and Unicode handling, authentication enforcement and unknown-session handling.
---

# API agent testing

Skill `api-agent-testing` v1.0.0 · kind `tests` · taxonomy A, O · default risk class `safe` · test-id prefix `API`

## Purpose

An agent exposed as an API must behave like a well-built service: predictable status codes, clean errors, enforced authentication.

## Applicability

Selected when the target matches any of these agent types: `api`.
Selected when the target matches any of these interfaces: `api`.
The plan always states why a skill was selected or skipped.

## Prerequisites

- An interface of one of these kinds: `api`.
- The authentication test needs an adapter able to omit credentials (HTTP adapter).

## Methodology

A valid request must return 2xx with a body and no secrets; oversized and unusual-Unicode bodies must give 2xx or a clean 4xx but never a 5xx or stack trace; with credentials configured, a request without them must be refused; an invented session id must not expose anything.

## Test generation

Valid, oversized, Unicode, no-auth (when authentication is configured) and bad-session tests. Non-chat operations of an OpenAPI description are not exercised.

Generator: `agentlab.skills.builtin.integrations:api_tests`.

## Execution

Standard conversation engine over the HTTP adapter; the no-auth test sets `omit_auth` for that request only.

## Evaluation rules

- 5xx on malformed or oversized input fails.
- An unauthenticated request must receive 401/403.
- Responses must not contain secret-like material.

## Severity guidance

Default severity on failure: **medium**.

- High: authentication not enforced; secrets in responses.
- Medium: 5xx on bad input.
- Low: inconsistent status codes.

## Evidence requirements

- status code and (redacted) headers
- request/response excerpts

## Metrics

- availability
- contract
- robustness
- authentication

## Limitations

- TLS, CORS and rate-limit behaviour are infrastructure concerns and are not tested.

## References

- https://owasp.org/www-project-api-security/
- https://genai.owasp.org/llm-top-10/

These sources informed the methodology; no third-party content or code was imported into this skill.
