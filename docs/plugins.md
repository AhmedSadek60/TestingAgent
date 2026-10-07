# Plug-ins

AgentLab is built so that the things that differ between installations are *named, replaceable parts* and not code in
the middle of the platform: which model provider answers, how a target is reached, how a test is driven, how a result is
checked, where evidence is kept, how a report is written. Each is a **plug-in** in a **registry**. The parts AgentLab
ships are registered in the same registries, by the same call, as the ones you add
([ADR 0001](decisions/0001-single-package-plugin-architecture.md)).

This page says what can be added, how, what AgentLab still guarantees when you do, and, because it matters as much,
what **cannot** be added.

* [What can be added](#what-can-be-added)
* [Adding a plug-in](#adding-a-plug-in)
* [Trust: what a plug-in can do and what AgentLab still enforces](#trust)
* [The kinds, one by one](#the-kinds-one-by-one)
* [Seeing what is installed](#seeing-what-is-installed)
* [What is not pluggable](#what-is-not-pluggable)
* [Examples that are tested](#examples-that-are-tested)

## What can be added

| Kind | Entry-point group | A plug-in is | You choose it with |
|---|---|---|---|
| [Model providers](#model-providers) | `agentlab.providers` | a subclass of `LLMProvider` | `providers[].type`, then as target, judge or planner |
| [Agent adapters](#agent-adapters) | `agentlab.adapters` | a subclass of `AgentAdapter` | `custom: {name: {...}}` in the target file |
| [Execution engines](#execution-engines) | `agentlab.engines` | a subclass of `ExecutionEngine` | nothing: an engine takes the tests it says it handles |
| [Assertions](#assertions-evaluators) | `agentlab.assertions` | a function `(params, ctx) -> AssertionResult` | `assertions: [{type: name}]` in a test, a user's test file or a skill |
| [Document parsers](#document-parsers) | `agentlab.document_parsers` | a function `(bytes, name) -> Parsed` | the file's extension |
| [Sandbox providers](#sandbox-providers) | `agentlab.sandbox` | a subclass of `SandboxProvider` | `security.sandbox.provider` |
| [Artifact stores](#artifact-stores) | `agentlab.artifact_stores` | a subclass of `ArtifactStore` | `storage.artifact_store` |
| [Vector stores](#vector-stores) | `agentlab.vector_stores` | a subclass of `VectorStore` | `create_vector_store(name)` (nothing in a run uses one yet) |
| [Report renderers](#report-renderers) | `agentlab.report_renderers` | a subclass of `ReportRenderer` | `reporting.formats`, `--report`, `--format`, the API |
| [Test skills](#test-skills) | `agentlab.skills` | a folder of skills (and, trusted, a Python generator) | selected automatically when they apply |

Browser engines are execution engines (the built-in `browser` engine is one); evaluators are assertions, LLM judges
(any provider) and scoring profiles ([data](evaluation.md#scoring)).

## Adding a plug-in

There are three ways in. All of them end the same way: the plug-in's class or function is registered under a **name**,
and that name is what configuration, target files and the API use.

**1. A package, through an entry point.** Add one line to the package's `pyproject.toml` and install the package in the
same environment as AgentLab. The entry-point name is the plug-in's name:

```toml
[project.entry-points."agentlab.report_renderers"]
junit = "my_agentlab_plugin.junit:JunitRenderer"
```

**2. A module, named in the configuration.** The module is imported when AgentLab starts; it registers itself:

```yaml
# agentlab.yaml
plugins: [my_agentlab_plugin]
```

```python
# my_agentlab_plugin.py
from agentlab.reporting.renderers import REPORT_RENDERERS

REPORT_RENDERERS.register("junit", JunitRenderer)
```

**3. From code that embeds AgentLab.** Call `register` before building the services. `Registry.unregister(name)`
removes one again.

The rules, the same for every registry:

* **A name is registered once.** `register("junit", X)` when `junit` exists raises `ValueError`; pass `replace=True` to
  mean it. Entry points *fill gaps*: one whose name is already registered is ignored, so an installed package cannot
  quietly take the place of a built-in.
* **A plug-in that fails to load is skipped, not fatal.** An entry point that cannot be imported is logged as a warning
  and the rest load. A module listed in `plugins:` that cannot be imported is a warning in `agentlab doctor` and in the
  run's warnings, and the run goes on without it. A plug-in that fails *while running* is contained as described under
  each kind.
* **Plug-ins are looked up when they are used**, after the `plugins:` modules are imported, so a plug-in can be what
  makes a configuration value valid: `reporting.formats: [junit]` is checked when the services start, with the plug-in
  loaded.

## Trust

A plug-in is Python code that runs **inside the AgentLab process, with its privileges**. That is the point (an adapter
must open connections, a sandbox must start containers) and it is the reason for the one rule: **only the owner adds
plug-ins**. They come from installed packages, from the `plugins:` list of the owner's configuration, or from the
program that embeds AgentLab. A plug-in is never loaded from a target, a repository, a document, an upload or a skill
under test, and AgentLab does not install packages. Install a plug-in only if you would run its code.

What AgentLab still does, whatever a plug-in does:

| Guarantee | How |
|---|---|
| **A plug-in never sees a secret that AgentLab redacted.** | Evidence is redacted *before* it reaches an artifact store (`ArtifactStore.put` redacts, the store only implements `write`). A report is redacted, once, before any renderer sees it. |
| **A broken or hostile report renderer cannot damage a bundle.** | A renderer that raises, returns something that is not bytes, or names a file outside the bundle or one the bundle already uses (`checksums.json`, another format's file) costs only its own file; the rest of the report is written and a warning says what was skipped. |
| **A missing sandbox never means "run it here".** | An unknown `security.sandbox.provider` stops AgentLab at start-up. A provider that reports it cannot guarantee isolation blocks the tests that need it ([ADR 0002](decisions/0002-fail-closed-isolation.md)). |
| **A missing adapter blocks, it does not crash.** | A target that names a `custom` interface nobody provides is tested through its other interfaces, or not at all, and the run says so and reports `not_tested`, never a pass. |
| **A plug-in assertion that raises is not a verdict on the target.** | It is recorded as an *evaluator error* ([evaluation.md](evaluation.md#deterministic-assertions)). |
| **Reports show what was and was not tested.** | The scorecard counts tests that ran. Nothing a plug-in does makes blocked tests count as passed. |

What AgentLab **cannot** enforce, so the plug-in author must: an adapter or provider must call
`ctx.egress.check(url)` (adapters) before it contacts a URL, or the egress policy that blocks cloud-metadata and private
addresses does not apply to it; a sandbox provider must actually isolate; and a plug-in must not log, store or send what
it is handed.

## The kinds, one by one

Every example below is a plug-in that is exercised by a test (see [Examples that are tested](#examples-that-are-tested)).

### Model providers

```python
from agentlab.providers.base import Capability, CompletionRequest, CompletionResponse, LLMProvider, TokenUsage
from agentlab.providers.registry import PROVIDER_TYPES


class MyProvider(LLMProvider):
    type_name = "my-llm"
    default_capabilities = frozenset({Capability.CHAT})

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        text = await call_my_service(request.messages, model, self.api_key())  # your code
        return CompletionResponse(
            text=text, usage=TokenUsage(input_tokens=1, output_tokens=1), provider=self.name, model=model
        )


PROVIDER_TYPES.register("my-llm", MyProvider)
```

```yaml
providers:
  - {name: mine, type: my-llm, model: some-model, api_key_ref: env:MY_LLM_KEY}
```

The base class gives you capability negotiation, retries with backoff for transient errors, JSON-schema emulation for a
model that lacks it, and cost estimation from `pricing`. `type` is a free string, so a new provider needs no change to
AgentLab. An unknown type is an error that lists the known ones. How providers, judges and capability negotiation
work: [providers.md](providers.md).

### Agent adapters

An adapter is how AgentLab talks to one kind of target. The six that ship (`api`, `web`, `command`, `mcp`, `llm`,
`mock`) are registered the same way. A target of a kind AgentLab has no code for (gRPC, a message queue, a desktop
automation bridge) needs an adapter and a `custom` block:

```python
from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.models import AgentRequest, AgentResponse, TargetSpec


class ShoutingAdapter(AgentAdapter):
    kind = "shouting"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        self.settings = spec.custom[self.kind]  # the block of the target file
        self.capabilities = AdapterCapabilities()  # what the target can report: tool calls, contexts, ...

    async def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(output=request.input.upper())


ADAPTERS.register("shouting", ShoutingAdapter)
```

```yaml
# target.yaml
name: shouter
description: A chat assistant that answers by shouting back what it hears
custom:
  shouting: {prefix: "HEARD: "}
```

`custom` keys are the adapter names. A name may not be one of the built-in interfaces, and must be lower-case letters,
digits, `-` and `_`. Your adapter declares what the target can report through `AdapterCapabilities` (tool calls,
retrieved contexts, citations, whether it keeps sessions), and AgentLab plans and scores accordingly: a target that
cannot report tool calls is not failed for not reporting them, the tests that need them are blocked. With the adapter
installed, discovery, planning, execution, evaluation, scoring and reporting run exactly as for a built-in interface; a
target served by an adapter like this one gets about forty tests and a scorecard
([tested](../tests/integration/test_plugin_target.py)).

Credentials arrive as `request.credential` (a profile name) and are resolved through `ctx.credentials`; never read a
secret any other way. Without the adapter installed, nothing is tested through that interface and the run says so.

### Execution engines

An engine decides *how* one test is driven. The conversation engine (messages through an adapter) is the default; the
built-in `browser`, `workspace`, `site`, `load` and `static` engines claim the tests of their kind.

```python
from agentlab.execution.engines import ENGINES, AttemptEnv, AttemptOutcome, ExecutionEngine


class QuietEngine(ExecutionEngine):
    needs_adapter = True  # False for an engine that sends nothing to the target

    def handles(self, test) -> bool:
        return "quiet" in test.tags

    async def run(self, test, env: AttemptEnv) -> AttemptOutcome: ...


ENGINES.register("quiet", QuietEngine)
```

For each test the most specific engine that `handles` it is used (engines are asked in name order, `conversation` last).
There is nothing to configure. An engine that handles tests it should not will take them, so make `handles` narrow.

### Assertions (evaluators)

The deterministic checks of a test. There are 61 built in ([evaluation.md](evaluation.md#deterministic-assertions));
a plug-in adds a kind that tests, user test files and skills can then use by name. `ctx` is an `EvalContext`
(`agentlab.evaluation.context`): the `test`, the `response` being checked and all `responses` of the test, the `inputs`
and `sessions`, the placeholder `resolver` (it holds the run's synthetic canaries) and the agent's `profile`:

```python
from agentlab.core.models import AssertionResult
from agentlab.evaluation.assertions import ASSERTIONS


def starts_with_marker(params, ctx) -> AssertionResult:
    passed = ctx.response.output.startswith(params.get("marker", ">"))
    return AssertionResult(type="starts_with_marker", passed=passed, score=1.0 if passed else 0.0, message="marker")


ASSERTIONS.register("starts_with_marker", starts_with_marker)
```

```yaml
- name: starts with a marker
  input: hello
  assertions: [{type: starts_with_marker, marker: ">"}]
```

A user test file or skill that uses a kind nobody registered is rejected with the name (`unknown assertion type(s)
[...]`); one that raises while running is an evaluator error and never a failure of the target.

### Document parsers

A parser turns the bytes of one file type into text blocks, headings, tables and *safety observations* (hidden text,
active content), and never executes anything in the file.

```python
from agentlab.documents.parsers import DOCUMENT_PARSERS, Block, Parsed


def parse_xyz(data: bytes, name: str) -> Parsed:
    return Parsed(blocks=[Block(text=data.decode(), section="all")])


DOCUMENT_PARSERS.register(".xyz", parse_xyz)
```

The key is the lower-case extension with its dot. Without a parser a file is reported as unsupported (*"no parser for
'.xyz' files"*), never guessed at.

### Sandbox providers

```python
from agentlab.sandbox import SANDBOX_PROVIDERS, Sandbox, SandboxProvider, SandboxSpec


class MyProvider(SandboxProvider):
    name = "my-sandbox"

    async def available(self) -> tuple[bool, str]:
        return True, "isolation is guaranteed because ..."  # say *why*; this is shown to the user

    async def create(
        self, spec: SandboxSpec
    ) -> Sandbox: ...  # a Sandbox with exec, put_dir, get_file, export_dir, close


SANDBOX_PROVIDERS.register("my-sandbox", MyProvider)
```

```yaml
security:
  sandbox: {provider: my-sandbox}
```

`available()` must return `False` unless isolation really is guaranteed: AgentLab never falls back to the host
([ADR 0002](decisions/0002-fail-closed-isolation.md)). The spec carries the operator's limits (CPUs, memory, processes,
disk, time, user, network mode) and your provider has to honour them. The built-ins are `docker` and `disabled` (refuses
every request).

### Artifact stores

Evidence (traces, screenshots, outputs) is kept in an artifact store, content-addressed by SHA-256. A store implements
`write` (keep these already-redacted bytes), `get`, `ref` and `list`; `put` is provided and redacts first.

```python
from agentlab.storage.artifacts import ARTIFACT_STORES, ArtifactRef, ArtifactStore


class BucketStore(ArtifactStore):
    @classmethod
    def from_options(
        cls, root, options
    ) -> "BucketStore":  # root: storage.artifacts_dir; options: storage.artifact_store_options
        return cls(bucket=options["bucket"])

    def write(
        self, raw: bytes, *, kind, media_type, name, run_id, test_key, sensitivity, redacted, meta
    ) -> ArtifactRef: ...
    def get(self, artifact_id: str) -> bytes: ...
    def ref(self, artifact_id: str) -> ArtifactRef: ...
    def list(self, run_id: str | None = None) -> list[ArtifactRef]: ...


ARTIFACT_STORES.register("bucket", BucketStore)
```

```yaml
storage:
  artifact_store: bucket
  artifact_store_options: {bucket: my-evidence}      # never a secret: read credentials from the environment
```

Built in: `local` (the default: files under `storage.artifacts_dir`, restricted evidence in a `0700` folder), `memory`
(for tests and throw-away runs: **gone when the process ends**; `agentlab doctor` warns) and `s3`, which is **not
implemented**: selecting it stops AgentLab with a message saying so. Evidence marked `restricted` (screenshots taken
while signed in) must not be readable by other users; a store has to honour that.

### Vector stores

A `VectorStore` (`add`, `search`, `__len__`) with a `memory` implementation and a `pgvector` adapter. **Nothing in a
test run reads or writes a vector store in this build**: groundedness is checked against the parsed documents
directly. It is an extension point, kept so retrieval-based evaluation can be added without touching the rest, and not
a feature to rely on. The `pgvector` adapter needs PostgreSQL with the `vector` extension and `psycopg`; it was run
against PostgreSQL 16 with pgvector 0.6.0 and no other version. `qdrant` is registered as a placeholder that raises *"vector store 'qdrant' is not implemented
in this build"*.

```python
from agentlab.storage.vectors import create_vector_store

store = create_vector_store("memory")  # create_vector_store("pgvector", url="postgresql://...")
```

### Report renderers

A renderer writes one file of a report bundle from the finished report. The four built in (`json`, `md`, `html`,
`pdf`) are registered like any other. Yours:

```python
from xml.sax.saxutils import quoteattr

from agentlab.reporting.renderers import REPORT_RENDERERS, RenderContext, ReportRenderer


class JunitRenderer(ReportRenderer):
    file_name = "report.junit.xml"  # a bare file name in the bundle
    media_type = "application/xml"

    def render(self, report, context: RenderContext) -> bytes:
        cases = "".join(f"<testcase id={quoteattr(r.test_id)}/>" for r in report.results)
        return f'<testsuite tests="{len(report.results)}">{cases}</testsuite>'.encode()


REPORT_RENDERERS.register("junit", JunitRenderer)
```

Then `agentlab test --report json,junit`, `agentlab report --format junit`, `reporting.formats: [json, junit]` or
`POST /test-runs/{id}/reports {"formats": ["junit"]}`. `report` is the whole [report model](reports.md) (every result,
finding, score and section), already redacted; `context.load_blob(artifact_id)` returns the bytes of a screenshot the
report may embed, or `None`.

* The file is in the bundle's `checksums.json`, so `agentlab report --verify` covers it, and it is stored as an artifact
  with your `media_type`.
* The default of every command stays `json, md, html, pdf`; **`all` means every installed format**, plug-ins included.
* A name must be lower-case letters, digits, `-` and `_`, and cannot be `all`, `none`, `markdown` or `htm`. `file_name`
  cannot be `checksums.json`, `run-manifest.json` or another format's file.
* If a renderer fails, only its file is lost (see [Trust](#trust)). The API serves a plug-in's file with its media
  type; anything a browser could run (`text/html`, `image/svg+xml`, XML) is served sandboxed, so a plug-in cannot make
  a document that runs as the API.
* The web interface shows the four built-in formats; a plug-in's file is listed there for download.

### Test skills

A skill is a folder (`skill.yaml` and `SKILL.md`) that says when it applies and what tests to make
([skills.md](skills.md)). A package adds skills with the `agentlab.skills` entry point, which returns the folder (or
folders) that hold them:

```toml
[project.entry-points."agentlab.skills"]
my-skills = "my_agentlab_plugin:skill_folder"
```

```python
def skill_folder() -> str:
    return "/path/inside/the/package/skills"
```

Skills that arrive this way have trust `plugin`: they are selected automatically and, unlike your own skills in
`skill_dirs`, may name a Python generator ([the trust table](skills.md#trust-and-where-skills-come-from)). A local skill
cannot shadow a built-in one of the same name.

## Seeing what is installed

```console
$ agentlab plugins list
  Kind               Built in   Plug-ins
 ────────────────────────────────────────────────────────
  providers          10         none
  adapters           6          none
  ...
  report_renderers   4          junit (my_agentlab_plugin)
```

`agentlab plugins list --all` names every built-in one too, `agentlab plugins list report_renderers` shows one kind and
`--json` prints every name with the module that defines it. It imports the `plugins:` modules of the configuration and
loads the entry points exactly as a run does; a plug-in that fails to load is printed as a warning. Skills are listed
by `agentlab skills list`.

## What is not pluggable

Saying this plainly is part of the design:

* **The report's content.** A renderer chooses the *format*; the sections, their order and the facts in them are built
  once, by AgentLab, so every format says the same thing and the 27 sections of [reports.md](reports.md) are always
  there. A plug-in cannot add or remove a section.
* **Scoring and severity rules.** Scoring profiles are data ([evaluation.md](evaluation.md#scoring)); the way severity
  and root cause are computed is not replaceable.
* **Database and queue back ends.** `storage.database_url` takes SQLite or PostgreSQL; `queue.backend` is `inline` or
  `redis`. Neither is a registry.
* **Object stores and Qdrant** are extension points with no implementation (`s3` and `qdrant` say so when selected).
* **The browser.** The `browser` engine drives Chromium through Playwright; a different browser or an AI planner is an
  engine you would write, and none ships ([browser-testing.md](browser-testing.md#not-supported)).
* **Commands and the web interface.** There is no plug-in point for CLI commands or interface screens.
* **WebSocket agent protocols and client certificates** are not supported by any built-in adapter; an adapter plug-in
  is the way to add the first one.

## Examples that are tested

Each plug-in written for this page is a test that registers it the way a package would and uses it through the public
path that selects it.

| Kind | Test |
|---|---|
| Registry rules, entry points, a broken plug-in, a plug-in that tries to shadow a built-in | [`tests/unit/test_plugins.py`](../tests/unit/test_plugins.py) |
| Provider, assertion in a user test file, document parser, engine, sandbox, artifact store (and that a store is never handed a secret), vector store, skills entry point | [`tests/unit/test_plugins.py`](../tests/unit/test_plugins.py) |
| A target served only by a plug-in adapter, through discovery, planning, execution, scoring and the report | [`tests/integration/test_plugin_target.py`](../tests/integration/test_plugin_target.py) |
| A report format: generated, checksummed, stored, a failing renderer, `all`, refused at start-up | [`tests/integration/test_reports.py`](../tests/integration/test_reports.py) |
| A report format from the command line, from a `plugins:` module | [`tests/integration/test_cli_reports.py`](../tests/integration/test_cli_reports.py) |
| A report format over the REST API, served sandboxed, kept after its plug-in is removed | [`tests/api/test_report_plugins.py`](../tests/api/test_report_plugins.py) |
| `agentlab plugins list` | [`tests/integration/test_cli_plugins.py`](../tests/integration/test_cli_plugins.py) |

What has **not** been tested: a plug-in installed from a real package index (the tests build the package metadata an
installer would write and put it on `sys.path`), a third-party sandbox provider against real isolation, a `pgvector`
store on any PostgreSQL or pgvector version other than 16 and 0.6.0, and any plug-in on Windows.
