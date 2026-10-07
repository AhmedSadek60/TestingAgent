"""Agent fingerprinting (spec section 4): evidence-based multi-label classification.

Each agent type accumulates weighted evidence from independent sources (repository analysis,
declared configuration, the user's description, documents, and *observed* behaviour). Confidence is
a noisy-OR of the weights, so two independent weak signals beat one, and no single weak signal can
claim certainty. A target is never forced into one category.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from agentlab.adapters.openapi import OpenApiAnalysis
from agentlab.core.enums import AgentType, EvaluationMode, Support
from agentlab.core.models import (
    AgentProfile,
    ArchitectureGraph,
    CapabilityEntry,
    DataSource,
    Evidence,
    TargetSpec,
    ToolInfo,
    TypeScore,
)
from agentlab.discovery.probe import ProbeResult
from agentlab.documents.models import AnalyzedDocument
from agentlab.repository.models import RepositoryAnalysis
from agentlab.repository.signals import infer_side_effects

KEYWORDS: dict[AgentType, list[tuple[str, float]]] = {
    AgentType.RAG: [
        (
            r"\b(rag|retriev\w+|knowledge ?base|uploaded (docs|documents|pdfs?)|from (the )?(documents|docs|pdfs?))\b",
            0.6,
        )
    ],
    AgentType.TOOL_CALLING: [(r"\b(tools?|function[- ]calling|calls? (apis?|functions?))\b", 0.45)],
    AgentType.BROWSER: [
        (r"\b(browser|web ?ui|navigates? (the )?web|click(s|ing)? (buttons?|links?)|playwright|selenium)\b", 0.6)
    ],
    AgentType.COMPUTER_USE: [(r"\b(computer[- ]use|desktop automation|gui agent)\b", 0.7)],
    AgentType.CODING: [
        (r"\b(coding agent|edits? (a )?repositor\w+|writes? code|fix(es)? (bugs|issues)|pull requests?)\b", 0.7)
    ],
    AgentType.MULTI_AGENT: [(r"\b(multi[- ]agent|sub-?agents?|supervisor|orchestrator|delegat\w+|handoffs?)\b", 0.6)],
    AgentType.MCP: [(r"\b(mcp|model context protocol)\b", 0.7)],
    AgentType.PLANNING: [(r"\b(plan(s|ning)?|decompos\w+|multi-step)\b", 0.4)],
    AgentType.AUTONOMOUS: [(r"\b(autonomous|runs? on its own|self-directed|long[- ]running)\b", 0.6)],
    AgentType.MEMORY: [(r"\b(remembers?|long[- ]term memory|conversation history|personali[sz]ation)\b", 0.5)],
    AgentType.DOCUMENT: [(r"\b(document (processing|extraction)|summari[sz]es? (documents|pdfs?)|invoices?)\b", 0.6)],
    AgentType.RESEARCH: [(r"\b(research|web search|literature review|deep research)\b", 0.55)],
    AgentType.DATA_ANALYSIS: [(r"\b(data analysis|analy[sz]es? (data|csv|spreadsheets?)|sql|dashboards?)\b", 0.55)],
    AgentType.MULTIMODAL: [(r"\b(images?|vision|screenshots?|multimodal|ocr)\b", 0.45)],
    AgentType.VOICE: [(r"\b(voice|speech|phone calls?|text[- ]to[- ]speech)\b", 0.6)],
    AgentType.CHATBOT: [(r"\b(chat ?bot|customer support|answers questions|assistant)\b", 0.4)],
    AgentType.CONVERSATIONAL: [(r"\b(conversation\w*|multi-turn|dialog\w*)\b", 0.4)],
    AgentType.WORKFLOW: [(r"\b(workflow|pipeline|etl|automation)\b", 0.45)],
    AgentType.EVENT_DRIVEN: [(r"\b(webhooks?|event[- ]driven|triggers?)\b", 0.5)],
}

#: routes that take what a person says (an LLM behind one of these is a chatbot, whatever else it does)
CHAT_ROUTE = re.compile(
    r"/(?:v\d+/)?(?:chat|messages?|ask|converse|conversations?|completions?|prompt|talk|assistant)\b", re.I
)
HISTORY_SIGNAL = re.compile(r"chat[_ ]?history|message[s_ ]history|conversation|session[_ ]history|messages\b", re.I)

CAP_TO_TYPE: dict[str, list[tuple[AgentType, float]]] = {
    "tools": [(AgentType.TOOL_CALLING, 0.75)],
    "rag": [(AgentType.RAG, 0.75)],
    "memory": [(AgentType.MEMORY, 0.6)],
    "planning": [(AgentType.PLANNING, 0.55)],
    "multi_agent": [(AgentType.MULTI_AGENT, 0.75)],
    "browser": [(AgentType.BROWSER, 0.7)],
    "computer_use": [(AgentType.COMPUTER_USE, 0.75)],
    "coding": [(AgentType.CODING, 0.7), (AgentType.REPOSITORY, 0.5)],
    "mcp": [(AgentType.MCP, 0.8)],
    "multimodal": [(AgentType.MULTIMODAL, 0.55)],
    "voice": [(AgentType.VOICE, 0.55)],
    "long_running": [(AgentType.LONG_RUNNING, 0.5)],
    "event_driven": [(AgentType.EVENT_DRIVEN, 0.5)],
    "documents": [(AgentType.DOCUMENT, 0.4)],
}

CAPABILITY_TESTABILITY: dict[str, tuple[str, str, str]] = {
    # capability -> (AgentType-ish detection key, required interface hint, note when untestable)
    "tool_calling": (
        "tools",
        "any",
        "needs tool-call visibility (API events, MCP or LLM adapter) to judge tool selection",
    ),
    "rag": (
        "rag",
        "any",
        "groundedness is judged from retrieved contexts and citations when exposed; otherwise from answers",
    ),
    "memory": ("memory", "any", "needs sessions or multi-turn conversation"),
    "planning": ("planning", "any", "judged from observable plan steps or tool trajectories"),
    "browser": ("browser", "web", "needs a web interface and Playwright/Chromium"),
    "multi_agent": ("multi_agent", "any", "needs handoff/delegation events or a repository to map the topology"),
    "mcp": ("mcp", "mcp", "needs a reachable MCP endpoint (or a repository describing it)"),
    "coding": ("coding", "command", "needs a sandboxed command target and Docker"),
    "multimodal": ("multimodal", "any", "needs attachment support in the adapter"),
}


@dataclass
class FingerprintInputs:
    spec: TargetSpec
    repo: RepositoryAnalysis | None = None
    docs: list[AnalyzedDocument] = field(default_factory=list)
    openapi: OpenApiAnalysis | None = None
    probe: ProbeResult | None = None
    mcp_tools: list[ToolInfo] = field(default_factory=list)
    web: dict | None = None
    description: str | None = None
    available_interfaces: list[str] = field(default_factory=list)
    docker_available: bool = False
    browser_available: bool = False


def _takes_messages(inp: FingerprintInputs) -> bool:
    """Some interface accepts free-text messages. A tool server (MCP) and a coding agent that is started on a workspace
    with a task do not, so exposing only those says nothing about being a chatbot."""
    for iface in inp.available_interfaces:
        if iface == "mcp" or (iface == "command" and inp.spec.command is not None and inp.spec.command.mode == "task"):
            continue
        return True
    return False


def noisy_or(weights: list[float]) -> float:
    p = 1.0
    for w in weights:
        p *= 1 - min(0.99, max(0.0, w))
    return round(min(0.99, 1 - p), 3)


def _add(ev: dict[AgentType, list[Evidence]], t: AgentType, source: str, detail: str, w: float) -> None:
    ev[t].append(Evidence(source=source, detail=detail, weight=w))


SECONDARY_TYPES = {AgentType.MEMORY, AgentType.API, AgentType.HYBRID, AgentType.REPOSITORY, AgentType.LONG_RUNNING}


def classify(inp: FingerprintInputs) -> list[TypeScore]:
    ev: dict[AgentType, list[Evidence]] = defaultdict(list)
    spec, repo, probe = inp.spec, inp.repo, inp.probe
    text = " ".join(x for x in [spec.description, spec.objective, inp.description] if x)

    # --- user description keywords
    for t, pats in KEYWORDS.items():
        for rx, w in pats:
            m = re.search(rx, text, re.I)
            if m:
                _add(ev, t, "user description", f"mentions '{m.group(0)}'", w)
    # --- declared types / tools
    for d in spec.declared_types:
        try:
            _add(ev, AgentType(d), "declared by user", f"target.yaml declares '{d}'", 0.85)
        except ValueError:
            pass
    if spec.declared_tools:
        _add(ev, AgentType.TOOL_CALLING, "target config", f"{len(spec.declared_tools)} tool(s) declared", 0.85)
    if spec.mock:
        if spec.mock.tools:
            _add(ev, AgentType.TOOL_CALLING, "target config", f"mock agent declares tools {spec.mock.tools}", 0.8)
        if spec.mock.knowledge:
            _add(ev, AgentType.RAG, "target config", f"{len(spec.mock.knowledge)} knowledge document(s)", 0.8)
    if spec.llm:
        if spec.llm.tools:
            _add(
                ev,
                AgentType.TOOL_CALLING,
                "target config",
                f"LLM target declares tools {[t.name for t in spec.llm.tools]}",
                0.85,
            )
        if spec.llm.knowledge:
            _add(ev, AgentType.RAG, "target config", "knowledge documents are placed in the model context", 0.7)
    # --- repository
    if repo:
        for cap, on in repo.capabilities.items():
            if on:
                for t, w in CAP_TO_TYPE.get(cap, []):
                    srcs = ", ".join(
                        sorted({s.location.ref() for s in repo.signals if s.location and cap in s.detail})[:2]
                    )
                    _add(ev, t, "repository", f"capability '{cap}' detected" + (f" ({srcs})" if srcs else ""), w)
        if repo.tools and any(t.parameters.get("properties") for t in repo.tools):
            _add(ev, AgentType.FUNCTION_CALLING, "repository", "tools defined with typed parameter schemas", 0.6)
        names = " ".join(d.name.lower() + " " + (d.role or "").lower() for d in repo.agent_definitions)
        if re.search(r"supervisor|orchestrator|router|triage|manager", names):
            _add(ev, AgentType.SUPERVISOR, "repository", "agent named like a supervisor/router/triage", 0.7)
        if len(repo.agent_definitions) >= 2 and repo.capabilities.get("multi_agent"):
            _add(ev, AgentType.SUB_AGENTS, "repository", f"{len(repo.agent_definitions)} agent definitions", 0.6)
        if repo.apis or repo.openapi_specs:
            _add(ev, AgentType.API, "repository", f"{len(repo.apis)} HTTP route(s)/OpenAPI present", 0.5)
        chat_routes = [r for r in repo.apis if CHAT_ROUTE.search(r.path)]
        if (repo.model_providers or repo.models) and chat_routes:
            route = chat_routes[0]
            llm = ", ".join(repo.models[:2] or repo.model_providers[:2])
            _add(
                ev,
                AgentType.CHATBOT,
                "repository",
                f"{route.method} {route.path} ({route.location.ref()}) answers with an LLM ({llm})",
                0.7,
            )
            if any(s.kind == "memory" and HISTORY_SIGNAL.search(s.detail) for s in repo.signals):
                _add(ev, AgentType.CONVERSATIONAL, "repository", "keeps the conversation history of each session", 0.5)
        if any("react" in s.detail.lower() for s in repo.signals):
            _add(ev, AgentType.REACT, "repository", "ReAct-style agent loop", 0.6)
        if any("stategraph" in s.detail.lower() for s in repo.signals) and not repo.capabilities.get("multi_agent"):
            _add(ev, AgentType.WORKFLOW, "repository", "explicit state graph / pipeline", 0.4)
    # --- documents
    if inp.docs:
        _add(ev, AgentType.DOCUMENT, "documents", f"{len(inp.docs)} document(s) supplied", 0.3)
        if any(d.name.lower().endswith((".pdf", ".docx", ".md", ".txt")) for d in inp.docs) and (
            repo is None or repo.capabilities.get("rag") or "rag" in text.lower()
        ):
            _add(ev, AgentType.RAG, "documents", "knowledge documents supplied for evaluation", 0.3)
    # --- OpenAPI / interfaces
    if inp.openapi and inp.openapi.endpoints:
        _add(ev, AgentType.API, "openapi", f"{len(inp.openapi.endpoints)} endpoint(s) described", 0.7)
        if any(e.streaming for e in inp.openapi.endpoints):
            _add(ev, AgentType.CONVERSATIONAL, "openapi", "streaming chat endpoint", 0.4)
    if spec.api:
        _add(ev, AgentType.API, "target config", f"HTTP interface {spec.api.method} {spec.api.url}", 0.6)
    if spec.mcp or inp.mcp_tools:
        _add(ev, AgentType.MCP, "target config", "MCP interface configured", 0.85)
        if inp.mcp_tools:
            _add(ev, AgentType.TOOL_CALLING, "mcp discovery", f"{len(inp.mcp_tools)} MCP tool(s) listed", 0.9)
    if spec.web or inp.web:
        _add(ev, AgentType.BROWSER, "target config", "web interface configured", 0.35)
    if spec.command:
        _add(ev, AgentType.CODING, "target config", "runs as a sandboxed command", 0.3)
    # --- observed behaviour (strong)
    if probe and probe.reachable:
        if probe.tools_seen:
            _add(ev, AgentType.TOOL_CALLING, "probe", f"observed tool calls {probe.tools_seen}", 0.95)
        if probe.contexts_seen:
            _add(ev, AgentType.RAG, "probe", f"observed {probe.contexts_seen} retrieved context(s)", 0.9)
        if probe.session_memory:
            _add(ev, AgentType.MEMORY, "probe", "recalled a fact within a session", 0.8)
            _add(ev, AgentType.CONVERSATIONAL, "probe", "multi-turn recall works", 0.8)
        if "handoff" in probe.event_types:
            _add(ev, AgentType.MULTI_AGENT, "probe", "observed handoff events", 0.9)
        if "plan_step" in probe.event_types:
            _add(ev, AgentType.PLANNING, "probe", "observed plan steps", 0.85)
        if "browser_action" in probe.event_types:
            _add(ev, AgentType.BROWSER, "probe", "observed browser actions", 0.9)
        if probe.self_reported_tools:
            _add(
                ev,
                AgentType.TOOL_CALLING,
                "probe (self-reported)",
                f"agent claims tools {probe.self_reported_tools[:4]}",
                0.3,
            )
        if not probe.tools_seen and not probe.contexts_seen and any(o.output for o in probe.observations):
            _add(ev, AgentType.CHATBOT, "probe", "answers conversationally without observable tools or retrieval", 0.55)
        if probe.streaming:
            _add(ev, AgentType.CONVERSATIONAL, "probe", "streaming responses", 0.3)
    # --- baseline: any conversational interface is at least a chatbot
    if _takes_messages(inp) and not ev.get(AgentType.CHATBOT):
        _add(ev, AgentType.CHATBOT, "interface", "exposes a conversational interface", 0.4)
    if (
        any(t in ev for t in (AgentType.TOOL_CALLING, AgentType.RAG))
        and not ev.get(AgentType.CONVERSATIONAL)
        and (probe is None or probe.reachable)
    ):
        _add(ev, AgentType.CONVERSATIONAL, "interface", "natural-language interface", 0.3)

    scores = [TypeScore(type=t, confidence=noisy_or([e.weight for e in evs]), evidence=evs) for t, evs in ev.items()]
    strong = [
        s
        for s in scores
        if s.confidence >= 0.6
        and s.type not in {AgentType.CHATBOT, AgentType.CONVERSATIONAL, AgentType.API, AgentType.HYBRID}
    ]
    if len(strong) >= 3:
        scores.append(
            TypeScore(
                type=AgentType.HYBRID,
                confidence=min(0.95, 0.6 + 0.1 * len(strong)),
                evidence=[
                    Evidence(
                        source="fingerprint",
                        detail="combines " + ", ".join(sorted(s.type.value for s in strong)),
                        weight=0.8,
                    )
                ],
            )
        )
    # equally confident types: what the agent *is* before what it merely has (a chatbot remembers a name; it is not
    # first a "memory agent")
    return sorted(scores, key=lambda s: (-round(s.confidence, 2), s.type in SECONDARY_TYPES))


def build_tools(inp: FingerprintInputs) -> list[ToolInfo]:
    tools: dict[str, ToolInfo] = {}

    def add(t: ToolInfo) -> None:
        cur = tools.get(t.name)
        if cur is None or (not cur.parameters and t.parameters) or (cur.source == "unknown"):
            tools[t.name] = t
        elif cur and t.source not in cur.source:
            cur.source += f", {t.source}"

    spec = inp.spec
    for d in spec.declared_tools:
        n = d.get("name")
        if n:
            add(
                ToolInfo(
                    name=n,
                    description=d.get("description", ""),
                    parameters=d.get("parameters", {}),
                    source="target config",
                    side_effects=d.get("side_effects") or infer_side_effects(n, d.get("description", "")),
                    requires_confirmation=d.get("requires_confirmation"),
                )
            )
    if spec.mock:
        for n in spec.mock.tools:
            add(ToolInfo(name=n, source="target config (mock)", side_effects=infer_side_effects(n, "")))
    if spec.llm:
        for lt in spec.llm.tools:
            add(
                ToolInfo(
                    name=lt.name,
                    description=lt.description,
                    parameters=lt.parameters,
                    source="target config (llm)",
                    side_effects=lt.side_effects
                    if lt.side_effects != "none"
                    else infer_side_effects(lt.name, lt.description),
                )
            )
    if inp.repo:
        for rt in inp.repo.tools:
            add(
                ToolInfo(
                    name=rt.name,
                    description=rt.description,
                    parameters=rt.parameters,
                    source=f"repository {rt.location.ref()}",
                    side_effects=rt.side_effects,
                    requires_confirmation=rt.requires_confirmation,
                )
            )
    for mt in inp.mcp_tools:
        add(mt)
    if inp.probe:
        for n in inp.probe.tools_seen:
            add(ToolInfo(name=n, source="observed during probing", side_effects=infer_side_effects(n, "")))
    return sorted(tools.values(), key=lambda t: t.name)


def build_capability_matrix(
    types: list[TypeScore], inp: FingerprintInputs, tools: list[ToolInfo]
) -> list[CapabilityEntry]:
    by = {s.type: s.confidence for s in types}
    ifaces = set(inp.available_interfaces)
    out: list[CapabilityEntry] = []

    def entry(cap: str, detected: bool, testable: Support, reason: str) -> None:
        out.append(CapabilityEntry(capability=cap, detected=detected, testable=testable, reason=reason))

    chat_command = "command" in ifaces and (inp.spec.command is None or inp.spec.command.mode == "chat")
    conv = bool(ifaces & {"mock", "llm", "api", "web"}) or chat_command
    entry(
        "conversation",
        bool(by.get(AgentType.CHATBOT) or by.get(AgentType.CONVERSATIONAL)),
        Support.SUPPORTED if conv else Support.UNSUPPORTED,
        "reachable through a configured interface" if conv else "no interface that accepts messages is available",
    )
    entry(
        "tool_calling",
        by.get(AgentType.TOOL_CALLING, 0) >= 0.5,
        Support.SUPPORTED if (ifaces & {"mock", "llm", "api", "mcp"}) else Support.PARTIAL,
        f"{len(tools)} tool(s) in inventory; tool calls are only observable if the interface reports them",
    )
    entry(
        "rag",
        by.get(AgentType.RAG, 0) >= 0.5,
        Support.SUPPORTED if conv else Support.UNSUPPORTED,
        "groundedness uses retrieved contexts/citations when exposed, otherwise answer-vs-document checks",
    )
    entry(
        "memory",
        by.get(AgentType.MEMORY, 0) >= 0.5,
        Support.SUPPORTED if conv else Support.UNSUPPORTED,
        "tested with multi-turn and cross-session conversations",
    )
    entry(
        "planning",
        by.get(AgentType.PLANNING, 0) >= 0.5,
        Support.PARTIAL,
        "judged from observable plan steps and tool trajectories, never hidden reasoning",
    )
    entry(
        "multi_agent",
        by.get(AgentType.MULTI_AGENT, 0) >= 0.5,
        Support.PARTIAL if conv else Support.UNSUPPORTED,
        "topology from repository analysis; delegation from handoff events when exposed",
    )
    entry(
        "browser",
        by.get(AgentType.BROWSER, 0) >= 0.5 or "web" in ifaces,
        Support.SUPPORTED if ("web" in ifaces and inp.browser_available) else Support.UNSUPPORTED,
        "Playwright + Chromium available"
        if "web" in ifaces and inp.browser_available
        else "requires a web interface and an installed Playwright browser",
    )
    entry(
        "mcp",
        by.get(AgentType.MCP, 0) >= 0.5,
        Support.SUPPORTED
        if "mcp" in ifaces
        else (Support.PARTIAL if inp.repo and inp.repo.mcp else Support.UNSUPPORTED),
        "MCP client adapter available"
        if "mcp" in ifaces
        else "no MCP endpoint configured; only static analysis possible",
    )
    entry(
        "coding",
        by.get(AgentType.CODING, 0) >= 0.5,
        Support.SUPPORTED if ("command" in ifaces and inp.docker_available) else Support.UNSUPPORTED,
        "disposable Docker workspace"
        if inp.docker_available
        else "Docker is unavailable; coding agents are never run on the host",
    )
    entry(
        "multimodal",
        by.get(AgentType.MULTIMODAL, 0) >= 0.5,
        Support.PARTIAL if ifaces else Support.UNSUPPORTED,
        "needs attachment support in the adapter; image understanding is judged only with a multimodal judge",
    )
    entry(
        "voice",
        by.get(AgentType.VOICE, 0) >= 0.5,
        Support.UNSUPPORTED,
        "voice/audio testing is not implemented in this build",
    )
    entry(
        "long_running",
        by.get(AgentType.LONG_RUNNING, 0) >= 0.5,
        Support.PARTIAL,
        "bounded by max_execution_time and step limits; background jobs are not polled",
    )
    return out


def attack_surfaces(types: list[TypeScore], inp: FingerprintInputs, tools: list[ToolInfo]) -> list[str]:
    by = {s.type: s.confidence for s in types}
    out = [
        "Direct user input to the conversational interface (prompt injection, instruction override, secret extraction)"
    ]
    if by.get(AgentType.RAG, 0) >= 0.5:
        out.append("Retrieved documents (indirect prompt injection, poisoned or conflicting sources)")
    if tools:
        risky = [t.name for t in tools if t.side_effects in {"write", "external", "destructive"}]
        out.append(
            "Tool inputs and outputs (unauthorised use, argument manipulation, poisoned tool results)"
            + (f"; side-effecting tools: {risky[:6]}" if risky else "")
        )
    if by.get(AgentType.MCP, 0) >= 0.5:
        out.append("MCP servers (malicious tool descriptions, poisoned output, cross-server escalation)")
    if by.get(AgentType.MEMORY, 0) >= 0.5:
        out.append("Memory store (cross-session/cross-user leakage, memory poisoning)")
    if by.get(AgentType.BROWSER, 0) >= 0.5:
        out.append("Web content seen by the browser agent (injected page instructions, unsafe actions)")
    if by.get(AgentType.CODING, 0) >= 0.5:
        out.append(
            "Repository content and commands (malicious README/issues, secret exposure, unsafe command execution)"
        )
    if by.get(AgentType.MULTI_AGENT, 0) >= 0.5:
        out.append("Inter-agent messages (impersonation, privilege escalation, delegation loops)")
    if inp.spec.api or inp.spec.web:
        out.append("Network interface (authentication, authorisation boundaries, SSRF-like fetch behaviour)")
    if inp.repo and inp.repo.secrets_found:
        out.append("Secrets committed to the repository")
    return out


def expected_limitations(types: list[TypeScore], inp: FingerprintInputs) -> list[str]:
    lims = []
    if (
        inp.probe
        and not inp.probe.tools_seen
        and any(s.type == AgentType.TOOL_CALLING and s.confidence >= 0.5 for s in types)
    ):
        lims.append(
            "Tool calls are not observable through the interface, so tool selection/arguments can only be judged from answers"
        )
    if inp.spec.interfaces() == ["api"] and not (inp.openapi and inp.openapi.endpoints):
        lims.append("No OpenAPI contract was found; request/response mapping was taken from configuration")
    if not inp.docker_available:
        lims.append("Docker is unavailable: repository execution and coding-agent tests are blocked (fail-closed)")
    if not inp.browser_available and inp.spec.web:
        lims.append("Playwright/Chromium is unavailable: browser tests are blocked")
    return lims


def evaluation_modes(inp: FingerprintInputs) -> list[EvaluationMode]:
    black = bool(inp.available_interfaces) or bool(inp.spec.interfaces())
    white = inp.repo is not None
    modes = []
    if black:
        modes.append(EvaluationMode.BLACK_BOX)
    if white:
        modes.append(EvaluationMode.WHITE_BOX)
    if black and white:
        modes.append(EvaluationMode.HYBRID)
    return modes


def authentication_model(inp: FingerprintInputs) -> dict:
    info: dict = {"required": None, "schemes": [], "credential_profiles": list(inp.spec.credentials)}
    if inp.openapi and inp.openapi.security_schemes:
        info["schemes"] = [f"{k} ({v})" for k, v in inp.openapi.security_schemes.items()]
        info["required"] = True
    if inp.spec.api and inp.spec.api.auth_credential:
        info["required"] = True
        info["schemes"].append(f"credential profile '{inp.spec.api.auth_credential}'")
    if inp.spec.web and inp.spec.web.auth_credential:
        info["required"] = True
        info["schemes"].append(f"web login via '{inp.spec.web.auth_credential}'")
    if inp.probe and any((o.status_code or 0) in (401, 403) for o in inp.probe.observations):
        info["required"] = True
        info["schemes"].append("endpoint answered 401/403 during probing")
    if info["required"] is None and (inp.spec.api or inp.spec.web):
        info["required"] = False
    return info


def _models(spec: TargetSpec, repo: RepositoryAnalysis | None) -> list[str]:
    """The models behind the agent: the one the owner named for an ``llm`` target (declared, so first), then the ones the
    repository's code refers to (inferred). A target with neither says nothing, rather than a guess."""
    declared = [f"{spec.llm.provider}:{spec.llm.model}" if spec.llm.model else spec.llm.provider] if spec.llm else []
    return list(dict.fromkeys([*declared, *(repo.models if repo else [])]))


