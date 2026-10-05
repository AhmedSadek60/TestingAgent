"""Discovery outputs: the target profile, capability matrix and architecture graph."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentlab.core.enums import AgentType, EvaluationMode, Support
from agentlab.core.models.base import Model


class Evidence(Model):
    source: str
    detail: str
    weight: float = 0.5


class TypeScore(Model):
    type: AgentType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class ToolInfo(Model):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: str = "unknown"
    side_effects: str = Field(default="unknown", description="none | read | write | external | destructive")
    requires_confirmation: bool | None = None


class DataSource(Model):
    name: str
    kind: str
    source: str
    details: dict[str, Any] = Field(default_factory=dict)


class ArchNode(Model):
    id: str
    label: str
    kind: str


class ArchEdge(Model):
    source: str
    target: str
    label: str = ""


class ArchitectureGraph(Model):
    nodes: list[ArchNode] = Field(default_factory=list)
    edges: list[ArchEdge] = Field(default_factory=list)

    def add_node(self, id: str, label: str, kind: str) -> None:
        if not any(n.id == id for n in self.nodes):
            self.nodes.append(ArchNode(id=id, label=label, kind=kind))

    def add_edge(self, source: str, target: str, label: str = "") -> None:
        if not any(e.source == source and e.target == target for e in self.edges):
            self.edges.append(ArchEdge(source=source, target=target, label=label))

    def to_mermaid(self) -> str:
        def nid(x: str) -> str:
            return "".join(c if c.isalnum() else "_" for c in x)

        lines = ["graph TD"]
        for n in self.nodes:
            label = n.label.replace('"', "'")
            lines.append(f'    {nid(n.id)}["{label}"]')
        for e in self.edges:
            lbl = f"|{e.label}|" if e.label else ""
            lines.append(f"    {nid(e.source)} -->{lbl} {nid(e.target)}")
        return "\n".join(lines)


class CapabilityEntry(Model):
    capability: str
    detected: bool
    testable: Support
    reason: str = ""


class AgentProfile(Model):
    """Output of the TargetDiscoveryAgent (spec section 5)."""

    target_name: str
    summary: str = ""
    modes: list[EvaluationMode] = Field(default_factory=list)
    types: list[TypeScore] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    authentication: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolInfo] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    rag: dict[str, Any] = Field(default_factory=dict)
    browser: dict[str, Any] = Field(default_factory=dict)
    multi_agent: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    expected_workflows: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    attack_surfaces: list[str] = Field(default_factory=list)
    capability_matrix: list[CapabilityEntry] = Field(default_factory=list)
    architecture: ArchitectureGraph = Field(default_factory=ArchitectureGraph)
    strategy: list[str] = Field(default_factory=list)
    knowledge_items: int = 0
    documents: list[str] = Field(default_factory=list)
    repository: dict[str, Any] = Field(default_factory=dict)
    raw_signals: dict[str, Any] = Field(default_factory=dict)

    def has_type(self, t: AgentType, threshold: float = 0.5) -> bool:
        return any(s.type == t and s.confidence >= threshold for s in self.types)

    def type_confidence(self, t: AgentType) -> float:
        return max((s.confidence for s in self.types if s.type == t), default=0.0)

    def tool(self, name: str) -> ToolInfo | None:
        return next((t for t in self.tools if t.name == name), None)
