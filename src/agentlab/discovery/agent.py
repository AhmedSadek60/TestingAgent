"""TargetDiscoveryAgent (spec section 5): understand the target *before* testing it.

Pipeline: ingest repository (safely) -> analyse repository -> analyse documents -> analyse OpenAPI ->
probe behaviour with SAFE questions -> discover MCP/web surfaces -> fingerprint -> optional LLM
enrichment. Discovery never performs side effects, and nothing destructive runs until it completes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from agentlab.adapters.base import AdapterContext, TargetRuntime
from agentlab.adapters.openapi import OpenApiAnalysis, analyze_openapi
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
    def __init__(self, config: AgentLabConfig, *, providers: Any = None, credentials: Any = None, bus: EventBus | None = None,
                 artifacts: Any = None, docker_available: bool = False, browser_available: bool = False,
                 workspace: Path | None = None, run_id: str | None = None, web_discoverer: Any = None,
                 extras: dict[str, Any] | None = None) -> None:
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
        self.extras = extras or {}
        self.egress = EgressPolicy(block_metadata=config.security.block_metadata_endpoints,
                                   allow_private=config.security.allow_private_networks)

    def _emit(self, type_: EventType, payload: dict[str, Any]) -> None:
        if self.bus:
            self.bus.emit(self.run_id, type_, payload)

    # ------------------------------------------------------------------ main
    async def discover(self, spec: TargetSpec, *, probe: bool = True, enrich: bool | None = None,
                       keep_repo: bool = False, runtime: TargetRuntime | None = None,
                       user_description: str | None = None) -> DiscoveryResult:
        self._emit(EventType.DISCOVERY_STARTED, {"target": spec.name, "interfaces": spec.interfaces(),
                                                 "repository": bool(spec.repository), "documents": len(spec.documents)})
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
                repo_ing = await RepositoryIngestor(self.workspace, self.egress).ingest(spec.repository, auth_header=auth)
                repo_an = RepositoryAnalyzer(self.config.security.custom_secret_patterns).analyze(repo_ing)
                warnings += repo_an.warnings
            except AgentLabError as exc:
                warnings.append(f"repository could not be analysed ({exc.kind.value}): {exc}")
        docs = self._documents(spec, warnings)
        openapi = await self._openapi(spec, repo_ing, repo_an, warnings)

        owned_runtime = runtime is None
        rt = runtime
        probe_res: ProbeResult | None = None
        mcp_tools: list[ToolInfo] = []
        web_info: dict[str, Any] | None = None
        if spec.interfaces():
            if rt is None:
                ctx = AdapterContext(config=self.config, credentials=self.credentials, egress=self.egress,
                                     artifacts=self.artifacts, providers=self.providers, run_id=self.run_id,
                                     extras=dict(self.extras))
                rt = await TargetRuntime(spec, ctx).open()
            for k, why in rt.errors.items():
                warnings.append(f"interface '{k}' is unavailable: {why}")
            try:
                if probe and rt.adapters:
                    adapter = rt.adapter()
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
            spec=spec, repo=repo_an, docs=docs, openapi=openapi, probe=probe_res, mcp_tools=mcp_tools, web=web_info,
            description=user_description, available_interfaces=(rt.available() if rt else []) or spec.interfaces(),
            docker_available=self.docker_available, browser_available=self.browser_available)
        profile = build_profile(inputs)
        if enrich is None:
            enrich = self.config.evaluation.llm_test_generation
        if enrich and self.providers is not None and (self.config.evaluation.judges or self.providers.names()):
            await self._enrich(profile, repo_an, docs, warnings)
        res = DiscoveryResult(profile=profile, repo_analysis=repo_an, documents=docs, probe=probe_res, openapi=openapi,
                              repo=repo_ing if keep_repo else None, mcp_tools=mcp_tools, web=web_info, warnings=warnings)
        if repo_ing and not keep_repo:
            repo_ing.cleanup()
        self._emit(EventType.DISCOVERY_COMPLETED, {
            "types": [{"type": t.type.value, "confidence": t.confidence} for t in profile.types[:8]],
            "tools": len(profile.tools), "documents": len(docs), "modes": [m.value for m in profile.modes],
            "warnings": len(warnings)})
        return res

    # ------------------------------------------------------------------ pieces
    def _documents(self, spec: TargetSpec, warnings: list[str]) -> list[AnalyzedDocument]:
        an = DocumentAnalyzer()
        docs: list[AnalyzedDocument] = []
        for ref in spec.documents:
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
                        self.artifacts.put(path.read_bytes(), kind="document", media_type=doc.media_type, name=doc.name,
                                           run_id=None if self.run_id == "discovery" else self.run_id)
                    except Exception as exc:  # artifact problems must not block discovery
                        warnings.append(f"could not store document artifact {doc.name}: {exc}")
                warnings += [f"{doc.name}: {w}" for w in doc.warnings]
                docs.append(doc)
        return docs

    async def _openapi(self, spec: TargetSpec, repo: IngestedRepo | None, an: RepositoryAnalysis | None,
                       warnings: list[str]) -> OpenApiAnalysis | None:
        data: dict[str, Any] | None = None
        try:
            if spec.api and spec.api.openapi_url:
                url = spec.api.openapi_url
                self.egress.check(url)
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    r = await client.get(url)
                    r.raise_for_status()
                    data = r.json() if "json" in r.headers.get("content-type", "") or url.endswith(".json") else yaml.safe_load(r.text)
            elif repo is not None and an and an.openapi_specs:
                p = repo.path / an.openapi_specs[0].path
                text = p.read_text(encoding="utf-8", errors="replace")
                data = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
        except (httpx.HTTPError, ValueError, yaml.YAMLError, AgentLabError, OSError) as exc:
            warnings.append(f"OpenAPI specification could not be read: {type(exc).__name__}: {str(exc)[:150]}")
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return analyze_openapi(data)
        return None

    async def _enrich(self, profile: AgentProfile, repo: RepositoryAnalysis | None, docs: list[AnalyzedDocument],
                      warnings: list[str]) -> None:
        """Ask a provider to summarise intent. Output is advisory and labelled; it never changes type scores."""
        from agentlab.providers import CompletionRequest, Message

        judges = self.config.evaluation.judges
        name = judges[0].provider if judges else self.providers.names()[0]
        context = [f"TARGET: {profile.target_name}", "DETECTED TYPES: " + ", ".join(
            f"{t.type.value}({t.confidence:.2f})" for t in profile.types[:6]),
            "TOOLS: " + ", ".join(f"{t.name} [{t.side_effects}]" for t in profile.tools[:20])]
        material = []
        if repo:
            material += [p.excerpt for p in repo.prompts[:3]]
        material += [i.text[:300] for d in docs[:2] for i in d.items[:3]]
        user = "\n".join(context) + "\n\n" + wrap_untrusted("repository", "\n---\n".join(material), max_chars=6000) + (
            "\n\nReturn JSON with a one-paragraph summary of what this agent is for, the expected user workflows, "
            "its likely limitations and the main risks. Do not invent capabilities that are not evidenced.")
        try:
            prov = self.providers.get(name)
            resp = await prov.complete(CompletionRequest(
                messages=[Message(role="system", content=EVALUATOR_POLICY), Message(role="user", content=user)],
                model=judges[0].model if judges else None, json_schema=ENRICH_SCHEMA, schema_name="enrichment",
                max_tokens=700, temperature=0.0))
            d = resp.parsed or {}
            if isinstance(d, dict):
                profile.expected_workflows = [str(x) for x in d.get("expected_workflows", [])][:10]
                profile.limitations += [f"(LLM-suggested, unverified) {x}" for x in d.get("limitations", [])][:5]
                profile.raw_signals["llm_enrichment"] = {"provider": name, "model": resp.model, "summary": d.get("summary", ""),
                                                         "risks": d.get("risks", []), "advisory": True}
                if not profile.summary or profile.summary.startswith(profile.target_name + ":"):
                    profile.summary = f"{d.get('summary', '')} (LLM-generated summary)"
        except AgentLabError as exc:
            warnings.append(f"LLM enrichment skipped ({exc.kind.value}): {str(exc)[:120]}")
        except Exception as exc:
            warnings.append(f"LLM enrichment skipped ({type(exc).__name__}): {str(exc)[:120]}")
