# Configuration

AgentLab is configured by one YAML file, `agentlab.yaml`. **Every key is optional**; the tables below give the
default of each. `agentlab init` writes a commented starting file, and `GET /settings` (and the Settings screen)
shows the configuration in effect with passwords masked.

* **Which file.** `--config PATH`, else `$AGENTLAB_CONFIG`, else `./agentlab.yaml`, else the defaults.
* **Unknown keys and wrong values are refused**, with the file and the key in the message, so a typo is never silently
  ignored (`invalid configuration in agentlab.yaml: ... server.port ...`).
* **Relative paths** are relative to the folder of the configuration file (the working folder when there is no file).
* **Secrets are never written here.** A provider key is a *reference* (`env:NAME` or `secret:NAME`); the API token is
  named by `server.token_ref`; a database or Redis password comes from `AGENTLAB_DATABASE_URL` or `AGENTLAB_REDIS_URL`.
* The file describes **AgentLab**. What it tests is described by a *target file* ([testing-agents.md](testing-agents.md)).

Environment variables:

| Variable | Effect |
|---|---|
| `AGENTLAB_CONFIG` | The configuration file (same as `--config`). |
| `AGENTLAB_DATABASE_URL` | Replaces `storage.database_url`. |
| `AGENTLAB_REDIS_URL` | Replaces `queue.redis_url`. |
| `AGENTLAB_API_TOKEN` | Conventional name for the API token; it is used only when `server.token_ref` is `env:AGENTLAB_API_TOKEN`. |
| `AGENTLAB_MASTER_KEY` | The Fernet key that encrypts stored credentials. Without it a key file (`secrets.key`, owner-only) sits next to the credential file. |
| `AGENTLAB_WEB_DIR` | A built web interface to serve instead of the one inside the package. |
| `AGENTLAB_PDF_FONT_DIR` | A folder with a TrueType font for PDF reports whose text needs more than the built-in fonts cover. |
| `PLAYWRIGHT_BROWSERS_PATH` | Where Chromium is looked for ([browser-testing.md](browser-testing.md)). |

## `providers`

A list. `providers: [mock, ollama]` is shorthand for providers named and typed by those words. Details and examples:
[providers.md](providers.md). The default is the offline `mock` provider.

| Key | Default | Meaning |
|---|---|---|
| `name` | *(required)* | What `evaluation.judges[].provider` and `--llm NAME:MODEL` refer to. |
| `type` | *(required)* | `mock`, `openai`, `openai_compatible`, `openrouter`, `gemini`, `anthropic`, `ollama`, `lmstudio`, `vllm`, `llamacpp`. |
| `base_url` | the type's | The endpoint. Required for `openai_compatible`. |
| `api_key_ref` | none | `env:NAME` or `secret:NAME`, never a key. |
| `model` | none | The default model. |
| `headers` | `{}` | Extra request headers. |
| `timeout` | `60.0` | Seconds to wait for one call. |
| `max_retries` | `2` | Repeats after a rate limit or a temporary failure; never more than `limits.max_retries`. |
| `capabilities` | negotiated | Overrides the capabilities of the model: `chat`, `streaming`, `structured_output`, `json_schema`, `tool_calling`, `multimodal`, `embeddings`, `model_discovery`. |
| `pricing` | `{}` | `{model: {input_per_mtok, output_per_mtok}}`, USD per million tokens. |
| `options` | `{}` | Provider-specific switches. |

## `evaluation`

