# Installation

AgentLab is a Python package with a web interface. Everything it needs to run a first evaluation is installed
by `pip`; the pieces below are optional and each one switches on a specific capability. When one is missing,
AgentLab says so (`agentlab doctor`) and reports the tests that need it as **blocked**, never as passed or failed.

## Requirements

| What | Needed for | Without it |
|---|---|---|
| Python 3.12 or newer | Everything | Cannot be installed. |
| Docker (a daemon you can run `docker` against) | Running a repository, a coding agent or an MCP server started by a command, always inside a sandbox | Those tests are **blocked**. Nothing is ever run on your machine instead ([ADR 0002](decisions/0002-fail-closed-isolation.md)). Testing a running agent over HTTP, a model, an MCP server by URL, or documents does not need Docker. |
| Chromium and the `playwright` package | Browser testing of an agent's web interface | Browser tests are **blocked**. See [browser-testing.md](browser-testing.md). |
| Node.js 22 and npm 10 | Building the web interface from a source checkout | `agentlab serve` still serves the API and says the interface is not built. The Docker image builds it for you. |
| Redis 5 or newer | Running evaluations in separate worker processes | The in-process queue is used (`queue.backend: inline`). |
| PostgreSQL 16 | A shared database for a team server | SQLite is used. |
| A model provider | An independent LLM judge, LLM-assisted test design, testing a model as the agent | Only deterministic checks run, and criteria that need a judge are reported as *not judged*. See [providers.md](providers.md). |

Test credentials, API keys and tokens are never needed to install AgentLab and are never written to its
configuration file ([security.md](security.md)).

## Install from a source checkout

```bash
git clone https://github.com/AhmedSadek60/TestingAgent.git
cd TestingAgent
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
agentlab --version
agentlab doctor
```

`.[dev]` adds the optional packages and the test tools. For a smaller install pick what you need:

| Extra | Adds | For |
|---|---|---|
| *(none)* | The CLI, the API, reports, SQLite, and the OpenAI, OpenRouter, Gemini, Ollama, LM Studio, vLLM, llama.cpp and OpenAI-compatible providers | Testing HTTP agents, models and documents |
| `anthropic` | The official `anthropic` SDK | The Anthropic provider |
| `browser` | `playwright` | Browser testing (you still need a Chromium, below) |
| `postgres` | `psycopg[binary]` | A PostgreSQL database |
| `redis` | `redis` | The Redis queue and workers |
| `mcp` | `mcp`, `httpx2` | Testing MCP servers |
| `all` | `anthropic`, `browser`, `postgres`, `redis`, `mcp` | All of the above |
| `dev` | `all`, `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy` | Working on AgentLab ([development.md](development.md)) |

`agentlab doctor` checks the environment and exits with 1 when something is broken (a *warning* such as a missing
Docker is not broken: it tells you what will be blocked). `agentlab doctor --live` also contacts the configured
remote providers.

## The web interface

The interface is a React and TypeScript application in `web/`. It is built into the Python package, and
`agentlab serve` serves it from the same address as the API.

```bash
cd web
npm ci
npm run build        # type-checks, then writes src/agentlab/api/static
cd ..
agentlab serve       # http://127.0.0.1:8080
```

`AGENTLAB_WEB_DIR` points the server at a build somewhere else. A wheel built after `npm run build` carries the
interface inside it; one built before does not, and `agentlab serve` then tells you to build it.

On `127.0.0.1` no token is needed. To listen on anything else a token is **required**, or the server refuses to start:

```bash
export AGENTLAB_API_TOKEN="$(openssl rand -hex 24)"      # 16 or more characters
agentlab serve --host 0.0.0.0                            # with server.token_ref: env:AGENTLAB_API_TOKEN
```

