"""Plug-ins (spec section 43): the registry itself, entry-point loading, and a working plug-in of every kind.

Every kind the specification lists as "plugins/adapters rather than hard-coded" gets a plug-in written here, registered
the way a third-party package would register it, and used through the public path that selects it (the configuration, a
target file, a report format name). Nothing is mocked in the code under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter, TargetRuntime
from agentlab.core.config import (
    AgentLabConfig,
    ProviderConfig,
    ReportingConfig,
    SandboxConfig,
    SecurityConfig,
    StorageConfig,
)
from agentlab.core.errors import UnsupportedCapability, UserError
from agentlab.core.models import AgentRequest, AgentResponse, ApiConfig, AssertionResult, TargetSpec, TestCase
from agentlab.core.plugins import Registry
from agentlab.design.user_tests import load_user_tests
from agentlab.documents.analyzer import DocumentAnalyzer
from agentlab.documents.parsers import DOCUMENT_PARSERS, Block, Parsed
from agentlab.evaluation.assertions import ASSERTIONS, evaluate_assertion
from agentlab.evaluation.context import EvalContext, PlaceholderResolver
from agentlab.execution.engines import (
    ENGINES,
    AttemptEnv,
    AttemptOutcome,
    ExecutionEngine,
    default_engines,
    pick_engine,
)
from agentlab.providers.mock import MockProvider
from agentlab.providers.registry import PROVIDER_TYPES, ProviderManager, create_provider
from agentlab.reporting.bundle import FORMATS, normalise_formats
from agentlab.reporting.renderers import (
    REPORT_RENDERERS,
    RenderContext,
    ReportRenderer,
    available_formats,
    media_type_of,
    renderer_for,
)
from agentlab.sandbox import SANDBOX_PROVIDERS, SandboxProvider, create_sandbox_provider
from agentlab.services import Services
from agentlab.skills import SkillRegistry
from agentlab.skills.model import REQUIRED_DOC_SECTIONS
from agentlab.storage.artifacts import (
    ARTIFACT_STORES,
    ArtifactRef,
    ArtifactStore,
    MemoryArtifactStore,
    create_artifact_store,
)
from agentlab.storage.vectors import (
    VECTOR_STORES,
    HashingEmbedder,
    InMemoryVectorStore,
    create_vector_store,
)


@pytest.fixture
def plugged() -> Iterator[Any]:
    """``plugged(registry, name, item)`` registers a plug-in and removes it when the test ends."""
    added: list[tuple[Registry[Any], str]] = []

    def add(registry: Registry[Any], name: str, item: Any) -> Any:
        registry.register(name, item)
        added.append((registry, name))
        return item

    yield add
    for registry, name in added:
        registry.unregister(name)


# ======================================================================================================== Registry
def test_a_registry_registers_looks_up_and_lists_by_name() -> None:
    reg: Registry[type] = Registry("widgets")
    reg.register("a", int)
    reg.register("b", str)
    assert reg.get("a") is int and "a" in reg and "c" not in reg
    assert reg.names() == ["a", "b"] and [n for n, _ in reg.items()] == ["a", "b"], "listed by name"


def test_a_name_cannot_be_registered_twice_unless_replacing_is_asked_for() -> None:
    reg: Registry[type] = Registry("widgets")
    reg.register("a", int)
    with pytest.raises(ValueError, match="widgets plug-in 'a' is already registered"):
        reg.register("a", str)
    reg.register("a", str, replace=True)
    assert reg.get("a") is str


def test_the_decorator_form_registers_the_class_and_returns_it_unchanged() -> None:
    reg: Registry[type] = Registry("widgets")

    @reg.register("cls")
    class Thing:
        pass

    assert reg.get("cls") is Thing


def test_an_unknown_name_says_what_is_known() -> None:
    reg: Registry[type] = Registry("widgets")
    reg.register("alpha", int)
    with pytest.raises(KeyError, match=r"unknown widgets plug-in 'beta' \(known: alpha\)"):
        reg.get("beta")


def test_unregister_removes_a_plug_in_and_ignores_one_that_is_not_there() -> None:
    reg: Registry[type] = Registry("widgets")
    reg.register("a", int)
    reg.unregister("a")
    reg.unregister("never-there")
    assert "a" not in reg and reg.names() == []


# ================================================================================================ entry points
def install_distribution(root: Path, name: str, group: str, entries: dict[str, str], modules: dict[str, str]) -> None:
    """A package as pip would leave it: a ``*.dist-info`` folder with ``entry_points.txt`` next to the modules."""
    dist = root / f"{name}-1.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n", encoding="utf-8")
    lines = "\n".join(f"{key} = {target}" for key, target in entries.items())
    (dist / "entry_points.txt").write_text(f"[{group}]\n{lines}\n", encoding="utf-8")
    for module, source in modules.items():
        (root / f"{module}.py").write_text(textwrap.dedent(source), encoding="utf-8")


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder on ``sys.path`` where a test can install packages, removed again afterwards."""
    root = tmp_path / "site"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    for module in ("good_plug", "other_plug", "shadow_plug", "skill_plug", "bad_plug"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    return root


def test_an_installed_package_adds_a_plug_in_through_its_entry_point(site: Path) -> None:
    install_distribution(
        site,
        "good-plug",
        "agentlab.widgets",
        {"good": "good_plug:Good"},
        {"good_plug": "class Good:\n    pass\n"},
    )
    reg: Registry[type] = Registry("widgets")
    assert reg.names() == ["good"] and reg.get("good").__name__ == "Good"


def test_a_broken_plug_in_is_logged_and_never_stops_the_others_or_agentlab(
    site: Path, caplog: pytest.LogCaptureFixture
) -> None:
    install_distribution(
        site,
        "mixed-plug",
        "agentlab.widgets",
        {"fine": "other_plug:Fine", "missing": "not_installed_anywhere:Nope", "raises": "bad_plug:Thing"},
        {"other_plug": "class Fine:\n    pass\n", "bad_plug": "raise RuntimeError('a plug-in that explodes')\n"},
    )
    reg: Registry[type] = Registry("widgets")
    with caplog.at_level(logging.WARNING, logger="agentlab.core.plugins"):
        assert reg.names() == ["fine"], "the one that works is there; the two that do not are skipped"
    text = caplog.text
    assert "failed to load widgets plug-in missing" in text and "failed to load widgets plug-in raises" in text
    assert "a plug-in that explodes" in text


def test_a_package_cannot_take_the_place_of_something_registered_before_it(site: Path) -> None:
    install_distribution(
        site,
        "shadow-plug",
        "agentlab.widgets",
        {"builtin": "shadow_plug:Impostor"},
        {"shadow_plug": "class Impostor:\n    pass\n"},
    )
    reg: Registry[type] = Registry("widgets")
    reg.register("builtin", int)
    assert reg.get("builtin") is int, "entry points fill gaps; they never replace a registered name"


def test_every_kind_of_plug_in_has_its_own_entry_point_group() -> None:
    kinds = {
        "providers": PROVIDER_TYPES,
        "adapters": ADAPTERS,
        "engines": ENGINES,
        "sandbox": SANDBOX_PROVIDERS,
        "assertions": ASSERTIONS,
        "artifact_stores": ARTIFACT_STORES,
        "vector_stores": VECTOR_STORES,
        "document_parsers": DOCUMENT_PARSERS,
        "report_renderers": REPORT_RENDERERS,
    }
    assert {name: reg.kind for name, reg in kinds.items()} == {name: name for name in kinds}
    assert all(reg.names() for reg in kinds.values()), "each one ships at least one built-in"


# ================================================================================================= providers
def test_a_provider_plug_in_is_chosen_by_the_type_in_the_configuration(plugged: Any) -> None:
    class EchoProvider(MockProvider):
        type_name = "echo-plugin"

    plugged(PROVIDER_TYPES, "echo-plugin", EchoProvider)
    provider = create_provider(ProviderConfig(name="mine", type="echo-plugin"))
    assert isinstance(provider, EchoProvider)
    manager = ProviderManager(AgentLabConfig(providers=[ProviderConfig(name="mine", type="echo-plugin")]))
    assert isinstance(manager.get("mine"), EchoProvider)
    with pytest.raises(UserError, match="unknown providers plug-in 'no-such-type'"):
        create_provider(ProviderConfig(name="x", type="no-such-type"))


# ============================================================================================== assertions
def eval_context(output: str = "> hello") -> EvalContext:
    response = AgentResponse(output=output)
    test = TestCase(id="T-001", name="t", category="functional", objective="o", input="hello")
    return EvalContext(
        test=test,
        turn_index=0,
        response=response,
        responses=[response],
        inputs=["hello"],
        sessions=["default"],
        resolver=PlaceholderResolver(),
    )


def test_an_assertion_plug_in_can_be_used_in_a_user_test_file_and_is_evaluated(plugged: Any, tmp_path: Path) -> None:
    tests_file = tmp_path / "tests.yaml"
    tests_file.write_text(
        yaml.safe_dump(
            [{"name": "starts with a marker", "input": "hello", "assertions": [{"type": "starts_with_marker"}]}]
        ),
        encoding="utf-8",
    )
    tests, problems = load_user_tests([tests_file])
    assert tests == [] and "unknown assertion type(s) ['starts_with_marker']" in problems[0], "not installed yet"

    def starts_with_marker(params: dict[str, Any], ctx: EvalContext) -> AssertionResult:
        passed = ctx.response.output.startswith(str(params.get("marker", ">")))
        return AssertionResult(type="starts_with_marker", passed=passed, score=1.0 if passed else 0.0, message="marker")

    plugged(ASSERTIONS, "starts_with_marker", starts_with_marker)
    tests, problems = load_user_tests([tests_file])
    assert problems == [] and [t.assertions[0].type for t in tests] == ["starts_with_marker"]
    assert evaluate_assertion("starts_with_marker", {}, eval_context("> hello")).passed
    assert not evaluate_assertion("starts_with_marker", {}, eval_context("hello")).passed


def test_an_assertion_plug_in_that_raises_is_an_evaluator_error_and_not_a_verdict_on_the_target(plugged: Any) -> None:
    def explodes(params: dict[str, Any], ctx: EvalContext) -> AssertionResult:
        raise RuntimeError("the plug-in has a bug")

    plugged(ASSERTIONS, "explodes", explodes)
    result = evaluate_assertion("explodes", {}, eval_context())
    assert result.evaluator_error and not result.passed and "the plug-in has a bug" in result.message


# =========================================================================================== document parsers
def test_a_document_parser_plug_in_makes_a_new_file_type_readable(plugged: Any) -> None:
    analyzer = DocumentAnalyzer()
    before = analyzer.analyze_bytes(b"data", "notes.xyz")
    assert before.unsupported and "no parser for '.xyz'" in before.unsupported

    def parse_xyz(data: bytes, name: str) -> Parsed:
        return Parsed(blocks=[Block(text=data.decode().upper(), section="all")])

    plugged(DOCUMENT_PARSERS, ".xyz", parse_xyz)
    after = analyzer.analyze_bytes(b"policy: 25 days", "notes.xyz")
    assert after.unsupported is None and [i.text for i in after.items] == ["POLICY: 25 DAYS"]


# ================================================================================================== engines
def test_an_execution_engine_plug_in_takes_the_tests_it_claims_and_no_others(plugged: Any) -> None:
    class QuietEngine(ExecutionEngine):
        name = "quiet"

        def handles(self, test: TestCase) -> bool:
            return "quiet" in test.tags

        async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
            return AttemptOutcome()

    plugged(ENGINES, "quiet", QuietEngine)
    engines = default_engines()
    plain = TestCase(id="T-001", name="t", category="functional", objective="o", input="hi")
    claimed = plain.model_copy(update={"tags": ["quiet"]})
    assert isinstance(pick_engine(claimed, engines), QuietEngine)
    assert pick_engine(plain, engines).name == "conversation"


# ============================================================================================ sandbox provider
class RecordingSandbox(SandboxProvider):
    name = "recording"

    async def available(self) -> tuple[bool, str]:
        return True, "a plug-in sandbox"

    async def create(self, spec: Any) -> Any:
        raise NotImplementedError


def services_for(root: Path, **config: Any) -> Services:
    cfg = AgentLabConfig(
        storage=StorageConfig(
            database_url=f"sqlite:///{root}/lab.db",
            artifacts_dir=str(root / "artifacts"),
            secrets_file=str(root / "secrets.enc"),
            **config.pop("storage", {}),
        ),
        reporting=ReportingConfig(formats=config.pop("formats", [])),
        **config,
    )
    return Services.create(cfg, base_dir=root)


def test_a_sandbox_plug_in_is_chosen_by_name_in_the_security_settings(plugged: Any, tmp_path: Path) -> None:
    plugged(SANDBOX_PROVIDERS, "recording", RecordingSandbox)
    services = services_for(tmp_path, security=SecurityConfig(sandbox=SandboxConfig(provider="recording")))
    assert isinstance(services.sandbox, RecordingSandbox)
    assert isinstance(create_sandbox_provider("recording"), RecordingSandbox)


def test_an_unknown_sandbox_name_is_refused_at_start_up_and_never_falls_back_to_running_on_the_host(
    tmp_path: Path,
) -> None:
    with pytest.raises(UserError, match=r"unknown sandbox provider 'nope'.*known: disabled, docker"):
        services_for(tmp_path, security=SecurityConfig(sandbox=SandboxConfig(provider="nope")))


# ============================================================================================== artifact store
class OptionsStore(MemoryArtifactStore):
    """Remembers what the configuration handed it."""

    @classmethod
    def from_options(cls, root: Path, options: Any) -> ArtifactStore:
        store = cls()
        store.settings = {"root": root, **dict(options)}  # type: ignore[attr-defined]
        return store


def test_an_artifact_store_plug_in_is_chosen_by_name_and_receives_its_options(plugged: Any, tmp_path: Path) -> None:
    plugged(ARTIFACT_STORES, "options-store", OptionsStore)
    services = services_for(
        tmp_path,
        storage={"artifact_store": "options-store", "artifact_store_options": {"bucket": "evidence"}},
    )
    store = services.artifacts
    assert isinstance(store, OptionsStore)
    assert store.settings == {"root": tmp_path / "artifacts", "bucket": "evidence"}  # type: ignore[attr-defined]
    ref = store.put("kept", kind="log", media_type="text/plain")
    assert store.get(ref.id) == b"kept"


class KeepsEverything(ArtifactStore):
    """The smallest store a plug-in can be: it implements ``write`` and the three reads."""

    def __init__(self) -> None:
        self.kept: dict[str, bytes] = {}
        self.refs: list[ArtifactRef] = []

    def write(self, raw: bytes, **fields: Any) -> ArtifactRef:
        digest = hashlib.sha256(raw).hexdigest()
        self.kept[digest] = raw
        ref = ArtifactRef(id=f"sha256-{digest}", sha256=digest, size=len(raw), **{**fields, "name": fields["name"]})
        self.refs.append(ref)
        return ref

    def get(self, artifact_id: str) -> bytes:
        return self.kept[artifact_id.removeprefix("sha256-")]

    def ref(self, artifact_id: str) -> ArtifactRef:
        return next(r for r in self.refs if r.id == artifact_id)

    def list(self, run_id: str | None = None) -> list[ArtifactRef]:
        return [r for r in self.refs if run_id in (None, r.run_id)]


def test_a_plug_in_store_is_never_handed_a_secret_to_keep() -> None:
    secret = "AKIA" + "IOSFODNN7EXAMPLE"  # assembled at run time: no secret-shaped literal sits in the repository
    store = KeepsEverything()
    ref = store.put(f"the key is {secret} ok", kind="log", media_type="text/plain", run_id="r1")
    assert secret.encode() not in store.get(ref.id) and ref.redacted, "redaction happens before the store is reached"
    assert store.put_json({"k": secret}, kind="data").redacted
    unredacted = store.put(f"the key is {secret}", kind="log", media_type="text/plain", redact=False)
    assert secret.encode() in store.get(unredacted.id), "only a caller that says so skips it"
    binary = store.put(b"\x89PNG" + secret.encode(), kind="screenshot", media_type="image/png")
    assert not binary.redacted, "binary evidence is kept as it is"


def test_the_local_store_is_the_default_and_the_object_store_is_honestly_unsupported(tmp_path: Path) -> None:
    from agentlab.storage.artifacts import LocalArtifactStore

    assert isinstance(services_for(tmp_path).artifacts, LocalArtifactStore)
    with pytest.raises(UserError, match="'s3' is not supported in this build"):
        create_artifact_store("s3", tmp_path / "x")
    with pytest.raises(UserError, match=r"unknown artifact store 'gcs'.*known: local, memory, s3"):
        create_artifact_store("gcs", tmp_path / "x")


# ============================================================================================ vector stores
def test_vector_stores_are_an_adapter_interface_with_one_working_backend_and_honest_placeholders() -> None:
    embedder = HashingEmbedder()
    store = create_vector_store("memory")
    assert isinstance(store, InMemoryVectorStore)
    for text in ("annual leave is 25 days", "the office closes at six", "expense claims need a receipt"):
        store.add(text, embedder.embed([text])[0], text)
    best = store.search(embedder.embed(["how many days of annual leave"])[0], k=1)[0]
    assert best.text == "annual leave is 25 days" and len(store) == 3
    assert VECTOR_STORES.names() == ["memory", "pgvector", "qdrant"]
    with pytest.raises(UnsupportedCapability, match="vector store 'qdrant' is not implemented in this build"):
        create_vector_store("qdrant", url="http://localhost:6333")
    with pytest.raises(UserError, match="unknown vector store 'milvus'"):
        create_vector_store("milvus")


# =================================================================================================== adapters
class EchoAdapter(AgentAdapter):
    """A target of a kind AgentLab has no code for: it answers by repeating what it was asked."""

    kind = "echo-plugin"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        self.settings = spec.custom[self.kind]
        self.capabilities = AdapterCapabilities(notes=["a plug-in adapter"])

    async def send(self, request: Any) -> AgentResponse:
        return AgentResponse(output=f"{self.settings.get('prefix', '')}{request.input}")


def test_a_target_can_name_an_interface_that_only_a_plug_in_provides(plugged: Any) -> None:
    spec = TargetSpec(name="t", custom={"echo-plugin": {"prefix": "> "}})
    assert spec.interfaces() == ["echo-plugin"]
    mixed = TargetSpec(name="t", api=ApiConfig(url="http://localhost:1/x"), custom={"zz": {}, "echo-plugin": {}})
    assert mixed.interfaces() == ["api", "echo-plugin", "zz"], "the built-in interfaces first, then the plug-in ones"

    plugged(ADAPTERS, "echo-plugin", EchoAdapter)

    async def drive() -> tuple[list[str], str]:
        async with TargetRuntime(spec, AdapterContext(config=AgentLabConfig())) as runtime:
            adapter = runtime.conversational_adapter()
            assert adapter is not None and adapter is runtime.adapter() is runtime.adapter("echo-plugin")
            reply = await adapter.send(AgentRequest(input="hi", session_id="s"))
            return runtime.available(), reply.output

    assert asyncio.run(drive()) == (["echo-plugin"], "> hi")


def test_an_interface_nobody_provides_blocks_only_the_tests_that_need_it_and_does_not_stop_the_run() -> None:
    spec = TargetSpec(name="t", custom={"grpc-nobody-installed": {"address": "localhost:50051"}})

    async def drive() -> TargetRuntime:
        return await TargetRuntime(spec, AdapterContext(config=AgentLabConfig())).open()

    runtime = asyncio.run(drive())
    assert runtime.available() == [] and "grpc-nobody-installed" in runtime.errors
    assert "unknown adapters plug-in 'grpc-nobody-installed'" in runtime.errors["grpc-nobody-installed"]


def test_custom_interface_names_cannot_collide_with_a_built_in_interface_or_be_malformed() -> None:
    for bad in ("api", "web", "command", "mcp", "llm", "mock"):
        with pytest.raises(ValueError, match="is a built-in interface"):
            TargetSpec(name="t", custom={bad: {}})
    for bad in ("Has Space", "UPPER", "1st", "", "a/b"):
        with pytest.raises(ValueError, match="must be lower-case letters"):
            TargetSpec(name="t", custom={bad: {}})


# ================================================================================================ skills
def test_a_package_can_add_test_skills_through_the_skills_entry_point(site: Path, tmp_path: Path) -> None:
    library = tmp_path / "plug-skills"
    skill = library / "plug-demo"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "plug-demo",
                "version": "0.1.0",
                "title": "Demo",
                "description": "demo",
                "id_prefix": "PLUGDEMO",
                "applicability": {"always": True},
                "templates": [
                    {
                        "id": "HELLO",
                        "test": {
                            "name": "Says hello",
                            "objective": "o",
                            "input": "hello",
                            "assertions": [{"type": "not_empty"}],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sections = "\n".join(f"## {s}\n\ntext\n" for s in REQUIRED_DOC_SECTIONS)
    (skill / "SKILL.md").write_text(f"# plug-demo\n\n{sections}", encoding="utf-8")
    install_distribution(
        site,
        "skill-plug",
        "agentlab.skills",
        {"demo": "skill_plug:skill_folder"},
        {"skill_plug": f"def skill_folder():\n    return {str(library)!r}\n"},
    )
    registry = SkillRegistry.default(load_plugins=True)
    found = registry.get("plug-demo")
    assert found.manifest.trust == "plugin" and found.problems == [], "its trust level says where it came from"
    assert len(registry.names()) == 31, "the thirty built-in skills and the one the package added"
    assert "plug-demo" not in SkillRegistry.default(load_plugins=False).names()


# ============================================================================================ report renderers
class MarkerRenderer(ReportRenderer):
    file_name = "report.marker.txt"
    media_type = "text/plain"

    def render(self, report: Any, context: RenderContext) -> bytes:
        return f"run {report.run.run_id}\n".encode()


def test_the_four_built_in_formats_come_first_and_plug_in_formats_follow_by_name(plugged: Any) -> None:
    assert available_formats() == list(FORMATS) == ["json", "md", "html", "pdf"]
    plugged(REPORT_RENDERERS, "zeta", MarkerRenderer)
    plugged(REPORT_RENDERERS, "alpha", MarkerRenderer)
    assert available_formats() == ["json", "md", "html", "pdf", "alpha", "zeta"]
    assert normalise_formats(None) == list(FORMATS), "the default stays the four formats AgentLab ships"
    assert normalise_formats("all") == available_formats(), "'all' means everything installed"
    assert normalise_formats(["ZETA", "json"]) == ["json", "zeta"]
    with pytest.raises(
        UserError, match=r"unknown report format 'docx' \(use json, md, html, pdf, alpha, zeta or all\)"
    ):
        normalise_formats("docx")
    assert media_type_of("zeta") == "text/plain" and media_type_of("gone") == "application/octet-stream"


def test_a_renderer_that_would_write_outside_the_bundle_or_over_another_file_is_refused(plugged: Any) -> None:
    def renderer(file_name: str, media_type: str = "text/plain") -> type[ReportRenderer]:
        return type("R", (MarkerRenderer,), {"file_name": file_name, "media_type": media_type})

    for index, (file_name, why) in enumerate(
        [
            ("../escape.txt", "is not a bare file name"),
            ("sub/dir.txt", "is not a bare file name"),
            ("checksums.json", "is a file of the bundle itself"),
            ("run-manifest.json", "is a file of the bundle itself"),
            ("report.html", "is already the file of format 'html'"),
            ("", "is not a bare file name"),
        ]
    ):
        name = f"bad{index}"
        plugged(REPORT_RENDERERS, name, renderer(file_name))
        with pytest.raises(UserError, match=why):
            renderer_for(name)
    plugged(REPORT_RENDERERS, "badtype", renderer("ok.txt", "not a media type"))
    with pytest.raises(UserError, match="is not a media type"):
        renderer_for("badtype")


def test_a_format_cannot_be_named_after_a_word_the_command_line_already_means(plugged: Any) -> None:
    for word in ("all", "none", "markdown", "htm", "Has Space", "UPPER"):
        plugged(REPORT_RENDERERS, word, MarkerRenderer)
    assert available_formats() == ["json", "md", "html", "pdf"], "none of them can be asked for, so none is offered"
    with pytest.raises(ValueError, match="already registered"):
        REPORT_RENDERERS.register("html", MarkerRenderer)


# ================================================================================================== doctor
def test_doctor_names_a_memory_store_as_a_warning_and_a_plug_in_store_as_such(plugged: Any, tmp_path: Path) -> None:
    from agentlab.diagnostics import run_checks

    def artifact_check(store_name: str) -> Any:
        services = services_for(tmp_path / store_name, storage={"artifact_store": store_name})
        try:
            checks = asyncio.run(run_checks(services, None, live=False))
        finally:
            services.store.db.dispose()
        return next(c for c in checks if c.name == "artifact store")

    plugged(ARTIFACT_STORES, "options-store", OptionsStore)
    memory = artifact_check("memory")
    assert memory.level == "warn" and "gone when it ends" in memory.detail
    plug_in = artifact_check("options-store")
    assert plug_in.level == "ok" and "provided by a plug-in" in plug_in.detail
