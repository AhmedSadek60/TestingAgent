"""TargetDiscoveryAgent (spec section 5): understand the target *before* testing it.

Pipeline: ingest repository (safely) -> analyse repository -> analyse documents -> analyse OpenAPI ->
probe behaviour with SAFE questions -> discover MCP/web surfaces -> fingerprint -> optional LLM
enrichment. Discovery never performs side effects, and nothing destructive runs until it completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from agentlab.adapters.base import AdapterContext, TargetRuntime
from agentlab.adapters.openapi import (
    MAX_DOCUMENT_BYTES,
    OpenApiAnalysis,
    analyze_openapi,
    parse_openapi_text,
    resolve_api,
)
from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import EventType
from agentlab.core.errors import AgentLabError, UserError
from agentlab.core.models import AgentProfile, TargetSpec, ToolInfo
from agentlab.discovery.fingerprint import FingerprintInputs, build_profile
from agentlab.discovery.probe import Prober, ProbeResult
from agentlab.documents.analyzer import DocumentAnalyzer, extract_facts
from agentlab.documents.models import AnalyzedDocument
from agentlab.repository.analyzer import RepositoryAnalyzer
from agentlab.repository.ingest import IngestedRepo, RepositoryIngestor
from agentlab.repository.models import RepositoryAnalysis
from agentlab.security.egress import EgressPolicy
from agentlab.security.untrusted import EVALUATOR_POLICY, wrap_untrusted
from agentlab.tracing import EventBus


@dataclass
class DiscoveryResult:
    profile: AgentProfile
    repo_analysis: RepositoryAnalysis | None = None
    documents: list[AnalyzedDocument] = field(default_factory=list)
    probe: ProbeResult | None = None
    openapi: OpenApiAnalysis | None = None
    repo: IngestedRepo | None = None
    mcp_tools: list[ToolInfo] = field(default_factory=list)
    web: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self.repo:
            self.repo.cleanup()
            self.repo = None


@dataclass
class IngestedTarget:
    """What can be learned from the target's files before anything is run (spec section 5: ingestion)."""

    repo: IngestedRepo | None = None
    repo_analysis: RepositoryAnalysis | None = None
    documents: list[AnalyzedDocument] = field(default_factory=list)
    openapi: OpenApiAnalysis | None = None
    #: the target with the ``api`` settings the OpenAPI document supplied (None when it supplied nothing)
    spec: TargetSpec | None = None
    warnings: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self.repo:
            self.repo.cleanup()
            self.repo = None


ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "expected_workflows": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "expected_workflows", "limitations", "risks"],
    "additionalProperties": False,
}