The interface asks for the token when it opens. There is no TLS in AgentLab: put a reverse proxy in front of it
before you expose it ([security.md](security.md#web-interface)).

## Configuration

```bash
agentlab init          # writes agentlab.yaml, target.yaml, skills/ and a private .agentlab/ folder
```

Every key is optional and every key is explained in [configuration.md](configuration.md). A relative path in the
configuration is relative to the folder of the file. State is kept under `.agentlab/` by default: the SQLite
database, artifacts, reports, uploads, working folders and the encrypted credential store. Back up that folder, or
the paths you configured.

Settings that carry a secret come from the environment, so nothing secret is written into a file:

| Variable | Replaces |
|---|---|
| `AGENTLAB_API_TOKEN` | The token named by `server.token_ref: env:AGENTLAB_API_TOKEN` |
| `AGENTLAB_DATABASE_URL` | `storage.database_url` |
| `AGENTLAB_REDIS_URL` | `queue.redis_url` |
| `AGENTLAB_MASTER_KEY` | The key file that encrypts stored credentials |
| `AGENTLAB_CONFIG` | The `--config` option |

## A shared database: PostgreSQL

```bash
pip install -e ".[postgres]"
export AGENTLAB_DATABASE_URL="postgresql+psycopg://agentlab:<password>@db.internal:5432/agentlab"
agentlab doctor          # shows the schema version
```

The tables are created and migrated on first use. A password with characters that are special in a URL has to be
URL-encoded; letters and digits avoid the problem. `/settings` and `agentlab doctor` mask the password.

## Workers: Redis

With `queue.backend: redis` the API only queues runs and one or more workers execute them, so the API stays
responsive and queued runs survive a restart of the API:

```bash
pip install -e ".[redis]"
export AGENTLAB_REDIS_URL="redis://127.0.0.1:6379/0"
agentlab serve --no-worker &           # the API (no worker in this process)
agentlab worker --concurrency 2        # start as many workers as you need
```

A worker that stops reporting for `queue.worker_timeout_seconds` has its runs closed as `failed`, with the reason.
`agentlab worker` stops taking runs when it is told to stop (Ctrl+C or SIGTERM), lets the ones in progress finish
for `--drain-seconds` (30 by default), and then asks the rest to stop at a safe point.

## The sandbox: Docker

Nothing needs configuring when `docker` works for your user. The first test that needs a sandbox pulls
`security.sandbox.image` (`mirror.gcr.io/library/python:3.12-slim`, a mirror of the official image; use any image
you prefer). A sandbox has no network, a read-only root file system, no capabilities, a non-root user and limits
for CPU, memory, processes and disk. `network: allowlist` is **not supported** and is refused rather than
accepted and ignored.

## Browser testing

```bash
pip install -e ".[browser]"
playwright install chromium
agentlab doctor          # "browser (Playwright): chromium at ..."
```

AgentLab drives **Chromium only**. Firefox and WebKit were not verified, so `browser.browsers` refuses them. To use a
Chromium you already have, set `browser.executable_path`, or point `PLAYWRIGHT_BROWSERS_PATH` at the folder.
When AgentLab runs as `root` (a container, a CI job) it starts Chromium with `--no-sandbox`, because Chromium's own
sandbox cannot start there.

## Docker

The repository ships an image and a Compose file that run the API with the web interface, a worker, Redis and
PostgreSQL:

```bash
export AGENTLAB_API_TOKEN="$(openssl rand -hex 24)"      # what the web interface asks you to sign in with
export AGENTLAB_DB_PASSWORD="$(openssl rand -hex 24)"    # letters and digits only: it is written into a URL
docker compose -f docker/compose.yaml up --build
```

Then open <http://127.0.0.1:8080> and sign in with the token. Compose refuses to start when either variable is
missing, and neither is ever written to a file. The API is published on `127.0.0.1` only; put TLS in front of it before
you publish it anywhere else.

What the containers are, and are not:

* **One image, two jobs.** `agentlab serve --no-worker` is the API and interface; `agentlab worker` is the worker.
  Both run as a non-root user (uid 10001) with a read-only root file system and no Linux capabilities.
* **All state is in volumes.** `data` (artifacts, reports, uploads, the credential key), `postgres` and `redis`.
  `docker compose down` keeps them; `docker compose down -v` deletes the data. The configuration is `docker/agentlab.yaml`
  inside the image: mount your own file over `/etc/agentlab/agentlab.yaml` to change it (every path in it must stay
  under `/data`, because nothing else is writable).
* **No Docker and no browser inside.** The image has no Docker client, and the Docker socket is never mounted, so
  tests that need a sandbox are **blocked** with that reason, and so are browser tests. Run those from a checkout or
  a host that has Docker and Chromium, pointed at the same database if you want the results in one place. This is a
  deliberate trade: mounting the socket would hand every tested repository the host.
* **Models.** The image configures only the `mock` provider. Mount a configuration that adds yours; keys are
  references (`env:NAME`) read from the container's environment.
* **An optional master key.** Set `AGENTLAB_MASTER_KEY` (a Fernet key) to keep the key that encrypts stored
  credentials out of the `data` volume.
* **The command line is in the image.** `docker compose -f docker/compose.yaml run --rm api doctor` runs `agentlab doctor`
  with the same configuration.

To build the image alone: `docker build -f docker/Dockerfile -t agentlab .` (it builds the interface in a Node stage and
installs the package in a Python stage; the base images are the `NODE_IMAGE` and `PYTHON_IMAGE` build arguments).

## Check the installation

```bash
agentlab doctor
agentlab fixtures verify chatbot     # proves AgentLab finds the defects planted in a built-in example agent
agentlab test --mock success --intensity quick
```

The second command needs no model, no network beyond your own machine, and no Docker. It exits with 0 when every
expectation of the example agent was met.

## Troubleshooting

| `agentlab doctor` says | Meaning and fix |
|---|---|
| `docker (sandbox): the 'docker' CLI is not installed` / `cannot reach the Docker daemon` | Install Docker or start the daemon. Until then tests that execute code are blocked. |
| `browser (Playwright): the 'playwright' package is not installed` | `pip install -e ".[browser]"`. |
| `browser (Playwright): no Chromium found` | `playwright install chromium`, or set `browser.executable_path`. |
| `queue (redis): ConnectionError` | Start Redis, fix `queue.redis_url` or `AGENTLAB_REDIS_URL`, or set `queue.backend: inline`. |
| `LLM judge: none configured` | Not an error. Add a judge ([providers.md](providers.md)) to grade subjective criteria. |
| `secret store key ... must not be group/world accessible` | `chmod 600` the key file, or use `AGENTLAB_MASTER_KEY`. |
| `agentlab serve` refuses to start | You asked it to listen beyond loopback without a token. Set `server.token_ref`. |