| Key | Default | Meaning |
|---|---|---|
| `judges` | `[]` | `[{provider, model, weight}]`. Empty means deterministic checks only. A judge is never the model under test. |
| `judge_strategy` | `average` | `single` (the first judge), `average` (weighted), `vote`, `min`. |
| `judge_enabled` | `true` | `false` switches judging off everywhere. |
| `repetitions` | `1` | Times every test is attempted (`--repetitions`). |
| `repetitions_by_risk` | `{}` | Per risk class, e.g. `{high_impact: 5}`. |
| `reliability_repetitions` | `3` | Repetitions of the tests that measure reliability. |
| `pass_threshold` | `1.0` | The fraction of a test's repetitions that must pass for it to pass. Flakiness is reported either way. |
| `timeout_seconds` | `300.0` | The longest any single test may take. A test's own timeout (60 seconds unless its skill says otherwise) is lowered to this, never raised. |
| `scoring_profile` | by agent type | A profile name or file ([evaluation.md](evaluation.md#scoring)); `--profile` overrides it. |
| `llm_test_generation` | `false` | Ask the evaluator model for a few extra safe test scenarios (marked unverified). |
| `latency_budget_ms` | `8000.0` | The latency a single reply should stay under. A scoring profile may set its own. |
| `max_tests_per_skill` | `40` | The most tests one skill may add to a plan. |

## `security`

| Key | Default | Meaning |
|---|---|---|
| `sandbox_required` | `true` | Repositories, coding agents and MCP servers started by a command run only in a sandbox. **Cannot be `false`**: it is refused, because untrusted code is never run on this machine. Without Docker those tests are blocked. |
| `allow_production_targets` | `false` | Whether a target marked `safety.production: true` may receive anything but safe, read-only tests. High-impact tests are never run against production. |
| `block_metadata_endpoints` | `true` | Refuse targets, redirects and API descriptions that point at cloud metadata services: `169.254.169.254`, `metadata.google.internal`, `fd00:ec2::254`, `100.100.100.200` and any link-local address, however the name is written or resolved. |
| `allow_private_networks` | `true` | Allow targets on `localhost` and private networks. `false` refuses them (for a server that should only test public agents). |
| `custom_secret_patterns` | `[]` | Extra regular expressions the redactor and the repository scanner treat as secrets. |
| `canary_prefix` | `AGENTLAB_CANARY` | The prefix of the harmless markers planted to detect leaks. |
| `sandbox.provider` | `docker` | `docker`, or `disabled` (every sandbox request is then refused). |
| `sandbox.image` | `mirror.gcr.io/library/python:3.12-slim` | The image sandboxes start from. |
| `sandbox.cpus` / `memory_mb` / `pids_limit` / `disk_mb` | `1.0` / `1024` / `256` / `512` | The limits of one sandbox. |
| `sandbox.timeout_seconds` | `300.0` | The longest one command may run. |
| `sandbox.user` | `65534:65534` | The non-root user and group commands run as. |

## `browser`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` blocks browser tests. |
| `browsers` | `[chromium]` | Only `chromium` is accepted; Firefox and WebKit were not verified and are refused. |
| `headless` | `true` | |
| `executable_path` | none | A Chromium you already have. |
| `record_video` | `false` | Keep a video of each browser session (restricted evidence). |
| `record_trace` | `true` | Keep a Playwright trace of each browser session. |
| `default_timeout_ms` | `15000` | The wait for an element or a page. |

## `limits`

A run stops *cleanly* when a limit is reached, finishes the evaluation of what ran, and records which limit stopped it
(`stopped_due_to_cost`, `stopped_due_to_timeout`, `stopped_due_to_step_limit`). Tests that did not run are listed as such.

| Key | Default | Meaning |
|---|---|---|
| `max_cost_usd` | `10.0` | Total cost of the run (`--max-cost`): what the target reports and what the judge's priced calls cost. A call with no known price adds nothing, so for those rely on `max_tokens`. |
| `max_test_cost_usd` | `1.0` | Cost of one test. |
| `max_tokens` | `500000` | Total tokens. The planner also fits the plan inside 90% of this. |
| `max_steps` | `100` | Steps (tool calls, turns) of one test. |
| `max_browser_actions` | `200` | Browser actions of one test. |
| `max_execution_time_seconds` | `3600.0` | Wall time of the whole run (`--max-time`). |
| `max_retries` | `2` | The most a call is repeated after a transient failure. A request to the agent is repeated **only when its connection could not be made**, so it provably never arrived (repeating one that may have arrived could repeat an action the agent took). `0` switches retries off. |

## `storage`

Where AgentLab keeps what it writes. A relative path is relative to the configuration file's folder. Back these up.

| Key | Default | Holds |
|---|---|---|
| `database_url` | `sqlite:///.agentlab/agentlab.db` | `sqlite:///relative`, `sqlite:////absolute`, or `postgresql+psycopg://user:password@host:5432/db`. Tables are created and migrated on first use. |
| `artifacts_dir` | `.agentlab/artifacts` | Content-addressed evidence: traces, screenshots, workspaces' outputs. Redacted before it is written. |
| `secrets_file` | `.agentlab/secrets.enc` | Encrypted stored credentials. |
| `reports_dir` | `.agentlab/reports` | `<run>/v<N>/report.{json,md,html,pdf}` and `checksums.json`. |
| `work_dir` | `.agentlab/work` | A temporary folder per run, removed afterwards unless `--keep-workspace`. |
| `uploads_dir` | `.agentlab/uploads` | Documents and archives sent to the API. |
| `skill_drafts_dir` | `.agentlab/skills/drafts` | Imported and generated skills waiting for a human to promote them. |

## `reporting`

| Key | Default | Meaning |
|---|---|---|
| `formats` | `[json, md, html, pdf]` | Written after every run (`--report`). `[]` writes none; `agentlab report` can write any later. |
| `include_sensitive_artifacts` | `false` | Embed restricted evidence (screenshots taken while signed in) in the files. |

## `queue`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `inline` | `inline` runs jobs in the API process; `redis` hands them to `agentlab worker` processes. |
| `redis_url` | `redis://localhost:6379/0` | Put a password in `AGENTLAB_REDIS_URL`, not here. |
| `key_prefix` | `agentlab` | Prefix of the Redis keys, so installations can share a server. |
| `max_concurrent_runs` | `2` | Runs one process works on at once (`agentlab worker --concurrency`). |
| `worker_timeout_seconds` | `45` | How long a worker may say nothing before its runs are closed as failed. |
| `job_ttl_seconds` | `86400` | How long a queued job may wait for a worker before it is given up. |

## `server`

| Key | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Anything but loopback requires a token; the server refuses to start without one. |
| `port` | `8080` | |
| `token_ref` | none | `env:NAME` or `secret:NAME`: the API token (16 or more characters) every request must present. |
| `cors_origins` | `[]` | Origins that may call the API from a page of their own. The bundled interface needs none. |
| `max_upload_mb` | `25` | The largest document or archive an upload may carry. |
| `allowed_paths` | `[]` | Folders on the server that a request may point a target at. Empty means API clients cannot name server paths at all (uploads are always allowed). |
| `serve_ui` | `true` | Serve the web interface at `/` when it has been built. |

## `planning`

| Key | Default | Meaning |
|---|---|---|
| `max_tests` | `400` | Upper bound on the tests of a plan (`--max-tests`). It is **soft**: every taxonomy area and security category keeps its most important runnable test, and so does every test you wrote, so a plan can exceed it. Trimmed tests stay in the plan, marked deselected, and can be re-selected. |
| `seconds_per_call` / `tokens_per_call` / `judge_tokens_per_call` | `3.0` / `800` / `1000` | The assumptions behind the plan's estimates of time and tokens. |
| `adaptive_max_tests` | `60` | The most tests the adaptive second wave may add. |
| `adaptive_repetitions` | `5` | Repetitions of the re-check of a flaky test. |
| `adaptive_variants_per_failure` | `3` | Rephrased variants added for each failed test. |

## Top level

| Key | Default | Meaning |
|---|---|---|
| `max_parallel` | `4` | Tests that run at the same time (`--parallel`). A target that cannot hold parallel sessions is tested one at a time. |
| `skill_dirs` | `[]` | Folders with your own skills ([skills.md](skills.md)). |
| `plugins` | `[]` | Python modules imported at start-up, for plug-ins ([plugins.md](plugins.md)). |
