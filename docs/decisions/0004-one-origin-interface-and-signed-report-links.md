# ADR-0004: One origin for the API and the interface; reports shown from signed, short-lived links

- Status: Proposed
- Date: 2026-10-06
- Deciders: Ahmed (to accept by review)

## Context

AgentLab has a REST API and a web interface. The API can make the server clone repositories, start containers,
drive a browser and send requests to arbitrary addresses, so who may call it matters. The interface has to
hold a token to call a protected API, and it has to show reports, which are HTML documents built from data an
agent under test produced.

## Problem

1. A web interface on another origin needs CORS, and a permissive CORS setting is a way for any web page to call
   a local security tool.
2. A report in an `<iframe>` cannot send an `Authorization` header, so something must authorise the frame's
   request without putting the API token in a URL (URLs end up in logs, history and `Referer` headers).
3. A report contains text that came from the target. Opened in the interface's own origin it could read the
   interface's session storage (the token) and call the API as the person looking at it.

## Decision

* `agentlab serve` serves the built interface from the **same origin** as the API. The interface calls relative
  paths, so there is no CORS configuration at all; the API refuses a browser request from a foreign origin.
  The interface keeps its token only in memory and in the tab's session storage.
* The interface asks `POST /reports/{id}/view-link` for a link. The server signs `{report, format, expiry}` with
  HMAC-SHA-256 under a key that exists only in the server process's memory and returns
  `/view/<token>`, valid for five minutes, for that one file. `GET /view/{token}` needs no header, checks the
  signature in constant time and serves only that file.
* A report is shown in `<iframe sandbox="allow-scripts">` (no `allow-same-origin`), and the response carries a
  `Content-Security-Policy` with `sandbox allow-scripts; default-src 'none'` plus `frame-ancestors 'self'`, so the
  document has no origin of its own, can reach nothing, and can be framed only by this server's pages.
* The interface's own pages get a strict CSP (`script-src 'self'`, `connect-src 'self'`, `object-src 'none'`).
  Text from an agent is rendered as text, never as markup.
* For development, `npm run dev` proxies the API's paths to a running server, so the code is the same.

## Alternatives considered

- **Separate origin plus CORS:** rejected; it widens the attack surface of a tool that can start containers.
- **Token in the report URL (`?token=`):** rejected; leaks through logs and referrers and never expires.
- **Cookie sessions:** not used; the API has no ambient credential that a foreign page could ride, which is what
  the origin check relies on.
- **Rendering report HTML inline in the interface:** rejected; agent-originated content would share the
  interface's origin.
- **Download-only reports:** kept as well (`GET /reports/{id}/files/{format}` with the token), but a viewer is
  what a reviewer needs.

## Consequences

The interface cannot be hosted separately without a reverse proxy that keeps one origin. View links stop
working when the server restarts, which is intended; the interface asks again. The built interface is not
committed (`src/agentlab/api/static` is ignored); CI and the Docker image build it, and `agentlab serve` says
clearly when it was not built and serves the API alone.

## Risks

A browser bug that breaks iframe sandboxing would expose the viewer. The report itself escapes everything an
agent said, so the sandbox is a second layer, not the only one. A five-minute link can be used by anyone who
obtains it during that time; it opens one report only.

## Migration / rollout notes

None. Tests: `tests/api/test_report_view.py` (links, expiry, tampering, headers) and `tests/e2e/test_web_ui.py`
(real Chromium against a real server, including hostile agent metadata).