def build_profile(inp: FingerprintInputs) -> AgentProfile:
    types = classify(inp)
    tools = build_tools(inp)
    spec, repo = inp.spec, inp.repo
    graph = ArchitectureGraph()
    if repo:
        graph = repo.architecture
    else:
        graph.add_node("user", "User", "actor")
        graph.add_node("target", spec.name, "agent")
        graph.add_edge("user", "target")
        for t in tools[:20]:
            graph.add_node(f"tool:{t.name}", t.name, "tool")
            graph.add_edge("target", f"tool:{t.name}")
        if any(s.type == AgentType.RAG and s.confidence >= 0.5 for s in types):
            graph.add_node("kb", "Knowledge base", "retrieval")
            graph.add_edge("target", "kb")
    data_sources: list[DataSource] = []
    for d in inp.docs:
        data_sources.append(
            DataSource(
                name=d.name,
                kind="document",
                source="uploaded",
                details={"pages": d.pages, "items": len(d.items), "version": d.version},
            )
        )
    if spec.mock and spec.mock.knowledge:
        for n in spec.mock.knowledge:
            data_sources.append(DataSource(name=n, kind="knowledge", source="target config"))
    if repo:
        for v in repo.vector_databases:
            data_sources.append(DataSource(name=v, kind="vector_store", source="repository"))
        for db in repo.databases:
            data_sources.append(DataSource(name=db, kind="database", source="repository"))
    by = {s.type: s.confidence for s in types}
    probe = inp.probe
    observed = list(probe.handoffs) if probe else []
    for source, target in observed:  # who was seen handing work to whom (behaviour, not code)
        for name in (source, target):
            if name != "?":
                graph.add_node(f"observed:{name}", name, "agent (observed)")
        graph.add_edge(f"observed:{source}", f"observed:{target}", "delegates (observed)")
    profile = AgentProfile(
        target_name=spec.name,
        summary=spec.description
        or spec.objective
        or (f"{spec.name}: " + ", ".join(f"{s.type.value} ({s.confidence:.2f})" for s in types[:4])),
        modes=evaluation_modes(inp),
        types=types,
        interfaces=sorted(set(inp.available_interfaces) | set(spec.interfaces())),
        authentication=authentication_model(inp),
        tools=tools,
        data_sources=data_sources,
        memory={
            "detected": by.get(AgentType.MEMORY, 0) >= 0.5,
            "session_recall_observed": bool(probe and probe.session_memory),
            "systems": repo.memory_systems if repo else [],
        },
        rag={
            "detected": by.get(AgentType.RAG, 0) >= 0.5,
            "vector_stores": repo.vector_databases if repo else [],
            "contexts_observed": probe.contexts_seen if probe else 0,
            "sanitisation_layer": bool(repo and repo.capabilities.get("guardrails")),
        },
        browser={
            "detected": by.get(AgentType.BROWSER, 0) >= 0.5,
            "frameworks": repo.browser_frameworks if repo else [],
            "web_interface": spec.web.url if spec.web else None,
            "page": inp.web or {},
        },
        multi_agent={
            "detected": by.get(AgentType.MULTI_AGENT, 0) >= 0.5,
            "agents": sorted({d.name for d in repo.agent_definitions}) if repo else [],
            "observed_agents": sorted({name for pair in observed for name in pair if name != "?"}),
            "handoffs": [list(pair) for pair in observed],
            "handoffs_observed": bool(probe and "handoff" in probe.event_types),
        },
        mcp={
            "detected": by.get(AgentType.MCP, 0) >= 0.5,
            "servers": [m.name for m in repo.mcp] if repo else [],
            "tools": [t.name for t in inp.mcp_tools],
        },
        models=_models(spec, repo),
        frameworks=repo.frameworks if repo else [],
        languages=repo.languages if repo else {},
        expected_workflows=[],
        limitations=expected_limitations(types, inp),
        attack_surfaces=attack_surfaces(types, inp, tools),
        capability_matrix=build_capability_matrix(types, inp, tools),
        architecture=graph,
        knowledge_items=sum(len(d.items) for d in inp.docs),
        documents=[d.name for d in inp.docs],
        repository={
            "name": repo.root_name,
            "commit": repo.commit,
            "url": repo.url,
            "files": repo.file_count,
            "providers": repo.model_providers,
            "deployment": repo.deployment,
            "env": repo.env_requirements,
            "tests": len(repo.tests),
            "entry_points": [e.path for e in repo.entry_points][:6],
            "warnings": repo.warnings[:10],
            "guidance_files": [g.path for g in repo.guidance_files],
        }
        if repo
        else {},
        raw_signals={
            "probe": probe.model_dump() if probe else None,
            "openapi": inp.openapi.model_dump() if inp.openapi else None,
            "repo_signals": [s.model_dump() for s in (repo.signals[:40] if repo else [])],
        },
    )
    profile.strategy = strategy_for(profile)
    return profile