class TargetDiscoveryAgent:
    def __init__(
        self,
        config: AgentLabConfig,
        *,
        providers: Any = None,
        credentials: Any = None,
        bus: EventBus | None = None,
        artifacts: Any = None,
        docker_available: bool = False,
        browser_available: bool = False,
        workspace: Path | None = None,
        run_id: str | None = None,
        web_discoverer: Any = None,
        sandbox: Any = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.providers = providers
        self.credentials = credentials
        self.bus = bus
        self.artifacts = artifacts
        self.docker_available = docker_available
        self.browser_available = browser_available
        self.workspace = workspace
        self.run_id = run_id or "discovery"
        self.web_discoverer = web_discoverer
        self.sandbox = sandbox
        self.extras = extras or {}
        self.egress = EgressPolicy(
            block_metadata=config.security.block_metadata_endpoints,
            allow_private=config.security.allow_private_networks,
        )

    def _emit(self, type_: EventType, payload: dict[str, Any]) -> None:
        if self.bus:
            self.bus.emit(self.run_id, type_, payload)

    # ------------------------------------------------------------------ ingestion
    async def ingest(self, spec: TargetSpec) -> IngestedTarget:
        """Phase "target ingestion": clone and analyse the repository (safely), read documents and the OpenAPI spec.

        Nothing is executed and the target is not contacted (an OpenAPI document may be fetched from its URL)."""
        warnings: list[str] = []
        repo_ing: IngestedRepo | None = None
        repo_an: RepositoryAnalysis | None = None
        if spec.repository and (spec.repository.url or spec.repository.path or spec.repository.archive):
            try:
                auth = None
                if self.credentials and spec.credentials:
                    for c in spec.credentials:
                        if self.credentials.has(c):
                            hdr = self.credentials.auth_headers(c, spec.repository.url)
                            auth = hdr.get("Authorization")
                            break
                repo_ing = await RepositoryIngestor(self.workspace, self.egress).ingest(
                    spec.repository, auth_header=auth
                )
                repo_an = RepositoryAnalyzer(self.config.security.custom_secret_patterns).analyze(repo_ing)
                warnings += repo_an.warnings
            except AgentLabError as exc:
                warnings.append(f"repository could not be analysed ({exc.kind.value}): {exc}")
        docs = self._documents(spec, warnings)
        openapi, document_url = await self._openapi(spec, repo_ing, repo_an, warnings)
        resolved = resolve_api(spec, openapi, document_url=document_url)
        warnings += [*resolved.notes, *resolved.warnings]
        return IngestedTarget(
            repo=repo_ing,
            repo_analysis=repo_an,
            documents=docs,
            openapi=openapi,
            spec=resolved.spec if resolved.spec is not spec else None,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ main
    async def discover(
        self,
        spec: TargetSpec,
        *,
        probe: bool = True,
        enrich: bool | None = None,
        keep_repo: bool = False,
        runtime: TargetRuntime | None = None,
        user_description: str | None = None,
        ingested: IngestedTarget | None = None,
    ) -> DiscoveryResult:
        """Fingerprint the target. Pass ``ingested`` when the caller already ran :meth:`ingest` (and so owns the
        cloned repository's clean-up); otherwise ingestion happens here."""
        self._emit(
            EventType.DISCOVERY_STARTED,
            {
                "target": spec.name,
                "interfaces": spec.interfaces(),
                "repository": bool(spec.repository),
                "documents": len(spec.documents),
            },
        )
        own_ingest = ingested is None
        ing = ingested if ingested is not None else await self.ingest(spec)
        spec = ing.spec or spec  # the endpoint the OpenAPI document supplied, when the owner gave only the document
        warnings: list[str] = list(ing.warnings)
        repo_ing, repo_an, docs, openapi = ing.repo, ing.repo_analysis, ing.documents, ing.openapi

        owned_runtime = runtime is None
        rt = runtime
        probe_res: ProbeResult | None = None
        mcp_tools: list[ToolInfo] = []
        web_info: dict[str, Any] | None = None
        if spec.interfaces():
            if rt is None:
                ctx = AdapterContext(
                    config=self.config,
                    credentials=self.credentials,
                    egress=self.egress,
                    artifacts=self.artifacts,
                    providers=self.providers,
                    sandbox=self.sandbox,
                    run_id=self.run_id,
                    extras=dict(self.extras),
                )
                rt = await TargetRuntime(spec, ctx).open()
            for k, why in rt.errors.items():
                warnings.append(f"interface '{k}' is unavailable: {why}")
            try:
                if probe and rt.adapters:
                    adapter = rt.conversational_adapter()  # a tool-call interface (MCP) cannot be asked questions
                    if adapter is not None:
                        rag_q = None
                        for d in docs:
                            facts = extract_facts(d, limit=1)
                            if facts:
                                rag_q = facts[0].question
                                break
                        probe_res = await Prober(adapter, rag_question=rag_q).run()
                        warnings += [f"probe: {e}" for e in probe_res.errors]
                mcp_adapter = rt.adapters.get("mcp") if rt else None
                if mcp_adapter is not None and hasattr(mcp_adapter, "discover_tools"):
                    mcp_tools = await mcp_adapter.discover_tools()
                if self.web_discoverer and "web" in (rt.adapters if rt else {}) and spec.web:
                    web_info = await self.web_discoverer(spec)
            finally:
                if owned_runtime and rt is not None:
                    await rt.close()
        inputs = FingerprintInputs(
            spec=spec,
            repo=repo_an,
            docs=docs,
            openapi=openapi,
            probe=probe_res,
            mcp_tools=mcp_tools,
            web=web_info,
            description=user_description,
            available_interfaces=(rt.available() if rt else []) or spec.interfaces(),
            docker_available=self.docker_available,
            browser_available=self.browser_available,
        )
        profile = build_profile(inputs)
        if enrich is None:
            enrich = self.config.evaluation.llm_test_generation
        if enrich and self.providers is not None and (self.config.evaluation.judges or self.providers.names()):
            await self._enrich(profile, repo_an, docs, warnings)
        res = DiscoveryResult(
            profile=profile,
            repo_analysis=repo_an,
            documents=docs,
            probe=probe_res,
            openapi=openapi,
            repo=repo_ing if (keep_repo or not own_ingest) else None,
            mcp_tools=mcp_tools,
            web=web_info,
            warnings=warnings,
        )
        if own_ingest and not keep_repo:
            ing.cleanup()
        self._emit(
            EventType.DISCOVERY_COMPLETED,
            {
                "types": [{"type": t.type.value, "confidence": t.confidence} for t in profile.types[:8]],
                "tools": len(profile.tools),
                "documents": len(docs),
                "modes": [m.value for m in profile.modes],
                "warnings": len(warnings),
            },
        )
        return res

    # ------------------------------------------------------------------ pieces
    def _documents(self, spec: TargetSpec, warnings: list[str]) -> list[AnalyzedDocument]:
        an = DocumentAnalyzer()
        docs: list[AnalyzedDocument] = []
        for ref in spec.documents:
            if ref.startswith(("http://", "https://")):
                # documents are read from this machine only: nothing is fetched on a target file's say-so
                warnings.append(f"{ref}: a URL is not downloaded; save the document and give its path")
                continue
            p = Path(ref)
            if p.is_dir():
                paths = sorted(x for x in p.rglob("*") if x.is_file())[:200]
            else:
                paths = [p]
            for path in paths:
                try:
                    doc = an.analyze_path(path)
                except UserError as exc:
                    warnings.append(str(exc))
                    continue
                if self.artifacts is not None:
                    try:
                        self.artifacts.put(
                            path.read_bytes(),
                            kind="document",
                            media_type=doc.media_type,
                            name=doc.name,
                            run_id=None if self.run_id == "discovery" else self.run_id,
                        )
                    except Exception as exc:  # artifact problems must not block discovery
                        warnings.append(f"could not store document artifact {doc.name}: {exc}")
                warnings += [f"{doc.name}: {w}" for w in doc.warnings]
                docs.append(doc)
        return docs

    async def _openapi(
        self, spec: TargetSpec, repo: IngestedRepo | None, an: RepositoryAnalysis | None, warnings: list[str]
    ) -> tuple[OpenApiAnalysis | None, str | None]:
        """The analysed OpenAPI document and the URL it was read from (None for a document inside the repository)."""
        data: Any = None
        document_url: str | None = None
        try:
            if spec.api and spec.api.openapi_url:
                document_url = spec.api.openapi_url
                data = parse_openapi_text(await self._download(document_url))
            elif repo is not None and an and an.openapi_specs:
                p = repo.path / an.openapi_specs[0].path
                if p.stat().st_size > MAX_DOCUMENT_BYTES:
                    raise UserError(
                        f"{an.openapi_specs[0].path} is larger than {MAX_DOCUMENT_BYTES // 1024 // 1024} MiB"
                    )
                data = parse_openapi_text(p.read_text(encoding="utf-8", errors="replace"))
        except (httpx.HTTPError, ValueError, yaml.YAMLError, AgentLabError, OSError, RecursionError) as exc:
            warnings.append(f"OpenAPI specification could not be read: {type(exc).__name__}: {str(exc)[:150]}")
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return analyze_openapi(data), document_url
        if data is not None:
            warnings.append(
                "the OpenAPI document is not an OpenAPI 3 or Swagger 2 document (no 'openapi' or 'swagger' key)"
            )
        return None, document_url

    async def _download(self, url: str) -> str:
        """The document at ``url``, read through the egress policy without following redirects, at most
        ``MAX_DOCUMENT_BYTES`` of it."""
        self.egress.check(url)
        async with (
            httpx.AsyncClient(timeout=20, follow_redirects=False) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise UserError(
                        f"the document is larger than {MAX_DOCUMENT_BYTES // 1024 // 1024} MiB and is not read"
                    )
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def _enrich(
        self, profile: AgentProfile, repo: RepositoryAnalysis | None, docs: list[AnalyzedDocument], warnings: list[str]
    ) -> None:
        """Ask a provider to summarise intent. Output is advisory and labelled; it never changes type scores."""
        from agentlab.providers import CompletionRequest, Message

        judges = self.config.evaluation.judges
        name = judges[0].provider if judges else self.providers.names()[0]
        context = [
            f"TARGET: {profile.target_name}",
            "DETECTED TYPES: " + ", ".join(f"{t.type.value}({t.confidence:.2f})" for t in profile.types[:6]),
            "TOOLS: " + ", ".join(f"{t.name} [{t.side_effects}]" for t in profile.tools[:20]),
        ]
        material = []
        if repo:
            material += [p.excerpt for p in repo.prompts[:3]]
        material += [i.text[:300] for d in docs[:2] for i in d.items[:3]]
        user = (
            "\n".join(context)
            + "\n\n"
            + wrap_untrusted("repository", "\n---\n".join(material), max_chars=6000)
            + (
                "\n\nReturn JSON with a one-paragraph summary of what this agent is for, the expected user workflows, "
                "its likely limitations and the main risks. Do not invent capabilities that are not evidenced."
            )
        )
        try:
            prov = self.providers.get(name)
            resp = await prov.complete(
                CompletionRequest(
                    messages=[Message(role="system", content=EVALUATOR_POLICY), Message(role="user", content=user)],
                    model=judges[0].model if judges else None,
                    json_schema=ENRICH_SCHEMA,
                    schema_name="enrichment",
                    max_tokens=700,
                    temperature=0.0,
                )
            )
            d = resp.parsed or {}
            if isinstance(d, dict):
                profile.expected_workflows = [str(x) for x in d.get("expected_workflows", [])][:10]
                profile.limitations += [f"(LLM-suggested, unverified) {x}" for x in d.get("limitations", [])][:5]
                profile.raw_signals["llm_enrichment"] = {
                    "provider": name,
                    "model": resp.model,
                    "summary": d.get("summary", ""),
                    "risks": d.get("risks", []),
                    "advisory": True,
                }
                if not profile.summary or profile.summary.startswith(profile.target_name + ":"):
                    profile.summary = f"{d.get('summary', '')} (LLM-generated summary)"
        except AgentLabError as exc:
            warnings.append(f"LLM enrichment skipped ({exc.kind.value}): {str(exc)[:120]}")
        except Exception as exc:
            warnings.append(f"LLM enrichment skipped ({type(exc).__name__}): {str(exc)[:120]}")
