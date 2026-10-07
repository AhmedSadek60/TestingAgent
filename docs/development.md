# Development

How to work on AgentLab: where things are, how to set it up, which checks to run, what the tests need, how to add a part,
and, in the last section, which claims of these pages were verified and how.

Everything in the repository's rules for people and agents applies: [AGENTS.md](../AGENTS.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md). This page only adds what is specific to AgentLab.

* [Where things are](#where-things-are)
* [Setting up](#setting-up)
* [The checks](#the-checks)
* [Tests](#tests)
* [The web interface](#the-web-interface)
* [Adding to AgentLab](#adding-to-agentlab)
* [Dependencies and their licenses](#dependencies-and-their-licenses)
* [The documents are tested](#the-documents-are-tested)
* [Verification status](#verification-status)

## Where things are

| Path | What is there |
|---|---|
| `src/agentlab/` | The Python package. [architecture.md](architecture.md) says what each part is for. |
| `src/agentlab/api/static/` | The built web interface. It is a build product, ignored by git, and written by `npm run build`. |
| `tests/` | `unit/`, `api/`, `integration/`, `security/` and `e2e/`, and `support/` (helpers, fake providers and servers). |
| `web/` | The web interface (React, TypeScript, Vite). `web/openapi.json` is the API's description; its TypeScript types are generated from it. |
| `docker/` | The image, the Compose file, the container's configuration, and what Railway uses ([deployment-railway.md](deployment-railway.md)). |
| `docs/` | These pages. `docs/decisions/` holds the architecture decision records. |
| `fixtures/` | Small sample repositories for the repository-analysis tests. |
| `scripts/` | `export_openapi.py`, and `ai/`, the validator of the governance files. |
| `railway.json` | Railway's configuration as code. |
| `.github/workflows/` | `ci.yml` (the checks below) and `ai-governance.yml`. Both only read the repository. |

## Setting up

You need Python 3.12 or later and Node 22.12 or later (for the web interface). Docker, a browser, PostgreSQL, Redis and
Ollama are optional: each is needed only by the tests that carry its [marker](#tests).

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cd web && npm ci && npm run build
```

`pip install -e ".[dev]"` installs AgentLab with every optional dependency and the tools the checks use.
`npm run build` writes the interface into `src/agentlab/api/static`, where `agentlab serve` finds it. A browser for the
browser tests is installed as [installation.md](installation.md#browser-testing) describes. `agentlab doctor` says what the
machine you are on can and cannot do.

## The checks

These are what the `CI` workflow runs, and what to run before opening a pull request.

| Command | What it checks |
|---|---|
| `ruff check .` | Lint. |
| `ruff format --check .` | Formatting. `ruff format` also formats the Python examples in the Markdown pages. |
| `mypy src` | Types. |
| `python scripts/export_openapi.py --check` | That `web/openapi.json` is the document the API serves. When it is not, run the script without `--check`, then `cd web && npm run gen:api`. |
| `pytest tests -q -m "not docker and not browser and not postgres and not redis and not ollama and not matrix"` | The standard tests. It takes a while: some start real servers and sub-processes. |
| `cd web && npm test` | The web interface's tests. |
| `cd web && npm run build` | The web interface type-checks and builds. |
| `python3 scripts/ai/validate_governance.py` | The governance files (run by its own workflow as well). |

`agentlab fixtures verify` is a check of AgentLab itself: for each example agent, the correct build must pass and every
planted defect must be found, and only those ([testing-agents.md](testing-agents.md)). It needs no model and no network
beyond loopback; the kinds that need Docker or a browser are skipped with a warning where there is none (`--require-all`
turns that into a failure).

## Tests

A test that needs something optional carries a marker and skips itself when the thing is missing, so a test run is never
failed by a machine that lacks it, and a test is never faked. `-m "not docker"` deselects a marker's tests altogether.

| Marker | Needs | Set |
|---|---|---|
| `docker` | A running Docker daemon (the sandbox, and tests that start containers). | |
| `browser` | The `browser` extra and a Chromium Playwright can start. | `PLAYWRIGHT_BROWSERS_PATH` or `browser.executable_path` when it is not in the default place. |
| `postgres` | A PostgreSQL server **that you can lose**: the test drops the `public` schema of its database and creates it again. The pgvector test also needs the `vector` extension and skips itself without it. | `AGENTLAB_TEST_POSTGRES_URL`, for example `postgresql+psycopg://user:password@127.0.0.1:5432/agentlab_test`. |
| `redis` | A Redis server that can be written to. Every test uses keys under a prefix of its own and removes them. | `AGENTLAB_TEST_REDIS_URL` |
| `ollama` | A reachable Ollama server with a small model. | `AGENTLAB_TEST_OLLAMA_URL`, and `AGENTLAB_TEST_OLLAMA_MODEL` when `qwen2.5:0.5b` is not the one to use. |
| `matrix` | Nothing extra, but each test runs a whole fixture through the full suite, which is slow. | |

Tests are grouped by what they exercise: `unit` calls functions, `api` talks to the application in the same process,
`integration` starts real servers, sub-processes and (with the markers) containers, `security` is about what AgentLab
must refuse, and `e2e` follows the scenarios of the specification from start to finish, including a browser driving the
web interface. Helpers that start fake servers (a model provider, an MCP server, a target agent) are in `tests/support/`.
No test calls a hosted service: the providers' tests talk to local stand-ins that speak their wire formats.

Two rules hold for every test. A bug fix comes with a test that fails without it. And a test is never skipped, loosened
or deleted to make a run green: if it is wrong, say why in the commit that changes it.

```bash
pytest tests/unit/test_scoring.py -q            # one file
pytest tests -q -k "canary and not browser"     # by name
AGENTLAB_TEST_REDIS_URL=redis://127.0.0.1:6379/0 pytest tests -q -m redis
```

## The web interface

It is one origin with the API: `agentlab serve` serves the built files at `/`, so there is no CORS to configure
([ADR 0004](decisions/0004-one-origin-interface-and-signed-report-links.md)). To work on it with live reload, start the
API and Vite side by side:

```bash
agentlab serve                                    # the API, on 127.0.0.1:8080
cd web && AGENTLAB_DEV_API=http://127.0.0.1:8080 npm run dev
```

`AGENTLAB_DEV_API` is where Vite's development proxy sends requests. After an API change, regenerate the types
(`python scripts/export_openapi.py && cd web && npm run gen:api`): TypeScript then names what no longer fits.

## Adding to AgentLab

| To add | Where, and what keeps it honest |
|---|---|
| A model provider, an agent adapter, an engine, an assertion, a document parser, a sandbox provider, an artifact store, a vector store or a report format | A plug-in in the same registry the built-in ones use: [plugins.md](plugins.md). `agentlab plugins list` shows what is registered. |
| A skill | `agentlab skills new NAME` for your own; a built-in skill is a folder under `src/agentlab/skills/library/` with `skill.yaml` and `SKILL.md` ([skills.md](skills.md)). The tests validate every built-in skill and the table in `docs/skills.md`. |
| A configuration key | A field in `src/agentlab/core/config.py` with a description, and a row in [configuration.md](configuration.md). Unknown keys are refused, and the documents test fails until the row exists. |
| A command or an option | A Typer command under `src/agentlab/cli/`, and an example in a page that spells the command as typed. The documents test checks every `agentlab ...` line. |
| An API route | A route under `src/agentlab/api/routes/` with a summary, a description and a tag (a test refuses an operation without them). Then regenerate `web/openapi.json` and the types as above. |
| A database column or table | A model in `src/agentlab/storage/orm.py` and an Alembic revision in `src/agentlab/storage/migrations/versions/`. A test fails when the models and the migrations disagree. |
| A security category, a taxonomy letter or an assertion | In the code that defines it, and in the page that lists it: the documents test compares both. |
| A decision that is hard to reverse | An ADR in `docs/decisions/`, from `.ai/templates/adr.md`, and a line in its index. Its status stays *Proposed* until a person accepts it. |

Behaviour changes come with the page that describes them. A page says only what a test or a recorded run supports, and
anything unproven is marked as such.

## Dependencies and their licenses

AgentLab is Apache-2.0 ([LICENSE](../LICENSE)). Before a dependency is added, check that it is not already there, that the
standard library or the code cannot do the job, that it is maintained and what its license is ([AGENTS.md](../AGENTS.md),
section 10). The licenses below were read from the packages' own metadata on 2026-10-07 (`pip show NAME`, and the `license`
fields of `web/package-lock.json`). Nobody has reviewed them beyond that, and a new release can change one.

* **Python:** every direct dependency is MIT, BSD, Apache-2.0 or a similar permissive license, except **`psycopg`** (and
  `psycopg-binary`, which it brings), the PostgreSQL driver of the `postgres` extra, which is **LGPL-3.0**. AgentLab
  imports it as a separate library and does not change it; it is an extra, so a plain install does not have it. The Docker
  image does install it, so whoever distributes an image should keep psycopg's license text with it. Whether that suits
  how you distribute AgentLab is for the maintainers to decide.
* **Web interface:** all 161 packages in `web/package-lock.json` are permissive (mostly MIT), except the twelve
  `lightningcss` packages (MPL-2.0, a file-level copyleft license). They are development tools that Vite uses to build
  the interface, and nothing of them is in what the build produces.

## The documents are tested

`tests/unit/test_docs.py` reads the pages and compares them with the code. It fails when:

* a relative link or an anchor does not resolve, or the README does not link to a page;
* a configuration key is documented that does not exist, exists and is not documented, or has another default than the
  one the table gives as a number or a switch;
* an `agentlab` line in an example names a command or an option that does not exist, or a command is not named anywhere;
* a route written as a method and a path, such as `GET /health`, is not in the API's OpenAPI document;
* the built-in skills, the assertions, the security categories or the taxonomy letters listed differ from the code, or
  a page states a count that is wrong;
* an environment variable the code uses is not documented, or one a page names is used nowhere;
* a repository path written in code font does not exist, the exit codes differ from the command line's, or a decision
  record is missing from its index;
* a Python example does not parse or imports something that does not exist, a YAML example is invalid (configuration,
  target, test and scoring-profile examples are validated against their models, the complete skill example is loaded as
  a skill), or a page is still a stub.

It does not check the prose, and the outputs shown in the pages (a run's summary, a report's excerpt) were copied from
real runs and shortened: they are not generated again.

## Verification status

The words used in these pages are *implemented* (the code exists and a test in this repository exercises it) and *verified*
(it was also run, end to end, in the way described here). This table says, for each part, which of those holds and what
was **not** done. Where it says a thing is unsupported, AgentLab says so too, at the point where you ask for it, instead
of pretending.

| Part | Status | What was done, and what was not |
|---|---|---|
| Planning, running, evaluating, scoring and reporting | Verified | The tests, and the example agents: for each kind, the correct build passes and each planted defect is found (`agentlab fixtures verify`: 12 kinds and 125 planted defects), plus the scenarios of the specification in `tests/e2e/test_acceptance.py`. This shows AgentLab finds what was planted; it does not show it finds what is not in the examples. |
| Command line | Verified | A test fails when a command is run by no test, and the commands and options in the pages' examples are checked against the command line. |
| REST API and the web interface | Verified | API tests run the application in process and as a server; the interface has its own tests and is driven by Chromium in `tests/e2e/test_web_ui.py`. Only Linux was used. |
| Docker sandbox | Verified on Linux | Tests start real containers (marker `docker`): no network, limits, non-root, read-only root, escape attempts. The `allowlist` network mode is **unsupported** and refused. Without Docker the tests that need a sandbox are BLOCKED, never run on the host. |
| Browser engine | Verified for Chromium | Playwright against local test sites (marker `browser`). Firefox and WebKit are **unsupported** and refused. The browser engine follows the steps a test lists; there is **no model-driven browser planner**. |
| SQLite and PostgreSQL | Verified | Migrations and the store run on both. The `postgres` tests ran against PostgreSQL 16, and a container of the image ran against a PostgreSQL 16 container. Only version 16 was tried. |
| Redis queue | Verified | Against a real Redis server (marker `redis`), and the in-process queue in every other test. |
| Model providers | Partly verified | Ollama was exercised against a real local server (marker `ollama`). OpenAI, Anthropic, Gemini, OpenRouter and the OpenAI-compatible servers (LM Studio, vLLM, llama.cpp, others) were verified **only against local stand-ins** that speak their wire formats: never against the real services, because no key was available. |
| Vector stores | Partly verified | The in-memory store is tested. The pgvector store ran against PostgreSQL 16 with pgvector 0.6.0 (it keeps vectors, replaces by id and ranks by cosine similarity); other versions were not tried. Qdrant is **unsupported**. Nothing in a run uses a vector store yet. |
| Artifact stores | Partly verified | `local` and `memory` are tested. An object store such as S3 is **unsupported**. |
| Plug-ins | Verified | The tests install plug-ins by entry point, by module and by code, for every kind, and check what a failing plug-in does. |
| Authorization, redaction and egress controls | Verified | `tests/security/`. These are controls of a testing tool; nobody outside the project has audited them. |
| Docker image and Compose | Partly verified | The image builds and runs (the web interface, a run, the data surviving a replaced container, an orderly stop, PostgreSQL as the database). The Compose stack, started from the built image, took a run through Redis to the worker, kept its data across a stop and a start, ran read-only without capabilities as an unprivileged user, and stopped with exit code 0 in about two seconds. GitHub's runner built the Dockerfile as written; here it was built through a wrapper that adds the CA certificate of the proxy this work was done behind, and `docker compose up --build` as written was not run: the stack was started from the built image. |
| Railway | Not verified on Railway | `railway.json` validates against Railway's published schema, the start script and the image were run the way [deployment-railway.md](deployment-railway.md) says Railway runs them, with stand-ins for the volume, the port and the PostgreSQL service. It was never deployed: no Railway account was used. |
| `CI` workflow | Verified on GitHub | It ran on the pull request that added it and passed there: lint, formatting, types, the OpenAPI document and the standard tests (1,512 passed and 3 skipped, in about eight minutes on GitHub's runner), the web interface's types, tests and build, and the build of the image. The runs before it found three problems. Two were tests that held only on the machine they were written on (a build product that a clean checkout lacks, and colour codes that Typer adds in CI). The third was in the product: a worker that was told to stop waited up to six seconds before it asked its runs to stop, so a test that stops a worker during a run failed whenever the run had got past its tests by then. It was fixed in `src/agentlab/jobs/worker.py`, with a test that fails without the fix. The three tests that start the Railway script as root skip on GitHub's runner, which is not root; they passed here, as root. The Docker, browser, PostgreSQL, Redis, Ollama and matrix tests do not run there. |
| Other systems | Not verified | Only Linux. Windows and macOS were not tried (the sandbox, the start script and the shell examples assume Linux). |
| Client certificates, WebSocket agent protocols, object stores | Unsupported | Asked for, they are refused with a message. |