def strategy_for(p: AgentProfile) -> list[str]:
    s = ["Run discovery-safe functional tests first (normal, invalid, empty, ambiguous, long and repeated inputs)."]
    for t, line in [
        (
            AgentType.RAG,
            "RAG: groundedness, citations, unsupported questions, conflicting/outdated documents, document injection.",
        ),
        (
            AgentType.TOOL_CALLING,
            "Tools: selection, arguments, necessity, failure handling, confirmation and authorisation per tool.",
        ),
        (AgentType.MEMORY, "Memory: recall, update, session/user isolation, poisoning."),
        (AgentType.PLANNING, "Planning: decomposition, ordering, replanning, loop and step limits."),
        (AgentType.MULTI_AGENT, "Multi-agent: routing, handoffs, role boundaries, delegation cycles."),
        (AgentType.MCP, "MCP: tool discovery, malicious tool descriptions, poisoned outputs, permission boundaries."),
        (AgentType.BROWSER, "Browser: navigation and form flows, expected vs actual actions, unsafe actions."),
        (
            AgentType.CODING,
            "Coding: disposable workspace, diff correctness, unrelated changes, secret handling, injected instructions.",
        ),
    ]:
        if p.has_type(t, 0.5):
            s.append(line)
    s.append(
        "Security: prompt injection, secret/canary leakage, excessive agency, runaway loops - synthetic canaries only."
    )
    s.append("Reliability: repeat risky tests; report pass rate and flakiness instead of a single PASS.")
    return s
