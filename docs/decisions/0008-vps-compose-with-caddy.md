# ADR-0008: On a single VPS, run the Compose stack with Caddy in front

- Status: Proposed
- Date: 2026-10-08
- Deciders: Ahmed (to accept by review)

## Context

AgentLab was to be deployed on a Hostinger VPS (Ubuntu 24.04, 1 vCPU, 4 GB RAM, Docker installed) with no domain yet.
The repository's primary container setup is `docker/compose.yaml` (API, worker, Redis, PostgreSQL), which publishes the
API on `127.0.0.1` only and says to put TLS in front of it before publishing it elsewhere.

## Problem

1. The API must be reachable from a browser, and a bearer token is the only authentication, so it must not travel over
   plain HTTP.
2. No domain means no publicly trusted certificate.
3. The sandbox and browser tests need Docker and Chromium, which the image deliberately lacks ([ADR 0002](0002-fail-closed-isolation.md)).

## Decision

* Reuse `docker/compose.yaml` unchanged and add `docker/compose.vps.yaml`: it removes the API's host port and adds a
  Caddy container that terminates TLS on port 443 with a certificate it makes itself for the server's IP.
* Secrets live in an env file outside the repository (`/root/agentlab/.env`, mode `0600`).
* A provider firewall allows ports 22 and 443 only.
* The Docker socket is not mounted; Docker-dependent tests stay BLOCKED.

## Alternatives considered

- **Plain HTTP on a public port:** rejected; the token would be readable on the path.
- **Loopback only plus an SSH tunnel:** safest and needs no certificate, but is awkward for daily use. Remains the fallback.
- **The Railway layout (one service, jobs inside):** exists because of Railway's volume limits; not needed here.
- **Hostinger's Docker Manager API:** takes a compose file without this repository as a build context.
- **Mounting the Docker socket for sandbox tests:** rejected; it gives every tested repository root on the host.

## Consequences

Browsers warn about the certificate until a domain and a public certificate replace it. The service is public and
single-token. One small server runs all five containers. The firewall is a provider setting, not in the repository.
Backups are local until an off-server place is chosen.
