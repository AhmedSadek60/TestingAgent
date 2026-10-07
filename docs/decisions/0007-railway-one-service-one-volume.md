# ADR-0007: On Railway, run one service with one volume, the jobs inside it

- Status: Proposed
- Date: 2026-10-07
- Deciders: Ahmed (to accept by review)

## Context

AgentLab was asked to be deployable on Railway, with its back end and its web interface. Locally it can run as several
processes: an API, workers that take jobs from Redis, a database and the files they share ([ADR 0004](0004-one-origin-interface-and-signed-report-links.md)
keeps the interface on the API's origin). A run writes reports, evidence and credentials to files that the API then
serves.

Railway's documentation, as read while this was written, says the following; none of it was tried on Railway itself,
which was not available (see [deployment-railway.md](../deployment-railway.md#what-was-verified-and-what-was-not)):

* a volume is attached to one service, and a service with a volume cannot have replicas;
* a volume is mounted owned by root, and `RAILWAY_RUN_UID=0` is the documented remedy for an image that runs as another
  user;
* the builder refuses a Dockerfile with a `VOLUME` instruction;
* a custom start command replaces the image's `ENTRYPOINT` and is not run through a shell, so `$PORT` is not expanded
  in it.

## Problem

1. Files written by one service cannot be read by another when they are on a volume, so a worker in a service of its own
   could not leave a report for the API to serve.
2. The image runs as an unprivileged user (uid 10001), and the volume is owned by root.
3. The server listens beyond loopback, so it must never start without a token.

## Decision

* **One service, built from `docker/Dockerfile`**, runs the API, the built interface and the jobs (`queue.backend:
  inline`), with `numReplicas: 1`. A PostgreSQL service holds the database; one volume at `/data` holds everything else.
* **A start script, `docker/railway-start.sh`**, is the start command. It listens on `$PORT`, uses
  `docker/agentlab.railway.yaml`, and, when started as root (`RAILWAY_RUN_UID=0`), gives `/data` to the unprivileged
  user and runs AgentLab as that user with `no_new_privs`. No root process serves a request.
* **No `VOLUME` instruction** in the Dockerfile; Compose names its own volume.
* **A token is required** (`AGENTLAB_API_TOKEN`); the server stops with a message that names the variable when it is
  missing. Targets on private networks are refused in this configuration (`security.allow_private_networks: false`).
* A `postgres://` or `postgresql://` URL, the form a platform hands out, is opened with the driver AgentLab installs.

## Alternatives considered

- **Separate API, worker and interface services:** rejected; the workers and the API need the same files, which a
  volume cannot give to two services, and object storage (`s3`) is not implemented. A separate interface service would
  also need CORS and a token held in a foreign origin (ADR 0004).
- **Keep the image's `USER` and ask Railway to fix ownership:** not possible with the documented controls; a volume
  owned by root cannot be written by uid 10001.
- **Run as root for good (`RAILWAY_RUN_UID=0` and no privilege drop):** rejected; the server would serve requests as
  root. The start script keeps root for the length of one `chown`.
- **Several instances for throughput:** rejected for now; see the consequences.

## Consequences

A deployment interrupts the runs in progress: the stop signal ends them as `cancelled` with what ran reported, and
Railway documents a short outage for a service with a volume. There is no horizontal scaling: the number of runs at once
is `queue.max_concurrent_runs` of one process. Tests that need Docker (repositories, coding agents, MCP servers started
by a command) and browser tests are BLOCKED, with the reason, because the container has neither; targets that are
reachable over HTTP are tested as usual. Evidence lives on one volume, so backups of the database and of the volume
both matter.

## Risks

Everything under "Not verified" in [deployment-railway.md](../deployment-railway.md#what-was-verified-and-what-was-not):
Railway may behave differently from its documentation, or from the stand-ins used here for the volume, the port and the
database service. The token is the only authentication, and a holder of it can make the server send requests from
Railway's network.

## Migration / rollout notes

Nothing to migrate: the Railway files are new. Moving to a topology with workers in separate services needs a shared
artifact store first, which is a new plug-in ([plugins.md](../plugins.md#artifact-stores)), and a Redis service.
