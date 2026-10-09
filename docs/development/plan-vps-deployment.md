# Implementation Plan: Deploy AgentLab on the Hostinger VPS

- Issue: none yet (unknown)
- Author / agent: Claude Code
- Risk class: HIGH RISK (production infrastructure, firewall, secrets, a public listener)
- Date: 2026-10-08
- Status: approved by the user on 2026-10-08 (Caddy, no IP limit, mock provider); executed. Results: [deployment-vps.md](../deployment-vps.md#what-was-verified-and-what-was-not)

## Objective

Run AgentLab (API, web interface, worker, Redis, PostgreSQL) on the Hostinger VPS `srv2045942` (KVM 1, 1 vCPU, 4 GB RAM,
50 GB disk, Ubuntu 24.04), with no domain yet, and replace the Railway-only deployment path as the target for this
installation.

## Context and constraints

Facts checked on 2026-10-08, not assumed:

| Fact | Evidence |
|---|---|
| This agent session runs **on the VPS itself** (hostname `srv2045942`, public IP `187.127.93.186` equals the VPS address). Docker `29.8.2` and Compose `v5.6.0` are already installed, no containers run. | `hostname`, `docker info`, `docker ps`, `curl api.ipify.org` |
| Only SSH (port 22) listens on public addresses. `ufw` is inactive. The Hostinger account has no firewall group attached (`firewall_group_id: null`). | `ss -ltn`, `ufw status`, Hostinger `vps_virtual-machines_get` |
| 3.9 GB RAM total, 1.6 GB free, 1 CPU. The image build (Node stage, then pip) is the heaviest step. | `free -m`, `nproc` |
| The repository is public, so the VPS can build from it. | HTTP 200 for the repo on GitHub |
| `docker/compose.yaml` already defines api, worker, redis and postgres, with a read-only root file system, no capabilities, and the API published on `127.0.0.1:8080` only. | `docker/compose.yaml` |
| The server refuses to start without a token of 16 or more characters when it listens beyond loopback. | `docs/configuration.md` (`server.host`) |
| The image has no Docker client and no browser. Tests that run untrusted code, and browser tests, are reported BLOCKED. The Docker socket is never mounted ([ADR 0002](../decisions/0002-fail-closed-isolation.md), [installation.md](../installation.md#docker)). | `docker/Dockerfile`, `docs/installation.md` |
| Without a domain there is no publicly trusted TLS certificate. A token sent over plain HTTP to a public IP can be read by anyone on the path. | TLS basics; `docs/security.md` |

## Current behavior / relevant code

The Railway path (`railway.json`, `docker/railway-start.sh`, `docker/agentlab.railway.yaml`, ADR 0007) is for one service
with one volume and the jobs inside it. It does not apply here: the VPS can run the full Compose stack with a separate
worker and Redis, which is the repository's primary path.

## Proposed approach

1. **Add a VPS Compose overlay**, `docker/compose.vps.yaml`, that reuses `docker/compose.yaml` and adds one reverse proxy
   service (Caddy) in front of the API. Caddy serves HTTPS on the IP with its own internal certificate
   (`tls internal`). The browser shows a certificate warning until a domain exists; the token is still encrypted.
   The API stays on the Compose network and is not published on a host port.
2. **Secrets**: generate `AGENTLAB_API_TOKEN` and `AGENTLAB_DB_PASSWORD` with `openssl rand -hex 24` on the VPS and keep
   them in `/root/agentlab/.env` with mode `0600`, outside the repository. The values are never printed to this chat, a
   commit, a log or the docs. You read the token once from that file to sign in.
3. **Firewall** (Hostinger API): create a firewall group `agentlab`, rules accept `22/tcp` and `443/tcp`, and the group
   drops everything else by default. Attach it to the VPS and sync it. Port 22 is the way back in if anything fails, so it
   is added before the group is attached. Optionally restrict both rules to your own IP (open question 2).
4. **Build and start** with `docker compose -f docker/compose.yaml -f docker/compose.vps.yaml --env-file /root/agentlab/.env up -d --build`
   from a fresh clone of `main` in `/root/agentlab/src`. The services restart on boot (`restart: unless-stopped`).
5. **Verify** (below), then write `docs/deployment-vps.md` (what was run and found, what was not) and ADR 0008, and
   list both in the indexes.
6. **Backups**: a nightly `pg_dump` plus a tar of the `data` volume into `/root/agentlab/backups` (7 kept), and a
   Hostinger VPS snapshot taken before the first start. Off-server backups are out of scope until you choose a place.

## Alternatives considered

- **Publish port 8080 over plain HTTP:** rejected; the token travels in the clear.
- **Bind to `127.0.0.1` and use an SSH tunnel:** safest, and the fallback if you do not want a certificate warning to
  be part of daily use. It needs no Caddy and no open `443`.
- **Hostinger Docker Manager (`vps_docker_create`):** rejected; it takes a compose file, not a build context from
  this repository, and would send the secrets to Hostinger's API as project variables.
- **The Railway single-service layout:** rejected; it exists only because of Railway's volume limits.
- **Mounting the Docker socket so sandbox tests run:** rejected; it hands every tested repository root on the host.
  Those tests stay BLOCKED. A separate sandbox host is the right fix and would be its own decision.

## Affected components and files

New: `docker/compose.vps.yaml`, `docker/Caddyfile`, `docs/deployment-vps.md`, `docs/decisions/0008-vps-compose-with-caddy.md`.
Edited: `docs/decisions/README.md` (index), `docs/installation.md` (one link), `README.md` (one link).
Not touched: application code, `railway.json`, CI, the existing Compose file. On the VPS: `/root/agentlab/` (new),
Docker images and volumes (new), a Hostinger firewall group (new) and one VPS snapshot.

## Verification plan

Run after the change and report as is:

1. `docker compose ... config` renders without errors; `pytest tests/integration/test_deploy_files.py -q` and
   `ruff check .` still pass (the existing checks guard the deploy files).
2. Containers `api`, `worker`, `redis`, `postgres`, `caddy` are healthy (`docker compose ps`).
3. `curl -k https://127.0.0.1/health` returns 200; the same without the token on an API route returns 401; with the
   token returns 200.
4. `agentlab doctor` (via `docker compose run --rm api doctor`) shows the database and Redis reachable and lists what is
   blocked (Docker sandbox, browser) with the reason.
5. A run of the bundled demo target completes and a report appears under the `data` volume.
6. From outside (a request to `https://187.127.93.186` from another network, if you can try one) answers; port 8080,
   5432 and 6379 do not. `ss -ltn` on the VPS shows only 22 and 443 on public addresses.
7. `docker compose restart` and a reboot bring the stack back with the data intact.

Not verifiable by me: that the certificate warning is acceptable to your browser, and access from your network.

## Risks, rollback, migrations

- **Lock-out**: a wrong firewall rule could cut SSH. This agent runs on the same server, so a lock-out also ends this
  session. Mitigation: add the accept rules first, attach second; the snapshot and the Hostinger console (`hPanel`)
  remain. Rollback: deactivate the firewall group with `vps_firewall_deactivate`.
- **Memory**: 4 GB for five containers plus a build on 1 vCPU may be tight; the build could take several minutes. If it
  is killed, add 2 GB swap and retry.
- **Public single-token service**: whoever has the token can start runs that send requests from this server and read
  all reports ([security.md](../security.md)). Targets on private networks stay refused in the VPS configuration unless
  you decide otherwise (open question 3).
- **Data**: `docker compose down -v` deletes the volumes; the plan never runs it. Rollback of the deployment is
  `docker compose down` (keeps data). The database is migrated on start and a migration is not undone by an older image.
- **Exposure of secrets**: the `.env` file is on the same disk as the database. Disk access equals full access.
- **The earlier pasted Hostinger API token** is considered exposed and should be revoked by you; this plan does not use it.

## Irreversible steps and human approval point

Approve this plan, and I will run steps 1 to 6 in order. Needs a specific yes for each of:

1. Creating and attaching the Hostinger firewall group (changes who can reach the server).
2. Taking the VPS snapshot (may replace an older snapshot; Hostinger keeps one).
3. Opening port `443` to the internet.
4. Writing the secrets file at `/root/agentlab/.env`.

Nothing is run, installed or changed on the VPS, the firewall, or in the repository until then.

## Open questions / assumptions

1. Certificate warning (Caddy `tls internal`) or SSH tunnel only (no open web port)? Recommended: Caddy now, a real
   certificate when a domain exists.
2. Your own public IP, if you want 22 and 443 limited to it. Unknown to me; this server's address is not yours.
3. Keep `allow_private_networks: false`? Recommended yes, as on Railway.
4. Model providers: only the offline `mock` provider is configured. Say which provider and I will add a reference to a
   key you set in `.env` yourself.
5. Assumed: the branch `claude/agentlab-votw7h` is the task branch and the pull request goes to `main`; no issue
   number exists.
