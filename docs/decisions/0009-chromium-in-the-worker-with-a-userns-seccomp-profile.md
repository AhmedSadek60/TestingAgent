# ADR-0009: Chromium in the worker, with a seccomp profile that allows user namespaces

- Status: Proposed
- Date: 2026-10-08
- Deciders: Ahmed (to accept by review)

## Context

On the VPS ([ADR 0008](0008-vps-compose-with-caddy.md)) a run started from the web interface on a web target was blocked: the
worker container had no browser. The same run worked from the host command line.

## Problem

Chromium's own sandbox needs unprivileged user namespaces. In the hardened worker (non-root, all capabilities dropped,
`no-new-privileges`) Docker's default seccomp profile blocks `clone` with namespace flags, `clone3`, `unshare` and `setns`
(measured on this host: `unshare -U` fails with "Operation not permitted"; with `seccomp=unconfined` it succeeds, with
`apparmor=unconfined` alone it still fails). Without the sandbox AgentLab would start Chromium with `--no-sandbox`, and a
browser exploit from a page under test would then reach the worker's database password, API token and data.

## Decision

* Build the image with Chromium only when asked (`--build-arg WITH_BROWSER=1`; the VPS overlay does, the default build and the
  Railway build do not).
* Give the worker `docker/seccomp-chromium.json`: Docker's default profile with the rules that restrict `clone`, `clone3` (the
  `ENOSYS` rule) and the `CAP_SYS_ADMIN`-gated namespace calls replaced by one rule that allows `clone`, `clone3`, `unshare`
  and `setns`. Everything else in the default profile is unchanged.
* Keep the rest of the container as it was; add `shm_size: 1gb` and `HOME=/tmp` (the root file system is read-only, and
  Chromium writes its crash-report database under `HOME`).

## Alternatives considered

- **`--no-sandbox`:** rejected; it removes the protection that matters most when the worker visits hostile pages.
- **`seccomp=unconfined` for the worker:** rejected; far wider than needed.
- **Browser tests only from the host command line:** kept for Docker tests, but it left the dashboard's Run button blocked for web
  targets, which is what was asked for.

## Consequences

Web runs work from the dashboard with Chromium's sandbox on. The worker can create user namespaces, which is a larger kernel
attack surface than Docker's default; a kernel bug in that area would be reachable from the worker. The profile was derived from
`github.com/moby/profiles` (`seccomp/default.json`, fetched 2026-10-08) and does not follow later changes to Docker's default
unless it is regenerated. The image is larger (Chromium and its libraries).
